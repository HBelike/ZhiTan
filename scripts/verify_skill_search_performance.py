"""离线验证 Skill 搜索的缓存、并行读取和 Star 非阻塞语义。"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.config_manager import AppConfig
from src.services.skill_library_service import (
    SkillLibraryService,
    SkillSearchItem,
    SkillSearchResult,
    SkillSummary,
)


class FakeResponse:
    """为 GitHub Code Search 构造最小 requests 响应替身。"""

    ok = True
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def json(self) -> dict[str, Any]:
        return self.payload


def build_config(project_root: Path) -> AppConfig:
    """创建无需真实 Key 的最小配置。"""

    return AppConfig(
        project_root=project_root,
        config_path=project_root / "config" / "app.yaml",
        raw={
            "app": {"name": "skill-test"},
            "github": {"api_version": "2022-11-28", "token_env": "SKILL_TEST_GITHUB_TOKEN"},
            "llm": {"model": "deepseek-v4-pro", "api_key_env": "SKILL_TEST_LLM_KEY"},
        },
    )


def write_skill(path: Path, *, name: str, repository: str) -> None:
    """写入带 GitHub 元信息的最小 Skill。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: 测试 Skill。\nrepository: {repository}\n---\n",
        encoding="utf-8",
    )


def main() -> int:
    """运行不访问真实 GitHub 或 LLM 的行为验证。"""

    with tempfile.TemporaryDirectory(prefix="skill-search-") as temporary_dir:
        root = Path(temporary_dir)
        write_skill(root / ".agents" / "skills" / "local" / "SKILL.md", name="local", repository="owner/local")
        service = SkillLibraryService(config=build_config(root))

        # 列表页不能为 Star 同步访问网络；缓存为空时应该直接显示“暂无”。
        service._fetch_repository_stars = lambda _repository: (_ for _ in ()).throw(AssertionError("Star 请求不应发生"))  # type: ignore[method-assign]
        assert service.list_skills()[0].stars is None

        # 八个候选以 0.2 秒模拟网络读取；并发后耗时应显著低于串行 1.6 秒。
        import src.services.skill_library_service as skill_module

        original_get = skill_module.requests.get
        skill_module.requests.get = lambda *_args, **_kwargs: FakeResponse(  # type: ignore[assignment]
            {
                "items": [
                    {
                        "url": f"https://api.github.com/repos/owner/repo{i}/contents/SKILL.md",
                        "path": f"skills/example-{i}/SKILL.md",
                        "sha": f"sha-{i}",
                        "html_url": f"https://github.com/owner/repo{i}/blob/main/SKILL.md",
                        "repository": {"full_name": f"owner/repo{i}", "owner": {"login": "owner"}},
                    }
                    for i in range(8)
                ]
            }
        )
        service._fetch_github_file_text = lambda item, _timeout: (  # type: ignore[method-assign]
            time.sleep(0.2) or f"---\nname: example-{item['sha']}\ndescription: example\n---\n"
        )
        started_at = time.monotonic()
        candidates = service._search_github_skill_files("example", deadline=started_at + 2)
        elapsed = time.monotonic() - started_at
        skill_module.requests.get = original_get
        assert len(candidates) == 8
        assert elapsed < 0.7, f"候选读取未并行，实际 {elapsed:.2f}s"

        # 成功的 GitHub 结果应进入本地持久缓存；同一查询第二次不再调用远端搜索。
        sample_skill = SkillSummary(
            id="github-sample",
            name="sample",
            description="sample",
            description_zh="示例 Skill。",
            author="owner",
            homepage_url="https://github.com/owner/repo",
            repository_full_name="owner/repo",
            stars=None,
            previous_stars=None,
            star_delta=None,
            star_growth_rate=None,
            stars_updated_at=None,
            source="github",
            source_label="GitHub",
            path_hint="owner/repo/SKILL.md",
            editable=False,
        )
        remote_calls = 0

        def fake_open_search(*, query: str, deadline: float) -> SkillSearchResult:
            nonlocal remote_calls
            remote_calls += 1
            return SkillSearchResult(
                items=[SkillSearchItem(skill=sample_skill, score=95, match_reason="测试命中", markdown="---\nname: sample\n---\n")],
                used_llm=False,
                model=None,
                fallback_reason=None,
                search_scope="github_open_skills",
                normalized_query=query,
            )

        service._search_open_skills = fake_open_search  # type: ignore[method-assign]
        first = service.search_skills("sample")
        second = service.search_skills("sample")
        assert first.items and not first.cache_hit
        assert second.items and second.cache_hit
        assert second.cache_state == "fresh"
        assert remote_calls == 1

        # 过期快照仍可读取，普通搜索不得因此再次访问 GitHub。
        cache_payload = json.loads(service.open_skill_cache_path.read_text(encoding="utf-8"))
        cache_record = next(iter(cache_payload["entries"].values()))
        cache_record["created_at"] = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
        service.open_skill_cache_path.write_text(
            json.dumps(cache_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        stale = service.search_skills("  SAMPLE  ")
        assert stale.items and stale.cache_hit
        assert stale.cache_state == "stale"
        assert remote_calls == 1

        # 只有显式刷新才允许绕过快照并重新请求 GitHub。
        refreshed = service.search_skills("sample", force_refresh=True)
        assert refreshed.items and not refreshed.cache_hit
        assert refreshed.cache_state == "live"
        assert remote_calls == 2

    print("skill_search_performance_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
