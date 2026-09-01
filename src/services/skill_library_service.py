from __future__ import annotations

import hashlib
import base64
import json
import logging
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

import requests

from src.config.config_manager import AppConfig
from src.observability.langsmith_runtime import trace_operation
from src.providers.deepseek_provider import DeepSeekMessage, DeepSeekProvider, parse_json_object_from_text


@dataclass(frozen=True)
class SkillSummary:
    """用于 WebUI 列表展示的一条 Skill 摘要。"""

    id: str
    name: str
    description: str
    description_zh: str
    author: str
    homepage_url: str | None
    repository_full_name: str | None
    stars: int | None
    previous_stars: int | None
    star_delta: int | None
    star_growth_rate: float | None
    stars_updated_at: str | None
    source: str
    source_label: str
    path_hint: str
    editable: bool


@dataclass(frozen=True)
class SkillDetail:
    """用于 WebUI 查看和编辑的一条 Skill 详情。"""

    summary: SkillSummary
    markdown: str
    # 仅供服务端 Skill Runtime 解析相对资源和 ${SKILL_DIR}；Web API 不返回该字段。
    skill_path: Path | None = None


@dataclass(frozen=True)
class SkillSearchItem:
    """DS4Pro 或本地回退检索返回的一条候选结果。"""

    skill: SkillSummary
    score: int
    match_reason: str
    markdown: str | None = None


@dataclass(frozen=True)
class SkillSearchResult:
    """Skill 搜索接口的结构化结果。"""

    items: list[SkillSearchItem]
    used_llm: bool
    model: str | None
    fallback_reason: str | None
    search_scope: str
    normalized_query: str
    status_message: str = ""
    cache_hit: bool = False
    elapsed_ms: int = 0
    snapshot_at: str | None = None
    cache_state: str = "local"
    data_source: str = "local_installed"


class SkillSearchBudgetExceeded(RuntimeError):
    """GitHub 开放 Skill 检索超过页面可接受的时间预算。"""


@dataclass(frozen=True)
class SkillSaveResult:
    """Skill 保存接口的结构化结果。"""

    skill: SkillSummary
    markdown: str
    created: bool
    saved_path: Path


@dataclass(frozen=True)
class SkillRepositoryItem:
    """GitHub 仓库中一份可独立安装的 Agent Skill。"""

    name: str
    description: str
    path: str
    file_count: int
    size_bytes: int


@dataclass(frozen=True)
class SkillRepositoryInspection:
    """远端 Skill 仓库的真实元数据与可安装清单。"""

    repository_full_name: str
    default_branch: str
    homepage_url: str
    stars: int
    forks: int
    license_spdx: str | None
    pushed_at: str | None
    skills: tuple[SkillRepositoryItem, ...]


@dataclass(frozen=True)
class SkillRepositoryInstallResult:
    """一次项目级 Skill 安装的结果摘要。"""

    repository_full_name: str
    installed_names: tuple[str, ...]
    skipped_names: tuple[str, ...]
    installed_file_count: int


