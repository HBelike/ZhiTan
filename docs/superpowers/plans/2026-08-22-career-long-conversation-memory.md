# 求职助手长对话记忆 Implementation Plan

> 历史说明：本计划已被 2026-08-26 的《求职助手长期记忆与动态上下文压缩 V2 Implementation Plan》取代，仅保留为历史记录，不再执行。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在固定 Token 预算内向求职助手 Prompt 注入结构化滚动摘要和最近完整对话轮次，使单个会话可持续进行数百轮。

**Architecture:** 扩展现有 `session_summaries`，用覆盖消息游标和乐观锁保存一份结构化摘要；新增 `ConversationMemoryService`，在生成回复前按需压缩最旧完整轮次，并选择预算内的最近原文。`CareerResponseRunner` 只消费服务返回的记忆上下文，原始脱敏消息始终保留且摘要失败不阻断回复。

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy Text SQL、PostgreSQL、Alembic、OpenAI-compatible Chat Completions、pytest/unittest

**Spec:** `docs/superpowers/specs/2026-08-22-career-long-conversation-memory-design.md`

## Global Constraints

- 只实现单会话两层记忆，不新增向量召回、跨会话事实表或记忆管理页面。
- 原始脱敏消息是唯一事实源，压缩不得删除或改写 `messages`。
- 预算只能选择完整用户—助手轮次，禁止构造孤立消息。
- 摘要失败、校验失败、数据库失败和并发冲突都不得使正常回复失败。
- 摘要只能保存脱敏文本，Trace 不记录对话正文或模型原始输出。
- 不修改当前工作区中与本功能无关的用户改动；每次提交只暂存任务列出的文件。
- 前端实现边界不变，本次不进行 PC、手机或平板页面改造。

---

### Task 1: 增加长对话记忆配置与共享 Token 估算

**Files:**
- Modify: `src/career_assistant/settings.py:145-165`
- Modify: `src/career_assistant/settings.py:540-585`
- Modify: `config/career_assistant.yaml`
- Create: `src/career_assistant/token_budget.py`
- Create: `tests/test_career_memory_settings.py`
- Create: `tests/test_career_token_budget.py`

**Interfaces:**
- Consumes: 现有 `_load_career_root_config()`、`_read_positive_number()` 配置读取函数。
- Produces: `ConversationMemorySettings`、`ResponseGenerationSettings.memory`、`estimate_text_tokens(text: str) -> int`、`estimate_chat_message_tokens(role: str, content: str) -> int`。

- [ ] **Step 1: 写配置失败测试**

```python
def write_config(path: Path, *, history: int, trigger: int, recent: int, maximum: int) -> Path:
    path.write_text(f"""
career_assistant:
  response_generation:
    max_completion_tokens: 4096
    request_timeout_seconds: 90
    max_attempts: 2
    retry_backoff_seconds: 0.8
    max_persisted_response_characters: 30000
    history_token_budget: {history}
    summary_trigger_tokens: {trigger}
    recent_turns_target: {recent}
    summary_max_characters: {maximum}
""".strip(), encoding="utf-8")
    return path

def test_response_generation_loads_memory_budget(tmp_path: Path) -> None:
    settings = load_response_generation_settings(write_config(
        tmp_path / "career.yaml", history=12000, trigger=8000, recent=8, maximum=8000,
    ))
    assert settings.memory.history_token_budget == 12000
    assert settings.memory.summary_trigger_tokens == 8000
    assert settings.memory.recent_turns_target == 8
    assert settings.memory.summary_max_characters == 8000

def test_summary_trigger_must_be_below_history_budget(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="summary_trigger_tokens"):
        load_response_generation_settings(write_config(
            tmp_path / "career.yaml", history=8000, trigger=9000, recent=8, maximum=8000,
        ))
```

- [ ] **Step 2: 运行配置测试并确认失败**

Run: `python -m pytest tests/test_career_memory_settings.py -q`

Expected: FAIL，提示 `ConversationMemorySettings` 或 `ResponseGenerationSettings.memory` 尚不存在。

- [ ] **Step 3: 实现配置模型和启动期校验**

```python
@dataclass(frozen=True)
class ConversationMemorySettings:
    history_token_budget: int
    summary_trigger_tokens: int
    recent_turns_target: int
    summary_max_characters: int


@dataclass(frozen=True)
class ResponseGenerationSettings:
    max_completion_tokens: int
    request_timeout_seconds: float
    max_persisted_response_characters: int
    memory: ConversationMemorySettings
    max_attempts: int = 1
    retry_backoff_seconds: float = 0.8
```

读取时要求：`2048 <= history_token_budget <= 65536`、`1024 <= summary_trigger_tokens < history_token_budget`、`1 <= recent_turns_target <= 32`、`1000 <= summary_max_characters <= 12000`。把四个已批准默认值写入 `config/career_assistant.yaml`，不提供静默代码默认值，缺少配置时启动失败。

