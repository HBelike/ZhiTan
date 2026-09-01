# 求职助手 Agent 技术架构文档

> 文档基线：2026-08-23
> 适用范围：当前仓库中的求职助手聊天主链、LLM、Tool、Memory、RAG、持久化 Turn 队列与生产部署。

## 1. 架构结论

求职助手采用“确定性流程 + LLM 推理 + 受控 Tool Calling”的混合 Agent 架构：

- FastAPI API 负责身份校验、会话边界、附件解析和 Turn 入队；
- PostgreSQL 是会话、消息、Turn、完整输入、事件、队列和并发槽位的事实来源；
- 独立 `career-agent-worker` 使用 `asyncio` 并发执行不同会话；
- 同一个 Conversation 内的 Turn 按数据库 `queue_sequence` 严格 FIFO 串行；
- 不同 Conversation 可以并发，所有 Worker 共享 PostgreSQL 全局执行槽位；
- LangGraph 负责固定的 Intake 状态机，LLM 不可以任意改变主流程；
- Tool 只有在用户显式激活 Skill 且所选模型支持 Tool Calling 时才暴露；
- Memory 以 PostgreSQL 对话历史为主，当前注入模型的是有限窗口，长期摘要方案尚未启用；
- 面经 RAG 由服务端在调用 LLM 前确定性执行，模型不自行决定是否检索。

该实现已经解决多人使用时最关键的执行冲突：请求不会因为上一轮未完成而只能停留在浏览器中，也不会让同一会话的两轮同时读写上下文。API 或 Worker 重启后，排队状态和事件仍保留在 PostgreSQL。

## 2. 核心概念

### 2.1 Conversation

Conversation 是一段可持续多轮的求职对话，保存简历、岗位、历史消息、模型选择和 Skill 激活上下文。

### 2.2 Turn

Turn 是用户的一次输入以及由此触发的一次完整 Agent 执行。例如用户连续发送两条消息，会产生两个 Turn：

```text
Conversation A
  Turn 101：帮我分析这份 JD
  Turn 102：再把项目经历改成 STAR
```

一问一答是界面上的常见交互，但网络重试、双标签页、用户快速连发、刷新恢复和多设备操作都可能在上一轮尚未完成时提交下一轮。因此一个 Conversation 可以存在多个 Turn，但任意时刻只允许其中一个处于 `running`。

### 2.3 Job、Event 与 Lease

- Turn 同时也是持久化 Job，状态包括 `queued/running/succeeded/failed/cancelled`；
- Event 是 `queued/started/progress/delta/done/error` 等可恢复 SSE 事件；
- Lease 是 Worker 对运行中 Turn 的限时所有权，Worker 定期 heartbeat 续租；
- Slot 是全局并发名额，由 PostgreSQL 统一分配，不随 API/Worker 副本数膨胀。

## 3. 生产拓扑

```text
浏览器 / Vue WebUI
        │ HTTPS / JSON / Multipart / SSE
        ▼
      Caddy
        ├────────► career-web（静态页面）
        │
        └────────► career-api（FastAPI）
                         │
                         ├─ 校验 actor_id / conversation 所有权
                         ├─ 解析 PDF、Word、图片等附件
                         ├─ 删除附件原件
                         └─ 完整解析文本 + Turn + queued Event 入库
                                      │
                                      ▼
                         PostgreSQL 16 + pgvector
                         ├─ Conversation / Message / Memory
                         ├─ Agent Turn / Payload / Event
                         ├─ Execution Slot / Lease
                         └─ Interview RAG Corpus
                                      ▲
                                      │ claim / heartbeat / finish
                                      ▼
                         career-agent-worker
                         ├─ asyncio 本地并发
                         ├─ LangGraph Intake
                         ├─ LLM / Tool Calling Loop
                         └─ 持久化 delta / done / error
                                      │
                  ┌───────────────────┼───────────────────┐
                  ▼                   ▼                   ▼
             LLM Provider       Embedding Provider    Skill Tools
```

API 不执行聊天主链，只负责将输入可靠地交给队列。Worker 不接收原始附件，只读取已经持久化的解析文本，因此附件临时目录不会成为 Worker 扩容的共享状态。

## 4. 一次 Turn 的完整调用链

