from __future__ import annotations

import copy

import pytest

from src.services.article_visual_planning_service import ArticleVisualPlanningService
from src.services.article_visual_spec import build_render_key
from src.services.article_visual_spec_validator import (
    ArticleVisualSpecError,
    ArticleVisualSpecValidator,
)


@pytest.fixture
def validator() -> ArticleVisualSpecValidator:
    return ArticleVisualSpecValidator()


def _common(role: str) -> dict[str, object]:
    return {
        "version": "article_visual_spec_v1",
        "repository_full_name": "owner/project",
        "figure_role": role,
        "purpose": "解释项目中可核验的核心机制",
        "headline": "从证据到工程结论",
        "evidence_refs": [
            {
                "kind": "repository_file",
                "path": "README.md",
                "claim": "README 给出明确的实现链路",
            }
        ],
        "takeaways": ["先核验证据，再表达工程结论"],
        "art_direction": {
            "style": "notion",
            "palette": "editorial_blue",
            "density": "medium",
        },
    }


def summary_spec(*, headline: str = "项目能力总结") -> dict[str, object]:
    return {
        **_common("summary_card"),
        "headline": headline,
        "positioning": "把仓库证据整理为可执行的技术判断",
        "capabilities": [
            {"label": "证据整理", "description": "读取仓库材料并保留来源"},
            {"label": "结论生成", "description": "围绕真实机制形成工程总结"},
        ],
    }


def flow_spec(*, label: str = "统一LLM适配层") -> dict[str, object]:
    return {
        **_common("flow"),
        "steps": [
            {"id": "spec", "label": "证据输入", "description": "读取已核验材料"},
            {"id": "adapter", "label": label, "description": "统一模型调用合同"},
            {"id": "output", "label": "结果输出", "description": "生成技术总结"},
        ],
        "edges": [
            {"from": "spec", "to": "adapter"},
            {"from": "adapter", "to": "output"},
        ],
    }


def architecture_spec() -> dict[str, object]:
    return {
        **_common("architecture"),
        "nodes": [
            {"id": "source", "label": "证据源", "description": "仓库文件与元数据"},
            {"id": "planner", "label": "规划服务", "description": "选择匹配的表达类型"},
            {"id": "renderer", "label": "渲染服务", "description": "确定性生成图片"},
        ],
        "edges": [
            {"from": "source", "to": "planner", "label": "提供证据"},
            {"from": "planner", "to": "renderer", "label": "传递规格"},
        ],
    }


def comparison_spec() -> dict[str, object]:
    return {
        **_common("comparison"),
        "left": {"label": "概率式生图", "description": "适合无文字概念背景"},
        "right": {"label": "确定性渲染", "description": "适合精确中文与拓扑"},
        "dimensions": [
            {"label": "文字精度", "left": "不可保证", "right": "可直接校验"},
            {"label": "关系方向", "left": "可能漂移", "right": "按规格绘制"},
        ],
    }


def timeline_spec() -> dict[str, object]:
    return {
        **_common("timeline"),
        "events": [
            {"id": "evidence", "time": "阶段一", "label": "收集证据", "description": "读取仓库材料"},
            {"id": "plan", "time": "阶段二", "label": "形成规格", "description": "选择内容结构"},
            {"id": "render", "time": "阶段三", "label": "生成图片", "description": "执行确定性渲染"},
        ],
    }


def test_flow_spec_keeps_complete_chinese_labels_and_edges(
    validator: ArticleVisualSpecValidator,
) -> None:
    spec = validator.validate(
        raw_spec=flow_spec(label="统一LLM适配层"),
        repository_full_name="owner/project",
        allowed_evidence_paths={"README.md"},
    )

    assert spec.value["steps"][1]["label"] == "统一LLM适配层"
    assert spec.edges == (("spec", "adapter"), ("adapter", "output"))
    assert spec.node_ids == ("spec", "adapter", "output")