```python
def _read_required_int(config: dict[str, object], key: str) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"response_generation.{key} 必须是整数")
    return value

memory = ConversationMemorySettings(
    history_token_budget=_read_required_int(response_generation, "history_token_budget"),
    summary_trigger_tokens=_read_required_int(response_generation, "summary_trigger_tokens"),
    recent_turns_target=_read_required_int(response_generation, "recent_turns_target"),
    summary_max_characters=_read_required_int(response_generation, "summary_max_characters"),
)
if not 1024 <= memory.summary_trigger_tokens < memory.history_token_budget:
    raise ValueError(
        "response_generation.summary_trigger_tokens 必须大于等于 1024 且小于 history_token_budget"
    )
```

- [ ] **Step 4: 写 Token 估算失败测试**

```python
def test_estimate_text_tokens_handles_chinese_and_ascii(self) -> None:
    self.assertEqual(estimate_text_tokens("后端开发"), 4)
    self.assertEqual(estimate_text_tokens("backend developer"), 4)
    self.assertEqual(estimate_text_tokens(""), 0)

def test_chat_message_adds_protocol_overhead(self) -> None:
    self.assertGreater(
        estimate_chat_message_tokens("user", "后端开发"),
        estimate_text_tokens("后端开发"),
    )
```

- [ ] **Step 5: 实现确定性估算器**

```python
_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[^\x00-\x7f]")

def estimate_text_tokens(text: str) -> int:
    total = 0
    for piece in _ASCII_TOKEN_PATTERN.findall(text):
        total += 1 if not piece.isascii() else max(1, math.ceil(len(piece) / 4))
    return total

def estimate_chat_message_tokens(role: str, content: str) -> int:
    return 6 + estimate_text_tokens(role) + estimate_text_tokens(content)
```

- [ ] **Step 6: 运行 Task 1 测试**

Run: `python -m pytest tests/test_career_memory_settings.py tests/test_career_token_budget.py -q`

Expected: PASS。

- [ ] **Step 7: 提交配置和估算器**

```bash
git add config/career_assistant.yaml src/career_assistant/settings.py src/career_assistant/token_budget.py tests/test_career_memory_settings.py tests/test_career_token_budget.py
git commit -m "feat: configure career conversation memory budgets"
```

---

### Task 2: 扩展摘要持久化和乐观锁仓储接口

**Files:**
- Create: `migrations/versions/20260822_17_conversation_memory.py`
- Modify: `src/career_assistant/persistence/records.py:60-70`
- Modify: `src/career_assistant/persistence/conversation_repository.py:297-410`
- Modify: `src/career_assistant/persistence/conversation_repository.py:812-825`
- Create: `tests/test_career_conversation_memory_repository.py`

**Interfaces:**
- Consumes: `MessageRecord`、`SessionSummaryRecord`、`CareerDatabase.transaction()`。
- Produces: `get_session_summary(actor_id, conversation_id) -> SessionSummaryRecord | None`、`list_completed_dialogue_messages(actor_id, conversation_id, after_message_id=None) -> list[MessageRecord]`、`save_summary_if_current(...) -> SessionSummaryRecord | None`。

- [ ] **Step 1: 写迁移并检查升级/降级 SQL**

```python
revision = "20260822_17"
down_revision = "20260821_16"

def upgrade() -> None:
    op.execute("""
        ALTER TABLE career_assistant.session_summaries
        ADD COLUMN covered_through_message_id UUID
            REFERENCES career_assistant.messages(id) ON DELETE SET NULL,
        ADD COLUMN summary_schema_version TEXT NOT NULL
            DEFAULT 'career-conversation-summary-v1'
    """)

def downgrade() -> None:
    op.execute("""
        ALTER TABLE career_assistant.session_summaries
        DROP COLUMN summary_schema_version,
        DROP COLUMN covered_through_message_id
    """)
```

Run: `python -m compileall migrations/versions/20260822_17_conversation_memory.py`

Expected: PASS。

- [ ] **Step 2: 写仓储失败测试**

```python
def test_completed_messages_are_actor_scoped_and_succeeded_only(self) -> None:
    rows = self.repository.list_completed_dialogue_messages(
        self.actor_id, self.conversation_id, after_message_id=self.cursor_id,
    )
    sql, params = self.database.connection.calls[0]
    self.assertIn("turn.status = 'succeeded'", sql)
    self.assertIn("message.role IN ('user', 'assistant')", sql)
    self.assertEqual(params["actor_id"], self.actor_id)
    self.assertEqual(params["after_message_id"], self.cursor_id)

def test_summary_compare_and_swap_rejects_stale_version(self) -> None:
    self.database.connection.return_row = None
    saved = self.repository.save_summary_if_current(
        self.actor_id,
        self.conversation_id,
        summary_text='{"schema_version":"career-conversation-summary-v1"}',
        covered_through_message_id=self.cursor_id,
        summary_schema_version="career-conversation-summary-v1",
        expected_summary_version=2,
        expected_covered_through_message_id=self.previous_cursor_id,
    )
    self.assertIsNone(saved)
```

- [ ] **Step 3: 扩展读取模型**

```python
@dataclass(frozen=True)
class SessionSummaryRecord:
    id: UUID
    conversation_id: UUID
    summary_text: str
    summary_version: int
    summary_schema_version: str
    covered_through_message_id: UUID | None
    contains_sensitive_data: bool
    created_at: datetime
    updated_at: datetime
```

