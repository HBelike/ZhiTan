# SummaryTask Dynamic N+1 Article Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `SummaryTask` 改为动态 N 次单项目生成加 1 次全局综合，并确定性拼装“开篇总结 + N 个项目章节 + 末尾工程启发”的公众号文章。

**Architecture:** `SummaryTask` 继续负责证据读取、模型调用、校验与入库。它按排名串行生成 N 份独立项目结果，再用 1 次综合调用生成标题、摘要、开篇、本周主线和结尾；文章结构及项目顺序由代码拼装，项目视觉简报继续交给现有媒体服务规范化。

**Tech Stack:** Python 3、DeepSeek OpenAI-compatible ChatCompletions、pytest、现有 `MediaCreativeBriefService` 与 `ArticleLayoutService`

**Spec:** `docs/superpowers/specs/2026-08-27-summary-n-plus-one-article-generation-design.md`

## Global Constraints

- 项目数量只能来自当期 `rankings`，不得写死为 5 或 6。
- 正常调用数必须为 N 次项目生成加 1 次全局综合；每个单元最多质量修复 1 次。
- 项目调用按排名串行执行；本次不引入并发、缓存、断点续跑或部分成功持久化。
- 每个项目只生成一张同时表达架构和主数据流的教学图，`image_prompts` 数量必须等于 N。
- 最终项目顺序固定为“概述、图片、技术特点、机制拆解、工程启发”；图片继续由现有排版服务插入项目首段之后。
- 源码路径、类、函数、模块关系、强弱耦合及同步异步结论只能来自输入证据。
- 不新增数据库迁移，不修改审核、视频、音频、排版或微信公众号交付接口。
- 不引入运行时 `humanizer` 依赖，只把相关自然表达约束编译进项目 Skill 与 SummaryTask 提示词。
- 保留工作区现有未提交修改，只编辑本计划列出的目标文件及其直接测试和文档。
- 未经单独授权不部署生产环境。

---

## File Structure

- Modify: `.agents/skills/github-project-blog/SKILL.md`：增加单项目生成与全局综合两种嵌入模式，把七项教学分析收进三个可见分点，并加入自然表达约束。
- Modify: `src/tasks/summary_task.py`：实现 N+1 调用、单元级重试、结果校验、确定性文章拼装和调用审计。
- Create: `tests/test_summary_generation_orchestration.py`：使用内存 Stub 验证 N+1 调用、选择性重试、动态 N、拼装顺序和 trace 元数据。
- Modify: `tests/test_summary_visual_brief.py`：把旧六标签样本改为新三分点，继续验证真实节点和通用占位节点拒绝逻辑。
- Modify: `tests/test_wechat_draft_image_count.py`：验证现有排版把每张项目图插在项目概述与三个分点之间。
- Modify: `src/services/article_layout_service.py`：取消 `Top {rank}` 项目标记，避免“Top N 项目拆解”误触发排名 N 的图片。
- Modify: `src/services/github_image_upgrade_service.py`：取消历史前 5 项截断，按动态 N 升级全部项目图片。
- Create: `tests/test_dynamic_project_image_count.py`：验证 N=6 时排版与手动图片升级都处理全部项目。
- Modify: `scripts/verify_summary_depth_contract.py`：更新离线完整合同，覆盖 N=1、N=3、N=6、三分点、项目概述和 N 张图片。
- Modify: `docs/summary_writing_skill_research.md`：记录 Skill 目标、N+1 调用链、事实边界和验证结果。
- Modify: `docs/weekly_media_pipeline.md`：同步 SummaryTask、ImageTask 与排版插图的最新调用关系。

---

### Task 1: 更新 github-project-blog 教学写作合同

**Files:**
- Modify: `.agents/skills/github-project-blog/SKILL.md`
- Modify: `scripts/verify_summary_depth_contract.py`

**Interfaces:**
- Consumes: `SummaryTask` 挂载的 Skill 正文和调用方提供的单项目或全局 JSON 合同
- Produces: 单项目章节写作方法、全局综合方法和不改变事实的自然表达约束

- [ ] **Step 1: 在离线验证中加入新 Skill 结构断言**

在 `scripts/verify_summary_depth_contract.py` 读取 Skill 文件，并加入以下断言：

```python
skill_text = (PROJECT_ROOT / ".agents/skills/github-project-blog/SKILL.md").read_text(encoding="utf-8")
for required_text in (
    "单项目生成模式",
    "全局综合模式",
    "技术特点",
    "机制拆解",
    "工程启发",
    "项目概述",
):
    assert required_text in skill_text
for removed_label in ("本周判断", "问题与代价", "落到工作流"):
    assert f"`{removed_label}`" not in skill_text
```

- [ ] **Step 2: 运行离线验证并确认它先失败**

Run: `.venv\Scripts\python.exe scripts\verify_summary_depth_contract.py`

