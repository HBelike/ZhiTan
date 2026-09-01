# 模型动态输出额度最小实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消回答请求固定 `4096 Token` 的硬上限，按“模型最大输出能力、全局 100000 上限、上下文剩余安全空间”动态确定本轮 `max_tokens`，并避免长度截断被静默当作完整回答。

**Architecture:** 不新增数据库列、不增加 Turn 状态、不建设自动续写或模型能力注册中心。继续复用 `reserved_output_tokens` 数据库/API 字段保存管理员核对的“模型最大输出能力”，仅用一条约束迁移放宽旧的“一半上下文”限制；复用 `response_generation.max_completion_tokens` 作为全局保护上限，`PromptContextService` 在压缩前后生成临时运行策略，`CareerResponseRunner` 使用最终上下文快照中的动态额度发起请求。

**Tech Stack:** Python 3、FastAPI、Pydantic、PostgreSQL、Vue 3、Node test runner、pytest

**Spec:** `docs/superpowers/specs/2026-08-26-career-memory-and-context-compaction-v2-design.md`（本计划仅实施对旧固定输出预算的最小修正）

## Global Constraints

- 仅本地修改、测试和构建，不连接或部署生产环境。
- 管理台模型卡片仍保留现有四个输入项，不新增页面或数据库字段；允许增加一条必要的数据库约束迁移。
- `response_generation.max_completion_tokens` 固定配置为 `100000`，作为产品级单次最终正文保护上限。
- 最小可用回答额度固定为 `1024 Token`，只作为代码常量，不新增配置项。
- 保留现有输入 Token `1.15` 安全系数、80% 压缩触发、60% 压缩目标和 95% 硬限制。
- 暂不实现自动续写、`partial` 状态、Provider 能力自动更新、结束原因持久化和 Tool Calling 每轮上下文重算。

---

### Task 1: 动态计算本轮有效输出额度

**Files:**
- Modify: `src/career_assistant/context_budget.py`
- Modify: `src/career_assistant/prompt_context.py`
- Modify: `src/career_assistant/response_runner.py`
- Modify: `src/career_assistant/web/router.py`
- Test: `tests/test_career_context_budget.py`
- Test: `tests/test_career_prompt_context.py`
- Test: `tests/test_career_response_memory_v2.py`

**Interfaces:**
- Consumes: `ModelContextPolicy.reserved_output_tokens` 作为模型最大输出能力，`ResponseGenerationSettings.max_completion_tokens` 作为全局保护上限。
- Produces: `ContextBudgetService.runtime_policy(...) -> ModelContextPolicy`；`PreparedPromptContext.context_usage.reserved_output_tokens` 保存本轮实际发送给 Provider 的 `max_tokens`。

- [ ] **Step 1: 为运行策略写失败测试**

在 `tests/test_career_context_budget.py` 增加以下测试，覆盖模型上限、系统上限和剩余空间三者取最小值：

```python
def test_runtime_policy_caps_output_by_model_system_and_safe_space() -> None:
    service = ContextBudgetService(FixedEstimator(70_000))
    components = (PromptComponent.pinned("input", (ChatMessage("user", "问题"),)),)
    source = ModelContextPolicy(
        context_window_tokens=100_000,
        reserved_output_tokens=40_000,
        context_window_source="admin",
    )

    runtime = service.runtime_policy(components, source, system_max_output_tokens=100_000)

    assert runtime.reserved_output_tokens == 14_472
    assert source.reserved_output_tokens == 40_000


def test_runtime_policy_uses_model_limit_when_it_is_lower() -> None:
    service = ContextBudgetService(FixedEstimator(1_000))
    components = (PromptComponent.pinned("input", (ChatMessage("user", "问题"),)),)
    source = ModelContextPolicy(
        context_window_tokens=100_000,
        reserved_output_tokens=8_192,
        context_window_source="admin",
    )

    runtime = service.runtime_policy(components, source, system_max_output_tokens=100_000)

    assert runtime.reserved_output_tokens == 8_192


def test_runtime_policy_uses_system_limit_when_it_is_lower() -> None:
    service = ContextBudgetService(FixedEstimator(1_000))
    components = (PromptComponent.pinned("input", (ChatMessage("user", "问题"),)),)
    source = ModelContextPolicy(
        context_window_tokens=1_000_000,
        reserved_output_tokens=384_000,
        context_window_source="admin",
    )

    runtime = service.runtime_policy(components, source, system_max_output_tokens=100_000)

    assert runtime.reserved_output_tokens == 100_000
```