```text
1. 用户发送文本、JD、简历或附件
2. API 校验登录身份和 Conversation 所有权
3. API 解析附件，保留完整 extracted_text，删除原件
4. 一个数据库事务写入 Agent Turn、Payload、queued Event
5. API 立即返回 202 和服务端排队位置
6. WebUI 订阅持久化 SSE；断线后携带 Last-Event-ID 续传
7. Worker 原子占用全局 Slot，并领取每个 Conversation 最早的 Turn
8. Worker heartbeat 续租，同时在线程中执行同步 Agent 主链
9. Intake Graph 构建上下文、写入用户消息并完成材料处理
10. RAG 检索和 Memory 窗口进入 Prompt
11. LLM 直接回复，或进入最多 6 轮的 Tool Calling Loop
12. Worker 批量持久化 delta，并在事务中写终态 Event、释放 Slot
13. WebUI 收到 done/error；刷新后也可从数据库恢复最终结果
```

## 5. LLM 设计

### 5.1 模型路由

`ModelGateway` 根据本轮 `ModelSelectionRequest` 和组织内模型档案选择 Provider、模型与能力。支持“免费额度优先”和显式模型档案等选择方式，密钥只在服务端解密使用。

### 5.2 Prompt 组成

模型上下文主要由以下部分组成：

1. 系统角色和求职助手行为边界；
2. 当前绑定的已确认简历与目标 JD；
3. 当前 Turn 的完整文本和附件解析内容；
4. 最近对话 Memory；
5. 面经 RAG 证据；
6. 显式激活 Skill 的说明与可用 Tool；
7. 当前任务的输出约束。

浏览器不能直接伪造系统简历、JD 或历史上下文；服务端按当前 actor 和 Conversation 重新读取。

### 5.3 流式与持久化

LLM 的流式 delta 不逐字符写数据库，而是由 Worker 按约 512 字符聚合成事件，降低数据库写放大。最终 Assistant Message 与 Turn 终态仍是权威结果。

## 6. Tool 与 Skill 设计

Tool 不是默认向所有对话开放。调用条件为：

- 用户显式选择或通过命令激活 Skill；
- Skill 声明了执行方式和 Tool 名称；
- 当前模型支持原生 Tool Calling；
- Tool 位于服务端 Registry 白名单中。

模型使用 `auto` Tool Calling。服务端执行 Tool 后，把带 `tool_call_id` 的结构化结果回传给模型，最多执行 6 轮，直到模型给出最终文本。Tool 失败会作为失败结果进入模型上下文和最终 `skill_executions`，不会伪装成成功。

当前 Tool 风险主要不是并发顺序，而是权限作用域：安装 Skill 等写操作影响共享运行目录。面向互不信任的公众用户前，应把全局写 Tool 限制为 admin/operator，普通聊天仅开放只读或用户私有 Tool。

## 7. Memory 设计

### 7.1 已实现的短期 Memory

- 完整 Conversation Message 会写入 PostgreSQL；
- 每轮构造 Prompt 时读取最近的有限消息窗口；
- 当前实现重点是控制 Token 与延迟，不会把无限历史全部发送给 LLM；
- 同会话 FIFO 保证下一轮只在上一轮完全收口后执行，因此读到的是完整轮次，不会出现“只有上一条 User Message、还没有 Assistant Message”的半轮上下文。

### 7.2 尚未启用的长期 Memory

仓库已有长对话摘要设计，但当前主链尚未启用滚动摘要、事实槽位和 CAS 更新。长对话可能逐渐忘记早期细节，因此不能仅凭“完整历史已落库”就宣称支持无限轮记忆。

建议后续将 Memory 分为：

- Working Memory：最近完整轮次；
- Conversation Summary：带版本号的滚动摘要；
- User Facts：经用户确认的稳定求职事实；
- Retrieval Memory：按当前问题检索早期相关片段。

摘要更新必须在 Turn 完成后执行，并使用版本/CAS 防止覆盖。

## 8. RAG 设计

面经 RAG 采用服务端预检索：Chunking 和 Embedding 在入库阶段完成，查询阶段进行语义检索与 lexical 检索，再通过 RRF 等策略融合结果，最后把带来源的证据注入 Prompt。

```text
面经文档 → 清洗/切块 → Embedding → PostgreSQL + pgvector
                                        ▲
用户问题 → Query Embedding ─────────────┤
用户问题 → Lexical Search ──────────────┤
                                        ▼
                               融合、过滤、证据注入
                                        ▼
                                       LLM
```

当前 RAG 按 organization 作用域共享，不是 actor 私有。若平台用户彼此不信任，需要改为“公共语料显式 public + 私有语料按 actor 隔离”，否则组织内用户可能检索到其他用户贡献的面经内容。