Expected: FAIL，提示 Skill 缺少“单项目生成模式”或仍使用旧六标签合同。

- [ ] **Step 3: 重写 Skill 的分析和文章组织规则**

在 `SKILL.md` 中保留现有公开证据、采集、事实优先级和输出纪律，新增两个明确模式：

```markdown
## 工作台嵌入模式

### 单项目生成模式

只分析调用方给出的一个仓库。项目概述写明本周新增 stars、总 stars、项目作用和使用场景。
正文只使用三个可见分点：技术特点、机制拆解、工程启发。

- 技术特点覆盖项目定位、问题、架构、模块及职责。
- 机制拆解覆盖入口到输出的数据流、关键文件或函数、依赖和调用关系。
- 工程启发覆盖技术难点、设计取舍、潜在坑点、使用边界和源码阅读顺序。

### 全局综合模式

只根据已经验证的项目结果生成标题、摘要、开篇总结、本周主线和末尾工程启发。
不得重写项目章节、视觉简报或补充新项目事实。
```

把原来的六个工作台项目标签删除或改写成三分点映射。加入 `humanizer` 约束：删除销售词、匿名权威、宏大结论、连续“不是 X，而是 Y”、标题复述、聊天残留、强制整齐句式和无证据补全；保留所有数字、名称、排名和限定语。

- [ ] **Step 4: 校验 Skill 格式**

Run: `.venv\Scripts\python.exe <CODEX_SKILLS>\skill-creator\scripts\quick_validate.py .agents\skills\github-project-blog`

Expected: `Skill is valid!`

- [ ] **Step 5: 提交 Skill 合同**

```bash
git add .agents/skills/github-project-blog/SKILL.md scripts/verify_summary_depth_contract.py
git commit -m "feat: define per-project teaching article contract"
```

---

### Task 2: 为动态 N+1 编排建立失败测试

**Files:**
- Create: `tests/test_summary_generation_orchestration.py`

**Interfaces:**
- Consumes: `SummaryTask._generate_normalized_content(provider, rankings, week_end, highest_star_repository, ranking_evidence, article_skill_instructions, regeneration_feedback, summary_instruction)`
- Produces: 对返回三元组 `(global_response, normalized, generation_audit)` 的可执行合同

- [ ] **Step 1: 创建 Stub Provider 与样本构造器**

在新测试文件中加入以下基础设施：

```python
from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from src.providers.deepseek_provider import DeepSeekChatResponse, DeepSeekMessage
from src.tasks.summary_task import SummaryTask
from scripts.verify_summary_depth_contract import build_evidence, build_rankings


class StubProvider:
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
        self.calls.append({"messages": messages, "trace_metadata": dict(trace_metadata)})
        payload = self.payloads.pop(0)
        return DeepSeekChatResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model="stub-deepseek",
            raw_response={"stub_call": len(self.calls)},
        )
```

新增 `build_project_payload(ranking)`，让 `overview_text` 原样包含 `current_stars` 和 `star_growth`，并返回三个正文分点与现有合法 `project_brief.visual_brief`。新增 `build_global_payload()` 返回：

```python
{
    "title": "离线动态项目文章",
    "digest": "本周项目把代理能力拆成可以检查和组合的工程环节。",
    "opening_markdown": "六个项目分别处理技能、规格、运行时和本地模型环节。",
    "weekly_theme_markdown": "共同变化是把模型能力放进有边界的工程流程。",
    "closing_markdown": "先确认输入、责任边界和验收方式，再决定接入哪一类工具。",
}
```

- [ ] **Step 2: 写 N=1、N=3、N=6 的参数化失败测试**

```python
@pytest.mark.parametrize("project_count", [1, 3, 6])
def test_generation_calls_each_project_then_one_global_synthesis(project_count: int) -> None:
    rankings = build_rankings(project_count)
    evidence = build_evidence(rankings)
    provider = StubProvider(
        [build_project_payload(item) for item in rankings] + [build_global_payload()]
    )
    task = object.__new__(SummaryTask)
    task.logger = logging.getLogger("test.summary.orchestration")

    global_response, normalized, audit = task._generate_normalized_content(
        provider=provider,
        rankings=rankings,
        week_end="2026-08-14",
        highest_star_repository=max(rankings, key=lambda item: item.current_stars),
        ranking_evidence=evidence,
        article_skill_instructions="# GitHub 项目文章\n只根据证据写作。",
        regeneration_feedback=None,
        summary_instruction="",
    )

    assert global_response.model == "stub-deepseek"
    assert len(provider.calls) == project_count + 1
    assert [call["trace_metadata"]["phase"] for call in provider.calls[:-1]] == [
        "project_generation"
    ] * project_count
    assert provider.calls[-1]["trace_metadata"]["phase"] == "global_synthesis"
    assert len(normalized["image_prompts"]) == project_count
    assert audit["project_count"] == project_count
    assert audit["provider_call_count"] == project_count + 1
```