class SkillLibraryService:
    """管理本地已安装 Skill 的读取、检索和保存。

    这个服务是 WebUI 的文件层适配器，不触碰业务数据库，也不修改系统/插件 Skill。
    保存时统一写入项目内 `.agents/skills/<skill-name>/SKILL.md`，这样可以安全地
    新增或覆盖项目本地副本，并让列表刷新后立即看到结果。
    """

    _frontmatter_pattern = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*", re.DOTALL)
    _name_pattern = re.compile(r"^name:\s*(?P<value>.+?)\s*$", re.MULTILINE)
    _description_pattern = re.compile(r"^description:\s*(?P<value>.+?)\s*$", re.MULTILINE)
    _frontmatter_field_pattern_template = r"^{field}:\s*(?P<value>.+?)\s*$"
    _skill_name_pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,79}$")

    # WebUI 的搜索请求不能被外部服务拖到几十秒。这里的时间预算覆盖一次查询改写、
    # GitHub Code Search 和候选 SKILL.md 并行读取；到时立即回退为本地结果。
    open_skill_search_budget_seconds = 9.0
    github_code_search_timeout_seconds = 3.5
    github_file_timeout_seconds = 3.0
    github_file_fetch_workers = 4
    github_file_candidate_limit = 8
    github_result_limit = 8
    open_skill_cache_ttl = timedelta(hours=6)
    open_skill_cache_max_entries = 40

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.project_skill_root = (self.config.project_root / ".agents" / "skills").resolve()
        self.star_cache_path = (self.config.project_root / "data" / "skill_star_cache.json").resolve()
        self.open_skill_cache_path = (self.config.project_root / "data" / "skill_search_cache.json").resolve()
        self.star_cache_ttl = timedelta(days=7)
        self.logger = logging.getLogger(__name__)

    def list_skills(self) -> list[SkillSummary]:
        """扫描当前可读的 Skill 目录，并返回去重后的摘要列表。"""

        summaries: dict[str, SkillSummary] = {}
        repository_by_name = self._repository_full_names_from_install_locks()
        for skill_path, source, source_label in self._iter_skill_files():
            try:
                markdown = self._read_text(skill_path)
            except OSError:
                continue

            name, description = self._parse_frontmatter(markdown, fallback_name=skill_path.parent.name)
            author = self._frontmatter_field(markdown, ["author", "authors", "maintainer"]) or source_label
            homepage_url = self._frontmatter_field(markdown, ["homepage", "url", "repo", "repository", "link"])
            repository_full_name = self._repository_full_name_for_skill(
                name=name,
                homepage_url=homepage_url,
                path_hint=self._path_hint(skill_path),
                markdown=markdown,
                repository_by_name=repository_by_name,
            )
            # 列表页必须只读本地缓存。Star 的周期刷新不能阻塞首次进入技能库。
            star_snapshot = self._repository_star_snapshot(repository_full_name, refresh_if_stale=False)
            skill_id = self._skill_id(skill_path)
            summary = SkillSummary(
                id=skill_id,
                name=name,
                description=description,
                description_zh=self._local_chinese_description(name=name, description=description),
                author=author,
                homepage_url=homepage_url,
                repository_full_name=repository_full_name,
                stars=star_snapshot["stars"],
                previous_stars=star_snapshot["previous_stars"],
                star_delta=star_snapshot["star_delta"],
                star_growth_rate=star_snapshot["star_growth_rate"],
                stars_updated_at=star_snapshot["stars_updated_at"],
                source=source,
                source_label=source_label,
                path_hint=self._path_hint(skill_path),
                editable=self._is_project_skill(skill_path),
            )

            existing = summaries.get(name)
            if existing is None or self._source_priority(summary.source) < self._source_priority(existing.source):
                summaries[name] = summary

        return sorted(
            summaries.values(),
            key=lambda item: (
                0 if item.name == "find-skills" else 1,
                self._source_priority(item.source),
                item.name.lower(),
            ),
        )

    def get_skill(self, skill_id: str) -> SkillDetail:
        """根据前端传入的稳定 id 读取 Skill.md。"""

        skill_path = self._resolve_skill_path(skill_id)
        markdown = self._read_text(skill_path)
        name, description = self._parse_frontmatter(markdown, fallback_name=skill_path.parent.name)
        source_label = self._source_label_for_path(skill_path)
        homepage_url = self._frontmatter_field(markdown, ["homepage", "url", "repo", "repository", "link"])
        path_hint = self._path_hint(skill_path)
        repository_full_name = self._repository_full_name_for_skill(
            name=name,
            homepage_url=homepage_url,
            path_hint=path_hint,
            markdown=markdown,
            repository_by_name=self._repository_full_names_from_install_locks(),
        )
        star_snapshot = self._repository_star_snapshot(repository_full_name, refresh_if_stale=False)
        summary = SkillSummary(
            id=self._skill_id(skill_path),
            name=name,
            description=description,
            description_zh=self._local_chinese_description(name=name, description=description),
            author=self._frontmatter_field(markdown, ["author", "authors", "maintainer"]) or source_label,
            homepage_url=homepage_url,
            repository_full_name=repository_full_name,
            stars=star_snapshot["stars"],
            previous_stars=star_snapshot["previous_stars"],
            star_delta=star_snapshot["star_delta"],
            star_growth_rate=star_snapshot["star_growth_rate"],
            stars_updated_at=star_snapshot["stars_updated_at"],
            source=self._source_for_path(skill_path),
            source_label=source_label,
            path_hint=path_hint,
            editable=self._is_project_skill(skill_path),
        )
        return SkillDetail(summary=summary, markdown=markdown, skill_path=skill_path)

    def refresh_stale_star_snapshots(self) -> dict[str, int]:
        """按周刷新本地 Skill 的 GitHub Star 快照，供独立定时命令调用。

        Web 请求只消费缓存；该方法特意不从 ``list_skills`` 调用，避免用户打开技能库
        时触发一串 GitHub API 请求。脚本或计划任务可每天运行一次，实际只有过期（七天）
        的仓库会访问网络。
        """

        repositories: set[str] = set()
        repository_by_name = self._repository_full_names_from_install_locks()
        for skill_path, _source, _source_label in self._iter_skill_files():
            try:
                markdown = self._read_text(skill_path)
            except OSError:
                continue
            name, _description = self._parse_frontmatter(markdown, fallback_name=skill_path.parent.name)
            homepage_url = self._frontmatter_field(markdown, ["homepage", "url", "repo", "repository", "link"])
            repository = self._repository_full_name_for_skill(
                name=name,
                homepage_url=homepage_url,
                path_hint=self._path_hint(skill_path),
                markdown=markdown,
                repository_by_name=repository_by_name,
            )
            if repository:
                repositories.add(repository)

        refreshed = 0
        unchanged = 0
        failed = 0
        for repository in sorted(repositories):
            before = self._repository_star_snapshot(repository, refresh_if_stale=False)
            try:
                after = self._repository_star_snapshot(repository, refresh_if_stale=True)
            except Exception:
                failed += 1
                continue
            if after["stars"] is None and before["stars"] is None:
                failed += 1
            elif after["stars_updated_at"] != before["stars_updated_at"]:
                refreshed += 1
            else:
                unchanged += 1

        return {
            "repositories": len(repositories),
            "refreshed": refreshed,
            "unchanged": unchanged,
            "failed": failed,
        }

    def search_skills(self, query: str, *, force_refresh: bool = False) -> SkillSearchResult:
        """搜索 Skill，并只向 LangSmith 暴露检索规模与执行结果元数据。"""

        return trace_operation(
            run_name="skills.search",
            run_type="chain",
            inputs={"query_characters": len(query.strip()), "force_refresh": force_refresh},
            metadata={
                "component": "skill_library",
                "search_scope": "github_open_skill",
            },
            tags=("skills", "search", "github"),
            execute=(
                lambda: self._search_skills_untraced(query, force_refresh=True)
                if force_refresh
                else self._search_skills_untraced(query)
            ),
            summarize=self._summarize_search_trace,
        )

    @staticmethod
    def _summarize_search_trace(result: SkillSearchResult) -> dict[str, Any]:
        """构造不包含关键词、链接、正文、路径或用户标识的检索摘要。"""

        return {
            "status": "completed",
            "result_count": len(result.items),
            "used_llm": result.used_llm,
            "cache_hit": result.cache_hit,
            "elapsed_ms": result.elapsed_ms,
            "search_scope": result.search_scope,
            "model": result.model or "none",
        }

    def _search_skills_untraced(self, query: str, *, force_refresh: bool = False) -> SkillSearchResult:
        """搜索 GitHub 开放 Skill，并在限定时间内保持页面可用。

        旧实现会在一次 HTTP 请求中串行执行两次 LLM、最多八次文件下载和八次 Star
        请求。任何一项慢网络都会让浏览器一直转圈。现在的顺序是：先取得本地回退
        结果和缓存，再在九秒预算内完成真正的 GitHub 搜索；Star 一律只读取周缓存。
        """

        started_at = time.monotonic()
        normalized_query = self._normalize_open_skill_cache_query(query)
        skills = self.list_skills()
        if not normalized_query:
            return SkillSearchResult(
                items=[
                    SkillSearchItem(skill=skill, score=90, match_reason="未输入关键词，展示已安装 Skill。")
                    for skill in skills[:20]
                ],
                used_llm=False,
                model=None,
                fallback_reason=None,
                search_scope="local_installed",
                normalized_query="",
                status_message="已展示本地已安装 Skill。",
                elapsed_ms=self._elapsed_ms(started_at),
                cache_state="local",
                data_source="local_installed",
            )

        cached_snapshot = self._read_open_skill_search_cache(normalized_query)
        if cached_snapshot is not None and not force_refresh:
            cached_result, created_at, is_fresh = cached_snapshot
            return replace(
                cached_result,
                cache_hit=True,
                status_message=(
                    "已读取最近 6 小时的 GitHub Skill 关键词快照；Star 数据来自周快照。"
                    if is_fresh
                    else "当前展示历史 GitHub Skill 关键词快照；如需更新，请点击“刷新 GitHub”。"
                ),
                elapsed_ms=self._elapsed_ms(started_at),
                snapshot_at=created_at.isoformat(),
                cache_state="fresh" if is_fresh else "stale",
                data_source="github_snapshot",
            )

        deadline = started_at + self.open_skill_search_budget_seconds
        try:
            open_result = self._search_open_skills(query=normalized_query, deadline=deadline)
        except SkillSearchBudgetExceeded:
            if cached_snapshot is not None:
                cached_result, created_at, _is_fresh = cached_snapshot
                return replace(
                    cached_result,
                    cache_hit=True,
                    fallback_reason="GitHub 刷新超时，当前继续展示上次关键词快照。",
                    status_message="刷新失败，旧快照仍可使用。",
                    elapsed_ms=self._elapsed_ms(started_at),
                    snapshot_at=created_at.isoformat(),
                    cache_state="stale",
                    data_source="github_snapshot",
                )
            return self._local_fallback_result(
                query=normalized_query,
                skills=skills,
                started_at=started_at,
                reason=f"GitHub 开放 Skill 搜索超过 {self.open_skill_search_budget_seconds:.0f} 秒时间预算，已立即展示本地已安装 Skill。",
            )
        except Exception as exc:
            self.logger.warning("GitHub 开放 Skill 搜索失败，使用本地回退：%s", exc.__class__.__name__)
            if cached_snapshot is not None:
                cached_result, created_at, _is_fresh = cached_snapshot
                return replace(
                    cached_result,
                    cache_hit=True,
                    fallback_reason=f"GitHub 刷新失败，当前继续展示上次关键词快照：{exc.__class__.__name__}。",
                    status_message="刷新失败，旧快照仍可使用。",
                    elapsed_ms=self._elapsed_ms(started_at),
                    snapshot_at=created_at.isoformat(),
                    cache_state="stale",
                    data_source="github_snapshot",
                )
            return self._local_fallback_result(
                query=normalized_query,
                skills=skills,
                started_at=started_at,
                reason=f"GitHub Skill 搜索暂不可用，已展示本地已安装结果：{exc.__class__.__name__}。",
            )

        if open_result.items:
            snapshot_at = datetime.now(UTC).isoformat()
            completed_result = replace(
                open_result,
                status_message="已刷新 GitHub 开放 Skill；结果已保存为关键词快照，Star 数据来自周快照。",
                elapsed_ms=self._elapsed_ms(started_at),
                snapshot_at=snapshot_at,
                cache_state="live",
                data_source="github_live",
            )
            self._write_open_skill_search_cache(normalized_query, completed_result, created_at=snapshot_at)
            return completed_result

        return self._local_fallback_result(
            query=normalized_query,
            skills=skills,
            started_at=started_at,
            reason="GitHub 暂未返回可用 Skill，已展示本地已安装结果。",
            normalized_query=open_result.normalized_query,
            model=open_result.model,
        )

    def _local_fallback_result(
        self,
        *,
        query: str,
        skills: list[SkillSummary],
        started_at: float,
        reason: str,
        normalized_query: str | None = None,
        model: str | None = None,
    ) -> SkillSearchResult:
        """构造可立即显示的本地回退结果。"""

        return SkillSearchResult(
            items=self._search_locally(query=query, skills=skills),
            used_llm=False,
            model=model,
            fallback_reason=reason,
            search_scope="fallback_local_installed",
            normalized_query=normalized_query or query,
            status_message="已优先保留本地 Skill 可用性。",
            elapsed_ms=self._elapsed_ms(started_at),
            cache_state="local",
            data_source="local_installed",
        )

    def save_skill(
        self,
        name: str,
        markdown: str,
        source_repository_full_name: str | None = None,
        source_homepage_url: str | None = None,
        source_author: str | None = None,
    ) -> SkillSaveResult:
        """把编辑后的 Skill.md 保存到项目本地 Skill 目录，并返回新的摘要。"""

        normalized_name = self._normalize_skill_name(name=name, markdown=markdown)
        normalized_markdown = self._ensure_frontmatter_name(markdown=markdown, name=normalized_name)
        normalized_markdown = self._ensure_saved_skill_metadata(
            markdown=normalized_markdown,
            source_repository_full_name=source_repository_full_name,
            source_homepage_url=source_homepage_url,
            source_author=source_author,
        )
        destination_dir = (self.project_skill_root / normalized_name).resolve()
        self._assert_inside_project_skill_root(destination_dir)
        destination_path = destination_dir / "SKILL.md"
        created = not destination_path.exists()

        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path.write_text(normalized_markdown, encoding="utf-8")

        saved_detail = self.get_skill(self._skill_id(destination_path))
        return SkillSaveResult(
            skill=saved_detail.summary,
            markdown=saved_detail.markdown,
            created=created,
            saved_path=destination_path,
        )

    def inspect_skill_repository(self, source: str) -> SkillRepositoryInspection:
        """读取 GitHub 仓库元数据和其中可独立安装的 ``SKILL.md`` 目录。"""

        repository_full_name = self._require_github_repository(source)
        repository, tree = self._load_github_repository_snapshot(repository_full_name)
        default_branch = str(repository.get("default_branch") or "main")
        skill_paths = self._repository_skill_paths(tree)
        file_stats: dict[str, tuple[int, int]] = {}
        for skill_path in skill_paths:
            directory = skill_path.rsplit("/", 1)[0] if "/" in skill_path else ""
            prefix = f"{directory}/" if directory else ""
            files = [
                item
                for item in tree
                if item.get("type") == "blob"
                and str(item.get("path") or "").startswith(prefix)
            ]
            file_stats[skill_path] = (
                len(files),
                sum(
                    int(item.get("size") or 0)
                    for item in files
                    if isinstance(item.get("size"), int)
                ),
            )

        def inspect_path(skill_path: str) -> SkillRepositoryItem:
            markdown = self._fetch_github_raw_text(
                repository_full_name,
                default_branch,
                skill_path,
                timeout_seconds=20,
            )
            fallback_name = self._skill_name_from_github_path(
                path=skill_path,
                repo_name=repository_full_name,
            )
            name, description = self._parse_frontmatter(markdown, fallback_name=fallback_name)
            file_count, size_bytes = file_stats[skill_path]
            return SkillRepositoryItem(
                name=name,
                description=description,
                path=skill_path,
                file_count=file_count,
                size_bytes=size_bytes,
            )

        with ThreadPoolExecutor(max_workers=6) as executor:
            skills = list(executor.map(inspect_path, skill_paths))

        license_payload = repository.get("license")
        license_spdx = None
        if isinstance(license_payload, dict):
            normalized_license = str(license_payload.get("spdx_id") or "").strip()
            license_spdx = normalized_license or None
        return SkillRepositoryInspection(
            repository_full_name=repository_full_name,
            default_branch=default_branch,
            homepage_url=str(repository.get("html_url") or f"https://github.com/{repository_full_name}"),
            stars=int(repository.get("stargazers_count") or 0),
            forks=int(repository.get("forks_count") or 0),
            license_spdx=license_spdx,
            pushed_at=str(repository.get("pushed_at") or "").strip() or None,
            skills=tuple(skills),
        )

    def install_skill_repository(
        self,
        source: str,
        skill_names: list[str] | tuple[str, ...] = (),
    ) -> SkillRepositoryInstallResult:
        """把远端仓库中的完整 Skill 目录复制到项目 ``.agents/skills``。"""

        inspection = self.inspect_skill_repository(source)
        by_name = {item.name.casefold(): item for item in inspection.skills}
        requested_names = [name.strip() for name in skill_names if name.strip()]
        if not requested_names or "*" in requested_names:
            selected = list(inspection.skills)
        else:
            missing = [name for name in requested_names if name.casefold() not in by_name]
            if missing:
                raise ValueError(f"仓库中未找到 Skill：{', '.join(missing)}")
            selected = []
            seen: set[str] = set()
            for name in requested_names:
                item = by_name[name.casefold()]
                if item.name.casefold() not in seen:
                    selected.append(item)
                    seen.add(item.name.casefold())

        preexisting_names: list[str] = []
        pending: list[SkillRepositoryItem] = []
        for skill in selected:
            destination = (self.project_skill_root / skill.name).resolve()
            self._assert_inside_project_skill_root(destination)
            if destination.exists():
                preexisting_names.append(skill.name)
            else:
                pending.append(skill)
        selected = pending
        if not selected:
            return SkillRepositoryInstallResult(
                repository_full_name=inspection.repository_full_name,
                installed_names=(),
                skipped_names=tuple(preexisting_names),
                installed_file_count=0,
            )

        archive_response = requests.get(
            f"https://codeload.github.com/{inspection.repository_full_name}/zip/refs/heads/"
            f"{inspection.default_branch}",
            timeout=60,
        )
        if not archive_response.ok:
            raise ValueError(f"GitHub 仓库归档下载失败：status={archive_response.status_code}")
        if len(archive_response.content) > 100 * 1024 * 1024:
            raise ValueError("GitHub 仓库归档超过 100 MB，已拒绝安装")
        try:
            archive = ZipFile(BytesIO(archive_response.content))
        except BadZipFile as exc:
            raise ValueError("GitHub 返回的仓库归档不是有效 ZIP") from exc

        archive_members: dict[str, list[tuple[object, str]]] = {}
        selected_total_size = 0
        selected_file_count = 0
        for skill in selected:
            directory = skill.path.rsplit("/", 1)[0] if "/" in skill.path else ""
            directory_marker = f"/{directory}/" if directory else "/"
            members: list[tuple[object, str]] = []
            for member in archive.infolist():
                normalized_name = member.filename.replace("\\", "/")
                marker_index = normalized_name.find(directory_marker)
                if member.is_dir() or marker_index < 0:
                    continue
                relative_path = normalized_name[marker_index + len(directory_marker) :]
                if not relative_path or self._unsafe_relative_path(relative_path):
                    continue
                members.append((member, relative_path))
                selected_total_size += int(member.file_size)
                selected_file_count += 1
            archive_members[skill.name] = members
        if selected_file_count > 2_000:
            raise ValueError("待安装 Skill 文件数超过 2000，已拒绝安装")
        if selected_total_size > 80 * 1024 * 1024:
            raise ValueError("待安装 Skill 总大小超过 80 MB，已拒绝安装")

        self.project_skill_root.mkdir(parents=True, exist_ok=True)
        installed_names: list[str] = []
        skipped_names: list[str] = list(preexisting_names)
        installed_file_count = 0
        with tempfile.TemporaryDirectory(
            prefix=".skill-install-",
            dir=self.project_skill_root,
        ) as temporary_root:
            staging_root = Path(temporary_root).resolve()
            for skill in selected:
                destination = (self.project_skill_root / skill.name).resolve()
                self._assert_inside_project_skill_root(destination)
                if destination.exists():
                    skipped_names.append(skill.name)
                    continue
                staging = (staging_root / skill.name).resolve()
                try:
                    staging.relative_to(staging_root)
                except ValueError as exc:
                    raise ValueError(f"Skill 名称产生了非法安装路径：{skill.name}") from exc
                for member, relative_path in archive_members.get(skill.name, []):
                    target = (staging / relative_path).resolve()
                    try:
                        target.relative_to(staging)
                    except ValueError as exc:
                        raise ValueError(f"Skill 文件路径越界：{relative_path}") from exc
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
                    installed_file_count += 1
                if not (staging / "SKILL.md").is_file():
                    raise ValueError(f"Skill {skill.name} 缺少 SKILL.md")
                shutil.copytree(staging, destination)
                installed_names.append(skill.name)

        return SkillRepositoryInstallResult(
            repository_full_name=inspection.repository_full_name,
            installed_names=tuple(installed_names),
            skipped_names=tuple(skipped_names),
            installed_file_count=installed_file_count,
        )

    def _ensure_saved_skill_metadata(
        self,
        markdown: str,
        source_repository_full_name: str | None,
        source_homepage_url: str | None,
        source_author: str | None,
    ) -> str:
        """保存远端 Skill 时，把仓库信息写入 frontmatter，方便后续刷新 Star。"""

        metadata: dict[str, str] = {}
        if source_repository_full_name and not self._frontmatter_field(markdown, ["repository_full_name", "repository"]):
            metadata["repository_full_name"] = source_repository_full_name
        if source_homepage_url and not self._frontmatter_field(markdown, ["homepage", "url", "link"]):
            metadata["homepage"] = source_homepage_url
        if source_author and not self._frontmatter_field(markdown, ["author", "authors", "maintainer"]):
            metadata["author"] = source_author
        if not metadata:
            return markdown

        match = self._frontmatter_pattern.match(markdown.strip())
        if match is None:
            return markdown

        frontmatter = match.group("body").rstrip()
        additions = "\n".join(f"{key}: {value}" for key, value in metadata.items())
        body_start = match.end()
        return f"---\n{frontmatter}\n{additions}\n---\n{markdown[body_start:]}"

    def _search_open_skills(self, query: str, deadline: float) -> SkillSearchResult:
        """按 find-skills 思路搜索 GitHub 上的开放 Skill。"""

        normalized_query, model = self._normalize_open_skill_query(query=query, deadline=deadline)
        markdown_files = self._search_github_skill_files(query=normalized_query, deadline=deadline)
        items: list[SkillSearchItem] = []
        seen_names: set[str] = set()

        for index, item in enumerate(markdown_files, start=1):
            markdown = str(item["markdown"])
            path = str(item["path"])
            repository = item["repository"]
            owner = str(repository.get("owner", {}).get("login", "GitHub"))
            repo_name = str(repository.get("full_name", "unknown/repository"))
            fallback_name = self._skill_name_from_github_path(path=path, repo_name=repo_name)
            name, description = self._parse_frontmatter(markdown, fallback_name=fallback_name)
            normalized_name_key = name.lower()
            if normalized_name_key in seen_names:
                continue
            seen_names.add(normalized_name_key)

            html_url = str(item.get("html_url", "")).strip() or None
            repository_full_name = repo_name if "/" in repo_name else None
            # 搜索结果只读已有的周 Star 快照，避免每个候选仓库再同步请求一次 GitHub。
            star_snapshot = self._repository_star_snapshot(repository_full_name, refresh_if_stale=False)
            summary = SkillSummary(
                id=f"github-{str(item.get('sha', hashlib.sha256(str(item).encode('utf-8')).hexdigest()))[:16]}",
                name=name,
                description=description,
                description_zh=self._local_chinese_description(name=name, description=description),
                author=owner,
                homepage_url=html_url,
                repository_full_name=repository_full_name,
                stars=star_snapshot["stars"],
                previous_stars=star_snapshot["previous_stars"],
                star_delta=star_snapshot["star_delta"],
                star_growth_rate=star_snapshot["star_growth_rate"],
                stars_updated_at=star_snapshot["stars_updated_at"],
                source="github",
                source_label="GitHub",
                path_hint=f"{repo_name}/{path}",
                editable=False,
            )
            score = max(60, 98 - index * 4)
            reason = f"来自 GitHub 仓库 {repo_name}，匹配开放 Skill 文件 {path}。"
            items.append(SkillSearchItem(skill=summary, score=score, match_reason=reason, markdown=markdown))
            if len(items) >= self.github_result_limit:
                break

        return SkillSearchResult(
            items=items,
            used_llm=model is not None,
            model=model,
            fallback_reason=None,
            search_scope="github_open_skills",
            normalized_query=normalized_query,
        )

    def _normalize_open_skill_query(self, query: str, deadline: float) -> tuple[str, str | None]:
        """必要时用 DS4Pro 改写关键词，但绝不超过本次页面时间预算。"""

        # 英文技术关键词本来就适合 GitHub Code Search，跳过 LLM 以减少一次外部调用。
        if not self._contains_cjk(query):
            return query[:180], None
        timeout_seconds = self._remaining_budget(deadline=deadline, maximum=3.5)
        if timeout_seconds <= 0:
            raise SkillSearchBudgetExceeded("查询改写已耗尽搜索预算")

        provider = DeepSeekProvider(config=self.config, run_name="skills.query_normalize")
        skills = self.list_skills()
        find_skill = next((skill for skill in skills if skill.name == "find-skills"), None)
        find_skill_markdown = ""
        if find_skill is not None:
            find_skill_markdown = self.get_skill(find_skill.id).markdown[:1800]

        system_prompt = (
            "你是一个 GitHub Skill 搜索关键词改写助手。只返回 JSON，不要解释。"
        )
        user_prompt = f"""
用户关键词：
{query}

find-skills 的本地说明片段：
{find_skill_markdown}

请返回 JSON 对象，格式必须是：
{{
  "github_query": "3 到 8 个英文关键词，空格分隔，不要包含 filename:SKILL.md"
}}

要求：
- 面向 GitHub Code Search；
- 保留技术名词，例如 React、video、prompt、WeChat、agent；
- 如果用户输入中文，请翻译为常见英文技术关键词；
- 不要添加路径限定语法，代码会自动追加 filename:SKILL.md。
"""
        response = provider.chat(
            [
                DeepSeekMessage(role="system", content=system_prompt),
                DeepSeekMessage(role="user", content=user_prompt.strip()),
            ],
            timeout_seconds=timeout_seconds,
            max_tokens=160,
            retry_empty_content=False,
        )
        parsed = parse_json_object_from_text(response.content)
        normalized_query = str(parsed.get("github_query", "")).strip()
        if not normalized_query:
            return query, response.model
        return normalized_query[:180], response.model

    def _search_github_skill_files(self, query: str, deadline: float) -> list[dict[str, Any]]:
        """调用 GitHub Code Search 并有限并行拉取 SKILL.md。"""

        search_terms = query.strip() or "agent skills"
        search_query = f"{search_terms} filename:SKILL.md"
        timeout_seconds = self._remaining_budget(deadline=deadline, maximum=self.github_code_search_timeout_seconds)
        if timeout_seconds <= 0:
            raise SkillSearchBudgetExceeded("GitHub Code Search 已耗尽搜索预算")
        response = requests.get(
            "https://api.github.com/search/code",
            headers=self._github_headers(),
            params={
                "q": search_query,
                "per_page": self.github_file_candidate_limit,
            },
            timeout=timeout_seconds,
        )
        if not response.ok:
            raise RuntimeError(f"GitHub Code Search 失败：status={response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub Code Search 响应不是对象")

        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list):
            return []

        candidates = [item for item in raw_items if isinstance(item, dict)][: self.github_file_candidate_limit]
        if not candidates:
            return []

        remaining_seconds = self._remaining_budget(deadline=deadline, maximum=self.github_file_timeout_seconds)
        if remaining_seconds <= 0:
            raise SkillSearchBudgetExceeded("候选 Skill 文件读取已耗尽搜索预算")

        # Code Search 只返回元数据；候选内容读取使用小规模线程池并发，避免 8 个网络
        # 往返被串行放大。等待超时后直接回退，不阻塞浏览器继续旋转。
        executor = ThreadPoolExecutor(max_workers=min(self.github_file_fetch_workers, len(candidates)))
        futures: dict[Future[str], tuple[int, dict[str, Any]]] = {
            executor.submit(self._fetch_github_file_text, item, remaining_seconds): (index, item)
            for index, item in enumerate(candidates)
        }
        completed, _pending = wait(futures, timeout=remaining_seconds)
        executor.shutdown(wait=False, cancel_futures=True)

        fetched: list[tuple[int, dict[str, Any]]] = []
        for future in completed:
            index, raw_item = futures[future]
            try:
                markdown = future.result()
            except Exception:
                continue
            if not markdown.strip():
                continue
            enriched = dict(raw_item)
            enriched["markdown"] = markdown
            fetched.append((index, enriched))

        if not fetched and _pending:
            raise SkillSearchBudgetExceeded("候选 Skill 文件读取超时")
        return [item for _index, item in sorted(fetched, key=lambda pair: pair[0])]

    def _fetch_github_file_text(self, code_search_item: dict[str, Any], timeout_seconds: float) -> str:
        """读取 GitHub Code Search 返回的单个文件内容。"""

        api_url = str(code_search_item.get("url", "")).strip()
        if not api_url.startswith("https://api.github.com/"):
            raise RuntimeError("GitHub 文件 API 地址无效")

        response = requests.get(api_url, headers=self._github_headers(), timeout=timeout_seconds)
        if not response.ok:
            raise RuntimeError(f"GitHub 文件读取失败：status={response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub 文件响应不是对象")

        content = str(payload.get("content", ""))
        encoding = str(payload.get("encoding", "")).lower()
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")

        download_url = str(payload.get("download_url", "")).strip()
        if download_url.startswith("https://"):
            raw_response = requests.get(download_url, timeout=timeout_seconds)
            if raw_response.ok:
                return raw_response.text
        return content

    def _read_open_skill_search_cache(
        self,
        query: str,
    ) -> tuple[SkillSearchResult, datetime, bool] | None:
        """读取 GitHub Skill 关键词快照；过期快照继续保留并交给页面显式标记。"""

        cache = self._load_open_skill_search_cache()
        entries = cache.get("entries", {})
        if not isinstance(entries, dict):
            return None
        record = entries.get(self._open_skill_cache_key(query))
        if not isinstance(record, dict):
            return None
        created_at = self._parse_cache_datetime(record.get("created_at"))
        if created_at is None:
            return None
        result = self._deserialize_open_skill_search_result(record.get("result"))
        if result is None or not result.items:
            return None
        return result, created_at, datetime.now(UTC) - created_at < self.open_skill_cache_ttl

    def _write_open_skill_search_cache(
        self,
        query: str,
        result: SkillSearchResult,
        *,
        created_at: str | None = None,
    ) -> None:
        """持久化公开 GitHub 搜索结果，避免短时间重复请求外部 API。"""

        try:
            cache = self._load_open_skill_search_cache()
            entries = cache.setdefault("entries", {})
            if not isinstance(entries, dict):
                entries = {}
                cache["entries"] = entries
            entries[self._open_skill_cache_key(query)] = {
                "created_at": created_at or datetime.now(UTC).isoformat(),
                "result": self._serialize_open_skill_search_result(result),
            }

            retained_entries: list[tuple[str, dict[str, Any]]] = []
            for key, value in entries.items():
                if not isinstance(value, dict):
                    continue
                if self._parse_cache_datetime(value.get("created_at")) is None:
                    continue
                retained_entries.append((key, value))
            retained_entries.sort(
                key=lambda item: str(item[1].get("created_at", "")),
                reverse=True,
            )
            cache["entries"] = dict(retained_entries[: self.open_skill_cache_max_entries])
            self._save_open_skill_search_cache(cache)
        except OSError:
            # 缓存写入失败不能让已经得到的 GitHub 搜索结果变成错误响应。
            self.logger.warning("Skill 搜索缓存写入失败，已忽略本次缓存")

    def _load_open_skill_search_cache(self) -> dict[str, Any]:
        """读取 Skill 搜索持久缓存，损坏时视为未命中。"""

        if not self.open_skill_cache_path.exists():
            return {"entries": {}}
        try:
            payload = json.loads(self.open_skill_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"entries": {}}
        if not isinstance(payload, dict):
            return {"entries": {}}
        if not isinstance(payload.get("entries"), dict):
            payload["entries"] = {}
        return payload

    def _save_open_skill_search_cache(self, cache: dict[str, Any]) -> None:
        """原子写入公开搜索缓存，避免运行中重启留下半截 JSON。"""

        self.open_skill_cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.open_skill_cache_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_path.replace(self.open_skill_cache_path)

    def _open_skill_cache_key(self, query: str) -> str:
        """使用归一化查询生成不暴露用户原文的缓存键。"""

        normalized = self._normalize_open_skill_cache_query(query)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_open_skill_cache_query(query: str) -> str:
        """统一关键词大小写与空白，确保语义相同的输入复用同一份快照。"""

        return re.sub(r"\s+", " ", query.strip().casefold())

    def _serialize_open_skill_search_result(self, result: SkillSearchResult) -> dict[str, Any]:
        """将公开 GitHub 结果转换为可持久化 JSON。"""

        return {
            "items": [
                {
                    "skill": asdict(item.skill),
                    "score": item.score,
                    "match_reason": item.match_reason,
                    "markdown": item.markdown,
                }
                for item in result.items
            ],
            "used_llm": result.used_llm,
            "model": result.model,
            "fallback_reason": result.fallback_reason,
            "search_scope": result.search_scope,
            "normalized_query": result.normalized_query,
        }

    def _deserialize_open_skill_search_result(self, raw: Any) -> SkillSearchResult | None:
        """校验并恢复磁盘缓存，缓存异常不会影响正常搜索。"""

        if not isinstance(raw, dict):
            return None
        raw_items = raw.get("items")
        if not isinstance(raw_items, list):
            return None
        items: list[SkillSearchItem] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict) or not isinstance(raw_item.get("skill"), dict):
                continue
            try:
                skill = SkillSummary(**raw_item["skill"])
                items.append(
                    SkillSearchItem(
                        skill=skill,
                        score=self._clamp_score(raw_item.get("score")),
                        match_reason=str(raw_item.get("match_reason", "GitHub 缓存结果。")),
                        markdown=None if raw_item.get("markdown") is None else str(raw_item.get("markdown")),
                    )
                )
            except (TypeError, ValueError):
                continue
        if not items:
            return None
        return SkillSearchResult(
            items=items,
            used_llm=bool(raw.get("used_llm", False)),
            model=None if raw.get("model") is None else str(raw.get("model")),
            fallback_reason=None,
            search_scope="github_open_skills",
            normalized_query=str(raw.get("normalized_query", "")),
        )

    def _parse_cache_datetime(self, value: Any) -> datetime | None:
        """解析缓存时间，兼容早期无时区的缓存内容。"""

        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        """返回当前操作耗时，供 API 和 UI 显示。"""

        return max(0, int((time.monotonic() - started_at) * 1000))

    @staticmethod
    def _remaining_budget(*, deadline: float, maximum: float) -> float:
        """根据总截止时间计算本步骤允许使用的请求超时。"""

        return max(0.0, min(maximum, deadline - time.monotonic()))

    def _github_headers(self) -> dict[str, str]:
        """构造 GitHub API 请求头。"""

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.github_api_version,
        }
        token = os.getenv(self.config.github_token_env, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _require_github_repository(self, source: str) -> str:
        """把 HTTPS、SSH 或 owner/repo 输入规范化为 GitHub full_name。"""

        repository = self._extract_github_repository_full_name(source)
        if repository is None:
            raise ValueError("只支持 GitHub 仓库地址或 owner/repo")
        return repository

    def _load_github_repository_snapshot(
        self,
        repository_full_name: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """读取仓库元数据和默认分支递归 Tree。"""

        headers = self._github_headers()
        repository_response = requests.get(
            f"https://api.github.com/repos/{repository_full_name}",
            headers=headers,
            timeout=20,
        )
        if not repository_response.ok:
            raise ValueError(
                f"GitHub 仓库读取失败：status={repository_response.status_code}",
            )
        repository = repository_response.json()
        if not isinstance(repository, dict):
            raise ValueError("GitHub 仓库响应不是对象")
        default_branch = str(repository.get("default_branch") or "main")
        tree_response = requests.get(
            f"https://api.github.com/repos/{repository_full_name}/git/trees/{default_branch}",
            params={"recursive": "1"},
            headers=headers,
            timeout=30,
        )
        if not tree_response.ok:
            raise ValueError(f"GitHub 文件树读取失败：status={tree_response.status_code}")
        tree_payload = tree_response.json()
        raw_tree = tree_payload.get("tree") if isinstance(tree_payload, dict) else None
        if not isinstance(raw_tree, list):
            raise ValueError("GitHub 文件树响应缺少 tree")
        tree = [item for item in raw_tree if isinstance(item, dict)]
        return repository, tree

    @staticmethod
    def _repository_skill_paths(tree: list[dict[str, Any]]) -> tuple[str, ...]:
        """优先识别标准 ``skills/``，避免把仓库维护用内部 Skill 一并安装。"""

        candidates = sorted(
            {
                str(item.get("path") or "")
                for item in tree
                if item.get("type") == "blob"
                and str(item.get("path") or "").lower().endswith("skill.md")
            },
        )
        preferred_roots = ("skills/", ".agents/skills/", ".claude/skills/")
        for root in preferred_roots:
            rooted = tuple(path for path in candidates if path.startswith(root))
            if rooted:
                return rooted
        return tuple(candidates)

    def _fetch_github_raw_text(
        self,
        repository_full_name: str,
        branch: str,
        path: str,
        *,
        timeout_seconds: float,
    ) -> str:
        """读取 GitHub 默认分支中的 UTF-8 文本。"""

        content = self._fetch_github_raw_bytes(
            repository_full_name,
            branch,
            path,
            timeout_seconds=timeout_seconds,
        )
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"远端文件不是 UTF-8：{path}") from exc

    @staticmethod
    def _fetch_github_raw_bytes(
        repository_full_name: str,
        branch: str,
        path: str,
        *,
        timeout_seconds: float,
    ) -> bytes:
        """下载 GitHub Raw 文件，不执行下载内容。"""

        response = requests.get(
            f"https://raw.githubusercontent.com/{repository_full_name}/{branch}/{path}",
            timeout=timeout_seconds,
        )
        if not response.ok:
            raise ValueError(f"GitHub 文件下载失败：status={response.status_code} path={path}")
        return response.content

    @staticmethod
    def _unsafe_relative_path(path: str) -> bool:
        """拒绝绝对路径、空路径和父目录跳转。"""

        normalized = path.replace("\\", "/").strip()
        parts = [part for part in normalized.split("/") if part]
        return (
            not normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized) is not None
            or any(part in {".", ".."} for part in parts)
        )

    def _skill_name_from_github_path(self, path: str, repo_name: str) -> str:
        """从 GitHub 路径推断 Skill 名称。"""

        parts = [part for part in path.replace("\\", "/").split("/") if part]
        if len(parts) >= 2 and parts[-1].lower() == "skill.md":
            return parts[-2]
        return repo_name.split("/", 1)[-1]

    def _localize_open_skill_items(self, query: str, items: list[SkillSearchItem]) -> list[SkillSearchItem]:
        """把 GitHub 搜索结果中面向用户展示的描述统一改为中文。"""

        if not items:
            return items

        try:
            provider = DeepSeekProvider(config=self.config, run_name="skills.result_rank")
            candidates = [
                {
                    "skill_id": item.skill.id,
                    "name": item.skill.name,
                    "description": item.skill.description[:500],
                    "author": item.skill.author,
                    "repository": item.skill.repository_full_name,
                    "path": item.skill.path_hint,
                }
                for item in items
            ]
            system_prompt = (
                "你是技术 Skill 搜索结果的中文信息整理助手。只返回 JSON。"
                "除人名、仓库名、Skill 名、API、框架名、模型名等关键技术名词外，其余面向用户的展示文字必须使用中文。"
            )
            user_prompt = f"""
用户搜索词：
{query}

GitHub Skill 候选：
{candidates}

请返回 JSON：
{{
  "items": [
    {{
      "skill_id": "候选 skill_id",
      "description_zh": "一句中文简介，保留必要英文技术名词",
      "match_reason": "一句中文匹配原因，保留作者名、仓库名、关键技术名词"
    }}
  ]
}}

要求：
- 不要翻译人名、仓库名、Skill 名；
- 不要把 API、React、GitHub、WeChat、prompt、agent、Skill 等技术词强行翻译；
- 不要编造安装量或 Star 数。
"""
            response = provider.chat(
                [
                    DeepSeekMessage(role="system", content=system_prompt),
                    DeepSeekMessage(role="user", content=user_prompt.strip()),
                ]
            )
            parsed = parse_json_object_from_text(response.content)
            raw_items = parsed.get("items", [])
            if not isinstance(raw_items, list):
                return items
            localized_by_id: dict[str, dict[str, str]] = {}
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                skill_id = str(raw_item.get("skill_id", "")).strip()
                if not skill_id:
                    continue
                localized_by_id[skill_id] = {
                    "description_zh": str(raw_item.get("description_zh", "")).strip(),
                    "match_reason": str(raw_item.get("match_reason", "")).strip(),
                }
        except Exception:
            return items

        localized_items: list[SkillSearchItem] = []
        for item in items:
            localized = localized_by_id.get(item.skill.id, {})
            description_zh = localized.get("description_zh") or item.skill.description_zh
            match_reason = localized.get("match_reason") or item.match_reason
            localized_items.append(
                SkillSearchItem(
                    skill=replace(item.skill, description_zh=description_zh),
                    score=item.score,
                    match_reason=match_reason,
                    markdown=item.markdown,
                )
            )
        return localized_items

    def _repository_full_name_for_skill(
        self,
        *,
        name: str,
        homepage_url: str | None,
        path_hint: str,
        markdown: str,
        repository_by_name: dict[str, str],
    ) -> str | None:
        """优先读取 Skill 自带元数据，缺失时回退到安装器锁文件。"""

        repository = self._repository_full_name_from_metadata(
            homepage_url=homepage_url,
            path_hint=path_hint,
            markdown=markdown,
        )
        if repository:
            return repository
        return repository_by_name.get(name)

    def _repository_full_names_from_install_locks(self) -> dict[str, str]:
        """读取本机或项目安装器记录的 Skill → GitHub 仓库映射。"""

        home = Path.home()
        candidates = [
            self.config.project_root / ".agents" / ".skill-lock.json",
            self.config.project_root / ".codex" / ".skill-lock.json",
            self.config.project_root / "deploy" / "skill-seeds" / ".skill-lock.json",
            home / ".agents" / ".skill-lock.json",
            home / ".codex" / ".skill-lock.json",
        ]
        repositories: dict[str, str] = {}
        seen_paths: set[Path] = set()
        for candidate in candidates:
            resolved_candidate = candidate.resolve()
            if resolved_candidate in seen_paths:
                continue
            seen_paths.add(resolved_candidate)
            try:
                payload = json.loads(resolved_candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            skills = payload.get("skills") if isinstance(payload, dict) else None
            if not isinstance(skills, dict):
                continue
            for raw_name, raw_entry in skills.items():
                name = str(raw_name).strip()
                if not name or name in repositories or not isinstance(raw_entry, dict):
                    continue
                source_type = str(raw_entry.get("sourceType", "")).strip().lower()
                if source_type and source_type != "github":
                    continue
                for key in ("sourceUrl", "source"):
                    repository = self._extract_github_repository_full_name(str(raw_entry.get(key, "")))
                    if repository:
                        repositories[name] = repository
                        break
        return repositories

    def _repository_full_name_from_metadata(
        self,
        homepage_url: str | None,
        path_hint: str,
        markdown: str,
    ) -> str | None:
        """从 frontmatter、链接或路径中识别 GitHub 仓库 full_name。"""

        candidates = [
            self._frontmatter_field(markdown, ["repository_full_name", "github_repository", "repository"]),
            homepage_url,
        ]
        if "github.com/" in path_hint:
            candidates.append(path_hint)
        for candidate in candidates:
            repository = self._extract_github_repository_full_name(str(candidate or ""))
            if repository:
                return repository
        return None

    def _extract_github_repository_full_name(self, value: str) -> str | None:
        """从 GitHub URL 或 owner/repo 文本中提取仓库名。"""

        normalized = value.strip().replace("\\", "/")
        if not normalized:
            return None

        normalized = re.sub(r"^git@github\.com:", "https://github.com/", normalized)
        github_match = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", normalized)
        if github_match:
            owner = github_match.group(1).strip()
            repo = github_match.group(2).strip().removesuffix(".git")
            if owner and repo:
                return f"{owner}/{repo}"

        owner_repo_match = re.search(r"(^|[^a-zA-Z0-9_.-])([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)(/|$)", normalized)
        if owner_repo_match:
            return owner_repo_match.group(2).removesuffix(".git")
        return None

    def _repository_star_snapshot(
        self,
        repository_full_name: str | None,
        *,
        refresh_if_stale: bool,
    ) -> dict[str, Any]:
        """读取 Star 缓存；仅显式刷新任务允许访问 GitHub。"""

        empty_snapshot = {
            "stars": None,
            "previous_stars": None,
            "star_delta": None,
            "star_growth_rate": None,
            "stars_updated_at": None,
        }
        if not repository_full_name:
            return empty_snapshot

        cache = self._load_star_cache()
        repositories = cache.setdefault("repositories", {})
        raw_record = repositories.get(repository_full_name)
        record = raw_record if isinstance(raw_record, dict) else {}
        now = datetime.now(UTC)

        if not self._is_star_record_stale(record, now=now):
            return self._star_payload_from_record(record)

        # WebUI 列表和搜索只读取旧快照或空值；这样 Star API 不会拖慢交互。
        if not refresh_if_stale:
            return self._star_payload_from_record(record) if record else empty_snapshot

        try:
            latest_stars = self._fetch_repository_stars(repository_full_name)
        except Exception:
            if record:
                return self._star_payload_from_record(record)
            return empty_snapshot

        previous_stars = record.get("stars") if isinstance(record.get("stars"), int) else None
        previous_updated_at = str(record.get("updated_at", "")) if record.get("updated_at") else None
        updated_record = {
            "stars": latest_stars,
            "previous_stars": previous_stars,
            "updated_at": now.isoformat(),
            "previous_updated_at": previous_updated_at,
        }
        repositories[repository_full_name] = updated_record
        self._save_star_cache(cache)
        return self._star_payload_from_record(updated_record)

    def _is_star_record_stale(self, record: dict[str, Any], now: datetime) -> bool:
        """判断 Star 缓存是否超过七天。"""

        if not record or not isinstance(record.get("stars"), int):
            return True
        updated_at = str(record.get("updated_at", "")).strip()
        if not updated_at:
            return True
        try:
            parsed = datetime.fromisoformat(updated_at)
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return now - parsed >= self.star_cache_ttl

    def _fetch_repository_stars(self, repository_full_name: str) -> int:
        """调用 GitHub 仓库接口读取真实 stargazers_count。"""

        response = requests.get(
            f"https://api.github.com/repos/{repository_full_name}",
            headers=self._github_headers(),
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(f"GitHub 仓库读取失败：status={response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub 仓库响应不是对象")
        return int(payload.get("stargazers_count", 0))

    def _star_payload_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """把缓存记录转换为前端展示字段。"""

        stars = record.get("stars") if isinstance(record.get("stars"), int) else None
        previous_stars = record.get("previous_stars") if isinstance(record.get("previous_stars"), int) else None
        star_delta = None
        star_growth_rate = None
        if stars is not None and previous_stars is not None:
            star_delta = stars - previous_stars
            if previous_stars > 0:
                star_growth_rate = star_delta / previous_stars
        return {
            "stars": stars,
            "previous_stars": previous_stars,
            "star_delta": star_delta,
            "star_growth_rate": star_growth_rate,
            "stars_updated_at": str(record.get("updated_at", "")) or None,
        }

    def _load_star_cache(self) -> dict[str, Any]:
        """读取本地 Star 缓存。"""

        if not self.star_cache_path.exists():
            return {"repositories": {}}
        try:
            payload = json.loads(self.star_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"repositories": {}}
        if not isinstance(payload, dict):
            return {"repositories": {}}
        repositories = payload.get("repositories")
        if not isinstance(repositories, dict):
            payload["repositories"] = {}
        return payload

    def _save_star_cache(self, cache: dict[str, Any]) -> None:
        """保存本地 Star 缓存。"""

        self.star_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.star_cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _local_chinese_description(self, name: str, description: str) -> str:
        """为本地列表提供中文展示兜底，保留 Skill 名和技术词。"""

        if self._contains_cjk(description):
            return description
        if description.strip():
            return f"{name}：用于扩展 Agent 工作流，覆盖该 Skill 描述中的专门能力。"
        return f"{name}：本地已安装 Skill。"

    def _contains_cjk(self, value: str) -> bool:
        """判断文本是否包含中文字符。"""

        return bool(re.search(r"[\u4e00-\u9fff]", value))

    def _search_locally(self, query: str, skills: list[SkillSummary]) -> list[SkillSearchItem]:
        """模型不可用时使用朴素关键词匹配，保证 UI 可用。"""

        tokens = [token for token in re.split(r"[\s,，。;；/\\]+", query.lower()) if token]
        ranked: list[SkillSearchItem] = []
        for skill in skills:
            haystack = f"{skill.name} {skill.description} {skill.source_label}".lower()
            matched = [token for token in tokens if token in haystack]
            if skill.name == "find-skills" and tokens:
                matched.append("find-skills")
            if not matched:
                continue
            score = min(95, 50 + len(set(matched)) * 12)
            ranked.append(
                SkillSearchItem(
                    skill=skill,
                    score=score,
                    match_reason=f"本地命中关键词：{'、'.join(sorted(set(matched))[:5])}",
                )
            )

        if not ranked:
            return [
                SkillSearchItem(
                    skill=skill,
                    score=60,
                    match_reason="未找到精确命中，展示本地已安装 Skill 供人工判断。",
                )
                for skill in skills[:8]
            ]

        return sorted(ranked, key=lambda item: (-item.score, item.skill.name.lower()))[:8]

    def _iter_skill_files(self) -> list[tuple[Path, str, str]]:
        """返回所有候选 SKILL.md 文件及其来源标签。"""

        home = Path.home()
        scan_roots: list[tuple[Path, str, str, bool]] = [
            (self.project_skill_root, "project", "项目本地", False),
            (self.config.project_root / ".codex" / "skills", "project", "项目本地", False),
            (home / ".agents" / "skills", "user", "用户已安装", False),
            (home / ".codex" / "skills", "user", "用户已安装", False),
            (home / ".codex" / "plugins" / "cache", "plugin", "插件内置", True),
        ]

        results: list[tuple[Path, str, str]] = []
        seen_paths: set[Path] = set()
        for root, source, source_label, recursive in scan_roots:
            resolved_root = root.resolve()
            if not resolved_root.exists() or not resolved_root.is_dir():
                continue
            candidates = resolved_root.rglob("SKILL.md") if recursive else resolved_root.glob("**/SKILL.md")
            for candidate in candidates:
                try:
                    resolved_candidate = candidate.resolve()
                except OSError:
                    continue
                if resolved_candidate in seen_paths:
                    continue
                seen_paths.add(resolved_candidate)
                results.append((resolved_candidate, source, source_label))

        return results

    def _resolve_skill_path(self, skill_id: str) -> Path:
        """重新扫描本地 Skill，并根据 id 找到对应 SKILL.md。"""

        normalized_id = skill_id.strip()
        for skill_path, _source, _source_label in self._iter_skill_files():
            if self._skill_id(skill_path) == normalized_id:
                return skill_path
        raise FileNotFoundError(f"Skill 不存在或已移动：{skill_id}")

    def _parse_frontmatter(self, markdown: str, fallback_name: str) -> tuple[str, str]:
        """从 SKILL.md frontmatter 中提取 name 和 description。"""

        match = self._frontmatter_pattern.match(markdown.strip())
        frontmatter = match.group("body") if match else ""

        raw_name = ""
        raw_description = ""
        name_match = self._name_pattern.search(frontmatter)
        description_match = self._description_pattern.search(frontmatter)
        if name_match:
            raw_name = name_match.group("value").strip().strip("\"'")
        if description_match:
            raw_description = description_match.group("value").strip().strip("\"'")

        name = raw_name or fallback_name
        description = raw_description or self._first_meaningful_line(markdown)
        return name, description

    def _frontmatter_field(self, markdown: str, field_names: list[str]) -> str | None:
        """读取 SKILL.md frontmatter 中的可选字段。"""

        match = self._frontmatter_pattern.match(markdown.strip())
        if match is None:
            return None

        frontmatter = match.group("body")
        for field_name in field_names:
            pattern = re.compile(
                self._frontmatter_field_pattern_template.format(field=re.escape(field_name)),
                re.MULTILINE,
            )
            field_match = pattern.search(frontmatter)
            if field_match is None:
                continue
            value = field_match.group("value").strip().strip("\"'")
            if value:
                return value
        return None

    def _first_meaningful_line(self, markdown: str) -> str:
        """当 description 缺失时，从正文提取一句短描述。"""

        for line in markdown.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("---") or stripped.startswith("name:"):
                continue
            if stripped.startswith("description:"):
                continue
            return stripped.lstrip("#").strip()[:160]
        return "暂无描述"

    def _normalize_skill_name(self, name: str, markdown: str) -> str:
        """校验并规范化保存时使用的 Skill 名称。"""

        parsed_name, _description = self._parse_frontmatter(markdown, fallback_name="")
        candidate = (name.strip() or parsed_name.strip()).strip().strip("\"'")
        candidate = candidate.replace(" ", "-")
        if not self._skill_name_pattern.match(candidate):
            raise ValueError("Skill 名称只能包含字母、数字、短横线和下划线，长度 2-80，且必须以字母或数字开头。")
        return candidate

    def _ensure_frontmatter_name(self, markdown: str, name: str) -> str:
        """确保保存后的 SKILL.md frontmatter 中包含最新 name。"""

        normalized_markdown = markdown.strip() + "\n"
        match = self._frontmatter_pattern.match(normalized_markdown)
        if match is None:
            return f"---\nname: {name}\ndescription: 本地保存的 Skill。\n---\n\n{normalized_markdown}"

        frontmatter = match.group("body")
        if self._name_pattern.search(frontmatter):
            updated_frontmatter = self._name_pattern.sub(f"name: {name}", frontmatter, count=1)
        else:
            updated_frontmatter = f"name: {name}\n{frontmatter}".strip()

        body_start = match.end()
        return f"---\n{updated_frontmatter}\n---\n{normalized_markdown[body_start:]}"

    def _skill_id(self, skill_path: Path) -> str:
        """根据绝对路径生成稳定但不暴露路径的 id。"""

        normalized_path = str(skill_path.resolve()).replace("\\", "/").lower()
        return hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()[:16]

    def _path_hint(self, skill_path: Path) -> str:
        """生成适合 UI 展示的路径提示，不暴露过长绝对路径。"""

        resolved_path = skill_path.resolve()
        roots = [
            self.config.project_root.resolve(),
            (Path.home() / ".agents").resolve(),
            (Path.home() / ".codex").resolve(),
        ]
        for root in roots:
            try:
                return str(resolved_path.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
        return resolved_path.name

    def _source_for_path(self, skill_path: Path) -> str:
        """根据路径判断 Skill 来源。"""

        resolved_path = skill_path.resolve()
        if self._is_project_skill(resolved_path):
            return "project"
        plugin_root = (Path.home() / ".codex" / "plugins" / "cache").resolve()
        try:
            resolved_path.relative_to(plugin_root)
            return "plugin"
        except ValueError:
            return "user"

    def _source_label_for_path(self, skill_path: Path) -> str:
        """根据路径生成来源中文标签。"""

        source = self._source_for_path(skill_path)
        if source == "project":
            return "项目本地"
        if source == "plugin":
            return "插件内置"
        return "用户已安装"

    def _is_project_skill(self, skill_path: Path) -> bool:
        """判断 Skill 是否位于项目本地可保存目录。"""

        resolved_path = skill_path.resolve()
        project_roots = [
            self.project_skill_root,
            (self.config.project_root / ".codex" / "skills").resolve(),
        ]
        for root in project_roots:
            try:
                resolved_path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _assert_inside_project_skill_root(self, destination_dir: Path) -> None:
        """保存前做路径边界检查，避免写出项目本地 Skill 根目录。"""

        try:
            destination_dir.relative_to(self.project_skill_root)
        except ValueError as exc:
            raise ValueError("保存路径不在项目本地 Skill 目录内。") from exc

    def _source_priority(self, source: str) -> int:
        """同名 Skill 去重时，项目本地副本优先，其次用户安装，最后插件内置。"""

        return {
            "project": 0,
            "user": 1,
            "plugin": 2,
        }.get(source, 9)

    def _read_text(self, skill_path: Path) -> str:
        """读取 SKILL.md，兼容 UTF-8 BOM。"""

        return skill_path.read_text(encoding="utf-8-sig")

    def _clamp_score(self, raw_score: Any) -> int:
        """把模型给出的分数收敛到 0-100。"""

        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            score = 80
        return max(0, min(score, 100))