## 9. 并发与排队设计

### 9.1 调度规则

```text
Conversation A: A1 running → A2 queued → A3 queued
Conversation B: B1 running → B2 queued
Conversation C: C1 running

全局最多 running = CAREER_AGENT_GLOBAL_CONCURRENCY
每个 Conversation 最多 running = 1
```

`claim_next` 在一个 PostgreSQL 事务中：

1. 通过 `FOR UPDATE SKIP LOCKED` 占用空闲全局 Slot；
2. 按 `queue_sequence` 查找最早可执行 Turn；
3. 使用 `NOT EXISTS` 排除同 Conversation 中前面仍为 queued/running 的 Turn；
4. 将 Turn 更新为 running、设置 lease_owner 和 lease_expires_at；
5. 写入 started Event。

多个 Worker 可以同时 claim 不同 Turn，但不能领取同一个 Turn，也不能让同一 Conversation 的后续 Turn 越过前一轮。

### 9.2 asyncio 的职责边界

`asyncio` 只负责单个 Worker 内的并发调度和 heartbeat 定时；同步的 LangGraph、LLM Client 和 Tool 通过 `asyncio.to_thread` 运行，避免阻塞事件循环。

跨进程正确性不依赖 `asyncio.Lock` 或 `asyncio.Semaphore`，而依赖 PostgreSQL 队列、行锁、Slot 和 Lease。因此可以部署多个 Worker 副本，且全局上限仍然是 8，而不是“副本数 × 8”。

### 9.3 崩溃恢复

- Worker 正常执行时周期性续租；
- Worker 崩溃后，过期 Lease 会被恢复；
- 未开始非幂等 Tool 的 Turn 可以重新排队；
- 已进入非幂等 Tool 的过期 Turn 标记失败，避免自动重放副作用；
- done/error Event 和 Slot 释放在同一事务中完成。

## 10. 多人同时使用时会发生什么

生产默认参数：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `CAREER_AGENT_GLOBAL_CONCURRENCY` | 8 | 所有 Worker 共享的最大执行数 |
| `CAREER_AGENT_WORKER_CONCURRENCY` | 4 | 单 Worker 本地最大执行数 |
| `CAREER_AGENT_LEASE_SECONDS` | 90 | Worker 租约有效期 |
| `CAREER_AGENT_HEARTBEAT_SECONDS` | 20 | 续租周期 |
| `CAREER_AGENT_EVENT_POLL_MILLISECONDS` | 300 | SSE 查询事件间隔 |

如果 30 个用户同时发送消息：

- 最多 8 个不同会话立即进入执行；
- 其余 Turn 保存在 PostgreSQL 中等待，不会返回 429，也不会只存在浏览器内存；
- 某个用户在同一会话连续发送 3 条，只执行第 1 条，后两条依次等待；
- 其他用户的会话仍可占用剩余 Slot，不会被该会话的后续消息并行插入；
- 浏览器刷新、SSE 断线或 API 重启不会丢失队列；
- Worker 重启后根据 Lease 恢复任务。

等待时间近似为：

```text
预计等待批次 = 排队中可执行 Turn 数 / 全局并发数
预计等待时间 = 预计等待批次 × 平均 Turn 执行时间
```

例如平均每轮 20 秒、前方有 24 个可执行 Turn、全局并发为 8，约需等待 3 个批次，即约 60 秒。真实时间还受模型首 Token、Tool 数量、Provider 限流和文档长度影响。

## 11. 多用户风险分析

| 风险 | 当前状态 | 影响与建议 |
| --- | --- | --- |
| 同会话并行导致上下文交错 | 已解决 | PostgreSQL 同会话 FIFO，任意时刻最多 1 个 running |
| API/Worker 重启导致排队丢失 | 已解决 | Payload、Event、状态和 Lease 全部持久化 |
| 多 Worker 使并发上限成倍增加 | 已解决 | 全局 Slot 由 PostgreSQL 共享 |
| SSE 断线导致任务取消 | 已解决 | Worker 与 HTTP 连接解耦，支持 Last-Event-ID |
| 附件原件长期保存 | 已解决 | API 解析后立即删除，只持久化完整解析文本 |
| 单用户多开会话占满 8 个 Slot | 未解决/P1 | 增加 actor/organization 并发与日额度 |
| 共享 Provider Key 触发 RPM/TPM/额度上限 | 未解决/P1 | 增加集中令牌桶、Retry-After、熔断和成本监控 |
| 普通用户修改组织模型或安装全局 Skill | 未解决/P0 | 后端 RBAC，写操作仅 admin/operator |
| 组织级 RAG 被不可信用户共享 | 需产品确认/P1 | 公共语料和用户私有语料分层 |
| 长对话早期事实遗忘 | 未解决/P1 | 落地滚动摘要、事实 Memory 和检索 Memory |
| 附件解析同时占满 API CPU/tmpfs | 部分缓解/P1 | 原件及时删除；仍需单独的解析资源池和容量监控 |
| 运行中取消不能立刻中断 Provider 请求 | 部分支持/P2 | queued 可取消；running 为协作式取消，需在安全边界检查 |

