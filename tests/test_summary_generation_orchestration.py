from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

import pytest

from src.providers.deepseek_provider import DeepSeekChatResponse, DeepSeekMessage
from src.providers.github_client import GitHubRepositoryEvidence
from src.repositories.weekly_ranking_repository import WeeklyRankingRecord
from src.tasks.summary_task import SummaryTask


class StubProvider:
    """按队列返回离线 JSON，并记录 SummaryTask 的调用顺序。"""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[DeepSeekMessage],
        *,
        retry_empty_content: bool,
        trace_metadata: dict[str, Any],
    ) -> DeepSeekChatResponse:
        self.calls.append(
            {
                "messages": messages,
                "retry_empty_content": retry_empty_content,
                "trace_metadata": dict(trace_metadata),
            }
        )
        payload = self.payloads.pop(0)
        return DeepSeekChatResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model="stub-deepseek",
            raw_response={"stub_call": len(self.calls)},
        )


def _build_rankings(count: int) -> list[WeeklyRankingRecord]:
    return [
        WeeklyRankingRecord(
            repository_id=index,
            rank=index,
            full_name=f"example-org/project-{index}",
            html_url=f"https://github.com/example-org/project-{index}",
            description=f"用于验证第 {index} 个项目的工程说明。",
            language="Python",
            current_stars=10_000 + index,
            star_growth=800 + index,
            growth_rate=0.12,
            score=0.9,
            reason="离线合同验证样本",
        )
        for index in range(1, count + 1)
    ]


def _build_evidence(
    rankings: list[WeeklyRankingRecord],
) -> dict[str, GitHubRepositoryEvidence]:
    return {
        item.full_name: GitHubRepositoryEvidence(
            full_name=item.full_name,
            description=item.description,
            topics=("agent", "workflow"),
            default_branch="main",
            license_name="MIT",
            readme_excerpt=(
                "README 说明证据卡进入编排器，编排器同步调用校验门，"
                "校验通过后形成交付物。四个模块职责独立，生产接入需要人工确认。"
            ),
            evidence_status="readme",
        )
        for item in rankings
    }


def _build_project_payload(item: WeeklyRankingRecord) -> dict[str, Any]:
    return {
        "repository_full_name": item.full_name,
        "overview_text": (
            f"{item.full_name} 本周新增 {item.star_growth} stars，总 stars 为 {item.current_stars}。"
            "它把事实证据组织成可检查的工程流程，适合需要人工验收的代理任务。"
        ),
        "technical_features_markdown": (
            "README 将证据卡、编排器、校验门和交付物分成职责明确的模块，"
            "让事实准备、步骤组织与结果验收可以分别检查。"
        ),
        "mechanism_breakdown_markdown": (
            "证据卡先把事实传给编排器，编排器同步调用校验门，"
            "校验通过后再把结果写入交付物。README 摘录没有提供具体类名和入口函数。"
        ),
        "engineering_insights_markdown": (
            "生产接入要保留人工确认。阅读源码时应先找证据入口，再看编排和校验边界，"
            "最后确认交付物如何保存。"
        ),
        "project_brief": {
            "summary_text": (
                "该项目用职责分层组织证据、编排与校验，适合需要人工确认的代理流程。"
            ),
            "visual_spec": {
                "version": "article_visual_spec_v1",
                "repository_full_name": item.full_name,
                "figure_role": "flow",
                "purpose": "解释证据如何经过编排和校验形成交付物",
                "headline": "从证据到交付物",
                "evidence_refs": [
                    {
                        "kind": "repository_file",
                        "path": "README.md",
                        "claim": "README 给出四个阶段的先后顺序",
                    }
                ],
                "steps": [
                    {"id": "evidence", "label": "证据卡", "description": "准备事实来源"},
                    {"id": "orchestrator", "label": "编排器", "description": "组织执行步骤"},
                    {"id": "gate", "label": "校验门", "description": "完成质量确认"},
                    {"id": "artifact", "label": "交付物", "description": "保存验收结果"},
                ],
                "edges": [
                    {"from": "evidence", "to": "orchestrator"},
                    {"from": "orchestrator", "to": "gate"},
                    {"from": "gate", "to": "artifact"},
                ],
                "takeaways": ["职责分层让流程可检查"],
                "art_direction": {
                    "style": "notion",
                    "palette": "editorial_blue",
                    "density": "medium",
                },
            },
        },
    }


