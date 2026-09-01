from __future__ import annotations

from pathlib import Path

from src.config.config_manager import ConfigManager
from src.services.image_prompt_design_service import ImagePromptDesignService
from src.services.media_creative_brief_service import MediaCreativeBriefService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_seedream_prompt_uses_exact_topology_contract() -> None:
    config = ConfigManager(PROJECT_ROOT).load()
    service = ImagePromptDesignService(config)
    prompt = service.build_project_architecture_prompt(
        repository_full_name="example/spec-kit",
        focus_prompt="规格驱动开发把需求逐步编译为计划和任务。",
        project_summary_text="规格先成为可执行输入，再经过计划和任务拆解进入实现。",
        visual_brief={
            "diagram_type": "linear_progression",
            "teaching_goal": "解释规格如何成为可执行开发流程",
            "visual_thesis": "规格经过计划与任务拆解后约束实现",
            "nodes": [
                {"id": "spec", "label": "需求规格", "role": "事实输入"},
                {"id": "plan", "label": "技术计划", "role": "方案编排"},
                {"id": "tasks", "label": "任务清单", "role": "执行拆解"},
                {"id": "code", "label": "代码实现", "role": "交付结果"},
            ],
            "relationships": [
                {"from": "spec", "to": "plan", "label": "数据流"},
                {"from": "plan", "to": "tasks", "label": "同步调用"},
                {"from": "tasks", "to": "code", "label": "事件推送"},
            ],
            "reading_order": ["spec", "plan", "tasks", "code"],
            "chinese_labels": ["需求规格", "技术计划", "任务清单", "代码实现"],
        },
        project_index=1,
    )

    assert len(prompt) <= 300
    assert "无标题的16:9工程架构信息图" in prompt
    assert "仅绘制4个模块" in prompt
    assert "每个模块只出现一次" in prompt
    assert "禁止镜像、复制或新增" in prompt
    assert "需求规格" in prompt
    assert "位置与阅读顺序：左到右" in prompt
    assert "箭头仅限" in prompt
    assert "「需求规格」→「技术计划」" in prompt
    assert "「技术计划」→「任务清单」" in prompt
    assert "「任务清单」→「代码实现」" in prompt
    assert "连线不写文字" in prompt
    assert "禁止标题区、装饰点" in prompt
    assert "example/spec-kit" not in prompt


def test_hub_prompt_places_each_skill_node_once_without_mirrored_fillers() -> None:
    config = ConfigManager(PROJECT_ROOT).load()
    service = ImagePromptDesignService(config)
    prompt = service.build_project_architecture_prompt(
        repository_full_name="mattpocock/skills",
        focus_prompt="两种安装路径汇入技能集合，再完成统一初始化配置。",
        visual_brief={
            "diagram_type": "hub_spoke",
            "visual_thesis": "两种安装路径汇入技能集合，再完成统一初始化配置",
            "nodes": [
                {"id": "plugin", "label": "插件安装", "role": "只读订阅"},
                {"id": "file", "label": "文件安装", "role": "本地掌控"},
                {"id": "skills", "label": "技能集合", "role": "统一入口"},
                {"id": "config", "label": "初始化配置", "role": "统一配置"},
            ],
            "relationships": [
                {"from": "plugin", "to": "skills", "label": "数据流"},
                {"from": "file", "to": "skills", "label": "数据流"},
                {"from": "skills", "to": "config", "label": "数据流"},
            ],
            "reading_order": ["plugin", "file", "skills", "config"],
            "chinese_labels": ["插件安装", "文件安装", "技能集合", "初始化配置"],
        },
        project_index=1,
    )

    assert len(prompt) <= 300
    assert "中心「技能集合」" in prompt
    assert "左上「插件安装」" in prompt
    assert "左下「文件安装」" in prompt
    assert "右侧「初始化配置」" in prompt
    assert "「插件安装」→「技能集合」" in prompt
    assert "「文件安装」→「技能集合」" in prompt
    assert "「技能集合」→「初始化配置」" in prompt
    assert "禁止为对称构图补节点" in prompt


def test_visual_brief_limits_nodes_and_normalizes_relationship_labels() -> None:
    brief = MediaCreativeBriefService().normalize_visual_brief(
        raw_brief={
            "diagram_type": "hub_spoke",
            "nodes": [
                {"id": f"node_{index}", "label": f"模块{index}", "role": "组件"}
                for index in range(1, 7)
            ],
            "relationships": [
                {"from": "node_1", "to": "node_2", "label": "调用"},
                {"from": "node_2", "to": "node_3", "label": "异步"},
                {"from": "node_3", "to": "node_4", "label": "自定义关系"},
            ],
        },
        repository_full_name="example/project",
        fallback_text="模块协作关系",
        project_index=1,
    )

    assert len(brief["nodes"]) == 4
    assert [item["label"] for item in brief["relationships"]] == [
        "同步调用",
        "异步调用",
        "数据流",
    ]
    assert len(brief["chinese_labels"]) == 4


def test_visual_brief_keeps_meaningful_labels_up_to_twelve_characters() -> None:
    brief = MediaCreativeBriefService().normalize_visual_brief(
        raw_brief={
            "nodes": [
                {
                    "id": "runtime",
                    "label": "统一LLM运行时接口",
                    "role": "适配不同模型",
                },
                {"id": "result", "label": "结构化结果", "role": "输出"},
            ]
        },
        repository_full_name="example/project",
        fallback_text="统一模型调用并输出结构化结果",
        project_index=1,
    )

    assert brief["nodes"][0]["label"] == "统一LLM运行时接口"
    assert len(brief["nodes"][0]["label"]) <= 12
