# Career Turn PostgreSQL Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将求职助手聊天 Turn 从进程内排队升级为 PostgreSQL 持久队列和独立 Worker，使同会话严格 FIFO、不同会话受控并发，并能在 API 断线或 Worker 重启后恢复。

**Architecture:** Career API 只校验、解析附件并原子写入 Turn、完整 payload 和首个事件；独立 Worker 使用 `FOR UPDATE SKIP LOCKED` 与数据库执行槽位领取任务，通过租约和心跳维持所有权。Worker 复用现有 Intake Graph、Memory、RAG、LLM 和 Tool 链路，API 通过可恢复 SSE 读取持久化事件。

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy 2、PostgreSQL 16、Alembic、asyncio、Vue 3、SSE、unittest、Node test runner

**Spec:** `docs/superpowers/specs/2026-08-23-career-turn-postgres-worker-design.md`

## Global Constraints

- PostgreSQL 是跨 API/Worker 的唯一权威队列，不增加 Redis、Celery 或 Kafka。
- 同一 Conversation 同时最多运行一个 Turn，顺序由 `queue_sequence` 确定。
- 不同 Conversation 在 `CAREER_AGENT_GLOBAL_CONCURRENCY` 允许时并发。
- 完整输入和完整附件解析文本持久化，不进行脱敏；原始附件二进制不入库，解析结束后删除。
- 已提交 Turn 不依赖原始 HTTP/SSE 连接继续执行。
- Worker 崩溃后只自动重试尚未进入非幂等 Tool 的 Turn。
- 任意 Tool 不宣称 exactly-once。
- 保留现有聊天入口兼容层，新增代码按职责拆分，不继续扩大 `web/router.py` 的队列逻辑。

---

### Task 1: 数据库队列表结构

**Files:**
- Create: `migrations/versions/20260823_17_career_turn_worker_queue.py`
- Test: `tests/test_career_turn_job_repository.py`

**Interfaces:**
- Produces: `agent_turns.queue_sequence/available_at/attempt_count/lease_owner/lease_expires_at/heartbeat_at/cancel_requested_at`
- Produces: `career_assistant.agent_turn_payloads`
- Produces: `career_assistant.agent_turn_events`
- Produces: `career_assistant.agent_execution_slots`

- [ ] **Step 1: 编写迁移结构测试**

```python
def test_queue_migration_contains_required_tables_and_indexes(self):
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    for token in (
        "queue_sequence", "agent_turn_payloads", "agent_turn_events",
        "agent_execution_slots", "cancel_requested_at",
    ):
        self.assertIn(token, source)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m unittest tests.test_career_turn_job_repository -v`
Expected: FAIL，迁移文件不存在。

- [ ] **Step 3: 编写 Alembic 迁移**

```python
revision = "20260823_17"
down_revision = "20260821_16"

def upgrade() -> None:
    op.execute("ALTER TABLE career_assistant.agent_turns ADD COLUMN queue_sequence BIGINT GENERATED ALWAYS AS IDENTITY")
    op.execute("""
        CREATE TABLE career_assistant.agent_turn_payloads (
            turn_id UUID PRIMARY KEY REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
            input_text TEXT NOT NULL,
            effective_text TEXT NOT NULL,
            job_url TEXT,
            attachment_payloads_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            request_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE career_assistant.agent_turn_events (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            turn_id UUID NOT NULL REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE career_assistant.agent_execution_slots (
            slot_number INTEGER PRIMARY KEY CHECK (slot_number > 0),
            turn_id UUID REFERENCES career_assistant.agent_turns(id) ON DELETE SET NULL,
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
```

- [ ] **Step 4: 增加领取索引和同会话顺序索引**

```sql
CREATE INDEX idx_career_agent_turns_queue_claim
ON career_assistant.agent_turns (status, available_at, queue_sequence);
CREATE INDEX idx_career_agent_turns_conversation_queue
ON career_assistant.agent_turns (conversation_id, queue_sequence);
```

- [ ] **Step 5: 运行迁移测试和 Alembic 语法检查**

Run: `.venv\Scripts\python.exe -m unittest tests.test_career_turn_job_repository -v`
Expected: PASS。

Run: `.venv\Scripts\python.exe -m compileall -q migrations/versions`
Expected: exit 0。

- [ ] **Step 6: 提交**

```powershell
git add migrations/versions/20260823_17_career_turn_worker_queue.py tests/test_career_turn_job_repository.py
git commit -m "feat: add durable career turn queue schema"
```

### Task 2: Turn payload、事件与领取仓储