@pytest.mark.parametrize("step_count", [1, 6, 12])
def test_flow_accepts_dynamic_step_count_and_derives_adjacent_edge_count(
    validator: ArticleVisualSpecValidator,
    step_count: int,
) -> None:
    raw = {
        **_common("flow"),
        "steps": [
            {
                "id": f"step{index}",
                "label": f"阶段{index}",
                "description": f"处理第{index}阶段",
            }
            for index in range(1, step_count + 1)
        ],
        "edges": [
            {"from": f"step{index}", "to": f"step{index + 1}"}
            for index in range(1, step_count)
        ],
    }

    spec = validator.validate(raw, "owner/project", {"README.md"})

    assert len(spec.value["steps"]) == step_count
    assert len(spec.value["edges"]) == max(0, step_count - 1)


@pytest.mark.parametrize(
    "phrase", ["16:9", "16：9", "16.9", "工程架构", "无标题", "流程图"]
)
def test_meta_words_are_rejected(
    validator: ArticleVisualSpecValidator,
    phrase: str,
) -> None:
    with pytest.raises(ArticleVisualSpecError, match="元文字"):
        validator.validate(summary_spec(headline=phrase), "owner/project", {"README.md"})


def test_dangling_edge_is_rejected(validator: ArticleVisualSpecValidator) -> None:
    raw = flow_spec()
    raw["edges"][1]["to"] = "missing"

    with pytest.raises(ArticleVisualSpecError, match="无效节点"):
        validator.validate(raw, "owner/project", {"README.md"})


@pytest.mark.parametrize(
    "raw_spec, role",
    [
        (summary_spec(), "summary_card"),
        (flow_spec(), "flow"),
        (architecture_spec(), "architecture"),
        (comparison_spec(), "comparison"),
        (timeline_spec(), "timeline"),
    ],
)
def test_five_figure_roles_accept_valid_minimum_specs(
    validator: ArticleVisualSpecValidator,
    raw_spec: dict[str, object],
    role: str,
) -> None:
    spec = validator.validate(raw_spec, "owner/project", {"README.md"})

    assert spec.figure_role == role
    assert spec.repository_full_name == "owner/project"
    assert spec.canonical_json
    assert spec.visible_texts


def test_empty_evidence_is_rejected(validator: ArticleVisualSpecValidator) -> None:
    raw = summary_spec()
    raw["evidence_refs"] = []

    with pytest.raises(ArticleVisualSpecError, match="evidence_refs"):
        validator.validate(raw, "owner/project")


def test_evidence_path_must_be_known(validator: ArticleVisualSpecValidator) -> None:
    with pytest.raises(ArticleVisualSpecError, match="证据路径"):
        validator.validate(summary_spec(), "owner/project", {"src/main.py"})


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda raw: raw["steps"].append(copy.deepcopy(raw["steps"][0])), "节点 ID 重复"),
        (lambda raw: raw["edges"].append({"from": "spec", "to": "spec"}), "自环"),
        (
            lambda raw: raw["edges"].append({"from": "spec", "to": "adapter"}),
            "重复边",
        ),
    ],
)
def test_invalid_topology_is_rejected(
    validator: ArticleVisualSpecValidator,
    mutation,
    message: str,
) -> None:
    raw = flow_spec()
    mutation(raw)

    with pytest.raises(ArticleVisualSpecError, match=message):
        validator.validate(raw, "owner/project")


def test_unknown_public_or_nested_fields_are_rejected(
    validator: ArticleVisualSpecValidator,
) -> None:
    raw = summary_spec()
    raw["css"] = "position:fixed"
    with pytest.raises(ArticleVisualSpecError, match="未知字段"):
        validator.validate(raw, "owner/project")

    raw = summary_spec()
    raw["capabilities"][0]["icon"] = "magic"
    with pytest.raises(ArticleVisualSpecError, match="未知字段"):
        validator.validate(raw, "owner/project")


