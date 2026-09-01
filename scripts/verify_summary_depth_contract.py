"""离线验证 SummaryTask 的动态 N+1 文章合同与媒体证据传递。

本脚本不读取 API Key、不调用 GitHub 或 DeepSeek。它只验证：
1. 动态 N 次项目生成后只执行 1 次全局综合；
2. 每个项目包含概述、三个教学分点、精确 stars 和一份架构加数据流简报；
3. 标题遵守微信 32 字合同，摘要、正文和分点不设置额外长度合同；
4. 最终 N 份 ContentBrief 能继续进入图片、排版和短视频链路。
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import logging
import sqlite3
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.deepseek_provider import DeepSeekChatResponse, DeepSeekMessage
from src.providers.github_client import GitHubRepositoryEvidence
from src.repositories.generated_content_repository import (
    GeneratedContentForStoryboard,
    GeneratedContentRepository,
)
from src.repositories.weekly_ranking_repository import WeeklyRankingRecord
from src.tasks.short_video_prompt_task import ShortVideoPromptTask
from src.tasks.summary_task import SummaryTask


class StubProvider:
    """离线返回预设 JSON，并记录模型调用阶段。"""

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
            model="offline-stub",
            raw_response={"offline_call": len(self.calls)},
        )


def build_rankings(count: int = 5) -> list[WeeklyRankingRecord]:
    """按动态数量构造与真实周榜字段一致的离线样本。"""

    return [
        WeeklyRankingRecord(
            repository_id=index,
            rank=index,
            full_name=f"example-org/project-{index}",
            html_url=f"https://github.com/example-org/project-{index}",
            description=f"用于验证第 {index} 个项目的工程说明。",
            language="Python",
            current_stars=10000 + index,
            star_growth=800 + index,
            growth_rate=0.12,
            score=0.9,
            reason="离线合同验证样本",
        )
        for index in range(1, count + 1)
    ]


def build_evidence(rankings: list[WeeklyRankingRecord]) -> dict[str, GitHubRepositoryEvidence]:
    """构造 README 已可用的公开证据样本。"""

    return {
        item.full_name: GitHubRepositoryEvidence(
            full_name=item.full_name,
            description=item.description,
            topics=("agent", "workflow"),
            default_branch="main",
            license_name="MIT",
            readme_excerpt=(
                "该项目把输入整理、任务编排、执行反馈和人工校验拆为可组合模块，"
                "README 明确说明了主流程与边界。"
            ),
            evidence_status="readme",
        )
        for item in rankings
    }


def build_project_section(item: WeeklyRankingRecord, index: int) -> str:
    """构造概述和三个教学分点完整的项目正文。"""

    overview = (
        f"{item.full_name} 本周新增 {item.star_growth} stars，总 stars 为 {item.current_stars}。"
        "它把事实证据组织成可检查的工程流程，适合需要人工验收的代理任务。"
    )
    labels_and_text = [
        ("技术特点", "证据、编排、校验和交付模块按职责分层。"),
        ("机制拆解", "证据卡先进入编排器，再经过校验门形成交付物。"),
        ("工程启发", "生产接入仍需人工确认，阅读时先看证据入口再看校验边界。"),
    ]
    body = "\n\n".join(f"**{label}** {text}" for label, text in labels_and_text)
    return f"#### 项目 {index}：{item.full_name}\n\n{overview}\n\n{body}"


def build_article(rankings: list[WeeklyRankingRecord]) -> str:
    """按输入项目数量拼出满足固定标题顺序的离线文章。"""

    mainline = (
        "本周的共同信号不是某个模型突然变强，而是越来越多仓库把输入、流程、反馈和人工判断拆成可观察的工程对象。"
        "这类变化对开发者更有价值，因为工具是否能进入团队，取决于它能否被接入、复核、回滚和持续维护。"
    )
    conclusion = (
        "把这些项目放在一起看，最值得带走的不是立刻替换现有工具，而是先确认团队缺失的是哪一段可观察的流程。"
        "只要输入、责任边界和失败处理还不清楚，再强的模型也只会把不确定性搬到更难排查的地方。"
    )
    return "\n\n".join(
        [
            "本周项目都在处理同一个问题：代理给出结果以后，工程团队如何检查过程并验收输出。",
            "### 本周主线",
            mainline,
            f"### Top {len(rankings)} 项目拆解",
            *[build_project_section(item, index) for index, item in enumerate(rankings, start=1)],
            "### 工程启发",
            conclusion,
        ]
    )


def build_project_briefs(rankings: list[WeeklyRankingRecord]) -> list[dict[str, object]]:
    """构造与文章事实一致、可验证的项目摘要和视觉规格。"""

    return [
        {
            "repository_full_name": item.full_name,
            "summary_text": "该项目减少人工复制成本，通过职责分层组织证据、编排与校验；生产接入仍需人工确认。",
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
                    {"id": "gate", "label": "校验门", "description": "完成人工确认"},
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
        }
        for item in rankings
    ]


def build_project_output(item: WeeklyRankingRecord) -> dict[str, Any]:
    """构造一次单项目模型调用的合法 JSON。"""

    brief = build_project_briefs([item])[0]
    return {
        "repository_full_name": item.full_name,
        "overview_text": (
            f"{item.full_name} 本周新增 {item.star_growth} stars，总 stars 为 {item.current_stars}。"
            "它把事实证据组织成可检查的工程流程，适合需要人工验收的代理任务。"
        ),
        "technical_features_markdown": "证据、编排、校验和交付模块按职责分层。",
        "mechanism_breakdown_markdown": (
            "证据卡先把事实传给编排器，编排器同步调用校验门，校验通过后形成交付物。"
        ),
        "engineering_insights_markdown": (
            "生产接入仍需人工确认，阅读源码时先找证据入口，再看编排与校验边界。"
        ),
        "project_brief": {
            "summary_text": brief["summary_text"],
            "visual_spec": brief["visual_spec"],
        },
    }


def build_global_output() -> dict[str, str]:
    """构造全局综合调用的合法 JSON。"""

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


def main() -> None:
    """执行合同验证并打印精简结果。"""

    rankings = build_rankings()
    evidence = build_evidence(rankings)
    task = object.__new__(SummaryTask)
    task.logger = logging.getLogger("verify_summary_depth_contract")
    article = build_article(rankings)

    project_sections = task._validate_article_depth(
        article_markdown=article,
        rankings=rankings,
        ranking_evidence=evidence,
    )
    assert len(project_sections) == len(rankings)

    skill_text = (PROJECT_ROOT / ".agents/skills/github-project-blog/SKILL.md").read_text(
        encoding="utf-8"
    )
    for required_text in (
        "单项目生成模式",
        "全局综合模式",
        "技术特点",
        "机制拆解",
        "工程启发",
        "项目概述",
    ):
        assert required_text in skill_text

    # 长度不再是生成失败条件：超长分点与全局字段仍能通过结构校验。
    first_item = rankings[0]
    long_article = article.replace(
        project_sections[first_item.full_name],
        project_sections[first_item.full_name] + ("补充机制说明" * 2_000),
    )
    long_sections = task._validate_article_depth(
        article_markdown=long_article,
        rankings=rankings,
        ranking_evidence=evidence,
    )
    assert len(long_sections) == len(rankings)
    long_project_output = build_project_output(first_item)
    long_project_output["technical_features_markdown"] += "补充模块说明" * 2_000
    normalized_long_project = task._normalize_project_output(
        long_project_output,
        first_item,
        evidence[first_item.full_name],
    )
    assert len(normalized_long_project["technical_features_markdown"]) > 2_000
    long_global_output = build_global_output()
    long_global_output["title"] = "标题" * 1_000
    long_global_output["digest"] = "摘要" * 1_000
    try:
        task._normalize_global_output(long_global_output, rankings)
    except ValueError as exc:
        assert "不能超过 32 个字" in str(exc)
    else:
        raise AssertionError("超长微信标题应被全局内容合同拒绝")

    long_global_output["title"] = "有效标题"
    normalized_long_global = task._normalize_global_output(long_global_output, rankings)
    assert normalized_long_global["digest"] == long_global_output["digest"]

    # 工作台可配置任意 Top N；用非默认数量验证标题、章节和校验器都不依赖 5。
    compact_rankings = build_rankings(count=3)
    compact_evidence = build_evidence(compact_rankings)
    compact_sections = task._validate_article_depth(
        article_markdown=build_article(compact_rankings),
        rankings=compact_rankings,
        ranking_evidence=compact_evidence,
    )
    assert len(compact_sections) == len(compact_rankings)

    # 周榜真实数值允许使用中文技术文章常见的千分位展示，不能因 143,902
    # 与数据库整数 143902 的格式差异让 SummaryTask 误失败；但近似单位
    # 仍不视为精确事实，避免质量合同被放宽。
    assert task._contains_exact_ranking_number("当前 stars 为 143,902", 143902)
    assert task._contains_exact_ranking_number("本周新增 1，281", 1281)
    assert task._contains_exact_ranking_number("本周新增 1 281", 1281)
    assert not task._contains_exact_ranking_number("当前 stars 约为 14.4 万", 143902)
    assert not task._contains_exact_ranking_number("本周新增约 1.3k", 1281)

    # 章节可以任意长短，但不能丢失固定分析结构。
    incomplete_article = article.replace(
        project_sections[first_item.full_name],
        (
            f"{first_item.full_name} 总 stars 为 {first_item.current_stars}，"
            f"本周新增 {first_item.star_growth}。\n\n"
            "**技术特点** 这一段故意缺少其余标签以验证结构合同。"
        ),
    )
    try:
        task._validate_article_depth(
            article_markdown=incomplete_article,
            rankings=rankings,
            ranking_evidence=evidence,
        )
    except ValueError as exc:
        assert "项目 1" in str(exc)
        assert "缺少技术拆解标签" in str(exc)
    else:
        raise AssertionError("缺少固定标签的项目正文必须被质量合同拒绝")

    article_skill_instructions = skill_text
    messages = task._build_project_messages(
        ranking=first_item,
        ranking_evidence=evidence[first_item.full_name],
        article_skill_instructions=article_skill_instructions,
        regeneration_feedback=None,
        summary_instruction="",
    )
    assert article_skill_instructions in messages[0].content
    main_prompt = messages[-1].content
    for length_rule in ("字以内", "中文字符", "最低字数", "至少 45", "不得超过"):
        assert length_rule not in main_prompt
    assert "source_evidence" in main_prompt
    assert "article_markdown 1900 字以内" not in main_prompt
    assert "overview_text" in main_prompt
    assert "technical_features_markdown" in main_prompt
    assert "mechanism_breakdown_markdown" in main_prompt
    assert "engineering_insights_markdown" in main_prompt
    assert "flow：steps 为 3 到 5 项" in main_prompt
    assert "edges 必须且只能按 steps 相邻顺序连接" in main_prompt
    assert "video_script 必须" not in main_prompt
    assert "image_prompts 必须" not in main_prompt
    assert all(item.full_name not in main_prompt for item in rankings[1:])

    retry_messages = task._build_project_messages(
        ranking=first_item,
        ranking_evidence=evidence[first_item.full_name],
        article_skill_instructions=article_skill_instructions,
        regeneration_feedback=None,
        summary_instruction="",
        validation_error="概述缺少本周增长",
    )
    assert article_skill_instructions in retry_messages[0].content
    retry_prompt = retry_messages[-1].content
    assert "请只修复当前项目的合同错误" in retry_prompt
    assert "概述缺少本周增长" in retry_prompt
    for length_rule in ("字以内", "中文字符", "最低字数", "至少 45", "不得超过"):
        assert length_rule not in retry_prompt
    assert "voiceover_text" not in retry_prompt

    project_outputs = [build_project_output(item) for item in rankings]
    global_messages = task._build_global_messages(
        rankings=rankings,
        week_end="2026-08-14",
        highest_star_repository=max(rankings, key=lambda item: item.current_stars),
        project_contents=project_outputs,
        article_skill_instructions=article_skill_instructions,
        regeneration_feedback=None,
        summary_instruction="",
    )
    assert article_skill_instructions in global_messages[0].content
    assert "visual_brief" not in global_messages[-1].content
    assert all(item.full_name in global_messages[-1].content for item in rankings)

    normalized: dict[str, Any] | None = None
    for count in (1, 3, 5, 6):
        dynamic_rankings = build_rankings(count=count)
        dynamic_evidence = build_evidence(dynamic_rankings)
        provider = StubProvider(
            [build_project_output(item) for item in dynamic_rankings] + [build_global_output()]
        )
        _, generated, audit = task._generate_normalized_content(
            provider=provider,
            rankings=dynamic_rankings,
            week_end="2026-08-14",
            highest_star_repository=max(dynamic_rankings, key=lambda item: item.current_stars),
            ranking_evidence=dynamic_evidence,
            article_skill_instructions=article_skill_instructions,
            regeneration_feedback=None,
            summary_instruction="",
        )
        assert audit["project_count"] == count
        assert audit["provider_call_count"] == count + 1
        assert len(provider.calls) == count + 1
        assert len(generated["image_prompts"]) == count
        assert generated["article_markdown"].count("**技术特点**") == count
        assert generated["article_markdown"].count("**机制拆解**") == count
        assert generated["article_markdown"].count("**工程启发**") == count
        assert all(
            item["visual_spec"]["figure_role"] == "flow"
            and len(item["visual_spec"]["edges"]) == 3
            for item in generated["image_prompts"]
        )
        if count == len(rankings):
            normalized = generated

    assert normalized is not None
    assert set(normalized) == {"title", "digest", "article_markdown", "image_prompts"}
    assert len(normalized["image_prompts"]) == len(rankings)
    assert all(
        item["prompt_stage"] == "article_visual_spec_v1"
        for item in normalized["image_prompts"]
    )
    assert all(item["project_analysis_markdown"] for item in normalized["image_prompts"])

    content = GeneratedContentForStoryboard(
        id=1,
        week_end="2026-08-14",
        title="离线长文合同验证",
        digest="验证后续视频可读取每个项目的独立证据卡。",
        article_markdown=article,
        video_script="离线验证脚本。",
        voiceover_text="离线验证脚本。",
        image_prompts=normalized["image_prompts"],
    )
    video_task = object.__new__(ShortVideoPromptTask)
    video_prompt = video_task._build_script_messages(content=content, video_instruction="")[-1].content
    assert "project_evidence_card" in video_prompt
    assert "example-org/project-5" in video_prompt
    assert "文章正文：" not in video_prompt

    voiceover = video_task._build_voiceover_text(
        {
            "scenes": [
                {"narration": "先看本周主线。"},
                {"narration": "再解释第一个项目。"},
                {"narration": "最后收束工程启发。"},
            ],
            "progressive_script": "不应使用这条兜底文本。",
        }
    )
    assert voiceover == "先看本周主线。\n再解释第一个项目。\n最后收束工程启发。"

    # SummaryTask 创建记录时媒体字段为空；只有 ShortVideoPromptTask 回写后，
    # 后续 AudioTask / VideoTask 才能读取该内容，避免误用旧脚本。
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE generated_contents (
            id INTEGER PRIMARY KEY,
            week_end TEXT NOT NULL,
            title TEXT NOT NULL,
            video_script TEXT,
            voiceover_text TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO generated_contents (id, week_end, title, video_script, voiceover_text)
        VALUES (1, '2026-08-14', '离线媒体计划验证', '', '')
        """
    )

    class InMemoryDatabaseManager:
        @contextmanager
        def connection(self):
            yield conn
            conn.commit()

    content_repository = GeneratedContentRepository(InMemoryDatabaseManager())
    assert content_repository.latest_for_video_generation() is None
    content_repository.update_media_plan(
        1,
        video_script="渐进式视频讲稿。",
        voiceover_text="统一旁白文本。",
    )
    media_plan = content_repository.latest_for_video_generation()
    assert media_plan is not None
    assert media_plan.video_script == "渐进式视频讲稿。"
    assert media_plan.voiceover_text == "统一旁白文本。"
    conn.close()

    print(
        "摘要深度合同验证通过："
        f"projects={len(project_sections)} call_contract=N+1 length_limits=disabled"
    )


if __name__ == "__main__":
    main()