**Files:**
- Create: `src/career_assistant/persistence/turn_job_repository.py`
- Modify: `src/career_assistant/persistence/records.py`
- Modify: `src/career_assistant/persistence/__init__.py`
- Modify: `tests/test_career_turn_job_repository.py`

**Interfaces:**
- Produces: `TurnPayloadRecord`、`TurnEventRecord`、`ClaimedTurnRecord`
- Produces: `CareerTurnJobRepository.enqueue(turn_id: UUID, conversation_id: UUID, actor_id: UUID, model_selection: ModelSelectionRequest, input_kind_codes: frozenset[str], payload: TurnPayloadRecord) -> AgentTurnRecord`
- Produces: `CareerTurnJobRepository.claim_next(worker_id, lease_seconds, global_limit) -> ClaimedTurnRecord | None`
- Produces: `heartbeat(turn_id, worker_id, lease_seconds) -> bool`
- Produces: `append_event(turn_id, event_type, payload) -> TurnEventRecord`
- Produces: `list_events(actor_id, turn_id, after_id) -> tuple[TurnEventRecord, ...]`
- Produces: `release_claim(turn_id, worker_id) -> bool`
- Produces: `recover_expired(global_limit) -> int`

- [ ] **Step 1: 编写 payload 原子入队失败测试**

```python
turn = repository.enqueue(
    turn_id=turn_id,
    conversation_id=conversation_id,
    actor_id=actor_id,
    model_selection=selection,
    input_kind_codes=frozenset({"text"}),
    payload=payload,
)
self.assertEqual(turn.status, AgentTurnStatus.QUEUED)
self.assertIn("INSERT INTO career_assistant.agent_turn_payloads", executed_sql)
self.assertIn("INSERT INTO career_assistant.agent_turn_events", executed_sql)
```

- [ ] **Step 2: 编写领取约束失败测试**

```python
claim = repository.claim_next("worker-a", lease_seconds=60, global_limit=4)
self.assertEqual(claim.worker_id, "worker-a")
self.assertIn("FOR UPDATE SKIP LOCKED", executed_sql)
self.assertIn("older.queue_sequence < turn.queue_sequence", executed_sql)
```

- [ ] **Step 3: 实现不可变 records 和 JSON 序列化边界**

```python
@dataclass(frozen=True)
class TurnPayloadRecord:
    turn_id: UUID
    input_text: str
    effective_text: str
    job_url: str | None
    attachment_payloads: tuple[dict[str, object], ...]
    request_metadata: dict[str, object]
```

- [ ] **Step 4: 实现原子 enqueue 和用户范围事件读取**

```python
def enqueue(self, *, turn_id, conversation_id, actor_id, model_selection,
            input_kind_codes, payload) -> AgentTurnRecord:
    with self._database.transaction() as connection:
        turn = self._insert_turn(
            connection,
            turn_id=turn_id,
            conversation_id=conversation_id,
            actor_id=actor_id,
            model_selection=model_selection,
            input_kind_codes=input_kind_codes,
        )
        self._insert_payload(connection, payload)
        self._insert_event(connection, turn_id, "queued", {"state": "queued"})
    return turn
```

- [ ] **Step 5: 实现槽位与 Turn 同事务领取、心跳、释放和过期恢复**

```sql
SELECT slot_number
FROM career_assistant.agent_execution_slots
WHERE slot_number <= :global_limit
  AND (turn_id IS NULL OR lease_expires_at < NOW())
ORDER BY slot_number
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

- [ ] **Step 6: 运行仓储测试**

Run: `.venv\Scripts\python.exe -m unittest tests.test_career_turn_job_repository -v`
Expected: payload、FIFO SQL、槽位、心跳、恢复和访问隔离测试全部 PASS。

- [ ] **Step 7: 提交**

```powershell
git add src/career_assistant/persistence tests/test_career_turn_job_repository.py
git commit -m "feat: add career turn job repository"
```

### Task 3: 完整附件内容的可持久执行输入

**Files:**
- Create: `src/career_assistant/turn_payloads.py`
- Modify: `src/career_assistant/contracts.py`
- Modify: `src/career_assistant/intake_graph.py`
- Modify: `src/career_assistant/agent_loop.py`
- Test: `tests/test_career_turn_payloads.py`

**Interfaces:**
- Produces: `PreparedAttachment(kind, media_type, extracted_text, page_count, image_width, image_height, metadata)`
- Produces: `prepare_turn_payload(inbound_message, parsed_attachments) -> TurnPayloadRecord`
- Produces: `restore_turn_input(claimed_turn) -> PreparedTurnInput`
- Produces: `CareerAgentLoop.activate_turn(actor_id, turn_id) -> ActiveAgentTurn`
- Produces: `CareerIntakeGraph.run_prepared(inbound_message, active_turn, parsed_attachments) -> IntakeGraphResult`

- [ ] **Step 1: 编写不脱敏往返测试**

```python
source = "张三在字节跳动负责推荐系统，手机号 13800138000"
restored = restore_turn_input(prepare_turn_payload(message, (parsed(source),)))
self.assertEqual(restored.parsed_attachments[0].extracted_text, source)
```

- [ ] **Step 2: 编写已领取 Turn 不重复创建测试**

```python
active = agent_loop.activate_turn(actor_id, turn_id)
self.assertEqual(active.turn.status, AgentTurnStatus.RUNNING)
self.assertNotIn("INSERT INTO career_assistant.agent_turns", executed_sql)
```

- [ ] **Step 3: 实现 payload 转换，完整保存文本和解析元数据**

```python
def prepared_attachment_from_parsed(item: ParsedAttachment) -> dict[str, object]:
    return {
        "kind": item.kind.value,
        "media_type": item.media_type,
        "extracted_text": item.extracted_text,
        "page_count": item.page_count,
        "image_width": item.image_width,
        "image_height": item.image_height,
    }