- [ ] **Step 3: 写选择性重试失败测试**

构造三个项目，第二个项目首次返回 `{"repository_full_name": "wrong/repo"}`，随后返回合法结果，再返回第三个项目和全局结果。断言调用 phase 顺序为：

```python
assert phases == [
    "project_generation",
    "project_generation",
    "project_repair",
    "project_generation",
    "global_synthesis",
]
assert repositories == [
    "example-org/project-1",
    "example-org/project-2",
    "example-org/project-2",
    "example-org/project-3",
    None,
]
```

同时断言项目 1 没有被重放，`provider_call_count == 5`。

- [ ] **Step 4: 写文章拼装顺序失败测试**

对 N=3 的 `normalized["article_markdown"]` 断言：

```python
article = normalized["article_markdown"]
assert article.count("### 本周主线") == 1
assert article.count("### 工程启发") == 1
assert article.count("**技术特点**") == 3
assert article.count("**机制拆解**") == 3
assert article.count("**工程启发**") == 3
assert "**本周判断**" not in article
assert article.index("#### 项目 1：") < article.index("#### 项目 2：") < article.index("#### 项目 3：")
```

- [ ] **Step 5: 运行新测试并确认失败原因正确**

Run: `.venv\Scripts\python.exe -m pytest tests\test_summary_generation_orchestration.py -q`

Expected: FAIL，因为当前实现只进行一次整篇文章调用，且返回值仍是二元组。

- [ ] **Step 6: 提交失败测试**

```bash
git add tests/test_summary_generation_orchestration.py
git commit -m "test: specify dynamic summary generation orchestration"
```

---

### Task 3: 实现单项目生成、校验与单元重试

**Files:**
- Modify: `src/tasks/summary_task.py`
- Test: `tests/test_summary_generation_orchestration.py`

**Interfaces:**
- Consumes: 单个 `WeeklyRankingRecord`、对应 `GitHubRepositoryEvidence`、Skill、管理员指令和审核反馈
- Produces: `_generate_project_content(provider, ranking, ranking_evidence, article_skill_instructions, regeneration_feedback, summary_instruction) -> tuple[DeepSeekChatResponse, dict[str, Any], list[dict[str, Any]]]`

- [ ] **Step 1: 把项目标签常量改为三个可见分点**

```python
from src.providers.deepseek_provider import (
    DeepSeekChatResponse,
    DeepSeekMessage,
    DeepSeekProvider,
    parse_json_object_from_text,
)

_project_section_labels = (
    "技术特点",
    "机制拆解",
    "工程启发",
)

_project_output_fields = {
    "repository_full_name",
    "overview_text",
    "technical_features_markdown",
    "mechanism_breakdown_markdown",
    "engineering_insights_markdown",
    "project_brief",
}
```

- [ ] **Step 2: 新增单项目初次与修复 Prompt 构造器**

实现：

```python
def _build_project_messages(
    self,
    ranking: WeeklyRankingRecord,
    ranking_evidence: GitHubRepositoryEvidence,
    article_skill_instructions: str,
    regeneration_feedback: str | None,
    summary_instruction: str,
    validation_error: str | None = None,
) -> list[DeepSeekMessage]:
    project_payload = self._build_ranking_payload(
        rankings=[ranking],
        ranking_evidence={ranking.full_name: ranking_evidence},
    )[0]
    feedback_section = self._build_regeneration_feedback_section(regeneration_feedback)
    instruction_section = self._build_runtime_instruction_section(
        title="管理员摘要指令",
        instruction=summary_instruction,
    )
    repair_section = (
        f"上次输出未通过合同：{validation_error}\n请只修复这个项目的合同错误。"
        if validation_error
        else ""
    )
    system_prompt = (
        "你是一名中文技术讲师，只分析当前一个 GitHub 仓库。"
        "所有事实只能来自输入证据，证据不足时直接说明。"
        "只输出合法 JSON，不输出解释或代码围栏。"
        f"\n\n{self._build_article_skill_section(article_skill_instructions)}"
    )
    user_prompt = f"""
为下面一个项目生成独立教学章节和一份架构加数据流视觉简报。

{feedback_section}
{instruction_section}
{repair_section}

JSON 字段必须严格为：
repository_full_name: 必须原样等于 {ranking.full_name}
overview_text: 一段概述，原样写出本周新增 {ranking.star_growth} stars、总 stars {ranking.current_stars}、项目作用和使用场景
technical_features_markdown: 解释项目定位、解决的问题、架构、模块与职责
mechanism_breakdown_markdown: 解释入口到输出的数据流、证据明确的关键文件或函数、依赖和调用关系
engineering_insights_markdown: 解释技术难点、设计取舍、坑点、使用边界和源码阅读顺序
project_brief: 只含 summary_text 和 visual_brief；visual_brief 含 diagram_type、teaching_goal、visual_thesis、3到4个真实 nodes、2到4条真实 relationships、reading_order、chinese_labels；至少一条 relationship 的 label 为数据流

不得输出 URL、仓库地址、供应商图片 Prompt、视频、旁白或源码大段摘录。不要使用赋能、解锁、颠覆、全面解析、聊天式开场或连续的“不是 X，而是 Y”。

项目事实：
{json.dumps(project_payload, ensure_ascii=False, indent=2)}
"""
    return [
        DeepSeekMessage(role="system", content=system_prompt),
        DeepSeekMessage(role="user", content=user_prompt.strip()),
    ]
```