def _build_global_payload() -> dict[str, str]:
    return {
        "title": "代理工具开始把生成能力放进可检查的工程流程",
        "digest": "本周项目共同处理证据、编排和验收边界，帮助开发者判断工具如何进入现有工作流。",
        "opening_markdown": (
            "本周项目的增长落在同一个问题上：模型给出结果以后，工程团队如何检查过程并验收输出。"
        ),
        "weekly_theme_markdown": (
            "这些项目分别处理证据、编排、校验和交付环节，共同要求输入与责任边界可以追踪。"
        ),
        "closing_markdown": (
            "接入代理工具前，应先明确事实入口、责任边界和验收方式，再决定自动化范围。"
        ),
    }


def _task() -> SummaryTask:
    task = object.__new__(SummaryTask)
    task.logger = logging.getLogger("test.summary.orchestration")
    return task


def _generate(
    rankings: list[WeeklyRankingRecord],
    provider: StubProvider,
) -> tuple[DeepSeekChatResponse, dict[str, Any], dict[str, Any]]:
    return _task()._generate_normalized_content(
        provider=provider,
        rankings=rankings,
        week_end="2026-08-14",
        highest_star_repository=max(rankings, key=lambda item: item.current_stars),
        ranking_evidence=_build_evidence(rankings),
        article_skill_instructions="# GitHub 项目文章\n只根据证据写作。",
        regeneration_feedback=None,
        summary_instruction="",
    )


@pytest.mark.parametrize("project_count", [1, 3, 6, 12])
def test_generation_calls_each_project_then_one_global_synthesis(project_count: int) -> None:
    rankings = _build_rankings(project_count)
    provider = StubProvider(
        [_build_project_payload(item) for item in rankings] + [_build_global_payload()]
    )

    global_response, normalized, audit = _generate(rankings, provider)

    assert global_response.model == "stub-deepseek"
    assert len(provider.calls) == project_count + 1
    assert [call["trace_metadata"]["phase"] for call in provider.calls[:-1]] == [
        "project_generation"
    ] * project_count
    assert provider.calls[-1]["trace_metadata"]["phase"] == "global_synthesis"
    assert len(normalized["image_prompts"]) == project_count
    assert audit["project_count"] == project_count
    assert audit["provider_call_count"] == project_count + 1

    for index, call in enumerate(provider.calls[:-1]):
        prompt = call["messages"][-1].content
        assert rankings[index].full_name in prompt
        assert "visual_spec" in prompt
        assert "summary_card" in prompt
        assert "description 不超过 150 字" in prompt
        assert "证据不足时选择 summary_card" in prompt
        assert "禁止输出 16:9" in prompt
        assert "weekly_ranking→weekly_ranking" in prompt
        assert "repository.description→repository_metadata" in prompt
        assert "repository.topics→repository_metadata" in prompt
        assert "repository.license→repository_metadata" in prompt
        assert "README.md→repository_file" in prompt
        assert "每个 node 都必须包含有效 layer" in prompt
        assert "每层至少被一个 node 使用" in prompt
        assert "layers 数量按证据中的真实分层动态确定" in prompt
        assert "layers 为 1 到 4 项" not in prompt
        assert "steps 数量按证据中的真实流程动态确定" in prompt
        assert "steps 为 3 到 5 项" not in prompt
        assert all(
            f'"full_name": "{other.full_name}"' not in prompt
            for other_index, other in enumerate(rankings)
            if other_index != index
        )
    global_prompt = provider.calls[-1]["messages"][-1].content
    assert all(item.full_name in global_prompt for item in rankings)
    assert "visual_brief" not in global_prompt


def test_generation_retries_only_the_failed_project() -> None:
    rankings = _build_rankings(3)
    invalid_second = _build_project_payload(rankings[1])
    invalid_second["repository_full_name"] = "wrong/repository"
    provider = StubProvider(
        [
            _build_project_payload(rankings[0]),
            invalid_second,
            _build_project_payload(rankings[1]),
            _build_project_payload(rankings[2]),
            _build_global_payload(),
        ]
    )

    _, _, audit = _generate(rankings, provider)

    assert [call["trace_metadata"]["phase"] for call in provider.calls] == [
        "project_generation",
        "project_generation",
        "project_repair",
        "project_generation",
        "global_synthesis",
    ]
    assert [
        call["trace_metadata"].get("repository_full_name") for call in provider.calls
    ] == [
        "example-org/project-1",
        "example-org/project-2",
        "example-org/project-2",
        "example-org/project-3",
        None,
    ]
    assert audit["provider_call_count"] == 5