```

- [ ] **Step 4: 为 Intake Graph 增加 prepared 执行入口**

```python
def run_prepared(self, inbound_message, active_turn, parsed_attachments):
    return self._consume_stream(
        self.stream_prepared(inbound_message, active_turn, parsed_attachments)
    )
```

prepared 路径跳过附件文件读取，但继续执行上下文、Memory 写入、RAG、LLM 所需的后续节点；`SensitiveDataRedactor(enabled=False)` 使完整细节进入用户消息和模型上下文，`is_redacted=False`。

- [ ] **Step 5: 运行 payload 与现有 Intake Graph 测试**

Run: `.venv\Scripts\python.exe -m unittest tests.test_career_turn_payloads tests.test_career_skill_runtime tests.test_career_skill_tools -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add src/career_assistant/turn_payloads.py src/career_assistant/contracts.py src/career_assistant/intake_graph.py src/career_assistant/agent_loop.py tests/test_career_turn_payloads.py
git commit -m "feat: persist complete career turn inputs"
```

### Task 4: 独立 asyncio Agent Worker

**Files:**
- Create: `src/career_assistant/turn_worker.py`
- Create: `scripts/run_career_agent_worker.py`
- Modify: `src/career_assistant/settings.py`
- Test: `tests/test_career_turn_worker.py`

**Interfaces:**
- Produces: `CareerTurnWorkerSettings`
- Produces: `CareerTurnWorker.run_forever() -> None`
- Consumes: `CareerTurnJobRepository.claim_next/heartbeat/release_claim`
- Consumes: `CareerIntakeGraph.run_prepared`、`CareerResponseRunner.stream`

- [ ] **Step 1: 编写同会话串行和跨会话并发 Worker 测试**

```python
await worker.run_until_idle()
self.assertEqual(max_running_by_conversation[conversation_id], 1)
self.assertGreaterEqual(max_global_running, 2)
```

- [ ] **Step 2: 编写心跳与异常收口测试**

```python
await worker.execute_claim(claim)
self.assertGreaterEqual(repository.heartbeat_calls, 1)
self.assertTrue(repository.released_claims)
self.assertEqual(repository.events[-1].event_type, "error")
```

- [ ] **Step 3: 实现 Worker 本地调度循环**

```python
async def run_forever(self) -> None:
    while not self._stopping.is_set():
        await self._fill_available_capacity()
        await asyncio.sleep(self._settings.poll_seconds)
```

- [ ] **Step 4: 实现执行、事件批量写入和心跳 Task**

```python
async with asyncio.TaskGroup() as group:
    group.create_task(self._heartbeat(claim))
    group.create_task(self._execute_agent(claim))
```

模型 delta 按字符数或时间窗口合并后写 `agent_turn_events`；最终 `done/error` 事件必须持久化。线程中的同步 Agent 未结束前不得释放槽位。

- [ ] **Step 5: 实现设置加载和 Worker CLI 入口**

```python
if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 6: 运行 Worker 测试和语法检查**

Run: `.venv\Scripts\python.exe -m unittest tests.test_career_turn_worker -v`
Expected: PASS。

Run: `.venv\Scripts\python.exe -m compileall -q src/career_assistant scripts/run_career_agent_worker.py`
Expected: exit 0。

- [ ] **Step 7: 提交**

```powershell
git add src/career_assistant/turn_worker.py src/career_assistant/settings.py scripts/run_career_agent_worker.py tests/test_career_turn_worker.py
git commit -m "feat: add durable career agent worker"
```