初次调用传 `validation_error=None`；修复调用传入上一次异常文本。两次都只包含当前一个项目的 `source_evidence`，不携带其他项目或已通过结果。

- [ ] **Step 3: 新增单项目规范化与校验**

实现：

```python
def _normalize_project_output(
    self,
    parsed: dict[str, Any],
    ranking: WeeklyRankingRecord,
) -> dict[str, Any]:
    if set(parsed) != self._project_output_fields:
        raise ValueError("项目输出字段不符合合同")
    if str(parsed["repository_full_name"]).strip() != ranking.full_name:
        raise ValueError(f"项目名称错误：expected={ranking.full_name}")

    overview = self._normalize_generated_prose(str(parsed["overview_text"]))
    if not self._contains_exact_ranking_number(overview, ranking.current_stars):
        raise ValueError(f"{ranking.full_name} 概述缺少当前 stars")
    if not self._contains_exact_ranking_number(overview, ranking.star_growth):
        raise ValueError(f"{ranking.full_name} 概述缺少本周增长")

    text_fields = (
        "technical_features_markdown",
        "mechanism_breakdown_markdown",
        "engineering_insights_markdown",
    )
    normalized = {"repository_full_name": ranking.full_name, "overview_text": overview}
    for field in text_fields:
        value = self._normalize_generated_prose(str(parsed[field]))
        if not value:
            raise ValueError(f"{ranking.full_name} 的 {field} 不能为空")
        normalized[field] = value

    raw_brief = parsed["project_brief"]
    if not isinstance(raw_brief, dict) or set(raw_brief) != {"summary_text", "visual_brief"}:
        raise ValueError(f"{ranking.full_name} 的 project_brief 字段不符合合同")
    brief_item = {
        "repository_full_name": ranking.full_name,
        "summary_text": self._normalize_generated_prose(str(raw_brief["summary_text"])),
        "visual_brief": raw_brief["visual_brief"],
    }
    self._validate_project_briefs([brief_item], [ranking])
    visual_brief = MediaCreativeBriefService().normalize_visual_brief(
        raw_brief=brief_item["visual_brief"],
        repository_full_name=ranking.full_name,
        fallback_text=normalized["mechanism_breakdown_markdown"],
        project_index=ranking.rank,
    )
    if len(visual_brief["nodes"]) < 3 or len(visual_brief["relationships"]) < 2:
        raise ValueError(f"{ranking.full_name} 的 visual_brief 缺少架构节点或关系")
    if not any(item["label"] == "数据流" for item in visual_brief["relationships"]):
        raise ValueError(f"{ranking.full_name} 的 visual_brief 缺少主数据流")
    normalized["project_brief"] = {
        "repository_full_name": ranking.full_name,
        "summary_text": brief_item["summary_text"],
        "visual_brief": visual_brief,
    }
    return normalized
```

新增以下统一文本规范化方法。三个正文分点必须非空。

```python
def _normalize_generated_prose(self, text: str) -> str:
    normalized = self._normalize_author_voice(
        self._remove_github_urls_and_link_sections(text)
    )
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    forbidden = (
        "项目地址",
        "GitHub 地址",
        "赋能",
        "解锁",
        "颠覆",
        "全面解析",
        "让我们深入",
        "欢迎继续",
        "—",
        "–",
    )
    matched = next((phrase for phrase in forbidden if phrase in normalized), None)
    if matched:
        raise ValueError(f"生成文本包含禁用表达：{matched}")
    if re.search(r"https?://", normalized, flags=re.IGNORECASE):
        raise ValueError("生成文本仍包含 URL")
    return normalized
```

- [ ] **Step 4: 新增单项目生成循环**