测试中的首个期望值按现有估算公式计算：`estimated_input = ceil((24 + 70000) * 1.15) = 80528`，95% 安全边界为 `95000`，剩余 `14472`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest tests/test_career_context_budget.py -q`

Expected: FAIL，提示 `ContextBudgetService` 没有 `runtime_policy`。

- [ ] **Step 3: 实现最小运行策略计算**

在 `src/career_assistant/context_budget.py` 增加复用现有估算逻辑的方法，不修改来源策略对象：

```python
MINIMUM_VIABLE_OUTPUT_TOKENS = 1_024


def _estimated_input_tokens(
    self,
    components: tuple[PromptComponent, ...],
) -> int:
    raw_input = PROVIDER_WRAPPER_TOKENS + sum(
        component.extra_token_estimate for component in components
    ) + sum(
        self._estimator.estimate_message(message)
        for component in components
        for message in component.messages
    )
    return math.ceil(raw_input * TOKEN_SAFETY_FACTOR)


def runtime_policy(
    self,
    components: tuple[PromptComponent, ...],
    policy: ModelContextPolicy,
    *,
    system_max_output_tokens: int,
) -> ModelContextPolicy:
    policy.validate()
    if system_max_output_tokens <= 0:
        raise ValueError("系统最大输出必须大于 0")
    estimated_input = self._estimated_input_tokens(components)
    safe_total = math.floor(policy.context_window_tokens * HARD_LIMIT_PERCENT / 100)
    available_output = max(1, safe_total - estimated_input)
    return replace(
        policy,
        reserved_output_tokens=min(
            policy.reserved_output_tokens,
            system_max_output_tokens,
            available_output,
        ),
    )
```

同时让 `measure()` 调用 `_estimated_input_tokens()`，避免复制 Token 估算公式。

- [ ] **Step 4: 让 Prompt 压缩前后都使用临时运行策略**

修改 `PromptContextService.__init__`，增加并校验 `system_max_output_tokens: int`，保存为实例字段。修改 `prepare()`：

```python
source_policy = resolution.profile.context_policy
runtime_policy = self._budget.runtime_policy(
    components,
    source_policy,
    system_max_output_tokens=self._system_max_output_tokens,
)
initial = self._budget.measure(components, runtime_policy)
```

触发压缩时，`target_prompt_tokens` 仍按期望回答额度计算，避免仅因为压缩前剩余空间小就接受短回答：

```python
desired_output_tokens = min(
    source_policy.reserved_output_tokens,
    self._system_max_output_tokens,
)
target_prompt_tokens = max(
    1,
    math.floor(
        source_policy.context_window_tokens
        * source_policy.compression_target_percent
        / 100,
    ) - desired_output_tokens,
)
```

压缩并重新构建 `components` 后再次调用 `runtime_policy()`。最终 `fit()` 使用新的运行策略，并执行以下明确检查，不请求额度不足的模型：

```python
fitted = self._budget.fit(
    components,
    runtime_policy,
    target_percent=(source_policy.compression_target_percent if compression_triggered else None),
)
if fitted.usage.reserved_output_tokens < MINIMUM_VIABLE_OUTPUT_TOKENS:
    raise ContextHardLimitError(fitted.usage)
