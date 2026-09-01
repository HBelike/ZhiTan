# 求职助手 PostgreSQL Turn 队列与 Worker 设计

## 1. 背景与目标

当前求职助手已经具备进程内 `asyncio` 排队能力：同一会话 FIFO 串行，不同会话在单个 Uvicorn 进程内并发。它不能覆盖多 API 实例、进程重启和长时间 LLM/Tool 调用。

本阶段增加 PostgreSQL 权威队列与独立 Agent Worker，重点解决：

1. 多名用户同时使用时互不阻塞、互不串话；
2. 同一会话的多个 Turn 严格按入队顺序执行；
3. API 与 Worker 可以横向扩展，排队状态不依赖某个 Python 进程；
4. 浏览器断开或 API 重启后，已受理 Turn 仍可继续执行；
5. 对 LLM/Tool 设置可控的全局并发上限；
6. 保留简历、项目和职位信息的完整细节，不进行脱敏。

## 2. 范围

### 本阶段包含

- 求职助手文本、职位链接、附件三类聊天 Turn；
- PostgreSQL 持久化 payload、队列状态、租约和进度事件；
- 独立 `career-agent-worker` 进程；
- 同会话串行、跨会话并发、跨 Worker 全局并发槽位；
- Worker 异常退出后的租约回收；
- WebUI 立即提交后续消息并显示服务端排队状态；
- 原始附件临时处理，完整解析内容持久化。

### 本阶段不包含

- 公众号旧流水线和 `pipeline-scheduler` 迁移；
- Redis、Celery、Kafka 等新增中间件；
- 原始 PDF、Word 文件的长期保存；
- 跨地域容灾和备份体系；
- 任意 Tool 的自动重复执行保证。

## 3. 方案选择

采用 PostgreSQL 权威队列：`FOR UPDATE SKIP LOCKED` 负责竞争任务，租约字段负责故障恢复，固定槽位表负责跨 Worker 的总并发上限。PostgreSQL 是现有基础设施和事实来源，不再引入第二套队列系统，避免数据库与消息队列双写不一致。

未选择的方案：

- 仅在 API 进程中使用 advisory lock：可以跨实例串行，但请求仍绑定 API 生命周期，无法可靠恢复长任务；
- Redis/Celery：生态成熟，但当前规模下会增加部署、监控和双写一致性成本；
- 继续只用 `asyncio.Queue`：适合单进程，不满足多实例和重启恢复要求。

`asyncio` 仍用于每个 Worker 内部的异步调度、并发槽位等待和阻塞代码隔离，但不再承担权威排队状态。

## 4. 总体架构

```text
WebUI
  │  POST /career/turns
  ▼
Career API ──解析附件──► 临时附件目录
  │                         │
  │ 单事务写入              └─解析完成后删除原文件
  ▼
PostgreSQL
  ├─ agent_turns                队列、状态、租约
  ├─ agent_turn_payloads        完整输入与附件解析内容
  ├─ agent_turn_events          可恢复的进度事件
  └─ agent_execution_slots      跨 Worker 全局并发槽位
  ▲
  │ SKIP LOCKED 领取
  ▼
Career Agent Worker（可多副本）
  └─ Memory → RAG → LLM → Tool → 持久化结果/事件
```

API 不执行 LLM/Tool 主循环。Worker 不读取 API 容器的临时文件，只读取 PostgreSQL 中已经完成解析的完整内容。

## 5. 数据模型

### 5.1 `agent_turns` 扩展

在现有表增加：

- `queue_sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE`：稳定的全局入队序号；
- `available_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`：最早可领取时间；
- `attempt_count INTEGER NOT NULL DEFAULT 0`；
- `lease_owner TEXT`：Worker 实例标识；
- `lease_expires_at TIMESTAMPTZ`；
- `heartbeat_at TIMESTAMPTZ`。

保留现有 `queued/running/succeeded/failed/cancelled` 状态。`created_at` 只用于展示，FIFO 判断统一使用 `queue_sequence`。

### 5.2 `agent_turn_payloads`

每个 Turn 一行，通过 `turn_id` 一对一关联：