所有摘要 SELECT/RETURNING 和 `_to_summary_record()` 同时读取两个新字段。

- [ ] **Step 4: 实现摘要读取和完整消息读取**

`get_session_summary()` 必须连接 `conversations` 并同时校验 `actor_id` 与未删除状态。`list_completed_dialogue_messages()` 使用 `agent_turns` 限定 `succeeded`，游标非空时通过游标消息的 `(created_at, id)` 选择其后的消息，最终按正向时间排序。

```sql
AND (
    :after_message_id IS NULL
    OR (message.created_at, message.id) > (
        SELECT cursor.created_at, cursor.id
        FROM career_assistant.messages AS cursor
        WHERE cursor.id = :after_message_id
          AND cursor.conversation_id = :conversation_id
    )
)
```

- [ ] **Step 5: 实现创建/更新两条 CAS 路径**

当 `expected_summary_version == 0` 时使用 `INSERT ... ON CONFLICT (conversation_id) DO NOTHING RETURNING`；否则使用：

```sql
UPDATE career_assistant.session_summaries AS summary
SET summary_text = :summary_text,
    summary_version = summary.summary_version + 1,
    summary_schema_version = :summary_schema_version,
    covered_through_message_id = :covered_through_message_id,
    contains_sensitive_data = FALSE,
    updated_at = NOW()
FROM career_assistant.conversations AS conversation
WHERE summary.conversation_id = :conversation_id
  AND summary.conversation_id = conversation.id
  AND conversation.actor_id = :actor_id
  AND conversation.status = 'active'
  AND summary.summary_version = :expected_summary_version
  AND summary.covered_through_message_id IS NOT DISTINCT FROM
      :expected_covered_through_message_id
RETURNING summary.id, summary.conversation_id, summary.summary_text,
          summary.summary_version, summary.summary_schema_version,
          summary.covered_through_message_id, summary.contains_sensitive_data,
          summary.created_at, summary.updated_at
```

- [ ] **Step 6: 运行仓储测试和迁移链检查**

Run: `python -m pytest tests/test_career_conversation_memory_repository.py tests/test_career_conversation_management.py tests/test_career_conversation_pagination.py -q`

Expected: PASS。

Run: `alembic heads`

Expected: 只显示 `20260822_17 (head)`。

- [ ] **Step 7: 提交持久化改动**

```bash
git add migrations/versions/20260822_17_conversation_memory.py src/career_assistant/persistence/records.py src/career_assistant/persistence/conversation_repository.py tests/test_career_conversation_memory_repository.py
git commit -m "feat: persist versioned career conversation summaries"
```

---

### Task 3: 实现纯内存的轮次分组、摘要 Schema 和预算选择

**Files:**
- Create: `src/career_assistant/conversation_memory.py`
- Create: `tests/test_career_conversation_memory.py`

**Interfaces:**
- Consumes: `MessageRecord`、`MessageRole`、`ConversationMemorySettings`、`estimate_chat_message_tokens()`。
- Produces: `SUMMARY_SCHEMA_VERSION`、`ConversationSummary`、`DialogueTurn`、`PreparedConversationMemory`、`group_complete_turns()`、`validate_summary()`、`select_recent_turns()`。

- [ ] **Step 1: 写完整轮次分组失败测试**

```python
NOW = datetime(2026, 8, 22, tzinfo=UTC)
CONVERSATION_ID = UUID(int=99)
TURN_0, TURN_1, TURN_2, TURN_3 = (UUID(int=value) for value in range(100, 104))

def make_message(
    role: MessageRole,
    content: str,
    *,
    turn_id: UUID,
    position: int,
) -> MessageRecord:
    return MessageRecord(
        id=UUID(int=position + 1),
        conversation_id=CONVERSATION_ID,
        turn_id=turn_id,
        role=role,
        content_text=content,
        is_redacted=True,
        created_at=NOW + timedelta(seconds=position),
    )

def test_group_complete_turns_skips_orphans_and_keeps_order(self) -> None:
    messages = [
        make_message(MessageRole.USER, "孤立用户", turn_id=TURN_0, position=0),
        make_message(MessageRole.USER, "问题一", turn_id=TURN_1, position=1),
        make_message(MessageRole.ASSISTANT, "回答一", turn_id=TURN_1, position=2),
        make_message(MessageRole.ASSISTANT, "孤立助手", turn_id=TURN_2, position=3),
        make_message(MessageRole.USER, "问题二", turn_id=TURN_3, position=4),
        make_message(MessageRole.ASSISTANT, "回答二", turn_id=TURN_3, position=5),
    ]
    turns = group_complete_turns(messages)
    self.assertEqual([turn.turn_id for turn in turns], [TURN_1, TURN_3])
    self.assertEqual(turns[0].covered_through_message_id, messages[2].id)
```

- [ ] **Step 2: 写摘要校验失败测试**

