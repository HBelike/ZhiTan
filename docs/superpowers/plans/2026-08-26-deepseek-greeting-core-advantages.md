# DeepSeek Greeting Core Advantages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 DeepSeek 招呼语优先提炼有简历证据的学历、工作经验、个人项目和奖项，而不是集中复述单一工作项目的技术细节。

**Architecture:** 保持 `CareerGreetingService`、JSON 输出协议和事实校验链不变，只增强 system Prompt 的内部优势扫描、选材优先级和 Few-shot 边界，并在 user Prompt 中提醒模型完整扫描 CV。使用字符串级单元测试锁定 Prompt 约束，再用现有服务测试与真实模型调用验证结果。

**Tech Stack:** Python 3、pytest、DeepSeek OpenAI-compatible Chat Completions

**Spec:** `docs/superpowers/specs/2026-08-26-deepseek-greeting-core-advantages.md`

## Global Constraints

- 仅修改 Prompt、测试和实现记录，不改变 API、JSON Schema、前端交互或模型配置。
- 固定模型 `deepseek-v4-pro`，固定 `temperature=0.2`。
- 某类优势没有简历证据时必须省略，禁止根据 JD、示例或常识补全。
- Few-shot 示例仅可学习选材结构，不得复制其中的任何候选人事实。

---

### Task 1: 锁定 Prompt 行为

**Files:**
- Modify: `tests/test_career_greetings.py`

**Interfaces:**
- Consumes: `CareerGreetingService._user_prompt(job, cv_evidence, jd_evidence, previous_message) -> str`
- Produces: 针对 system Prompt 与 user Prompt 的回归断言

- [x] **Step 1: 写失败测试**

```python
def test_prompt_prioritizes_supported_candidate_advantages():
    assert "学历" in greeting_module._SYSTEM_PROMPT
    assert "工作经验" in greeting_module._SYSTEM_PROMPT
    assert "个人项目" in greeting_module._SYSTEM_PROMPT
    assert "奖项" in greeting_module._SYSTEM_PROMPT
    assert "没有证据" in greeting_module._SYSTEM_PROMPT
    assert "不是固定模板" in greeting_module._SYSTEM_PROMPT
```

- [x] **Step 2: 运行测试并确认失败**

Run: `pytest tests/test_career_greetings.py -q`

Expected: 新增断言因当前 Prompt 缺少完整优势扫描和示例边界而失败。

- [x] **Step 3: 提交测试交付物**

Run: `git diff --check -- tests/test_career_greetings.py`

Expected: 无空白错误，失败测试准确描述已确认设计。

### Task 2: 增强 DeepSeek Prompt

**Files:**
- Modify: `src/career_assistant/greetings.py:27`
- Test: `tests/test_career_greetings.py`

**Interfaces:**
- Consumes: 编号后的 `CV-xxx` 与 `JD-xxx` 证据
- Produces: 保持现有 JSON 字段不变的 DeepSeek 请求消息

- [x] **Step 1: 实现优势扫描与条件省略规则**

在 `_SYSTEM_PROMPT` 中加入内部分类扫描，要求从有证据的类别中选择 2 至 4 项优势；有证据时优先覆盖学历、工作经验和个人项目，并把有区分度的奖项作为高价值可选项。

- [x] **Step 2: 加入非模板 Few-shot 示例**

加入一条虚构示例，明确其只展示“学历 + 工作经验 + 个人项目 + 沟通邀请”的信息组织方式；示例事实不得迁移，真实输出仍须逐项绑定 `CV-xxx`。

- [x] **Step 3: 强化 user Prompt 的完整扫描指令**

在候选人资料前要求先阅读全部 CV 证据，不得只挑与 JD 最接近的单一项目技术片段。

- [x] **Step 4: 运行定向测试**

Run: `pytest tests/test_career_greetings.py -q`

Expected: 全部通过，并继续验证 `deepseek-v4-pro`、`temperature=0.2`、证据编号和事实校验。

### Task 3: 验证真实生成质量并记录结果

**Files:**
- Modify: `docs/career_assistant_module.md`

**Interfaces:**
- Consumes: 当前本地简历、完整岗位 JD、已配置的 DeepSeek 凭据
- Produces: 真实生成结果与验证记录，不记录 API Key

- [x] **Step 1: 运行完整相关测试**

Run: `pytest tests/test_career_greetings.py tests/test_career_greeting_api.py -q`

Expected: 全部通过。

- [x] **Step 2: 调用真实 DeepSeek 验证**

使用当前本地简历和代表性完整 JD 运行生成链，检查文案是否在有证据时覆盖至少两个优势类别，且没有示例事实泄漏或无依据扩写。

- [x] **Step 3: 更新模块文档**

在 `docs/career_assistant_module.md` 记录优势扫描目标、非模板 Few-shot 边界、事实约束和验证结果。

- [x] **Step 4: 最终差异检查**

Run: `git diff --check -- src/career_assistant/greetings.py tests/test_career_greetings.py docs/career_assistant_module.md docs/superpowers/specs/2026-08-26-deepseek-greeting-core-advantages.md docs/superpowers/plans/2026-08-26-deepseek-greeting-core-advantages.md`

Expected: 无空白错误，差异仅包含本次 Prompt、测试和文档。