- `input_text`：用户输入原文；
- `job_url`：可选职位链接；
- `attachment_text`：附件完整解析内容，不脱敏；
- `attachment_metadata_json`：文件名、MIME、页数、解析器等非二进制元数据；
- `request_metadata_json`：入口和客户端请求信息；
- `created_at`、`updated_at`。

不保存附件二进制。现有 `messages.is_redacted` 对新 Turn 写入 `false`，运行配置关闭求职助手输入脱敏，确保 Memory、RAG、LLM 能使用完整细节。

### 5.3 `agent_turn_events`

事件表保存 `queued`、`started`、`progress`、`delta`、`tool`、`done`、`error` 等事件：

- `id BIGINT GENERATED ALWAYS AS IDENTITY`；
- `turn_id`；
- `event_type`；
- `payload_json`；
- `created_at`。

事件 ID 同时作为 SSE `id`，客户端使用 `Last-Event-ID` 断线续传。模型的超细粒度 token delta 可合并后批量写入，避免每个 token 一次数据库写入。

### 5.4 `agent_execution_slots`

表中预置固定数量的槽位行，数量等于线上允许的 LLM/Tool 总并发：

- `slot_number`；
- `turn_id`；
- `lease_owner`；
- `lease_expires_at`；
- `updated_at`。

领取 Turn 与领取槽位在同一事务中完成。增加 Worker 副本不会突破全局并发上限。

## 6. 入队流程

1. WebUI 每次发送都立即调用 API，不在浏览器内等待上一轮结束；服务端返回 `202 Accepted`、`turn_id` 和当前排队信息。
2. API 校验用户、会话所有权和输入类型。
3. 附件请求先写入 tmpfs，完成解析后得到完整 `attachment_text`。
4. API 在一个数据库事务中创建 `agent_turns`、`agent_turn_payloads` 和首个 `queued` 事件。
5. 事务提交成功才算受理成功；无论成功或失败，API 都在 `finally` 中删除原始附件。
6. WebUI 使用 `turn_id` 订阅事件，页面刷新后可通过会话的非终态 Turn 恢复等待列表。

如果 API 在附件解析期间退出，由于 Turn 尚未提交，客户端得到失败或断线结果，可以重新上传；不会产生只有队列记录却没有 payload 的残缺 Turn。

## 7. Worker 领取与执行

Worker 每次领取必须同时满足：

1. Turn 为 `queued` 且 `available_at <= NOW()`；
2. 同一 `conversation_id` 不存在 `queue_sequence` 更小的 `queued` 或 `running` Turn；
3. 存在未占用或租约已过期的全局执行槽位。

领取使用一个短事务和 `FOR UPDATE SKIP LOCKED`，原子设置 Turn 为 `running`、写入 `lease_owner/lease_expires_at`、占用执行槽位并生成 `started` 事件。事务提交后才运行 Agent。

每个 Worker 以 `asyncio` 调度多个 Turn；同步的 LLM/Tool/文档处理代码通过 `asyncio.to_thread()` 或现有同步执行边界运行，避免阻塞 Worker 事件循环。Worker 本地并发不能高于它可能领取的槽位数。

Worker 开始执行时才加载会话 Memory，因此一定能读到前一个 Turn 已提交的 assistant/tool 消息。同一会话不会出现两个同时修改 Memory 的 Worker。

## 8. 租约、失败与重试

- Worker 周期性刷新 Turn 和槽位租约；租约时长必须大于心跳周期三倍。
- Worker 正常完成时，在事务中写入最终消息、终态、`done/error` 事件并释放槽位。
- Worker 进程崩溃后，其他 Worker 可回收过期租约。
- 如果尚未执行可能产生外部副作用的 Tool，可将 Turn 重新排队并增加 `attempt_count`。
- 如果已经开始非幂等 Tool，过期 Turn 标记为 `failed`，不自动重放，避免重复投递、重复发送或重复写入。
- 用户取消只对 `queued` Turn 保证立即生效；`running` Turn 设置取消请求，由 Worker 在 LLM/Tool 安全边界检查并停止。
- 单个 Turn 失败后，该 Turn 进入终态，随后同会话的下一 Turn 才能继续，避免整条会话永久堵塞。