```python
def valid_payload() -> dict[str, object]:
    return {
        "schema_version": "career-conversation-summary-v1",
        "user_stated_facts": ["用户只考虑上海"],
        "current_goals": ["寻找后端开发岗位"],
        "decisions": [],
        "open_loops": [],
        "assistant_suggestions": ["建议补充量化指标"],
        "corrections": [],
        "companies": [],
        "roles": ["后端开发"],
    }

def test_validate_summary_separates_user_facts_and_suggestions(self) -> None:
    summary = validate_summary(valid_payload(), max_characters=8000)
    self.assertEqual(summary.user_stated_facts, ("用户只考虑上海",))
    self.assertEqual(summary.assistant_suggestions, ("建议补充量化指标",))

def test_validate_summary_rejects_missing_or_unknown_fields(self) -> None:
    payload = valid_payload()
    payload["unexpected"] = []
    with self.assertRaisesRegex(ValueError, "字段"):
        validate_summary(payload, max_characters=8000)
```

- [ ] **Step 3: 实现不可变领域对象和严格 Schema**

```python
SUMMARY_SCHEMA_VERSION = "career-conversation-summary-v1"
SUMMARY_FIELDS = (
    "user_stated_facts", "current_goals", "decisions", "open_loops",
    "assistant_suggestions", "corrections", "companies", "roles",
)

@dataclass(frozen=True)
class ConversationSummary:
    user_stated_facts: tuple[str, ...]
    current_goals: tuple[str, ...]
    decisions: tuple[str, ...]
    open_loops: tuple[str, ...]
    assistant_suggestions: tuple[str, ...]
    corrections: tuple[str, ...]
    companies: tuple[str, ...]
    roles: tuple[str, ...]

@dataclass(frozen=True)
class DialogueTurn:
    turn_id: UUID
    messages: tuple[MessageRecord, MessageRecord]
    token_estimate: int
    covered_through_message_id: UUID

@dataclass(frozen=True)
class PreparedConversationMemory:
    rendered_summary: str | None
    recent_messages: tuple[MessageRecord, ...]
    estimated_tokens: int
    summary_version: int | None

    @classmethod
    def empty(cls) -> "PreparedConversationMemory":
        return cls(None, (), 0, None)
```

每个数组最多 24 项、单项最多 300 字符，去除空项和精确重复；序列化使用 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))` 并再次校验总长度。

- [ ] **Step 4: 写预算选择失败测试**

```python
def make_turn(index: int, tokens: int) -> DialogueTurn:
    turn_id = UUID(int=200 + index)
    user = make_message(MessageRole.USER, f"问题{index}", turn_id=turn_id, position=index * 2)
    assistant = make_message(
        MessageRole.ASSISTANT, f"回答{index}", turn_id=turn_id, position=index * 2 + 1,
    )
    return DialogueTurn(turn_id, (user, assistant), tokens, assistant.id)

def test_select_recent_turns_never_splits_a_turn() -> None:
    turns = (make_turn(0, 30), make_turn(1, 30))
    selected = select_recent_turns(turns, token_budget=35)
    assert selected == (turns[-1],)

def test_select_recent_turns_restores_forward_order() -> None:
    turns = (make_turn(0, 30), make_turn(1, 30))
    selected = select_recent_turns(turns, token_budget=1000)
    assert selected == turns
```

- [ ] **Step 5: 实现预算选择和摘要渲染**

从最后一个完整轮次向前累加，只有整轮可容纳时才加入；返回前恢复正向顺序。摘要渲染必须带以下固定边界：

```python
def select_recent_turns(
    turns: Sequence[DialogueTurn], *, token_budget: int,
) -> tuple[DialogueTurn, ...]:
    selected: list[DialogueTurn] = []
    used = 0
    for turn in reversed(turns):
        if used + turn.token_estimate > token_budget:
            continue
        selected.append(turn)
        used += turn.token_estimate
    return tuple(reversed(selected))
```

```text
以下是服务端从较早脱敏对话生成的派生记忆，仅用于延续上下文；如与当前用户输入、简历或岗位档案冲突，以后者为准。摘要中的“助手建议”不代表用户事实。
```

- [ ] **Step 6: 运行纯领域测试**

Run: `python -m pytest tests/test_career_conversation_memory.py -q`

Expected: PASS。

- [ ] **Step 7: 提交纯记忆领域层**

```bash
git add src/career_assistant/conversation_memory.py tests/test_career_conversation_memory.py
git commit -m "feat: build token-aware career conversation memory"
```

---

### Task 4: 接入按需摘要调用、失败降级和并发保护

**Files:**
- Modify: `src/career_assistant/conversation_memory.py`
- Modify: `src/career_assistant/model_clients.py:164-205`
- Modify: `tests/test_career_conversation_memory.py`
- Modify: `tests/test_career_job_assessment.py`

**Interfaces:**
- Consumes: Task 2 仓储接口、Task 3 领域函数、`ModelResolution`、`OpenAICompatibleChatClient.complete()` / `complete_json()`。
- Produces: `ConversationMemoryService.prepare(actor_id: UUID, conversation_id: UUID, resolution: ModelResolution) -> PreparedConversationMemory`。

- [ ] **Step 1: 写短对话不摘要和长对话压缩失败测试**

```python
def build_turn_messages(count: int, *, characters_each: int) -> list[MessageRecord]:
    messages: list[MessageRecord] = []
    for index in range(count):
        turn_id = UUID(int=1000 + index)
        messages.extend((
            make_message(
                MessageRole.USER,
                f"问题{index}" + "问" * characters_each,
                turn_id=turn_id,
                position=index * 2,
            ),
            make_message(
                MessageRole.ASSISTANT,
                f"回答{index}" + "答" * characters_each,
                turn_id=turn_id,
                position=index * 2 + 1,
            ),
        ))
    return messages