```python
def _generate_project_content(
    self,
    provider: DeepSeekProvider,
    ranking: WeeklyRankingRecord,
    ranking_evidence: GitHubRepositoryEvidence,
    article_skill_instructions: str,
    regeneration_feedback: str | None,
    summary_instruction: str,
) -> tuple[DeepSeekChatResponse, dict[str, Any], list[dict[str, Any]]]:
    validation_error = ""
    audit: list[dict[str, Any]] = []
    for attempt_index, phase in enumerate(("project_generation", "project_repair"), start=1):
        messages = self._build_project_messages(
            ranking=ranking,
            ranking_evidence=ranking_evidence,
            article_skill_instructions=article_skill_instructions,
            regeneration_feedback=regeneration_feedback,
            summary_instruction=summary_instruction,
            validation_error=validation_error or None,
        )
        response = provider.chat(
            messages,
            retry_empty_content=False,
            trace_metadata={
                "phase": phase,
                "attempt_index": attempt_index,
                "project_rank": ranking.rank,
                "repository_full_name": ranking.full_name,
            },
        )
        audit.append({"phase": phase, "model": response.model, "raw": response.raw_response})
        try:
            return response, self._normalize_project_output(
                parse_json_object_from_text(response.content), ranking
            ), audit
        except (JSONDecodeError, ValueError) as exc:
            validation_error = str(exc)
            if attempt_index == 2:
                raise
    raise RuntimeError(f"{ranking.full_name} 项目生成失败")
```

- [ ] **Step 5: 运行选择性重试测试**

Run: `.venv\Scripts\python.exe -m pytest tests\test_summary_generation_orchestration.py -q`

Expected: 选择性重试相关断言开始通过；全局综合和最终二元组断言仍失败。

- [ ] **Step 6: 提交单项目生成实现**

```bash
git add src/tasks/summary_task.py tests/test_summary_generation_orchestration.py
git commit -m "feat: generate each weekly project independently"
```

---

### Task 4: 实现全局综合、确定性拼装与 N+1 审计

**Files:**
- Modify: `src/tasks/summary_task.py`
- Test: `tests/test_summary_generation_orchestration.py`

**Interfaces:**
- Consumes: 已验证且保持排名顺序的项目结果列表
- Produces: `_generate_normalized_content(provider, rankings, week_end, highest_star_repository, ranking_evidence, article_skill_instructions, regeneration_feedback, summary_instruction) -> tuple[DeepSeekChatResponse, dict[str, Any], dict[str, Any]]`

- [ ] **Step 1: 新增全局 Prompt 和结果合同**

定义：

```python
_global_output_fields = {
    "title",
    "digest",
    "opening_markdown",
    "weekly_theme_markdown",
    "closing_markdown",
}
```

实现下面的全局 Prompt 构造器。全局输入只保留榜单数字、项目概述和三个分点，不传 `visual_brief` 或供应商图片 Prompt：

```python
def _build_global_messages(
    self,
    rankings: list[WeeklyRankingRecord],
    project_contents: list[dict[str, Any]],
    article_skill_instructions: str,
    regeneration_feedback: str | None,
    summary_instruction: str,
    validation_error: str | None = None,
) -> list[DeepSeekMessage]:
    compact_projects = [
        {
            "repository_full_name": ranking.full_name,
            "current_stars": ranking.current_stars,
            "star_growth": ranking.star_growth,
            "overview_text": project["overview_text"],
            "technical_features_markdown": project["technical_features_markdown"],
            "mechanism_breakdown_markdown": project["mechanism_breakdown_markdown"],
            "engineering_insights_markdown": project["engineering_insights_markdown"],
        }
        for ranking, project in zip(rankings, project_contents, strict=True)
    ]
    repair_section = (
        f"上次输出未通过合同：{validation_error}\n只修复全局总结字段。"
        if validation_error
        else ""
    )
    system_prompt = (
        "你是一名中文技术主编，只综合已经验证的项目结果。"
        "不得重写项目章节、视觉简报或添加输入外事实。"
        "只输出合法 JSON。"
        f"\n\n{self._build_article_skill_section(article_skill_instructions)}"
    )
    user_prompt = f"""
根据以下 {len(rankings)} 个已验证项目生成整篇文章的全局部分。

{self._build_regeneration_feedback_section(regeneration_feedback)}
{self._build_runtime_instruction_section("管理员摘要指令", summary_instruction)}
{repair_section}

JSON 字段必须严格为：title、digest、opening_markdown、weekly_theme_markdown、closing_markdown。
opening_markdown 用具体数字或明确问题开篇；weekly_theme_markdown 总结项目之间有证据的共同方向；closing_markdown 提炼可迁移的工程判断。不要逐项复述项目，不要输出项目章节、URL、图片 Prompt、视频或旁白。

已验证项目：
{json.dumps(compact_projects, ensure_ascii=False, indent=2)}
"""
    return [
        DeepSeekMessage(role="system", content=system_prompt),
        DeepSeekMessage(role="user", content=user_prompt.strip()),
    ]
```

- [ ] **Step 2: 新增全局规范化与重试**

实现 `_normalize_global_output(parsed)`，要求字段集合精确匹配，五个字段均调用 `_normalize_generated_prose` 且非空：