本阶段提供“至少一次领取、受控重试”语义，不宣称任意 Tool exactly-once。

## 9. API 与 WebUI 行为

核心接口分成提交与观察两步：

- `POST /career/turns`：创建 Turn，返回 `202`；
- `GET /career/turns/{turn_id}`：查询状态和排队信息；
- `GET /career/turns/{turn_id}/events`：SSE 事件流，支持 `Last-Event-ID`；
- `POST /career/turns/{turn_id}/cancel`：取消等待中的 Turn，或请求停止运行中的 Turn。

现有聊天入口保留兼容层，但内部统一调用新的 enqueue service。WebUI 的等待列表以服务端 Turn 为准；用户连续发送三条消息时，三条都立即持久化，关闭页面不会丢失后两条。

排队位置只作为近似展示，因为其他会话可能并发执行；同一会话内部顺序必须准确。

## 10. 多人并发时的保证

- 不同用户、不同会话：由多个槽位和 Worker 并发执行；
- 同一用户、同一会话、多标签页：全部进入同一 PostgreSQL FIFO，只执行一个；
- 同一用户、不同会话：允许并发；
- 多 API 实例：只负责入队和读事件，无进程内共享状态依赖；
- 多 Worker 实例：通过行锁、租约和槽位竞争，不重复占用同一个 Turn；
- API 或浏览器断线：不影响已提交 Turn；
- Worker 崩溃：租约到期后受控恢复，不永久占用并发名额。

## 11. 配置与部署

新增 `career-agent-worker` 服务，与 API 使用同一镜像、数据库和必要的只读 Skill 配置。Worker 不挂载临时附件目录。

最小配置项：

- `CAREER_AGENT_GLOBAL_CONCURRENCY`：初始化/校验全局槽位数；
- `CAREER_AGENT_WORKER_CONCURRENCY`：单 Worker 最大并行数；
- `CAREER_AGENT_LEASE_SECONDS`；
- `CAREER_AGENT_HEARTBEAT_SECONDS`；
- `CAREER_AGENT_EVENT_POLL_MILLISECONDS`。

首次上线先部署一个 Worker，确认稳定后增加副本；全局并发仍由槽位表限制。API 可独立扩容。

## 12. 验证与验收

### 数据库与仓储测试

- 同一会话只领取最早 Turn；
- 不同会话可领取多个 Turn；
- 多线程/多进程竞争不会重复领取；
- 全局槽位耗尽后不再领取；
- 租约过期能够回收；
- payload 与 Turn 原子创建；
- 终态提交与槽位释放原子完成。

### API 与事件测试

- 文本、职位链接、附件均返回持久化 `turn_id`；
- 附件完整解析内容入库且未脱敏，原文件被删除；
- SSE 可从 `Last-Event-ID` 恢复；
- 页面刷新后等待列表可恢复；
- 无权访问其他用户的 Turn。

### Worker 与并发测试

- 同会话连续 Turn 不重叠且严格 FIFO；
- 多会话在槽位允许时并发；
- 杀死一个 Worker 后，安全任务可被另一 Worker 接管；
- 非幂等 Tool 已开始时不自动重放；
- 100 个模拟用户并发入队时无重复执行、无串话、无丢失，数据库连接数和 LLM 并发不超过配置上限。

### 兼容性测试

- 现有 Agent、Memory、RAG、Tool、模型路由测试继续通过；
- 现有 WebUI 发送、进度、错误展示行为保持可用；
- 单 Worker 部署可作为默认模式运行。

## 13. 实施顺序

1. 数据库迁移与队列仓储；
2. enqueue service、payload 和事件查询 API；
3. Worker 领取、租约、心跳与执行适配；
4. WebUI 改为立即入队并恢复服务端等待列表；
5. Compose 增加 Worker，完成并发与故障测试；
6. 移除聊天主路径对进程内队列的权威依赖，保留它作为单进程兼容代码直到新链路稳定。