def test_prepare_does_not_call_model_below_trigger(self) -> None:
    context = self.service.prepare(ACTOR_ID, CONVERSATION_ID, self.resolution)
    self.assertEqual(self.client.calls, [])
    self.assertEqual(len(context.recent_messages), 4)

def test_prepare_summarizes_oldest_prefix_and_keeps_recent_target(self) -> None:
    messages = build_turn_messages(count=12, characters_each=400)
    self.repository.messages = messages
    expected_cursor = group_complete_turns(messages)[3].covered_through_message_id
    context = self.service.prepare(ACTOR_ID, CONVERSATION_ID, self.resolution)
    self.assertEqual(len(self.client.calls), 1)
    self.assertEqual(len(context.recent_messages), 16)
    self.assertEqual(self.repository.saved_covered_id, expected_cursor)
```

- [ ] **Step 2: 实现压缩前缀选择和摘要 Prompt**

当游标后完整轮次总 Token 达到阈值时，从最旧轮次开始选择压缩前缀，至少留下 `recent_turns_target` 个轮次。摘要 Prompt 同时包含上一版有效摘要和带角色标签的待压缩消息：

```python
summary_messages = [
    ChatMessage(role="system", content=SUMMARY_SYSTEM_PROMPT),
    ChatMessage(
        role="user",
        content=json.dumps({
            "previous_summary": previous_payload,
            "dialogue": [
                {"role": message.role.value, "content": message.content_text}
                for turn in prefix for message in turn.messages
            ],
        }, ensure_ascii=False),
    ),
]
```

`SUMMARY_SYSTEM_PROMPT` 必须逐项声明 Schema，并明确只有 user 内容能进入用户事实、目标、决定和纠正，assistant 内容只能进入建议。

- [ ] **Step 3: 让 JSON 调用 Trace 支持记忆 operation**

```python
def complete_json(
    self,
    profile: ModelProfileRecord,
    credential_env_name: str | None,
    messages: list[ChatMessage],
    *,
    api_key: str | None = None,
    operation: str = "job_assessment",
) -> str:
```

`run_name` 使用 `f"career.{operation}.completion"`，metadata 写入同一 operation，tags 使用 `("career", operation.replace("_", "-"), "privacy:metadata-only")`。岗位分析现有调用不传 operation，行为与测试保持不变。

- [ ] **Step 4: 实现结构化与纯文本模型兼容调用**

如果档案声明 `ModelCapability.STRUCTURED_OUTPUT`，调用：

```python
raw = self._client.complete_json(
    resolution.profile,
    resolution.credential_env_name,
    summary_messages,
    api_key=resolution.credential,
    operation="conversation_memory",
)
```

否则调用普通 `complete()`，只接受整个响应是 JSON Object 或单个 ```json fenced block；不得从任意自然语言中截取第一个大括号片段。两条路径都经过同一个 `validate_summary()`。

```python
_JSON_FENCE = re.compile(r"\A\s*```json\s*(\{.*\})\s*```\s*\Z", re.DOTALL)

def parse_summary_response(raw: str) -> dict[str, object]:
    candidate = raw.strip()
    match = _JSON_FENCE.fullmatch(candidate)
    if match:
        candidate = match.group(1)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("会话摘要必须是 JSON Object")
    return parsed
```

- [ ] **Step 5: 写失败和并发降级测试**

```python
def test_invalid_summary_does_not_advance_cursor(self) -> None:
    self.client.response = "不是 JSON"
    context = self.service.prepare(ACTOR_ID, CONVERSATION_ID, self.resolution)
    self.assertEqual(self.repository.save_calls, [])
    self.assertTrue(context.recent_messages)

def test_compare_and_swap_loss_uses_winning_summary(self) -> None:
    self.repository.save_result = None
    self.repository.summary_after_conflict = SessionSummaryRecord(
        id=UUID(int=700),
        conversation_id=CONVERSATION_ID,
        summary_text=json.dumps(valid_payload(), ensure_ascii=False),
        summary_version=3,
        summary_schema_version=SUMMARY_SCHEMA_VERSION,
        covered_through_message_id=UUID(int=600),
        contains_sensitive_data=False,
        created_at=NOW,
        updated_at=NOW,
    )
    context = self.service.prepare(ACTOR_ID, CONVERSATION_ID, self.resolution)
    self.assertIn("新摘要", context.rendered_summary)

def test_repository_failure_degrades_to_empty_memory(self) -> None:
    self.repository.raise_on_read = RuntimeError("database unavailable")
    context = self.service.prepare(ACTOR_ID, CONVERSATION_ID, self.resolution)
    self.assertIsNone(context.rendered_summary)
    self.assertEqual(context.recent_messages, ())
```