```

`snapshot_for_conversation()`、`empty_snapshot()` 和 `estimate_post_turn()` 使用相同临时策略，确保占用展示与真实请求一致。

- [ ] **Step 5: 让回答请求使用最终动态额度**

把 `src/career_assistant/response_runner.py` 普通与流式路径中的：

```python
max_tokens=resolution.profile.context_policy.reserved_output_tokens
```

统一替换为：

```python
max_tokens=prepared.context_usage.reserved_output_tokens
```

在 `_complete_skill_agent()` 内为中间 Tool Calling 请求建立局部选项，避免 6 轮规划分别使用 100000：

```python
tool_options = replace(
    options or CompletionRequestOptions(),
    max_tokens=min((options.max_tokens if options else None) or 4_096, 4_096),
)
```

`complete_with_tools()` 使用 `tool_options`；普通最终正文仍使用原 `options`。

- [ ] **Step 6: 注入已有全局配置，不新增配置键**

修改 `src/career_assistant/web/router.py` 中 `PromptContextService(...)` 的构造：

```python
prompt_context_service = PromptContextService(
    conversation_repository,
    conversation_memory_service,
    ContextBudgetService(),
    request.app.state.career_skill_tool_registry,
    system_max_output_tokens=response_generation_settings.max_completion_tokens,
)
```

- [ ] **Step 7: 更新上下文和回答路径测试**

在 `tests/test_career_prompt_context.py` 增加断言：模型能力为 `140_000`、系统上限为 `100_000` 时，压缩目标扣除 `100_000` 而不是 `140_000`，最终快照输出额度不超过安全剩余空间。

更新 `tests/test_career_response_memory_v2.py` 的源码契约断言：

```python
assert source.count("max_tokens=prepared.context_usage.reserved_output_tokens") == 2
assert "max_tokens=resolution.profile.context_policy.reserved_output_tokens" not in source
assert "max_tokens=min((options.max_tokens if options else None) or 4_096, 4_096)" in source
```

- [ ] **Step 8: 运行本任务测试**

Run: `pytest tests/test_career_context_budget.py tests/test_career_prompt_context.py tests/test_career_response_memory_v2.py -q`

Expected: PASS。

- [ ] **Step 9: 提交本任务**

```bash
git add src/career_assistant/context_budget.py src/career_assistant/prompt_context.py src/career_assistant/response_runner.py src/career_assistant/web/router.py tests/test_career_context_budget.py tests/test_career_prompt_context.py tests/test_career_response_memory_v2.py
git commit -m "fix: dynamically bound career response output"
```

---

### Task 2: 最小调整管理配置语义和持久化边界

**Files:**
- Modify: `config/career_assistant.yaml`
- Modify: `src/career_assistant/persistence/model_profile_repository.py`
- Modify: `src/career_assistant/web/admin_context_router.py`
- Modify: `src/career_assistant/settings.py`
- Modify: `src/career_assistant/response_runner.py`
- Create: `migrations/versions/20260830_32_model_output_capability.py`
- Modify: `web-ui/src/components/AdminModelContextPanel.vue`
- Test: `tests/test_model_context_policy.py`
- Test: `tests/test_career_admin_context_api.py`
- Test: `web-ui/src/admin-model-context.test.js`

**Interfaces:**
- Consumes: 现有 `reserved_output_tokens` 字段和 `/context-policy` API；迁移只调整旧约束并回填已核对的 DeepSeek V4，不新增数据库列。
- Produces: 管理台把该字段展示为“模型最大输出”；全局配置允许最终回答最多 100000 Token；持久化字符边界提高到 500000。

- [ ] **Step 1: 写模型输出能力校验失败测试**

把 `tests/test_model_context_policy.py::test_policy_rejects_output_larger_than_half_context` 改为验证“最大输出不能超过上下文容量”，并增加允许超过一半的用例：

```python
def test_policy_allows_model_output_above_half_context() -> None:
    ModelContextPolicy(
        context_window_tokens=128_000,
        reserved_output_tokens=96_000,
    ).validate()


def test_policy_rejects_model_output_above_context() -> None:
    with pytest.raises(ValueError, match="模型最大输出"):
        ModelContextPolicy(
            context_window_tokens=128_000,
            reserved_output_tokens=128_001,
        ).validate()