```python
def _normalize_global_output(self, parsed: dict[str, Any]) -> dict[str, str]:
    if set(parsed) != self._global_output_fields:
        raise ValueError("全局综合输出字段不符合合同")
    normalized: dict[str, str] = {}
    for field in self._global_output_fields:
        value = self._normalize_generated_prose(str(parsed[field]))
        if not value:
            raise ValueError(f"全局综合字段 {field} 不能为空")
        normalized[field] = value
    return normalized
```

实现以下生成循环，trace phase 只使用 `global_synthesis` 和 `global_repair`：

```python
def _generate_global_content(
    self,
    provider: DeepSeekProvider,
    rankings: list[WeeklyRankingRecord],
    project_contents: list[dict[str, Any]],
    article_skill_instructions: str,
    regeneration_feedback: str | None,
    summary_instruction: str,
) -> tuple[DeepSeekChatResponse, dict[str, str], list[dict[str, Any]]]:
    validation_error = ""
    audit: list[dict[str, Any]] = []
    for attempt_index, phase in enumerate(("global_synthesis", "global_repair"), start=1):
        messages = self._build_global_messages(
            rankings=rankings,
            project_contents=project_contents,
            article_skill_instructions=article_skill_instructions,
            regeneration_feedback=regeneration_feedback,
            summary_instruction=summary_instruction,
            validation_error=validation_error or None,
        )
        response = provider.chat(
            messages,
            retry_empty_content=False,
            trace_metadata={
                "phase": phase,
                "attempt_index": attempt_index,
                "project_count": len(rankings),
            },
        )
        audit.append({"phase": phase, "model": response.model, "raw": response.raw_response})
        try:
            return response, self._normalize_global_output(
                parse_json_object_from_text(response.content)
            ), audit
        except (JSONDecodeError, ValueError) as exc:
            validation_error = str(exc)
            if attempt_index == 2:
                raise
    raise RuntimeError("全局文章综合失败")
```

- [ ] **Step 3: 新增确定性文章拼装**

```python
def _assemble_article_markdown(
    self,
    global_content: dict[str, str],
    rankings: list[WeeklyRankingRecord],
    project_contents: list[dict[str, Any]],
) -> str:
    blocks = [
        global_content["opening_markdown"],
        "### 本周主线",
        global_content["weekly_theme_markdown"],
        f"### Top {len(rankings)} 项目拆解",
    ]
    for index, (ranking, project) in enumerate(
        zip(rankings, project_contents, strict=True), start=1
    ):
        blocks.extend(
            [
                f"#### 项目 {index}：{ranking.full_name}",
                project["overview_text"],
                "**技术特点**",
                project["technical_features_markdown"],
                "**机制拆解**",
                project["mechanism_breakdown_markdown"],
                "**工程启发**",
                project["engineering_insights_markdown"],
            ]
        )
    blocks.extend(["### 工程启发", global_content["closing_markdown"]])
    return "\n\n".join(block.strip() for block in blocks if block.strip())
```

该顺序让 `ArticleLayoutService` 在项目概述之后插入对应图片，无需正文占位符。

- [ ] **Step 4: 重写 `_generate_normalized_content` 为 N+1 编排**

按排名循环 `_generate_project_content`，保存项目输出与每次 audit；全部成功后调用 `_generate_global_content`。拼装文章后复用 `_validate_article_depth`，并将每个 `project["project_brief"]` 传给 `_build_content_briefs`。

返回：

```python
generation_audit = {
    "project_count": len(rankings),
    "provider_call_count": sum(len(item["attempts"]) for item in project_audits)
    + len(global_attempts),
    "project_calls": project_audits,
    "global_calls": global_attempts,
}
return global_response, normalized, generation_audit
```

- [ ] **Step 5: 更新文章深度校验**

让 `_validate_article_depth` 只要求每个项目按顺序包含 `技术特点`、`机制拆解`、`工程启发`，并继续检查仓库名、当前 stars、本周增长和动态项目数量。把项目正文映射保留给 `project_analysis_markdown`，供图片和视频任务复用。

- [ ] **Step 6: 运行编排测试**

Run: `.venv\Scripts\python.exe -m pytest tests\test_summary_generation_orchestration.py -q`

Expected: PASS，N=1、3、6 均为 N+1 调用；第二个项目修复不会重放第一个项目；文章结构和图片简报数量正确。

- [ ] **Step 7: 提交全局综合与拼装**

```bash
git add src/tasks/summary_task.py tests/test_summary_generation_orchestration.py
git commit -m "feat: synthesize dynamic weekly article from project results"
```

---

### Task 5: 接入 SummaryTask 入库与多调用审计

**Files:**
- Modify: `src/tasks/summary_task.py`
- Test: `tests/test_summary_generation_orchestration.py`

**Interfaces:**
- Consumes: `_generate_normalized_content` 返回的全局响应、标准化文章和 `generation_audit`
- Produces: 兼容现有 `generated_contents` 的单篇文章记录与 N 份 `image_prompts`