- [ ] **Step 6: 实现边界内异常捕获和重读**

只在记忆服务边界捕获 `ModelInvocationError`、`ValueError` 和数据库访问异常，记录 `LOGGER.warning()` 的安全错误类别。摘要 CAS 成功或失败后都重新读取摘要和游标后消息，再执行最终预算选择；不把模型原始输出写入日志。

- [ ] **Step 7: 运行摘要服务与岗位分析回归测试**

Run: `python -m pytest tests/test_career_conversation_memory.py tests/test_career_job_assessment.py -q`

Expected: PASS。

- [ ] **Step 8: 提交摘要服务**

```bash
git add src/career_assistant/conversation_memory.py src/career_assistant/model_clients.py tests/test_career_conversation_memory.py tests/test_career_job_assessment.py
git commit -m "feat: consolidate career conversation memory"
```

---

### Task 5: 将同一记忆上下文接入流式与非流式回复

**Files:**
- Modify: `src/career_assistant/response_runner.py:45-80`
- Modify: `src/career_assistant/response_runner.py:110-205`
- Modify: `src/career_assistant/response_runner.py:473-617`
- Modify: `src/career_assistant/web/router.py:480-625`
- Modify: `tests/test_career_skill_runtime.py`
- Create: `tests/test_career_response_memory.py`

**Interfaces:**
- Consumes: `ConversationMemoryService.prepare()`、`PreparedConversationMemory`、已有 `ModelResolution`。
- Produces: `CareerResponseRunner(..., conversation_memory_service=...)`；`_build_prompt(active_turn, context, memory_context=None)`。

- [ ] **Step 1: 写 Prompt 顺序和去重失败测试**

```python
def test_prompt_places_summary_before_complete_history_and_current_user(self) -> None:
    prompt = self.runner._build_prompt(
        self.active_turn,
        self.model_context,
        PreparedConversationMemory(
            rendered_summary="派生记忆",
            recent_messages=(self.old_user, self.old_assistant),
            estimated_tokens=30,
            summary_version=2,
        ),
    )
    self.assertEqual([item.role for item in prompt[-4:]], [
        "system", "user", "assistant", "user",
    ])
    self.assertIn("派生记忆", prompt[-4].content)
    self.assertEqual(prompt[-3].content, self.old_user.content_text)
    self.assertEqual(prompt[-2].content, self.old_assistant.content_text)
    self.assertEqual(
        sum("本轮用户输入" in item.content for item in prompt),
        1,
    )
```

- [ ] **Step 2: 删除固定 `limit=6` 历史读取**

`_build_prompt()` 不再访问 repository，也不再执行每条 1600 字符截断。它只按顺序加入 `memory_context.rendered_summary` 和 `memory_context.recent_messages`；`memory_context=None` 用于无记忆服务的隔离测试，行为等同空记忆。

```python
memory = memory_context or PreparedConversationMemory.empty()
if memory.rendered_summary:
    messages.append(ChatMessage(role="system", content=memory.rendered_summary))
for history_message in memory.recent_messages:
    messages.append(ChatMessage(
        role=history_message.role.value,
        content=history_message.content_text,
    ))
```

- [ ] **Step 3: 写记忆失败不阻断回复测试**

```python
def test_run_continues_when_memory_service_returns_empty_context(self) -> None:
    self.memory_service.prepare.return_value = PreparedConversationMemory(
        rendered_summary=None,
        recent_messages=(),
        estimated_tokens=0,
        summary_version=None,
    )
    result = self.runner.run(self.inbound, self.intake_result)
    self.assertEqual(result.assistant_message.content_text, "正常回答")

def test_stream_and_run_both_prepare_memory_once(self) -> None:
    self.runner.run(self.inbound, self.intake_result)
    self.assertEqual(self.memory_service.prepare.call_count, 1)
    self.memory_service.prepare.reset_mock()
    list(self.runner.stream(self.inbound, self.intake_result))
    self.assertEqual(self.memory_service.prepare.call_count, 1)
```

- [ ] **Step 4: 接入 Runner 构造与两条调用路径**

```python
def __init__(..., conversation_memory_service: ConversationMemoryService | None = None, ...):
    self._conversation_memory_service = conversation_memory_service

def _prepare_memory(self, active_turn, resolution) -> PreparedConversationMemory:
    if self._conversation_memory_service is None:
        return PreparedConversationMemory.empty()
    return self._conversation_memory_service.prepare(
        active_turn.conversation.actor_id,
        active_turn.conversation.id,
        resolution,
    )
```

`run()` 和 `stream()` 都在模型已解析、Prompt 构建前调用一次 `_prepare_memory()`。记忆服务内部已经完成降级，Runner 不捕获或重复调用摘要。

- [ ] **Step 5: 在服务装配处创建单例记忆服务**

```python
conversation_memory_service = ConversationMemoryService(
    repository=conversation_repository,
    model_client=model_connection_client,
    settings=response_generation_settings.memory,
)
```

把该实例注入 `CareerResponseRunner`。不创建新的 HTTP 客户端或数据库连接。