```

在 `tests/test_career_admin_context_api.py` 增加 API 接受 `context_window_tokens=1_048_576`、`reserved_output_tokens=384_000` 的测试。

- [ ] **Step 2: 运行后端测试并确认失败**

Run: `pytest tests/test_model_context_policy.py tests/test_career_admin_context_api.py -q`

Expected: FAIL，现有“一半上下文”和 API `32768` 上限拒绝新值。

- [ ] **Step 3: 放宽能力字段校验但保留旧字段名**

修改 `ModelContextPolicy.validate()`：

```python
if not 0 < self.reserved_output_tokens <= self.context_window_tokens:
    raise ValueError("模型最大输出必须为正数且不能超过上下文容量")
```

修改 `UpdateModelContextPolicyRequest`：

```python
reserved_output_tokens: int = Field(ge=1, le=2_000_000)
```

保留字段名是本次最小兼容取舍；在 dataclass 和 API 模型注释中明确它现在表示管理员核对的模型最大输出能力。

- [ ] **Step 4: 修改管理台文案和前端校验**

`AdminModelContextPanel.vue` 继续提交 `reserved_output_tokens`，但把标签改为“模型最大输出”，把校验改为：

```javascript
if (!Number.isInteger(outputTokens) || outputTokens < 1 || outputTokens > windowTokens) {
  return '模型最大输出必须为正数且不能超过上下文容量。'
}
```

卡片底部补充短提示：“单次回答仍受系统 100000 Token 上限约束”。更新 `web-ui/src/admin-model-context.test.js`，断言新中文标签存在、旧“预留输出”标签不存在。

- [ ] **Step 5: 提高全局请求与持久化上限**

修改 `config/career_assistant.yaml`：

```yaml
response_generation:
  max_completion_tokens: 100000
  request_timeout_seconds: 90
  max_attempts: 2
  retry_backoff_seconds: 0.8
  max_persisted_response_characters: 500000
```

同步把 `load_response_generation_settings()` 对 `max_completion_tokens` 的最大校验从 `32768` 调整为 `100000`；把 `ResponseGenerationSettings`、`load_response_generation_settings()` 和 `CareerResponseRunner.__init__()` 对持久化字符数的最大校验从 `30000` 调整为 `500000`。继续保留最终切片作为极端保护，使 100K Token 的正常纯文本不会再轻易碰到应用层字符边界。

- [ ] **Step 6: 运行后端和前端定向测试**

Run: `pytest tests/test_model_context_policy.py tests/test_career_admin_context_api.py -q`

Expected: PASS。

Run: `Set-Location web-ui; node --test src/admin-model-context.test.js`

Expected: PASS。

- [ ] **Step 7: 提交本任务**

```bash
git add config/career_assistant.yaml src/career_assistant/persistence/model_profile_repository.py src/career_assistant/web/admin_context_router.py src/career_assistant/settings.py src/career_assistant/response_runner.py web-ui/src/components/AdminModelContextPanel.vue tests/test_model_context_policy.py tests/test_career_admin_context_api.py web-ui/src/admin-model-context.test.js
git commit -m "fix: clarify model output capability limits"
```

---

### Task 3: 对长度截断给出明确提示

**Files:**
- Modify: `src/career_assistant/model_clients.py`
- Test: `tests/test_career_model_client.py`
- Test: `tests/test_career_skill_tools.py`

**Interfaces:**
- Consumes: OpenAI-compatible `choices[0].finish_reason`。
- Produces: 普通与流式正文在长度截断时附加统一提示；被截断的 Tool Calling 直接失败，不执行不完整参数。

- [ ] **Step 1: 写普通、流式和 Tool Calling 截断测试**

在 `tests/test_career_model_client.py` 增加普通与 SSE 响应测试，构造：

```python
{
    "choices": [{
        "finish_reason": "length",
        "message": {"content": "未完成正文"},
    }],
}
```

以及最后一个流式帧：

```python
{"choices": [{"finish_reason": "length", "delta": {}}]}
```

断言结果保留正文，并包含“达到模型输出上限”。在 `tests/test_career_skill_tools.py` 构造 `finish_reason="length"` 的 tool response，断言抛出 `ModelInvocationError`，且没有执行工具。

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest tests/test_career_model_client.py tests/test_career_skill_tools.py -q`