def test_capacity_overflow_is_rejected_without_truncation(
    validator: ArticleVisualSpecValidator,
) -> None:
    long_label = "这是一段明显超过模板容量但绝对不能被静默截断的节点标签"
    raw = flow_spec(label=long_label)

    with pytest.raises(ArticleVisualSpecError, match="容量"):
        validator.validate(raw, "owner/project")
    assert raw["steps"][1]["label"] == long_label


def test_summary_card_accepts_positioning_up_to_180_characters(
    validator: ArticleVisualSpecValidator,
) -> None:
    positioning = "可核验的项目定位" * 20
    assert len(positioning) == 160
    raw = summary_spec()
    raw["positioning"] = positioning

    spec = validator.validate(raw, "owner/project")

    assert spec.value["positioning"] == positioning


def test_summary_card_accepts_capability_description_up_to_150_characters(
    validator: ArticleVisualSpecValidator,
) -> None:
    description = "可核验的项目能力描述" * 15
    assert len(description) == 150
    raw = summary_spec()
    raw["capabilities"][0]["description"] = description

    spec = validator.validate(raw, "owner/project")

    assert spec.value["capabilities"][0]["description"] == description


def test_summary_card_maximum_fields_fit_total_visible_capacity(
    validator: ArticleVisualSpecValidator,
) -> None:
    raw = summary_spec()
    raw["purpose"] = "目" * 80
    raw["headline"] = "题" * 30
    raw["positioning"] = "定" * 180
    raw["takeaways"] = ["甲" * 50, "乙" * 50, "丙" * 50]
    raw["capabilities"] = [
        {"label": f"能力{index}", "description": character * 150}
        for index, character in enumerate(("一", "二", "三", "四"), start=1)
    ]

    spec = validator.validate(raw, "owner/project")

    assert len(spec.value["capabilities"]) == 4


def test_flow_rejects_duplicate_visible_node_labels_with_different_ids(
    validator: ArticleVisualSpecValidator,
) -> None:
    raw = flow_spec()
    raw["steps"][2]["label"] = raw["steps"][0]["label"]

    with pytest.raises(ArticleVisualSpecError, match="节点 label 重复"):
        validator.validate(raw, "owner/project")


@pytest.mark.parametrize("variant", ["missing", "reversed", "extra", "out_of_order"])
def test_flow_requires_exact_adjacent_step_chain(
    validator: ArticleVisualSpecValidator,
    variant: str,
) -> None:
    raw = flow_spec()
    if variant == "missing":
        raw["steps"].insert(
            2,
            {"id": "review", "label": "结果校验", "description": "核对结构合同"},
        )
    elif variant == "reversed":
        raw["edges"][0] = {"from": "adapter", "to": "spec"}
    elif variant == "extra":
        raw["edges"].append({"from": "spec", "to": "output"})
    else:
        raw["edges"] = list(reversed(raw["edges"]))

    with pytest.raises(ArticleVisualSpecError, match="相邻顺序链"):
        validator.validate(raw, "owner/project")


def test_architecture_layers_require_complete_assignment_and_usage(
    validator: ArticleVisualSpecValidator,
) -> None:
    raw = architecture_spec()
    raw["layers"] = [
        {"id": "input", "label": "输入层"},
        {"id": "core", "label": "核心层"},
    ]
    raw["nodes"][0]["layer"] = "input"
    raw["nodes"][1]["layer"] = "core"
    raw["nodes"][2]["layer"] = "core"
    assert validator.validate(raw, "owner/project").value["nodes"][0]["layer"] == "input"

    missing = copy.deepcopy(raw)
    del missing["nodes"][2]["layer"]
    with pytest.raises(ArticleVisualSpecError, match="必须显式引用 layer"):
        validator.validate(missing, "owner/project")

    invalid = copy.deepcopy(raw)
    invalid["nodes"][0]["layer"] = "missing"
    with pytest.raises(ArticleVisualSpecError, match="无效层"):
        validator.validate(invalid, "owner/project")

    unused = copy.deepcopy(raw)
    unused["layers"].append({"id": "output", "label": "输出层"})
    with pytest.raises(ArticleVisualSpecError, match="没有节点使用"):
        validator.validate(unused, "owner/project")