### Task 5: 入队、状态、事件和取消 API

**Files:**
- Create: `src/career_assistant/web/turn_router.py`
- Modify: `src/career_assistant/web/router.py`
- Test: `tests/test_career_turn_api.py`

**Interfaces:**
- Produces: `POST /career/conversations/{conversation_id}/turns`
- Produces: `POST /career/conversations/{conversation_id}/turns-with-materials`
- Produces: `GET /career/turns/{turn_id}`
- Produces: `GET /career/turns/{turn_id}/events`
- Produces: `POST /career/turns/{turn_id}/cancel`
- Consumes: `CareerTurnJobRepository.enqueue/list_events/request_cancel`

- [ ] **Step 1: 编写文本入队返回 202 测试**

```python
response = client.post(f"/career/conversations/{conversation_id}/turns", json={"text": "继续优化项目经历"})
self.assertEqual(response.status_code, 202)
self.assertEqual(response.json()["turn"]["status"], "queued")
```

- [ ] **Step 2: 编写附件完整解析与清理测试**

```python
response = client.post(url, files={"resume_file": ("resume.pdf", content, "application/pdf")})
self.assertEqual(stored_payload.attachment_payloads[0]["extracted_text"], full_text)
self.assertFalse(temporary_path.exists())
```

- [ ] **Step 3: 实现 enqueue service 和两个提交接口**

```python
parsed = tuple(await asyncio.to_thread(services.attachment_parser.parse, item) for item in attachments)
payload = prepare_turn_payload(inbound_message, parsed)
turn = services.turn_job_repository.enqueue(
    turn_id=inbound_message.turn_id,
    conversation_id=inbound_message.conversation_id,
    actor_id=inbound_message.actor_id,
    model_selection=inbound_message.model_selection,
    input_kind_codes=collect_input_kind_codes(inbound_message, parsed),
    payload=payload,
)
```

附件清理放在 `finally`；事务提交前解析失败不创建 Turn。

- [ ] **Step 4: 实现状态、SSE 断点续传和取消接口**

```python
after_id = int(request.headers.get("Last-Event-ID", "0"))
events = repository.list_events(actor.actor_id, turn_id, after_id=after_id)
```

- [ ] **Step 5: 让既有四个聊天入口通过兼容层调用 enqueue service**

流式兼容入口先入队，再转发该 Turn 的持久事件；非流式兼容入口等待终态并返回既有响应字段。新 WebUI 直接使用新接口，不依赖兼容等待。

- [ ] **Step 6: 运行 API 与附件测试**

