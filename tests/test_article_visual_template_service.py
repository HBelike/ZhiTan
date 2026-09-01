from __future__ import annotations

import json
import re

import pytest

from src.services.article_visual_spec_validator import ArticleVisualSpecValidator
from src.services.article_visual_template_service import ArticleVisualTemplateService


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


def _raw_spec(role: str) -> dict[str, object]:
    raw = _common(role)
    if role == "summary_card":
        raw.update(
            positioning="把仓库证据整理为可执行的技术判断",
            capabilities=[
                {"label": "证据整理", "description": "读取仓库材料并保留来源"},
                {"label": "结论生成", "description": "围绕真实机制形成工程总结"},
            ],
        )
    elif role == "flow":
        raw.update(
            steps=[
                {"id": "source", "label": "证据输入", "description": "读取已核验材料"},
                {"id": "plan", "label": "形成规格", "description": "统一内容结构"},
                {"id": "render", "label": "结果输出", "description": "生成技术总结"},
            ],
            edges=[
                {"from": "source", "to": "plan", "label": "归纳"},
                {"from": "plan", "to": "render", "label": "渲染"},
            ],
        )
    elif role == "architecture":
        raw.update(
            layers=[
                {"id": "input", "label": "输入层"},
                {"id": "core", "label": "核心层"},
            ],
            nodes=[
                {
                    "id": "source",
                    "label": "证据源",
                    "description": "仓库文件与元数据",
                    "layer": "input",
                },
                {
                    "id": "planner",
                    "label": "规划服务",
                    "description": "选择匹配的表达类型",
                    "layer": "core",
                },
                {
                    "id": "renderer",
                    "label": "渲染服务",
                    "description": "确定性生成图片",
                    "layer": "core",
                },
            ],
            edges=[
                {"from": "source", "to": "planner", "label": "提供证据"},
                {"from": "planner", "to": "renderer", "label": "传递规格"},
            ],
        )
    elif role == "comparison":
        raw.update(
            left={"label": "概率式生图", "description": "适合无文字概念背景"},
            right={"label": "确定性渲染", "description": "适合精确中文与拓扑"},
            dimensions=[
                {"label": "文字精度", "left": "不可保证", "right": "可直接校验"},
                {"label": "关系方向", "left": "可能漂移", "right": "按规格绘制"},
            ],
        )
    else:
        raw.update(
            events=[
                {"id": "evidence", "time": "阶段一", "label": "收集证据", "description": "读取仓库材料"},
                {"id": "plan", "time": "阶段二", "label": "形成规格", "description": "选择内容结构"},
                {"id": "render", "time": "阶段三", "label": "生成图片", "description": "执行确定性渲染"},
            ]
        )
    return raw


def _validated(role: str, **overrides: object):
    raw = _raw_spec(role)
    raw.update(overrides)
    return ArticleVisualSpecValidator().validate(raw, "owner/project", {"README.md"})


@pytest.mark.parametrize(
    "figure_role", ["summary_card", "flow", "architecture", "comparison", "timeline"]
)
def test_each_role_renders_a_self_validating_document(figure_role: str) -> None:
    document = ArticleVisualTemplateService().render(_validated(figure_role))

    assert '<meta charset="utf-8">' in document.html
    assert "NotoSansSC-VF.ttf" in document.html
    assert "width:2048px" in document.html
    assert "height:1152px" in document.html
    assert "window.__visualValidation" in document.html
    assert "status = 'passed'" in document.html
    assert "document.fonts.ready" in document.html
    assert "16:9" not in document.html
    assert "工程架构" not in document.html
    assert document.expected_texts


def test_visible_text_is_html_escaped() -> None:
    document = ArticleVisualTemplateService().render(
        _validated("summary_card", headline="A < B & C")
    )

    assert "A &lt; B &amp; C" in document.html
    assert "A < B & C" not in document.html
    assert ("headline", "A < B & C") in document.expected_texts


def test_expected_text_json_cannot_close_script_element() -> None:
    payload = "</script><script>"
    document = ArticleVisualTemplateService().render(
        _validated("summary_card", headline=payload)
    )

    match = re.search(
        r'<script id="expected-texts" type="application/json">(.*?)</script>',
        document.html,
        flags=re.DOTALL,
    )
    assert match is not None
    assert payload not in match.group(1)
    assert "\\u003c/script\\u003e" in match.group(1)
    assert json.loads(match.group(1))["headline"] == payload
    assert document.html.count(payload) == 0


@pytest.mark.parametrize("role", ["flow", "architecture"])
def test_relationship_templates_preserve_edge_identity_and_one_marker(role: str) -> None:
    spec = _validated(role)
    document = ArticleVisualTemplateService().render(spec)

    assert document.edges == spec.edges
    assert document.html.count('<marker id="arrowhead"') == 1
    for source, target in spec.edges:
        edge = f'data-from="{source}" data-to="{target}"'
        assert edge in document.html
    assert document.html.count('marker-end="url(#arrowhead)"') == len(spec.edges)