def test_architecture_accepts_dynamic_layer_count(
    validator: ArticleVisualSpecValidator,
) -> None:
    raw = architecture_spec()
    raw["layers"] = [
        {"id": f"layer{index}", "label": f"第{index}层"}
        for index in range(1, 6)
    ]
    raw["nodes"] = [
        {
            "id": f"node{index}",
            "label": f"模块{index}",
            "description": f"负责第{index}层能力",
            "layer": f"layer{index}",
        }
        for index in range(1, 6)
    ]
    raw["edges"] = [
        {"from": f"node{index}", "to": f"node{index + 1}"}
        for index in range(1, 5)
    ]

    spec = validator.validate(raw, "owner/project", {"README.md"})

    assert len(spec.value["layers"]) == 5
    assert [item["layer"] for item in spec.value["nodes"]] == [
        "layer1",
        "layer2",
        "layer3",
        "layer4",
        "layer5",
    ]


def test_summary_and_comparison_labels_must_be_unique(
    validator: ArticleVisualSpecValidator,
) -> None:
    summary = summary_spec()
    summary["capabilities"][1]["label"] = summary["capabilities"][0]["label"]
    with pytest.raises(ArticleVisualSpecError, match="capabilities.label 重复"):
        validator.validate(summary, "owner/project")

    comparison = comparison_spec()
    comparison["dimensions"][1]["label"] = comparison["dimensions"][0]["label"]
    with pytest.raises(ArticleVisualSpecError, match="dimensions.label 重复"):
        validator.validate(comparison, "owner/project")


def test_placeholder_text_is_rejected(validator: ArticleVisualSpecValidator) -> None:
    raw = summary_spec()
    raw["capabilities"][0]["description"] = "TODO"

    with pytest.raises(ArticleVisualSpecError, match="占位"):
        validator.validate(raw, "owner/project")


def test_canonical_json_and_render_key_are_stable(
    validator: ArticleVisualSpecValidator,
) -> None:
    first = validator.validate(summary_spec(), "owner/project")
    reordered = {key: summary_spec()[key] for key in reversed(summary_spec())}
    second = validator.validate(reordered, "owner/project")

    assert first.canonical_json == second.canonical_json
    assert build_render_key(first, "template-v1", "renderer-v1", "font-v1") == build_render_key(
        second,
        "template-v1",
        "renderer-v1",
        "font-v1",
    )
    assert build_render_key(first, "template-v2", "renderer-v1", "font-v1") != build_render_key(
        first,
        "template-v1",
        "renderer-v1",
        "font-v1",
    )


def test_legacy_content_always_degrades_to_summary_card_without_old_nodes() -> None:
    service = ArticleVisualPlanningService()
    item = {
        "repository_full_name": "example/spec-kit",
        "summary_text": "该项目把规格逐步转化为可执行任务。",
        "project_analysis_markdown": """
**技术特点**
规格、计划和任务分担不同职责。

**机制拆解**
需求规格依次进入计划和任务清单。

**工程启发**
先写清验收条件，再进入实现。
""".strip(),
        "visual_brief": {
            "nodes": [{"id": "adapter", "label": "统一LLMA"}],
            "relationships": [{"from": "adapter", "to": "output"}],
        },
    }

    spec = service.plan_legacy_content_brief(item)

    assert spec.figure_role == "summary_card"
    assert "统一LLMA" not in spec.canonical_json
    assert [item["label"] for item in spec.value["capabilities"]] == [
        "技术特点",
        "机制拆解",
        "工程启发",
    ]
    assert spec.value["evidence_refs"][0]["path"] == "project_analysis_markdown"


