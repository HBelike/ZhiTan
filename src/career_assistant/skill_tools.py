"""求职助手 Skill 的受控 Tool Registry。"""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests

from src.career_assistant.contracts import ActivatedSkill, SkillExecutionTrace
from src.career_assistant.model_clients import FunctionToolCall, FunctionToolDefinition
from src.services.skill_library_service import SkillLibraryService


@dataclass(frozen=True)
class SkillToolExecution:
    """可回送模型的工具结果与可展示的安全轨迹。"""

    content: str
    trace: SkillExecutionTrace


class SkillToolRegistry:
    """为已挂载 Skill 提供仓库发现、检查和项目级安装工具。"""

    _definitions = {
        "search_skill_registry": FunctionToolDefinition(
            name="search_skill_registry",
            description=(
                "通过 skills.sh 官方搜索接口真实搜索开放 Agent Skill。"
                "当用户给出能力关键词而不是具体仓库时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要寻找的能力或任务，例如：制作 PPT 演示文稿",
                        "minLength": 1,
                        "maxLength": 300,
                    },
                    "owner": {
                        "type": "string",
                        "description": "可选的 GitHub owner，用于限制搜索来源",
                        "maxLength": 100,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        "inspect_skill_repository": FunctionToolDefinition(
            name="inspect_skill_repository",
            description=(
                "真实读取一个 GitHub 仓库的元数据、许可证和其中所有可安装 Skill。"
                "用户提供 GitHub URL、SSH 地址或 owner/repo 时，应先调用此工具，"
                "不要凭地址猜测仓库内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "repository": {
                        "type": "string",
                        "description": "GitHub URL、SSH 地址或 owner/repo",
                        "minLength": 3,
                        "maxLength": 300,
                    },
                },
                "required": ["repository"],
                "additionalProperties": False,
            },
        ),
        "install_skill_repository": FunctionToolDefinition(
            name="install_skill_repository",
            description=(
                "把已检查的 GitHub 仓库 Skill 完整安装到当前项目 .agents/skills。"
                "只有用户明确要求安装或下载时才能调用；应先调用 inspect_skill_repository。"
                "skill_names 省略或包含 * 表示安装仓库标准 skills/ 目录中的全部 Skill。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "repository": {
                        "type": "string",
                        "description": "GitHub URL、SSH 地址或 owner/repo",
                        "minLength": 3,
                        "maxLength": 300,
                    },
                    "skill_names": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                        "maxItems": 50,
                        "description": "要安装的 Skill 名称；省略表示安装全部标准 Skill",
                    },
                },
                "required": ["repository"],
                "additionalProperties": False,
            },
        ),
    }

    def __init__(self, skill_library: SkillLibraryService) -> None:
        self._skill_library = skill_library

    def definitions_for(
        self,
        activated_skills: tuple[ActivatedSkill, ...],
    ) -> tuple[FunctionToolDefinition, ...]:
        """只有本轮显式挂载了 Skill 时才暴露通用 Skill 管理工具。"""

        if not activated_skills:
            return ()
        return tuple(self._definitions.values())

    def execute(
        self,
        tool_call: FunctionToolCall,
        *,
        execution_mode: str,
        skill_name: str = "skill-runtime",
    ) -> SkillToolExecution:
        """执行一项白名单工具并返回结构化 JSON；未知工具立即拒绝。"""

        if tool_call.name not in self._definitions:
            raise ValueError(f"模型请求了未注册工具：{tool_call.name}")
        payload = self._arguments_object(tool_call.arguments)
        if tool_call.name == "search_skill_registry":
            result_payload, result_count, message = self._search_registry(payload)
        elif tool_call.name == "inspect_skill_repository":
            result_payload, result_count, message = self._inspect_repository(payload)
        else:
            result_payload, result_count, message = self._install_repository(payload)
        content = json.dumps(result_payload, ensure_ascii=False)
        return SkillToolExecution(
            content=content,
            trace=SkillExecutionTrace(
                skill_name=skill_name,
                tool_name=tool_call.name,
                execution_mode=execution_mode,
                status="succeeded",
                result_count=result_count,
                message=message,
            ),
        )

    def _search_registry(self, payload: dict[str, object]) -> tuple[dict[str, object], int, str]:
        query = self._required_string(payload, "query", maximum=300)
        owner = self._optional_string(payload, "owner", maximum=100)
        params: dict[str, str | int] = {"q": query, "limit": 20}
        response = requests.get("https://skills.sh/api/search", params=params, timeout=20)
        if not response.ok:
            raise ValueError(f"skills.sh 搜索失败：status={response.status_code}")
        raw = response.json()
        raw_skills = raw.get("skills") if isinstance(raw, dict) else None
        if not isinstance(raw_skills, list):
            raise ValueError("skills.sh 响应缺少 skills")
        items = []
        for item in raw_skills:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            if owner and not source.casefold().startswith(f"{owner.casefold()}/"):
                continue
            skill_id = str(item.get("skillId") or item.get("name") or "").strip()
            if not skill_id:
                continue
            items.append(
                {
                    "name": skill_id,
                    "source": source,
                    "installs": int(item.get("installs") or 0),
                    "url": f"https://skills.sh/{source}/{skill_id}" if source else None,
                },
            )
        return (
            {
                "status": "succeeded",
                "query": query,
                "owner": owner,
                "result_count": len(items),
                "data_source": "skills.sh_live",
                "items": items,
            },
            len(items),
            "已完成 skills.sh 实时搜索",
        )

    def _inspect_repository(self, payload: dict[str, object]) -> tuple[dict[str, object], int, str]:
        repository = self._required_string(payload, "repository", maximum=300)
        result = self._skill_library.inspect_skill_repository(repository)
        items = [
            {
                "name": item.name,
                "description": item.description,
                "path": item.path,
                "file_count": item.file_count,
                "size_bytes": item.size_bytes,
            }
            for item in result.skills
        ]
        return (
            {
                "status": "succeeded",
                "repository": result.repository_full_name,
                "default_branch": result.default_branch,
                "homepage_url": result.homepage_url,
                "stars": result.stars,
                "forks": result.forks,
                "license": result.license_spdx,
                "pushed_at": result.pushed_at,
                "skill_count": len(items),
                "skills": items,
            },
            len(items),
            f"已检查仓库，发现 {len(items)} 个可安装 Skill",
        )

    def _install_repository(self, payload: dict[str, object]) -> tuple[dict[str, object], int, str]:
        repository = self._required_string(payload, "repository", maximum=300)
        raw_names = payload.get("skill_names", [])
        if raw_names is None:
            raw_names = []
        if not isinstance(raw_names, list) or not all(isinstance(item, str) for item in raw_names):
            raise ValueError("install_skill_repository.skill_names 必须是字符串数组")
        if len(raw_names) > 50:
            raise ValueError("单次最多指定 50 个 Skill")
        result = self._skill_library.install_skill_repository(repository, raw_names)
        return (
            {
                "status": "succeeded",
                "repository": result.repository_full_name,
                "installed_count": len(result.installed_names),
                "installed_names": list(result.installed_names),
                "skipped_existing_names": list(result.skipped_names),
                "ready_count": len(result.installed_names) + len(result.skipped_names),
                "installed_file_count": result.installed_file_count,
                "install_scope": "project",
                "catalog_refreshed": True,
            },
            len(result.installed_names) + len(result.skipped_names),
            f"已安装 {len(result.installed_names)} 个 Skill，跳过 {len(result.skipped_names)} 个已存在项",
        )

    @staticmethod
    def _arguments_object(arguments: str) -> dict[str, object]:
        normalized = arguments.strip()
        if not normalized:
            raise ValueError("工具参数不能为空")
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise ValueError("工具参数必须是 JSON 对象") from exc
        if not isinstance(payload, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        return payload

    @staticmethod
    def _required_string(payload: dict[str, object], key: str, *, maximum: int) -> str:
        value = str(payload.get(key) or "").strip()
        if not value:
            raise ValueError(f"工具参数缺少 {key}")
        if len(value) > maximum:
            raise ValueError(f"工具参数 {key} 不能超过 {maximum} 个字符")
        return value

    @staticmethod
    def _optional_string(payload: dict[str, object], key: str, *, maximum: int) -> str | None:
        value = str(payload.get(key) or "").strip()
        if not value:
            return None
        if len(value) > maximum:
            raise ValueError(f"工具参数 {key} 不能超过 {maximum} 个字符")
        return value