def test_flow_is_a_strict_left_to_right_chain() -> None:
    document = ArticleVisualTemplateService().render(_validated("flow"))
    positions = [
        int(value)
        for value in re.findall(r'data-node-id="(?:source|plan|render)" data-x="(\d+)"', document.html)
    ]

    assert positions == sorted(positions)
    assert len(positions) == 3


@pytest.mark.parametrize("step_count", [6, 12])
def test_dynamic_flow_uses_multiple_rows_without_dropping_steps(step_count: int) -> None:
    steps = [
        {
            "id": f"step{index}",
            "label": f"阶段{index}",
            "description": f"处理第{index}阶段",
        }
        for index in range(1, step_count + 1)
    ]
    edges = [
        {"from": f"step{index}", "to": f"step{index + 1}"}
        for index in range(1, step_count)
    ]

    document = ArticleVisualTemplateService().render(
        _validated("flow", steps=steps, edges=edges)
    )
    row_indexes = {
        int(value)
        for value in re.findall(
            r'data-node-id="step\d+"[^>]*data-row="(\d+)"', document.html
        )
    }

    assert len(row_indexes) >= 2
    assert document.html.count('class="flow-node node-card') == step_count
    assert document.html.count('marker-end="url(#arrowhead)"') == step_count - 1


def test_architecture_nodes_are_partitioned_by_declared_layer() -> None:
    document = ArticleVisualTemplateService().render(_validated("architecture"))

    assert 'data-layer-id="input"' in document.html
    assert 'data-layer-id="core"' in document.html
    assert 'data-node-id="source" data-layer="input"' in document.html
    assert 'data-node-id="planner" data-layer="core"' in document.html
    assert 'data-node-id="renderer" data-layer="core"' in document.html


@pytest.mark.parametrize("layer_count", [5, 7])
def test_architecture_compacts_dynamic_layers_without_dropping_content(
    layer_count: int,
) -> None:
    layers = [
        {"id": f"layer{index}", "label": f"第{index}层"}
        for index in range(1, layer_count + 1)
    ]
    nodes = [
        {
            "id": f"node{index}",
            "label": f"模块{index}",
            "description": f"负责第{index}层能力",
            "layer": f"layer{index}",
        }
        for index in range(1, layer_count + 1)
    ]
    edges = [
        {"from": f"node{index}", "to": f"node{index + 1}"}
        for index in range(1, layer_count)
    ]

    document = ArticleVisualTemplateService().render(
        _validated("architecture", layers=layers, nodes=nodes, edges=edges)
    )

    assert f"architecture-layer-count-{layer_count}" in document.html
    assert document.html.count('class="architecture-layer"') == layer_count
    assert document.html.count('class="architecture-node node-card"') == layer_count
    assert all(
        f'data-layer-id="layer{index}"' in document.html
        and f'data-node-id="node{index}"' in document.html
        for index in range(1, layer_count + 1)
    )


def test_document_exposes_exact_expected_text_entries() -> None:
    spec = _validated("comparison")
    document = ArticleVisualTemplateService().render(spec)

    assert len(document.expected_texts) == len(dict(document.expected_texts))
    assert set(dict(document.expected_texts).values()) == set(spec.visible_texts)
    for key, _text in document.expected_texts:
        assert document.html.count(f'data-text-key="{key}"') == 1


def test_three_card_summary_compacts_80_character_descriptions() -> None:
    descriptions = [
        "可核验的项目能力描述" * 8,
        "可追踪的项目机制说明" * 8,
        "可落地的项目工程结论" * 8,
    ]
    assert all(len(item) == 80 for item in descriptions)
    document = ArticleVisualTemplateService().render(
        _validated(
            "summary_card",
            capabilities=[
                {"label": "技术特点", "description": descriptions[0]},
                {"label": "机制拆解", "description": descriptions[1]},
                {"label": "工程启发", "description": descriptions[2]},
            ],
        )
    )

    assert 'class="capability-grid capability-count-3 capability-copy-compact"' in document.html
    for description in descriptions:
        assert description in document.html


def test_four_card_summary_uses_dense_layout_for_150_character_descriptions() -> None:
    descriptions = [
        "可核验的项目能力描述" * 15,
        "可追踪的项目机制说明" * 15,
        "可落地的项目工程结论" * 15,
        "可复用的项目实践经验" * 15,
    ]
    assert all(len(item) == 150 for item in descriptions)
    document = ArticleVisualTemplateService().render(
        _validated(
            "summary_card",
            capabilities=[
                {"label": f"能力{index}", "description": description}
                for index, description in enumerate(descriptions, start=1)
            ],
        )
    )

    assert 'class="capability-grid capability-count-4 capability-copy-dense"' in document.html
    for description in descriptions:
        assert description in document.html
