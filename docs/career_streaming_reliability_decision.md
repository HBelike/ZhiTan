# 求职助手：流式交互与可靠性决策记录

## 目标

让求职助手在解析附件、调用视觉模型和生成长回复时持续给出真实进度；发生临时网络故障时有限重试，发生不可恢复错误时给出可操作提示。该设计面向后续公网部署：不依赖浏览器本地状态、不暴露 Key，也不把原始附件写入历史记录。

## 2026-08-07：SSE 流与消息确认

- 浏览器点击发送后立即清空输入框，并先插入本地 `sending` 用户消息。
- LangGraph 每完成一个确定性节点就推送 `progress`：校验输入、解析附件、读取职位信息、保存历史、生成回复。进展不包含也不伪造模型的内部思维链。
- 用户消息写入 PostgreSQL 后，后端推送 `accepted`，前端以持久化消息原位替换临时消息；因此不会出现“已发送内容仍留在输入框”或同一条消息重复显示。
- 文本模型的正文通过 `delta` 事件逐段推送；最终由 `done` 写入完整助手消息与 Turn 状态。页面允许在服务端接受 Turn 后切换会话，每条 SSE 由会话 ID 与观察代次隔离，避免响应落入错误会话。

调用链：

```text
CareerAssistantPage
  -> POST intake-with-materials-stream
  -> CareerIntakeGraph.stream
  -> progress / accepted
  -> CareerResponseRunner.stream
  -> delta / done
  -> PostgreSQL conversation_messages + agent_turns
```

## 2026-08-07：阶段级超时与有限重试

### 重试边界

| 阶段 | 默认超时 | 最大尝试次数 | 重试条件 | 不重试条件 |
| --- | ---: | ---: | --- | --- |
| Docling 文档解析 | 120 秒 | 2 | 超时、网络错误、429、5xx | 文件损坏、格式不支持、鉴权/权限错误 |
| 云端 Vision 复核 | 90 秒 | 2 | 超时、网络错误、429、5xx | 400、401、403、413 等配置或输入错误 |
| 文本模型生成 | 90 秒 | 2 | 在收到首个正文前的超时、网络错误、429、5xx | 已开始输出、鉴权/权限/参数错误 |

- 重试间隔来自 YAML：Docling 为 0.6 秒，视觉和文本模型为 0.8 秒。
- 文本流已经产生 `delta` 后，绝不重放同一模型请求；否则会让用户看到两段相互重复或矛盾的回答，也可能多消耗一次额度。
- 云端 Vision 仅在当前连接完成受控重试后，才使用 `fallback_connection_keys` 中由管理员显式开启的备用连接。默认不自动切换付费模型。
- 失败消息只描述可行动原因（例如“模型限流”“API Key 无权限”“文档处理超时”），不回显密钥、上游响应正文或附件原文。

## 配置

所有策略由 `config/career_assistant.yaml` 声明：

```yaml
response_generation:
  request_timeout_seconds: 90
  max_attempts: 2
  retry_backoff_seconds: 0.8

document_processing:
  docling_serve:
    request_timeout_seconds: 120
    max_attempts: 2
    retry_backoff_seconds: 0.6
  cloud_vision:
    max_attempts: 2
    retry_backoff_seconds: 0.8
```

生产环境可根据实际 SLA 下调最大尝试次数为 `1`；上限固定为 `2`，防止瞬时故障演变为无限等待或不可控费用。

## 2026-08-07：代理保活与并发保护

Docling、OCR 与上游模型都可能在首个结果前持续几十秒。浏览器直连时通常不明显，
但部署到 Nginx、Cloudflare 或托管平台后，空闲 SSE 连接可能被代理提前关闭。因此
流式接口现在采用以下实现：

- 业务处理仍在受控的事件生成器中运行；SSE 桥接层每 `stream_heartbeat_seconds`
  秒输出一次 `: keepalive` 注释帧，浏览器不会把它渲染为内容；
- 浏览器刷新、切网或关闭连接后，SSE 桥接层停止投递无人消费的事件，但不会取消已
  开始的 Graph 与模型调用；当前 Turn 会继续完成并写入会话历史，重新进入会话即可
  读取最终消息；
- 会话详情接口同时返回该会话的最近一个 `agent_turn` 状态。浏览器重新打开会话时，
  若该 Turn 仍为 `queued` 或 `running`，页面每 3 秒仅刷新已脱敏的消息与 Turn 状态；
  完成后自动停止轮询。因此断线恢复不依赖浏览器内存，也不会保存附件原文件；
- 超过 `turn_timeout_seconds` 时只显示一次“处理时间较长、仍在继续”的真实状态，
  不伪造模型推理内容，也不从线程外粗暴杀死正在执行的 HTTP 请求；
- 真实请求超时仍由 Docling、Vision 和文本模型各自的 transport timeout 约束；
- `max_concurrent_turns` 使用进程内信号量限制同时进行的流式 Turn，达到上限返回
  HTTP 429，并立即清理尚未进入处理流程的临时附件。

这套机制适合当前单实例个人平台。未来多实例部署时，应把并发配额迁移到 Redis
或队列系统，并将长文档任务交给带 TTL 临时对象存储的 Worker；不能用进程内状态
伪装成跨实例的可靠任务队列。

## 验证

- `scripts/verify_career_intake_graph.py`：真实 PostgreSQL 的 LangGraph 流节点顺序、用户消息持久化与测试会话清理。
- `scripts/verify_career_model_client.py`：OpenAI-compatible 文本模型协议。
- `scripts/verify_career_cloud_vision.py`：云端 Vision 连接池、配置和安全错误映射。
- `scripts/verify_career_docling_document_parser.py`：Docling multipart、匿名文件名和文档错误映射。
- `scripts/verify_career_sse_heartbeat.py`：长处理期间的协议保活、预期耗时提示和后台线程异常映射。
- `scripts/verify_career_latest_turn_recovery.py`：真实 PostgreSQL 的最近 Turn 恢复查询。
- `npm --prefix web-ui run build`：前端 SSE 状态渲染构建。

## 2026-08-25：生成期间切换会话与恢复

- 根因：旧实现切换会话时只清空 `pendingTurns` 和 `activeObservedTurnId`，没有取消旧 SSE；旧观察器结束后的回调与 `finally` 仍可覆盖新会话状态。同时，会话详情读取为 `queued/running`、随后 active-turns 已为空的完成竞态不会启动恢复轮询，造成页面长期显示旧状态。
- 生命周期：每次 Turn 观察绑定 `conversationId / turnId / AbortController`。切换会话或卸载页面时只取消浏览器 SSE，并使旧观察器全部晚到回调失效；PostgreSQL 队列和独立 Worker 不受影响。
- 恢复：切回会话时先读取持久消息与 active-turns。仍在执行的 Turn 从持久事件重新回放 progress/delta 并继续展示；已经完成的 Turn 直接读取最终消息。详情与 active-turns 出现终态竞态时，每 3 秒重新校准，取得终态后停止。
- 交互：Turn 尚未收到服务端 `202` 前暂时禁止切换，避免消息归属不确定；服务端受理后即使模型仍在生成，也允许切换会话或新建会话。
- 验证：`career-turn-observation.test.js` 覆盖旧 SSE 取消、观察代次隔离、晚到完成保护和终态校准条件；Web UI 全量测试与 production build 验证组件接线。

后续高并发部署时，90 页文档应由持久化任务队列执行，并用任务状态轮询或 SSE 续连提供进度；当前同步 SSE 链路仍适合个人平台的低并发场景。