def test_legacy_content_requires_all_source_sections() -> None:
    service = ArticleVisualPlanningService()
    with pytest.raises(ArticleVisualSpecError, match="工程启发"):
        service.plan_legacy_content_brief(
            {
                "repository_full_name": "example/project",
                "summary_text": "这是项目总结。",
                "project_analysis_markdown": "**技术特点**\n特点。\n\n**机制拆解**\n机制。",
            }
        )


def test_legacy_content_uses_complete_first_semantic_clause_without_slicing() -> None:
    service = ArticleVisualPlanningService()
    long_summary = (
        "该项目通过可追踪的仓库证据、稳定的结构合同和确定性渲染形成技术文章配图，"
        "同时保留失败关闭、资产复用与来源核验等工程边界。最终产物可以安全复用。"
    )
    spec = service.plan_legacy_content_brief(
        {
            "repository_full_name": "example/long-project",
            "summary_text": long_summary,
            "project_analysis_markdown": """
**技术特点**
需求规格负责定义目标与约束，后续内容即使很长也不应按字符位置裁切或进入总结卡。

**机制拆解**
证据先进入规划层；规格通过校验后进入渲染层，最终形成可复用资产。

**工程启发**
先核验证据，再表达结论。后续说明不属于第一语义分句。
""".strip(),
        }
    )

    assert spec.value["positioning"] == long_summary
    assert [item["description"] for item in spec.value["capabilities"]] == [
        "需求规格负责定义目标与约束，后续内容即使很长也不应按字符位置裁切或进入总结卡。",
        "证据先进入规划层；",
        "先核验证据，再表达结论。",
    ]
    assert spec.value["takeaways"] == [
        "最终产物可以安全复用。",
    ]


def test_legacy_content_does_not_treat_colon_as_a_complete_sentence() -> None:
    service = ArticleVisualPlanningService()
    spec = service.plan_legacy_content_brief(
        {
            "repository_full_name": "example/colon-project",
            "summary_text": "该项目先判断需求，再进入实现。后续执行保留人工确认。",
            "project_analysis_markdown": """
**技术特点**
项目通过阶段检查减少过度构建。

**机制拆解**
入口是：代理接到任务后，先检查是否真的需要写代码。

**工程启发**
这个项目的核心取舍是：先增加一次判断，再减少无效实现。
""".strip(),
        }
    )

    assert [item["description"] for item in spec.value["capabilities"]] == [
        "项目通过阶段检查减少过度构建。",
        "入口是：代理接到任务后，先检查是否真的需要写代码。",
        "这个项目的核心取舍是：先增加一次判断，再减少无效实现。",
    ]
    assert spec.value["takeaways"] == ["该项目先判断需求，再进入实现。"]
    assert not set(spec.value["takeaways"]) & {
        item["description"] for item in spec.value["capabilities"]
    }


def test_video_brief_only_maps_explicit_flow_nodes_and_edges() -> None:
    service = ArticleVisualPlanningService()
    flow = service.plan(flow_spec(), "owner/project", {"README.md"})
    flow_brief = service.to_video_visual_brief(flow)

    assert flow_brief["version"] == "creative_brief_v2"
    assert [node["label"] for node in flow_brief["nodes"]] == [
        "证据输入",
        "统一LLM适配层",
        "结果输出",
    ]
    assert [(edge["from"], edge["to"]) for edge in flow_brief["relationships"]] == [
        ("spec", "adapter"),
        ("adapter", "output"),
    ]

    comparison = service.plan(comparison_spec(), "owner/project", {"README.md"})
    comparison_brief = service.to_video_visual_brief(comparison)
    assert comparison_brief["nodes"] == []
    assert comparison_brief["relationships"] == []
    assert comparison_brief["chinese_labels"] == [
        "概率式生图",
        "确定性渲染",
        "文字精度",
        "关系方向",
    ]