- [ ] **Step 1: 更新 `execute()` 解包与审计数据**

```python
response, normalized, generation_audit = self._generate_normalized_content(
    provider=provider,
    rankings=rankings,
    week_end=week_end,
    highest_star_repository=highest_star_repository,
    ranking_evidence=ranking_evidence,
    article_skill_instructions=article_skill_instructions,
    regeneration_feedback=regeneration_feedback,
    summary_instruction=context.config.runtime_prompt("summary"),
)
```

保持 `GeneratedContentInput` 字段不变，把 `raw_response` 更新为：

```python
raw_response={
    "model": response.model,
    "article_skill": self.article_skill_name,
    "parsed": normalized,
    "raw": response.raw_response,
    "generation_audit": generation_audit,
    "input_evidence": [
        ranking_evidence[item.full_name].audit_payload()
        for item in rankings
        if item.full_name in ranking_evidence
    ],
}
```

返回结果新增 `llm_call_count`，并保持 `image_prompt_count == ranking_count == N`。

- [ ] **Step 2: 增加入库边界测试**

在编排测试中断言成功结果只形成一个 `normalized` 对象；再构造第二次修复仍非法的项目，断言 `_generate_normalized_content` 抛出 `ValueError` 且不会进入 `global_synthesis`。`execute()` 只在生成函数成功返回后调用 repository `create`，因此不产生半篇文章。

- [ ] **Step 3: 运行 SummaryTask 专项测试**

Run: `.venv\Scripts\python.exe -m pytest tests\test_summary_generation_orchestration.py tests\test_summary_visual_brief.py tests\test_article_skill_prompt_loader.py -q`

Expected: PASS。

- [ ] **Step 4: 提交任务接入**

```bash
git add src/tasks/summary_task.py tests/test_summary_generation_orchestration.py
git commit -m "feat: persist n-plus-one summary generation audit"
```

---

### Task 6: 更新视觉简报与离线深度合同

**Files:**
- Modify: `tests/test_summary_visual_brief.py`
- Modify: `tests/test_wechat_draft_image_count.py`
- Modify: `src/services/article_layout_service.py`
- Modify: `scripts/verify_summary_depth_contract.py`

**Interfaces:**
- Consumes: 新三分点项目正文和每项目独立 `project_brief`
- Produces: 动态 N、事实数字、架构加数据流图片和后续视频证据的回归保护

- [ ] **Step 1: 更新视觉简报测试样本**

把 `_project_analysis()` 改为：

```python
return """
example/spec-kit 本周新增 10 stars，总 stars 为 100。它用规格约束实现，适合需求需要持续追踪的团队。

**技术特点**
需求规格、技术计划和任务清单分担不同职责。

**机制拆解**
需求规格依次流向技术计划、任务清单和代码实现。

**工程启发**
把验收条件前置；规格质量仍需要人工确认，阅读时先看规格入口再看任务生成。
""".strip()
```

保留真实节点断言，并新增：

```python
assert any(
    relation["label"] == "数据流"
    for relation in briefs[0]["visual_brief"]["relationships"]
)
```

- [ ] **Step 2: 重写离线文章与项目结果构造器**

把旧 `build_project_section` 的六标签改为“概述 + 三分点”。新增 `build_project_output` 和 `build_global_output`，让离线脚本直接覆盖 `_generate_normalized_content` 的 Stub 调用路径，而不是继续向已删除的整篇文章 `_build_messages` 传参。

- [ ] **Step 3: 增加项目图片位置回归测试**

在 `tests/test_wechat_draft_image_count.py` 导入 `GeneratedContentForLayout`，构造一个项目正文和一张现有图片资产：

```python
with tempfile.TemporaryDirectory() as temporary_dir:
    project_root = Path(temporary_dir)
    content = GeneratedContentForLayout(
        id=17,
        week_end="2026-08-14",
        title="Top 1",
        digest="digest",
        article_markdown=(
            "### Top 1 项目拆解\n\n"
            "#### 项目 1：owner/repo\n\n"
            "本周新增 10 stars，总 stars 为 100。它用于验证排版，适合单项目流程。\n\n"
            "**技术特点**\n\n模块职责清楚。\n\n"
            "**机制拆解**\n\n数据从入口流向结果。\n\n"
            "**工程启发**\n\n先验证边界。"
        ),
        video_script="",
        voiceover_text="",
        image_prompts=[
            {
                "repository_full_name": "owner/repo",
                "rank": 1,
                "summary_text": "项目架构与主数据流。",
            }
        ],
        status="approved",
        created_at="2026-08-14T00:00:00Z",
        updated_at="2026-08-14T00:00:00Z",
    )
    result = ArticleLayoutService().build(
        content=content,
        media_assets=[_image_asset(project_root)],
    )
    overview_index = result.article_html.index("本周新增 10 stars")
    image_index = result.article_html.index("data-asset-id=\"101\"")
    feature_index = result.article_html.index("技术特点")
    self.assertLess(overview_index, image_index)
    self.assertLess(image_index, feature_index)
```