Run: `.venv\Scripts\python.exe -m unittest tests.test_career_turn_api tests.test_career_turn_queue -v`
Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add src/career_assistant/web/turn_router.py src/career_assistant/web/router.py tests/test_career_turn_api.py
git commit -m "feat: expose durable career turn APIs"
```

### Task 6: WebUI 服务端等待队列

**Files:**
- Modify: `web-ui/src/career-turn-client-queue.js`
- Modify: `web-ui/src/career-turn-client-queue.test.js`
- Modify: `web-ui/src/components/CareerAssistantPage.vue`

**Interfaces:**
- Produces: `submitTurnNow(request) -> Promise<ServerTurn>`
- Produces: `resumeConversationTurns(conversationId) -> Promise<ServerTurn[]>`
- Consumes: Turn SSE `queued/started/progress/delta/done/error`

- [ ] **Step 1: 将队列测试改为所有消息立即提交**

```javascript
await Promise.all([queue.submit(first), queue.submit(second), queue.submit(third)])
assert.equal(submitCalls.length, 3)
assert.deepEqual(queue.items.map(item => item.status), ['running', 'queued', 'queued'])
```

- [ ] **Step 2: 编写页面恢复测试**

```javascript
const restored = restoreServerTurns([{ id: '1', status: 'running' }, { id: '2', status: 'queued' }])
assert.equal(restored.length, 2)
```

- [ ] **Step 3: 实现立即提交和每 Turn 事件订阅**

发送按钮始终可用；每条消息提交后立即获得 `turn_id`。队列列表不再保存未发出的浏览器临时任务，只展示服务端 `queued/running` Turn。

- [ ] **Step 4: 页面打开会话时恢复非终态 Turn**

```javascript
pendingTurns.value = await api.listActiveTurns(conversationId)
pendingTurns.value.forEach(turn => subscribeTurnEvents(turn.id))
```

- [ ] **Step 5: 运行前端测试和构建**

Run: `Set-Location web-ui; npm test`
Expected: 所有 Node 测试 PASS。

Run: `Set-Location web-ui; npm run build`
Expected: Vite production build 成功。

- [ ] **Step 6: 提交**

```powershell
git add web-ui/src/career-turn-client-queue.js web-ui/src/career-turn-client-queue.test.js web-ui/src/components/CareerAssistantPage.vue
git commit -m "feat: persist career chat waiting turns"
```

### Task 7: 生产 Worker 部署与运行配置

**Files:**
- Modify: `docker-compose.production.yml`
- Modify: `docker/Dockerfile.api`
- Modify: `.env.production.example`
- Test: `tests/test_career_turn_worker_deployment.py`

**Interfaces:**
- Produces: `career-agent-worker` Compose service
- Produces: `CAREER_AGENT_GLOBAL_CONCURRENCY`、`CAREER_AGENT_WORKER_CONCURRENCY`、`CAREER_AGENT_LEASE_SECONDS`、`CAREER_AGENT_HEARTBEAT_SECONDS`、`CAREER_AGENT_EVENT_POLL_MILLISECONDS`

- [ ] **Step 1: 编写部署配置测试**

```python
self.assertIn("career-agent-worker:", compose)
self.assertIn("scripts/run_career_agent_worker.py", compose)
self.assertNotIn("career-temporary-attachments", worker_service)
```

- [ ] **Step 2: 增加 Worker 服务和健康依赖**

Worker 与 API 共享数据库和 Skill 卷，不挂载附件 tmpfs；先以单副本和本地并发 4 启动。

- [ ] **Step 3: 增加示例配置**

```dotenv
CAREER_AGENT_GLOBAL_CONCURRENCY=8
CAREER_AGENT_WORKER_CONCURRENCY=4
CAREER_AGENT_LEASE_SECONDS=90
CAREER_AGENT_HEARTBEAT_SECONDS=20
CAREER_AGENT_EVENT_POLL_MILLISECONDS=300
```

- [ ] **Step 4: 运行部署测试和 Compose 配置检查**

Run: `.venv\Scripts\python.exe -m unittest tests.test_career_turn_worker_deployment -v`
Expected: PASS。

Run: `docker compose -f docker-compose.production.yml config --quiet`
Expected: exit 0；如果本机未提供生产环境变量，记录为环境限制并完成静态 YAML 测试。

- [ ] **Step 5: 提交**

```powershell
git add docker-compose.production.yml docker/Dockerfile.api .env.production.example tests/test_career_turn_worker_deployment.py
git commit -m "feat: deploy career agent worker"
```

### Task 8: 回归、并发验收和文档同步

**Files:**
- Modify: `docs/career_agent_technical_architecture.md`
- Modify: `docs/career_assistant_module.md`
- Create: `tests/test_career_turn_worker_concurrency.py`

**Interfaces:**
- Consumes: 全部新增队列、Worker、API 和 WebUI 行为
- Produces: 线上运行边界、配置说明和验证记录

- [ ] **Step 1: 编写 100 用户模拟并发测试**

```python
await enqueue_conversations(count=100, turns_per_conversation=3)
await run_workers(count=3)
self.assertEqual(duplicate_turn_ids, set())
self.assertEqual(max_per_conversation, 1)
self.assertLessEqual(max_global, configured_slots)
```

- [ ] **Step 2: 编写 Worker 崩溃租约恢复测试**

```python
claim = repository.claim_next("dead-worker", lease_seconds=1, global_limit=8)
clock.advance(seconds=2)
self.assertEqual(repository.recover_expired(global_limit=8), 1)
```

- [ ] **Step 3: 更新架构和模块文档**

文档必须说明完整内容不脱敏、原文件删除、服务端 FIFO、全局槽位、租约恢复、非幂等 Tool 不自动重放，以及从单进程 `asyncio` 队列迁移后的边界。

- [ ] **Step 4: 运行 Python 全量测试**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`
Expected: 全部 PASS。

- [ ] **Step 5: 运行前端全量测试和构建**

Run: `Set-Location web-ui; npm test`
Expected: 全部 PASS。

Run: `Set-Location web-ui; npm run build`
Expected: Vite production build成功。

- [ ] **Step 6: 运行静态检查**

Run: `.venv\Scripts\python.exe -m compileall -q src tests scripts migrations/versions`
Expected: exit 0。

Run: `git diff --check`
Expected: 无空白错误。

- [ ] **Step 7: 提交**

```powershell
git add docs/career_agent_technical_architecture.md docs/career_assistant_module.md tests/test_career_turn_worker_concurrency.py
git commit -m "docs: record durable career turn architecture"
```