结论：多人同时聊天的“任务丢失、同会话串写、跨 Worker 超卖并发”风险已经处理。正式向大量公众用户开放前，最重要的剩余项是 RBAC、用户级公平配额、Provider 集中限流和长期 Memory。

## 12. 数据与隐私边界

按当前产品决定：

- 用户原始文本和附件完整解析文本不脱敏，持久化到 PostgreSQL；
- 原始 PDF、Word、图片仅存在于 API 临时目录，解析并成功入队后删除；
- Worker Payload 不包含原始二进制文件；
- Message、Turn、Event 读取均校验 actor_id；
- 模型密钥服务端加密保存，不返回浏览器；
- 生产必须持续开启平台认证，不能让匿名请求回退为共享默认 Actor。

完整解析文本属于敏感个人数据，运维侧仍应对数据库账号、备份访问、日志输出和管理员权限做最小授权。

## 13. 容量与上线建议

建议先以默认 8 个全局 Slot 上线，再根据真实数据调整，而不是直接提高并发。重点观察：

- queued/running 数量和 P50/P95 排队时间；
- LLM 首 Token、总耗时、RPM、TPM、429 和费用；
- 每个 actor 的活跃会话数；
- PostgreSQL 连接池等待、慢查询和 Event 表增长；
- Worker heartbeat 失败和 Lease 恢复次数；
- API 附件解析耗时、tmpfs 峰值和 Docling 队列；
- Tool 调用次数、失败率和非幂等重放拦截数。

扩容方式：

1. API 无状态扩容，用于提升上传、查询和 SSE 连接能力；
2. Worker 横向扩容，用于提升执行能力，但单副本并发总和可以高于全局 Slot；
3. 只有 Provider、数据库和 CPU 容量允许时，再提高全局 Slot；
4. 不要把 `CAREER_AGENT_GLOBAL_CONCURRENCY` 按 Worker 副本分别设置成不同值。

## 14. 验证结果

自动化模拟使用 2 个 Worker、每个本地并发 4、全局并发 8，并生成 100 个 Conversation、每个连续 2 个 Turn：

- 200 个 Turn 全部完成；
- 实际峰值并发为 8；
- 任一 Conversation 的峰值并发均为 1；
- 每个 Conversation 的启动顺序均为 `[0, 1]`；
- 生产 Compose 可通过配置解析；
- 后端、前端、编译和构建结果见本次交付记录。

这属于调度语义和回归测试，不等同于真实 Provider 压测。上线前仍需用实际模型 Key 对 10/30/50 用户阶梯压测，获得真实延迟、429 和成本数据。

## 15. 关键实现位置

- `migrations/versions/20260823_17_career_turn_worker_queue.py`：队列表、Event、Slot、Lease；
- `src/career_assistant/persistence/turn_job_repository.py`：enqueue、claim、heartbeat、finish、recover；
- `src/career_assistant/turn_payloads.py`：完整 Turn 输入序列化与恢复；
- `src/career_assistant/turn_worker.py`：asyncio Worker 和 Agent Processor；
- `scripts/run_career_agent_worker.py`：独立 Worker 启动入口；
- `src/career_assistant/web/turn_router.py`：提交、状态、SSE、取消 API；
- `src/career_assistant/intake_graph.py`：LangGraph Intake；
- `src/career_assistant/response_runner.py`：LLM、Prompt、Tool Calling Loop；
- `src/career_assistant/model_gateway.py`：模型路由；
- `src/career_assistant/skill_runtime.py`、`skill_tools.py`：Skill 与 Tool；
- `src/career_assistant/interview_library/`：RAG；
- `web-ui/src/career-turn-client-queue.js`：服务端队列客户端；
- `docker-compose.production.yml`：API、Worker、PostgreSQL 生产拓扑。