def test_generation_repairs_visual_spec_with_evidence_path_outside_repository_inputs() -> None:
    rankings = _build_rankings(1)
    invalid = _build_project_payload(rankings[0])
    invalid["project_brief"]["visual_spec"]["evidence_refs"][0]["path"] = "docs/unknown.md"
    provider = StubProvider(
        [invalid, _build_project_payload(rankings[0]), _build_global_payload()]
    )

    _, normalized, audit = _generate(rankings, provider)

    assert [call["trace_metadata"]["phase"] for call in provider.calls] == [
        "project_generation",
        "project_repair",
        "global_synthesis",
    ]
    assert audit["provider_call_count"] == 3
    assert normalized["image_prompts"][0]["visual_spec"]["evidence_refs"][0]["path"] == "README.md"


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("repository.description", "repository_metadata"),
        ("repository.topics", "repository_metadata"),
        ("repository.license", "repository_metadata"),
        ("README.md", "repository_file"),
    ],
)
def test_empty_repository_evidence_cannot_be_referenced(path: str, kind: str) -> None:
    ranking = replace(_build_rankings(1)[0], description="")
    evidence = GitHubRepositoryEvidence(
        full_name=ranking.full_name,
        description="",
        topics=(),
        default_branch="main",
        license_name="",
        readme_excerpt="",
        evidence_status="basic",
    )
    payload = _build_project_payload(ranking)
    payload["project_brief"]["visual_spec"]["evidence_refs"] = [
        {"kind": kind, "path": path, "claim": "空证据不应通过"}
    ]

    with pytest.raises(ValueError, match="证据路径不在允许范围内"):
        _task()._normalize_project_output(payload, ranking, evidence)

    prompt = _task()._build_project_messages(
        ranking=ranking,
        ranking_evidence=evidence,
        article_skill_instructions="只依据证据写作。",
        regeneration_feedback=None,
        summary_instruction="",
    )[-1].content
    allowed_line = next(
        line for line in prompt.splitlines() if line.startswith("- 本项目允许的 evidence_refs.path：")
    )
    assert allowed_line == "- 本项目允许的 evidence_refs.path：weekly_ranking"


def test_generation_stops_before_global_synthesis_when_project_repair_fails() -> None:
    rankings = _build_rankings(2)
    invalid_second = _build_project_payload(rankings[1])
    invalid_second["repository_full_name"] = "wrong/repository"
    provider = StubProvider(
        [
            _build_project_payload(rankings[0]),
            invalid_second,
            invalid_second,
        ]
    )

    with pytest.raises(ValueError, match="项目名称错误"):
        _generate(rankings, provider)

    assert [call["trace_metadata"]["phase"] for call in provider.calls] == [
        "project_generation",
        "project_generation",
        "project_repair",
    ]


def test_article_is_assembled_in_overview_and_three_section_order() -> None:
    rankings = _build_rankings(3)
    provider = StubProvider(
        [_build_project_payload(item) for item in rankings] + [_build_global_payload()]
    )

    _, normalized, _ = _generate(rankings, provider)

    article = normalized["article_markdown"]
    assert article.count("### 本周主线") == 1
    assert article.count("### 工程启发") == 1
    assert article.count("**技术特点**") == 3
    assert article.count("**机制拆解**") == 3
    assert article.count("**工程启发**") == 3
    assert "**本周判断**" not in article
    assert "**问题与代价**" not in article
    assert "**落到工作流**" not in article
    assert article.index("#### 项目 1：") < article.index("#### 项目 2：")
    assert article.index("#### 项目 2：") < article.index("#### 项目 3：")

    for item, prompt in zip(rankings, normalized["image_prompts"], strict=True):
        section_start = article.index(f"#### 项目 {item.rank}：{item.full_name}")
        next_start = (
            article.index(f"#### 项目 {item.rank + 1}：", section_start)
            if item.rank < len(rankings)
            else article.index("### 工程启发", section_start)
        )
        section = article[section_start:next_start]
        assert section.index(str(item.star_growth)) < section.index("**技术特点**")
        assert section.index(str(item.current_stars)) < section.index("**技术特点**")
        assert prompt["prompt_stage"] == "article_visual_spec_v1"
        assert prompt["visual_spec"]["repository_full_name"] == item.full_name
        assert prompt["visual_spec"]["figure_role"] == "flow"
        assert prompt["visual_brief"]["reading_order"] == [
            "evidence",
            "orchestrator",
            "gate",
            "artifact",
        ]