Expected: FAIL，当前普通响应提前返回正文，流式响应忽略 `finish_reason`，Tool Calling 继续解析不完整参数。

- [ ] **Step 3: 增加最小结束原因判断和统一提示**

在 `model_clients.py` 增加：

```python
OUTPUT_TRUNCATION_NOTICE = (
    "\n\n> 本次回答达到模型输出上限，内容可能未完整结束；你可以回复“继续”。"
)


def _is_length_finish_reason(value: object) -> bool:
    return isinstance(value, str) and value.casefold() in {
        "length",
        "max_tokens",
        "max_output_tokens",
    }
```

非流式 `_request_completion()` 在返回非空正文前读取 `choice.get("finish_reason")`；命中长度原因时返回 `正文 + OUTPUT_TRUNCATION_NOTICE`。

流式 `_stream_request_completion()` 保存最后一个非空 `finish_reason`，完成帧读取后若命中长度原因，再 `yield OUTPUT_TRUNCATION_NOTICE`。

`_request_tool_completion()` 在解析或执行 `tool_calls` 前检查结束原因；命中长度原因时抛出：

```python
raise ModelInvocationError("模型 Tool Calling 达到输出上限，请缩小任务范围后重试。")
```

本次不增加自动续写或 `partial` 状态；明确提示是为消除“静默完整成功”的最小闭环。

- [ ] **Step 4: 运行模型客户端测试**

Run: `pytest tests/test_career_model_client.py tests/test_career_skill_tools.py -q`

Expected: PASS。

- [ ] **Step 5: 提交本任务**

```bash
git add src/career_assistant/model_clients.py tests/test_career_model_client.py tests/test_career_skill_tools.py
git commit -m "fix: surface truncated model responses"
```

---

### Task 4: 更新模块文档并完成本地验证

**Files:**
- Modify: `docs/career_assistant_module.md`
- Modify: `docs/career_model_connections.md`

**Interfaces:**
- Consumes: Tasks 1-3 的最终行为。
- Produces: 动态输出公式、兼容字段语义、最小实现边界和验证结果的项目记录。

- [ ] **Step 1: 更新设计与调用链说明**

在 `docs/career_assistant_module.md` 记录：

```text
本轮回答 max_tokens = min(管理员核对的模型最大输出、系统 100000、95% 安全边界内的剩余输出空间)。
上下文输入估算继续使用 1.15 安全系数；低于 1024 Token 时拒绝调用。Tool Calling 中间规划单次最多 4096 Token。
```

在 `docs/career_model_connections.md` 说明 `reserved_output_tokens` 是为兼容旧数据库/API 暂时保留的字段名，管理台语义已调整为“模型最大输出”；未知模型仍需管理员按具体 Model ID 核对。本次未实现自动续写、`partial` 状态和自动能力发现。

- [ ] **Step 2: 运行全部相关后端回归**

Run: `pytest tests/test_career_context_budget.py tests/test_career_prompt_context.py tests/test_career_response_memory_v2.py tests/test_model_context_policy.py tests/test_career_admin_context_api.py tests/test_career_model_client.py tests/test_career_skill_tools.py -q`

Expected: PASS。

- [ ] **Step 3: 运行前端测试和生产构建**

Run: `Set-Location web-ui; node --test src/admin-model-context.test.js; npm run build`

Expected: 测试 PASS，Vite production build 成功。

- [ ] **Step 4: 运行 Python 编译与差异检查**

Run: `python -m compileall -q src tests`

Expected: exit code 0。

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 5: 提交文档与最终验证记录**

```bash
git add docs/career_assistant_module.md docs/career_model_connections.md
git commit -m "docs: record dynamic career output budgeting"
```

## 明确延期项

- 自动续写一次及正文去重。
- `AgentTurnStatus.PARTIAL`、前端“继续生成”按钮和长期记忆跳过 partial。
- Provider 官方能力注册表、来源链接和最后核对日期。
- `finish_reason`、截断率及自动续写成功率的持久化统计。
- Tool Calling 每轮工具结果加入后的完整上下文重新测量。
- 独立 `max_input_tokens` 和 Thinking Token Provider Adapter。
