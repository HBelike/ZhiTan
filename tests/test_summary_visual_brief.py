from __future__ import annotations

import json

import pytest

from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.weekly_ranking_repository import WeeklyRankingRecord
from src.services.article_visual_spec import ArticleVisualSpecError
from src.tasks.summary_task import SummaryTask


def _ranking() -> WeeklyRankingRecord:
    return WeeklyRankingRecord(
        repository_id=1,
        rank=1,
        full_name="example/spec-kit",
        html_url="https://github.com/example/spec-kit",
        description="规格驱动开发工具",
        language="Python",
        current_stars=100,
        star_growth=10,
        growth_rate=0.1,
        score=1.0,
        reason="测试",
    )


def _project_analysis() -> str:
    return """
example/spec-kit 本周新增 10 stars，总 stars 为 100。它用规格约束实现，适合需要持续追踪需求的团队。

**技术特点**
需求规格、技术计划和任务清单分担不同职责，减少需求与实现脱节。

**机制拆解**
需求规格依次流向技术计划、任务清单和代码实现。

**工程启发**
把验收条件前置；规格质量仍需要人工确认，阅读时先看规格入口再看任务生成。
""".strip()


def _flow_visual_spec() -> dict[str, object]:
    return {
        "version": "article_visual_spec_v1",
        "repository_full_name": "example/spec-kit",
        "figure_role": "flow",
        "purpose": "解释规格如何转化为可执行任务",
        "headline": "从规格到可执行任务",
        "evidence_refs": [
            {
                "kind": "repository_file",
                "path": "README.md",
                "claim": "README 给出阶段顺序",
            }
        ],
        "steps": [
            {"id": "spec", "label": "需求规格", "description": "明确目标与约束"},
            {"id": "plan", "label": "技术计划", "description": "拆分交付步骤"},
            {"id": "task", "label": "任务清单", "description": "形成执行清单"},
        ],
        "edges": [
            {"from": "spec", "to": "plan"},
            {"from": "plan", "to": "task"},
        ],
        "takeaways": ["先约束再实现"],
        "art_direction": {
            "style": "notion",
            "palette": "editorial_blue",
            "density": "medium",
        },
    }


def _summary_card_visual_spec() -> dict[str, object]:
    return {
        "version": "article_visual_spec_v1",
        "repository_full_name": "example/spec-kit",
        "figure_role": "summary_card",
        "purpose": "总结项目定位和适用边界",
        "headline": "规格驱动开发",
        "evidence_refs": [
            {
                "kind": "repository_metadata",
                "path": "repository.description",
                "claim": "项目提供规格驱动开发工具",
            }
        ],
        "positioning": "用规格连接需求、计划与执行，适合需要持续追踪交付条件的团队。",
        "capabilities": [
            {"label": "需求约束", "description": "把目标和验收条件写入规格"},
            {"label": "计划拆解", "description": "把规格转为可执行的交付步骤"},
        ],
        "takeaways": ["规格质量仍需人工确认"],
        "art_direction": {
            "style": "notion",
            "palette": "editorial_blue",
            "density": "medium",
        },
    }


def _project_brief(visual_spec: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "repository_full_name": "example/spec-kit",
        "summary_text": "example/spec-kit 用规格解决需求与实现脱节，通过计划和任务拆解约束编码；规格质量仍需人工确认。",
        "visual_spec": visual_spec or _flow_visual_spec(),
    }


def test_content_brief_persists_visual_spec_and_derives_video_compatibility() -> None:
    task = object.__new__(SummaryTask)
    ranking = _ranking()

    briefs = task._build_content_briefs(
        rankings=[ranking],
        project_analyses={ranking.full_name: _project_analysis()},
        raw_project_briefs=[_project_brief()],
    )

    assert len(briefs) == 1
    assert briefs[0]["summary_text"].startswith("该项目用规格解决需求与实现脱节")
    assert "人工确认" in briefs[0]["summary_text"]
    assert briefs[0]["prompt"] == "解释规格如何转化为可执行任务"
    assert briefs[0]["raw_prompt"] == "解释规格如何转化为可执行任务"
    assert briefs[0]["prompt_stage"] == "article_visual_spec_v1"
    assert briefs[0]["visual_spec"]["figure_role"] == "flow"
    assert [node["label"] for node in briefs[0]["visual_brief"]["nodes"]] == [
        "需求规格",
        "技术计划",
        "任务清单",
    ]
    assert briefs[0]["visual_brief"]["relationships"] == [
        {"from": "spec", "to": "plan"},
        {"from": "plan", "to": "task"},
    ]
    assert briefs[0]["video_brief"]["mechanism"] == "解释规格如何转化为可执行任务"


def test_summary_card_does_not_invent_static_architecture_or_data_flow() -> None:
    task = object.__new__(SummaryTask)
    ranking = _ranking()

    brief = task._build_content_briefs(
        rankings=[ranking],
        project_analyses={ranking.full_name: _project_analysis()},
        raw_project_briefs=[_project_brief(_summary_card_visual_spec())],
    )[0]

    assert brief["visual_spec"]["figure_role"] == "summary_card"
    assert brief["visual_brief"]["nodes"] == []
    assert brief["visual_brief"]["relationships"] == []
    assert brief["visual_brief"]["chinese_labels"] == ["需求约束", "计划拆解"]


def test_content_brief_rejects_non_adjacent_flow_edges() -> None:
    task = object.__new__(SummaryTask)
    ranking = _ranking()
    visual_spec = _flow_visual_spec()
    visual_spec["edges"] = [
        {"from": "spec", "to": "task"},
        {"from": "task", "to": "plan"},
    ]

    with pytest.raises(ArticleVisualSpecError, match="相邻顺序链"):
        task._build_content_briefs(
            rankings=[ranking],
            project_analyses={ranking.full_name: _project_analysis()},
            raw_project_briefs=[_project_brief(visual_spec)],
        )


def test_repository_normalizer_preserves_visual_spec() -> None:
    repository = object.__new__(GeneratedContentRepository)
    visual_spec = _flow_visual_spec()

    items = repository._normalize_image_prompts(
        json.dumps(
            {
                "items": [
                    {
                        "repository_full_name": "example/spec-kit",
                        "prompt": "解释规格如何转化为可执行任务",
                        "visual_spec": visual_spec,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        require_prompt=True,
    )

    assert items[0]["visual_spec"] == visual_spec