- [ ] **Step 6: 更新既有 Skill Prompt 测试**

`tests/test_career_skill_runtime.py` 调用 `_build_prompt()` 时显式传 `PreparedConversationMemory.empty()`，继续断言 Skill system 消息和当前用户消息顺序，不为测试 FakeRepository 增加无关记忆方法。

- [ ] **Step 7: 运行 Runner 回归测试**

Run: `python -m pytest tests/test_career_response_memory.py tests/test_career_skill_runtime.py tests/test_career_skill_tools.py tests/test_career_history_fast_path.py -q`

Expected: PASS。

- [ ] **Step 8: 提交回复链路接入**

```bash
git add src/career_assistant/response_runner.py src/career_assistant/web/router.py tests/test_career_response_memory.py tests/test_career_skill_runtime.py
git commit -m "feat: inject long conversation memory into career replies"
```

---

### Task 6: 增加 100/300 轮回忆评测和完整回归

**Files:**
- Create: `tests/test_career_long_conversation_memory.py`
- Modify: `src/career_assistant/conversation_memory.py`

**Interfaces:**
- Consumes: `ConversationMemoryService` 公共接口和可控 Fake Repository/Model Client。
- Produces: 可重复的 100/300 轮长对话回忆、纠正优先和预算不越界回归测试。

- [ ] **Step 1: 写 100 轮目标延续测试**

```python
NOW = datetime(2026, 8, 22, tzinfo=UTC)
ACTOR_ID = UUID(int=1)
CONVERSATION_ID = UUID(int=2)
SETTINGS = ConversationMemorySettings(12000, 8000, 8, 8000)
RESOLUTION = SimpleNamespace(
    profile=SimpleNamespace(capabilities=frozenset({ModelCapability.TEXT})),
    credential_env_name=None,
    credential="test-key",
)

class FakeMemoryRepository:
    def __init__(self, messages: list[MessageRecord]) -> None:
        self.messages = messages
        self.summary: SessionSummaryRecord | None = None

    def get_session_summary(self, _actor_id, _conversation_id):
        return self.summary

    def list_completed_dialogue_messages(
        self, _actor_id, _conversation_id, *, after_message_id=None,
    ):
        if after_message_id is None:
            return list(self.messages)
        cursor = next(
            index for index, item in enumerate(self.messages)
            if item.id == after_message_id
        )
        return list(self.messages[cursor + 1:])

    def save_summary_if_current(
        self, _actor_id, _conversation_id, *, summary_text,
        covered_through_message_id, summary_schema_version,
        expected_summary_version, expected_covered_through_message_id,
    ):
        current_version = self.summary.summary_version if self.summary else 0
        current_cursor = self.summary.covered_through_message_id if self.summary else None
        if (current_version, current_cursor) != (
            expected_summary_version, expected_covered_through_message_id,
        ):
            return None
        self.summary = SessionSummaryRecord(
            id=UUID(int=3), conversation_id=CONVERSATION_ID,
            summary_text=summary_text, summary_version=current_version + 1,
            summary_schema_version=summary_schema_version,
            covered_through_message_id=covered_through_message_id,
            contains_sensitive_data=False, created_at=NOW, updated_at=NOW,
        )
        return self.summary

def build_service(repository, client) -> ConversationMemoryService:
    return ConversationMemoryService(
        repository=repository,
        model_client=client,
        settings=SETTINGS,
    )

def summary_payload(*, goal: str, correction: str | None = None) -> str:
    payload = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "user_stated_facts": [],
        "current_goals": [goal],
        "decisions": [],
        "open_loops": [],
        "assistant_suggestions": [],
        "corrections": [correction] if correction else [],
        "companies": [],
        "roles": [],
    }
    return json.dumps(payload, ensure_ascii=False)

class FakeSummaryClient:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, *_args, **_kwargs) -> str:
        return self.response

    def complete_json(self, *_args, **_kwargs) -> str:
        return self.response

def build_scripted_turns(count: int, facts: dict[int, str]) -> list[MessageRecord]:
    messages: list[MessageRecord] = []
    base = datetime(2026, 8, 22, tzinfo=UTC)
    for index in range(count):
        turn_id = UUID(int=10000 + index)
        user_text = facts.get(index, f"第 {index} 轮普通问题") + "背景" * 40
        messages.extend((
            MessageRecord(
                id=UUID(int=index * 2 + 1), conversation_id=CONVERSATION_ID,
                turn_id=turn_id, role=MessageRole.USER, content_text=user_text,
                is_redacted=True, created_at=base + timedelta(seconds=index * 2),
            ),
            MessageRecord(
                id=UUID(int=index * 2 + 2), conversation_id=CONVERSATION_ID,
                turn_id=turn_id, role=MessageRole.ASSISTANT,
                content_text=f"第 {index} 轮普通回答" + "说明" * 40, is_redacted=True,
                created_at=base + timedelta(seconds=index * 2 + 1),
            ),
        ))
    return messages

def assert_complete_pairs(messages: tuple[MessageRecord, ...]) -> None:
    assert len(messages) % 2 == 0
    for index in range(0, len(messages), 2):
        assert messages[index].role is MessageRole.USER
        assert messages[index + 1].role is MessageRole.ASSISTANT
        assert messages[index].turn_id == messages[index + 1].turn_id

def test_one_hundred_turns_keep_early_goal_in_summary(self) -> None:
    repository = FakeMemoryRepository(build_scripted_turns(
        count=100,
        facts={3: "目标是上海后端开发"},
    ))
    service = build_service(
        repository,
        FakeSummaryClient(summary_payload(goal="目标是上海后端开发")),
    )
    context = service.prepare(ACTOR_ID, CONVERSATION_ID, RESOLUTION)
    self.assertIn("目标是上海后端开发", context.rendered_summary)
    self.assertLessEqual(context.estimated_tokens, SETTINGS.history_token_budget)
    assert_complete_pairs(context.recent_messages)
```

