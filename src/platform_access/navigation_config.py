"""平台路由模块与子功能的默认目录和可见性规则。"""

from __future__ import annotations

from typing import Any

from src.platform_access.contracts import PlatformRole


ROUTE_MODULE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "career_assistant",
        "label": "求职助手",
        "path": "/career",
        "description": "简历匹配、岗位分析与职业咨询",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "workbench",
        "label": "工作台",
        "path": "/review",
        "description": "公众号内容审核与媒体工作流",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "interview_library",
        "label": "面经库",
        "path": "/interviews",
        "description": "结构化面经沉淀与检索",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "job_library",
        "label": "职位库",
        "path": "/interviews/jobs",
        "description": "通过浏览器助手查找当前在招职位",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "skill_library",
        "label": "技能库",
        "path": "/skills",
        "description": "本地 Skill 查找、查看与维护",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "langsmith",
        "label": "LangSmith",
        "path": "/observability",
        "description": "模型调用链路与运行观测",
        "admin_only": True,
        "locked": False,
    },
    {
        "key": "career_interview_master",
        "label": "面试大师",
        "path": "/career/interview-master",
        "description": "控制求职助手中的面试大师入口",
        "admin_only": False,
        "locked": False,
        "scope": "feature",
        "parent_key": "career_assistant",
    },
    {
        "key": "career_online_assessment",
        "label": "线上笔试助手",
        "path": "/career/online-assessment",
        "description": "控制求职助手中的线上笔试助手入口",
        "admin_only": False,
        "locked": False,
        "scope": "feature",
        "parent_key": "career_assistant",
    },
    {
        "key": "admin_console",
        "label": "管理台",
        "path": "/admin/modules",
        "description": "平台路由、运行参数与内容工作流配置",
        "admin_only": True,
        "locked": True,
    },
)

DEFAULT_ROUTE_MODULE_SETTINGS: dict[str, bool] = {
    definition["key"]: True for definition in ROUTE_MODULE_DEFINITIONS
}
LEGACY_CAREER_TOOLS_KEY = "career_interview_tools"
CAREER_FEATURE_KEYS = ("career_interview_master", "career_online_assessment")
RETIRED_ROUTE_MODULE_KEYS = frozenset({"resume_assistant", "evaluation_center"})


def normalize_route_module_settings(value: dict[str, object] | None) -> dict[str, bool]:
    """将管理员输入合并到完整模块目录，并拒绝未知键与非布尔值。"""

    source = value or {}
    accepted_keys = {
        *DEFAULT_ROUTE_MODULE_SETTINGS,
        *RETIRED_ROUTE_MODULE_KEYS,
        LEGACY_CAREER_TOOLS_KEY,
    }
    unknown_keys = sorted(set(source) - accepted_keys)
    if unknown_keys:
        raise ValueError(f"存在未知路由模块：{', '.join(unknown_keys)}")

    legacy_enabled = source.get(LEGACY_CAREER_TOOLS_KEY)
    if LEGACY_CAREER_TOOLS_KEY in source and not isinstance(legacy_enabled, bool):
        raise ValueError(f"路由模块 {LEGACY_CAREER_TOOLS_KEY} 的启用状态必须是布尔值")

    normalized = dict(DEFAULT_ROUTE_MODULE_SETTINGS)
    if isinstance(legacy_enabled, bool):
        for key in CAREER_FEATURE_KEYS:
            normalized[key] = legacy_enabled
    for key, enabled in source.items():
        if key == LEGACY_CAREER_TOOLS_KEY or key in RETIRED_ROUTE_MODULE_KEYS:
            continue
        if not isinstance(enabled, bool):
            raise ValueError(f"路由模块 {key} 的启用状态必须是布尔值")
        normalized[key] = enabled

    # 管理台是管理员恢复其他模块配置的唯一入口，不能被关闭。
    normalized["admin_console"] = True
    return normalized


def route_modules_for_ui(value: dict[str, object] | None, role: PlatformRole) -> list[dict[str, object]]:
    """返回 UI 可直接渲染的有序目录；子功能开关对所有角色生效。"""

    settings = normalize_route_module_settings(value)
    is_admin = role.allows(PlatformRole.ADMIN)
    items: list[dict[str, object]] = []
    for definition in ROUTE_MODULE_DEFINITIONS:
        key = str(definition["key"])
        scope = str(definition.get("scope", "route"))
        enabled = settings[key]
        accessible = (
            enabled and (is_admin or not definition["admin_only"])
            if scope == "feature"
            else is_admin or (enabled and not definition["admin_only"])
        )
        items.append(
            {
                **definition,
                "scope": scope,
                "parent_key": definition.get("parent_key"),
                "enabled": enabled,
                "accessible": accessible,
            }
        )
    return items