如果测试显示图片出现在概述之前，检查 `find_project_repository_for_heading`。保留仓库全名、仓库短名、`项目 {rank}` 和 `第 {rank}`，从 `rank_markers` 删除 `Top {rank}` 与 `TOP {rank}`，防止总标题误匹配；不改变图片卡片渲染和其他排版接口。

- [ ] **Step 4: 增加动态 N 与调用审计断言**

对 N=1、3、6 分别断言：

```python
assert audit["project_count"] == count
assert audit["provider_call_count"] == count + 1
assert len(normalized["image_prompts"]) == count
assert normalized["article_markdown"].count("**技术特点**") == count
assert normalized["article_markdown"].count("**机制拆解**") == count
assert normalized["article_markdown"].count("**工程启发**") == count
```

继续保留精确数字、无长度合同、视频证据卡和数据库媒体计划验证。

- [ ] **Step 5: 运行视觉、排版与离线合同**

Run: `.venv\Scripts\python.exe -m pytest tests\test_summary_visual_brief.py -q`

Expected: PASS。

Run: `.venv\Scripts\python.exe -m pytest tests\test_wechat_draft_image_count.py -q`

Expected: PASS，图片位于项目概述与“技术特点”之间。

Run: `.venv\Scripts\python.exe scripts\verify_summary_depth_contract.py`

Expected: 输出 `摘要深度合同验证通过`，并报告动态项目数与 N+1 调用合同。

- [ ] **Step 6: 提交回归合同**

```bash
git add src/services/article_layout_service.py tests/test_summary_visual_brief.py tests/test_wechat_draft_image_count.py scripts/verify_summary_depth_contract.py
git commit -m "test: verify dynamic project article contract"
```

---

### Task 7: 同步设计记录并执行完整验证

**Files:**
- Modify: `docs/summary_writing_skill_research.md`
- Modify: `docs/weekly_media_pipeline.md`

**Interfaces:**
- Consumes: 已实现并验证的 N+1 调用链
- Produces: 当前设计目标、技术取舍、调用链、依赖、验证结果和后续边界记录

- [ ] **Step 1: 更新写作 Skill 接入记录**

在 `docs/summary_writing_skill_research.md` 写明：

- `github-project-blog` 分为单项目与全局综合两种嵌入模式；
- `SummaryTask` 正常执行 N+1 次调用，项目调用按排名串行；
- 三个可见分点承载七项教学分析；
- `humanizer` 只提供编译进 Skill 的表达约束，不构成运行时依赖；
- 项目路径、函数、耦合和同步异步仍受 README 证据范围限制。

- [ ] **Step 2: 更新周榜媒体流水线**

在 `docs/weekly_media_pipeline.md` 把 SummaryTask 调用链更新为：

```text
rankings(N)
  -> N 次项目生成（概述 + 三分点 + visual_brief）
  -> 1 次全局综合（标题 + 摘要 + 开篇 + 本周主线 + 结尾）
  -> 确定性拼装单篇 article_markdown
  -> N 份 ContentBrief
  -> N 次项目生图
  -> ArticleLayoutService 在每个项目概述后插入一张图
```

记录本次不引入并发、数据库迁移或排版占位符。

- [ ] **Step 3: 运行 Python 编译检查**

Run: `.venv\Scripts\python.exe -m compileall -q src\tasks\summary_task.py scripts\verify_summary_depth_contract.py tests\test_summary_generation_orchestration.py tests\test_summary_visual_brief.py`

Expected: exit code 0。

- [ ] **Step 4: 运行专项测试与离线脚本**

Run: `.venv\Scripts\python.exe -m pytest tests\test_summary_generation_orchestration.py tests\test_summary_visual_brief.py tests\test_article_skill_prompt_loader.py tests\test_image_prompt_design_service.py tests\test_wechat_draft_image_count.py -q`

Expected: PASS。

Run: `.venv\Scripts\python.exe scripts\verify_summary_depth_contract.py`

Expected: PASS。

Run: `.venv\Scripts\python.exe scripts\verify_media_creative_contract.py`

Expected: PASS，证明 N 份 ContentBrief 仍能进入图片和视频链路。

- [ ] **Step 5: 检查改动边界**

Run: `git diff --check`

Expected: 无空白错误。

Run: `git status --short`

Expected: 只看到本计划目标文件和执行前已经存在的用户改动；不得出现数据库、媒体产物或生产配置新增。

- [ ] **Step 6: 提交文档与最终验证记录**

```bash
git add docs/summary_writing_skill_research.md docs/weekly_media_pipeline.md
git commit -m "docs: record dynamic weekly article generation"
```