- [ ] **Step 2: 写 300 轮纠正优先测试**

```python
def test_three_hundred_turns_keep_correction_and_drop_old_goal(self) -> None:
    repository = FakeMemoryRepository(build_scripted_turns(
        count=300,
        facts={3: "目标是上海后端开发", 170: "改为杭州 AI 应用开发"},
    ))
    service = build_service(
        repository,
        FakeSummaryClient(summary_payload(
            goal="改为杭州 AI 应用开发",
            correction="目标从上海后端开发改为杭州 AI 应用开发",
        )),
    )
    context = service.prepare(ACTOR_ID, CONVERSATION_ID, RESOLUTION)
    self.assertIn("改为杭州 AI 应用开发", context.rendered_summary)
    self.assertNotIn("目标是上海后端开发", context.rendered_summary)
    self.assertLessEqual(context.estimated_tokens, SETTINGS.history_token_budget)
```

- [ ] **Step 3: 运行长对话和全量后端测试**

Run: `python -m pytest tests/test_career_long_conversation_memory.py -q`

Expected: PASS，100/300 轮用例均不访问真实 Provider。

Run: `python -m pytest -q`

Expected: PASS；若仓库存在与本功能无关的既有失败，记录准确测试名和失败证据，不修改无关模块。

- [ ] **Step 4: 执行语法和差异检查**

Run: `python -m compileall src/career_assistant tests migrations/versions/20260822_17_conversation_memory.py`

Expected: PASS。

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 5: 提交长对话评测**

```bash
git add src/career_assistant/conversation_memory.py tests/test_career_long_conversation_memory.py
git commit -m "test: cover long career conversations"
```

---

### Task 7: 更新模块文档并记录验证边界

**Files:**
- Modify: `docs/career_assistant_module.md`

**Interfaces:**
- Consumes: 已实现的配置名、调用链、Schema 版本、迁移版本和最终测试结果。
- Produces: 与实现同步的长对话记忆维护文档。

- [ ] **Step 1: 检查文档当前未提交差异并选择非冲突插入点**

Run: `git diff -- docs/career_assistant_module.md`

Expected: 明确识别用户已有修改；只在现有求职助手架构或持久化章节新增“长对话记忆”小节，不覆盖、不重排已有改动。

- [ ] **Step 2: 写入实现事实**

新增小节必须准确记录：

```text
- 设计目标：固定 Token 预算下延续单会话长对话。
- 调用链：解析模型 -> prepare memory -> 可选压缩 -> 组装 Prompt -> 生成回复。
- 数据：messages 是事实源；session_summaries 是带游标、版本和 Schema 的派生快照。
- 配置：history_token_budget、summary_trigger_tokens、recent_turns_target、summary_max_characters。
- 降级：摘要模型、Schema、CAS 或数据库失败不阻断回复，不推进游标。
- 依赖：复用 PostgreSQL、现有 Chat Client 和脱敏消息，不依赖 pgvector。
- 验证：列出实际执行的测试命令和结果。
- 边界：不支持跨会话自由事实、历史向量召回和逐字无限记忆。
```

- [ ] **Step 3: 复核文档与配置/接口一致**

Run: `rg -n "history_token_budget|summary_trigger_tokens|career-conversation-summary-v1|ConversationMemoryService" docs/career_assistant_module.md config/career_assistant.yaml src/career_assistant`

Expected: 文档名称与实现完全一致。

- [ ] **Step 4: 最终状态检查并提交文档的新增块**

Run: `git diff --check -- docs/career_assistant_module.md`

Expected: PASS。

如果该文件仍包含用户此前的未提交改动，使用交互式暂存只选择本功能新增块；不能把用户原有块带入提交。

```bash
git add -p docs/career_assistant_module.md
git commit -m "docs: document career conversation memory"
```

---

## 完成判定

- `session_summaries` 有覆盖游标、Schema 版本和 CAS 更新。
- Prompt 不再读取固定 6 条消息或截断为 1600 字符。
- 短对话零摘要调用，长对话只压缩最旧完整前缀。
- 摘要与最近原文合计不超过配置预算。
- 流式和非流式路径行为一致。
- 100/300 轮测试证明目标纠正优先且原始历史不被删除。
- 所有相关测试、全量测试、compileall、Alembic head 和 `git diff --check` 均完成并记录结果。
