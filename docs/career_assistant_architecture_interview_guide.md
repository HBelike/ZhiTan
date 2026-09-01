# 求职助手技术架构与面试讲解指南

> 文档基线：2026-08-25 代码工作区
> 目标读者：需要理解项目、形成个人总结并向面试官讲解的开发者
> 说明：本文以当前代码为事实来源，明确区分“已经实现”“默认关闭”和“后续演进”。

## 1. 先用一句话定义项目

求职助手是一个以候选人简历和目标岗位为上下文，串联岗位获取、岗位证据评估、求职对话、简历优化、面经 RAG 和实时面试辅助的 AI 求职工作台。

它不是一个只把用户问题拼进 Prompt 的聊天机器人。系统把不同性质的工作拆给不同层：

- LangGraph 负责必须按顺序完成的确定性输入流程；
- LLM 负责语言理解、内容生成和受控 Tool Calling；
- PostgreSQL 负责会话、上下文、任务队列、事件和业务结果的事实存储；
- 独立 Worker 负责耗时 Agent Turn 的可靠执行；
- RAG、岗位 Judge、简历优化和实时面试是围绕主 Agent 的专业能力模块；
- Vue WebUI、Chrome Extension 和 WebSocket 实时页面分别承载不同交互场景。

面试时可以先给出这个结论，再从“为什么不是普通聊天机器人”向下展开。

## 2. 产品问题与设计目标

普通 LLM 求职对话存在四个问题：

1. 简历、JD 和历史消息没有稳定的数据边界，容易在多轮对话中丢失或混淆；
2. 模型可以生成看似合理但没有简历证据的经历、分数和判断；
3. 文档解析、RAG、模型调用都可能很慢，浏览器刷新或服务重启容易丢任务；
4. 搜岗位、改简历、准备面试和实时答题被分散在不同工具中，数据不能复用。

因此项目的设计目标不是“让模型回答得像 HR”，而是建立一条可追溯的求职工作流：

```text
候选人资料
    ↓
版本化职业上下文
    ↓
岗位获取与证据评估
    ↓
带 Memory / RAG / Skill 的求职 Agent
    ↓
简历优化与面试准备
    ↓
实时面试辅助与面经沉淀
```

## 3. 系统全景架构

### 3.1 运行拓扑

```text
┌──────────────────────── 客户端层 ────────────────────────┐
│ Vue 3 WebUI                                               │
│ ├─ 求职助手  ├─ 简历助手  ├─ 面经库  ├─ 职位库          │
│ └─ 浏览器实时面试大师                                     │
│                                                           │
│ Chrome MV3 Extension：复用用户已登录的 BOSS 页面          │
│ Electron 客户端：保留的 Windows 双音轨实时面试方案         │
└───────────────────────────┬───────────────────────────────┘
                            │ REST / Multipart / SSE / WS
                            ▼
┌──────────────────────── 接入与应用层 ─────────────────────┐
│ Caddy / Nginx                                             │
│ FastAPI                                                   │
│ ├─ 登录身份与 actor 边界                                  │
│ ├─ Conversation / Context / Model / Interview APIs        │
│ ├─ 附件接收与预解析                                       │
│ └─ Turn 入队、状态查询、持久化 SSE                         │
└───────────────────────────┬───────────────────────────────┘
                            │ enqueue / query
                            ▼
┌──────────────────────── 数据与调度层 ─────────────────────┐
│ PostgreSQL 16 + pgvector                                  │
│ ├─ Conversation / Message / Context / Assessment          │
│ ├─ Turn Payload / Event / Queue / Lease / Slot            │
│ ├─ Interview Experience / Chunk / Embedding               │
│ └─ Resume / Live Interview / Evaluation                   │
└───────────────────────────┬───────────────────────────────┘
                            │ claim / heartbeat / finish
                            ▼
┌──────────────────────── Agent 执行层 ─────────────────────┐
│ career-agent-worker                                       │
│ ├─ asyncio 本地并发调度                                   │
│ ├─ LangGraph Intake Graph                                 │
│ ├─ ModelGateway + OpenAI-compatible Client                │
│ ├─ Prompt / Tool Calling Agent Loop                       │
│ └─ delta / done / error 持久化                            │
└───────────────────────────┬───────────────────────────────┘
                            │ HTTPS / WebSocket
                            ▼
┌──────────────────────── 外部能力层 ───────────────────────┐
│ Chat / Embedding / Vision / Realtime ASR Provider         │
│ Docling Serve / Gotenberg / BOSS Web Page                 │
│ LangSmith（metadata-only 旁路观测）                        │
└───────────────────────────────────────────────────────────┘
```

### 3.2 架构风格

项目整体是“模块化单体 API + 独立 Agent Worker + 独立文档服务 + 多形态客户端”。

选择这种形态的原因是：业务模块之间需要共享用户、模型连接、职业上下文和 PostgreSQL，但耗时 LLM Turn 又不能绑定在 HTTP 请求生命周期中。因此 API 仍保持统一应用边界，Agent 执行被拆成独立进程。

## 4. Agent 架构：核心设计

### 4.1 不是多 Agent，而是混合式单 Agent

当前主链不是多个自治 Agent 协商，也没有 Supervisor 在多个 Agent 之间动态路由。准确说法是：

> 一个以固定工作流编排为骨架、以 LLM 为推理与生成核心、以白名单 Tool 和专业服务为外部能力的混合式 Agent。

岗位 Judge、RAG、简历优化和实时回答是专业子服务或独立模型调用，不应包装成已经落地的 Multi-Agent 系统。

### 4.2 为什么混合确定性流程与 LLM

如果把上传、解析、脱敏、持久化和清理都交给模型决定，结果会不可预测；如果全部写成规则，又无法处理开放式求职咨询。因此边界如下：

| 工作 | 负责层 | 原因 |
| --- | --- | --- |
| 输入校验、附件清理、状态迁移 | 确定性代码 | 必须可验证、不可跳过 |
| 文档解析路由 | 确定性代码 | 根据 MIME、文本质量和服务状态选择路径 |
| 求职分析与回答组织 | LLM | 输入开放，输出需要语义理解 |
| 是否调用已暴露 Tool | LLM + 白名单 Registry | 保留 Agent 自主性，同时限制执行边界 |
| 岗位评估计分 | LLM 提取判断 + 服务端复算 | 避免模型自报不可验证分数 |
| 面经检索 | 服务端确定性预检索 | 避免模型漏检索或随意扩大数据范围 |
| 排队、并发、重试恢复 | PostgreSQL + Worker | 不能依赖模型或单进程内存 |

### 4.3 Agent 的统一输入契约

`CareerInboundMessage` 是一次 Turn 的统一输入，主要包含：

- `turn_id / conversation_id / actor_id`；
- 用户文本与移除 Skill 调用标记后的 `effective_text`；
- 模型选择策略；
- 已解析附件类型；
- 面经检索证据；
- 本轮激活的 Skill；
- 服务端读取的基准简历、目标岗位和绑定版本。

这个契约的重要意义是：Web 请求、PostgreSQL Payload 和 Worker 恢复后的输入都使用同一业务模型，避免在线路径与恢复路径出现两套逻辑。

### 4.4 LangGraph Intake Graph

LangGraph 没有负责整段自由推理，而是编排七个固定节点：

```text
START
  → validate_input
  → build_context
  → parse_material
  → extract_job_description
  → redact_sensitive_data
  → persist_history
  → cleanup_temporary_files
  → END
```

各节点职责：

1. `validate_input`：验证文本、职位链接、附件和模型选择契约；
2. `build_context`：建立仅在当前 Graph 内存存在的运行上下文；
3. `parse_material`：消费 API 已解析材料，兼容旧路径中的临时附件解析；
4. `extract_job_description`：读取公开职位页，失败时降级为让用户粘贴 JD；
5. `redact_sensitive_data`：分离“模型本轮可用上下文”和“允许写入历史的内容”；
6. `persist_history`：把用户消息绑定到当前 Turn；
7. `cleanup_temporary_files`：记录清理步骤，真正清理由 `finally` 保证。

Graph 使用 `stream_mode=[updates, values]` 输出真实节点完成事件。前端再把这些内部节点折叠为“整理资料、分析回复、完成回复”等用户语言，不展示模型思维链。

### 4.5 Prompt 组装与 Memory

`CareerResponseRunner` 组装模型消息，顺序大致为：

```text
系统求职规则
  + 已激活 SKILL.md 指令（可选）
  + 最近 6 条历史消息，每条最多 1600 字符
  + 当前用户问题
  + 当前附件解析文本
  + 已确认基准简历
  + 已确认目标岗位
  + 面经 RAG 证据
  → 最后一条 user message
```

这里有三类 Memory：

- 对话 Memory：PostgreSQL 保存完整消息，当前 Prompt 只读取最近 6 条；
- 职业事实 Memory：版本化保存简历档案和目标岗位，通过会话绑定注入每一轮；
- 检索 Memory：对面试准备问题，从面经库召回相关片段。

`session_summaries` 数据结构已经预留，但滚动摘要和事实槽位尚未接入当前主链。因此准确表述是“完整历史可追溯 + 有限窗口短期记忆 + 版本化职业上下文”，不能说已经支持无限长对话记忆。

### 4.6 ModelGateway

模型层没有把业务锁死在某个 Provider。`ModelGateway` 根据以下条件解析模型：

- 用户明确选择的模型档案，或 `free_quota_first` 策略；
- 模型是否启用；
- TEXT、TOOLS、EMBEDDING 等能力是否覆盖当前任务；
- 当前策略是否允许付费或本地模型；
- API Key 是否已经在服务端可用。

模型调用统一走 OpenAI-compatible Chat Completions 协议。数据库中保存的 Key 使用 Fernet 加密，调用时在服务端解密；也兼容环境变量凭据。浏览器只看到 readiness，不接触明文 Key。

### 4.7 Tool Calling 与 Skill

Skill 的工作方式分两层：

1. `CareerSkillRuntime` 只根据用户文本中的 `/skill` 或 `@skill` 确定性加载 SKILL.md；前端残留的 Skill ID 不能暗中激活指令；
2. 当本轮存在 Skill、模型支持 Tools 且 Registry 提供定义时，`CareerResponseRunner` 进入最多 6 轮的 Tool Calling Loop。

Tool Loop：

```text
LLM 返回 tool_calls
  → 服务端校验工具名和 JSON 参数
  → 执行白名单 Tool
  → 以 tool_call_id 回填结构化结果
  → LLM 决定继续调用或输出最终正文
```

当前 Registry 主要提供 Skill 搜索、GitHub Skill 仓库检查和项目级安装。Tool 失败会形成失败 Trace 并回到模型上下文，不会伪装成功。

## 5. 一次聊天 Turn 的完整调用链

```text
1. CareerAssistantPage 提交文本或附件
2. FastAPI 依据登录会话解析 actor，并校验 Conversation 归属
3. API 解析附件，读取职业上下文、Skill 和面经证据
4. prepare_turn_payload 保存完整可恢复输入，不保存原始二进制文件
5. PostgreSQL 单事务写 agent_turn + payload + queued event
6. API 返回 202；WebUI 开始订阅该 Turn 的持久化 SSE
7. Worker 从 PostgreSQL 原子领取可执行 Turn 和全局 Slot
8. Worker heartbeat 续租，并在线程中运行同步 Agent 主链
9. LangGraph 完成输入处理与用户消息持久化
10. ModelGateway 选择模型，ResponseRunner 组装 Prompt
11. LLM 流式生成，或执行最多 6 轮 Tool Calling
12. Worker 将 delta 按约 512 字符聚合写入事件表
13. 助手消息落库；done/error 与 Slot 释放在事务中收口
14. WebUI 收到终态；刷新或重连时可从 Last-Event-ID 继续
```

这一链路最值得在面试中强调的是：浏览器连接只是观察者，任务是否继续执行由数据库和 Worker 决定。切换会话、刷新页面或 SSE 断开，不会取消已经入队的任务。

## 6. 排队、并发和故障恢复

### 6.1 两条并发规则

```text
同一 Conversation：Turn 1 → Turn 2 → Turn 3，严格 FIFO
不同 Conversation：在全局 Slot 上限内并发执行
```

`claim_next` 在 PostgreSQL 事务中完成：

- 用 `FOR UPDATE SKIP LOCKED` 占用一个全局执行 Slot；
- 按 `queue_sequence` 找到最早可执行 Turn；
- 用 `NOT EXISTS` 排除同一 Conversation 中仍未完成的更早 Turn；
- 把 Turn 更新为 `running`，写入 Worker、Lease、Heartbeat 和 `started` Event。

因此，`asyncio` 只负责一个 Worker 内部的并发和定时心跳；跨进程一致性由 PostgreSQL 保证。增加 Worker 副本不会把全局并发上限重复放大。

### 6.2 任务生命周期

```text
queued → running → succeeded
                 ↘ failed
queued / running → cancelled 或 cancel_requested
```

Turn Payload、Event、Lease 和 Slot 都在数据库中。Worker 崩溃后可根据过期 Lease 恢复。当前运行中取消不是强杀 Provider 请求，而是协作式取消边界；排队中的任务可以立即取消。

### 6.3 持久化 SSE

事件类型包括 `queued / started / progress / delta / done / error`。每条事件有递增 ID，浏览器通过 `Last-Event-ID` 续传；没有新事件时发送 heartbeat。最终 Assistant Message 和 Turn 终态是权威结果，SSE 只是可恢复的展示通道。

## 7. 业务模块框架

### 7.1 求职对话与职业上下文

核心页面允许简历和目标岗位都为空，也允许只绑定其中一个：

- 没有资料：回答通用求职问题；
- 只有简历：围绕已有经历做表达和准备；
- 只有岗位：解释岗位要求和准备重点；
- 两者都有：才能做个性化匹配分析。

`candidate_profiles`、`target_role_profiles` 和 `conversation_context_bindings` 都有版本。浏览器不能直接提交一段“系统简历”伪造 Prompt；服务端按 actor 和 Conversation 重新读取当前绑定，并将 `binding_version` 写入 Turn Payload。

### 7.2 岗位证据 Judge

绑定简历和岗位后，系统创建 `job_match_assessments`，使用管理员固定 Judge 模型进行分析。它采用“模型判断、服务端验证与计分”的架构：

1. 服务端把 JD 和 CV 的非空段落编号为 `JD-001`、`CV-001`；
2. 模型通过唯一的 `submit_job_assessment` Tool 返回结构化维度、要求、判断和引用 ID；
3. 服务端验证引用必须真实存在，并检查 supported/partial 必须有 CV 证据；
4. 服务端根据 requirement type 和 verdict 重新计算指标；
5. 不生成综合录用分，只输出动态维度、关键缺口和证据；
6. Judge 不可用时，降级到确定性的 lexical 匹配算法。

这能回答面试官常问的“如何降低 LLM 幻觉”：不是只在 Prompt 里要求模型别编，而是让结论绑定原文 ID，并由服务端验证 Schema、引用和分数。

当前岗位评估通过 FastAPI `BackgroundTasks` 执行，并不是独立 Worker 队列；它的可靠性边界弱于聊天 Turn，这是后续可演进点。

### 7.3 职位库与 BOSS 浏览器扩展

BOSS 场景依赖用户登录态，而且没有在服务端接入稳定公开 API，因此采用 Chrome MV3 扩展：

```text
JobSearchWorkspace
  → window.postMessage
  → Content Script
  → chrome.runtime.sendMessage
  → Service Worker
  → 用户已登录的 BOSS 页面同源请求 / DOM 操作
```

扩展支持城市目录、岗位搜索、详情读取、打招呼预检和真实发送。网页拿到结构化岗位后，可以映射为目标岗位档案，再进入上下文绑定和岗位 Judge。

真实发送严格串行；验证码、限流、登录失效和结果未知会停止批次。它不导出 Cookie，也不把 BOSS 临时 Token 返回 WebUI。代价是依赖 BOSS 内部接口和 DOM，平台改版时需要维护适配层。

### 7.4 简历助手

简历助手是独立的“分析—确认—生成—保存”流程：

```text
上传简历 + JD
  → AttachmentParser 提取文本
  → LLM 生成结构化修改建议
  → 用户选择接受哪些建议
  → LLM 只按已确认建议重写
  → 保存原版预览、建议、优化 Markdown、DOCX、PDF
```

核心约束是事实不新增：Prompt 要求保持公司、项目、技术和数字的事实边界；用户先审核建议，再生成最终版本。文件层使用 `python-docx`（不可用时有最小 DOCX 容器降级）和 PDF 输出逻辑。

### 7.5 面经库与 RAG

面经入库链：

```text
手工文本 / 文件 / 公开页面
  → 元数据提取与人工确认
  → HierarchicalMarkdownChunker
  → PostgreSQL 全文检索字段
  → 可选 1024 维 Embedding / pgvector
```

查询链：

```text
用户问题 + 当前公司 + 当前岗位
  ├─ PostgreSQL lexical retrieval
  └─ optional semantic retrieval
          ↓
       RRF 融合
          ↓
最多 4～6 条带来源证据
          ↓
注入 CareerResponseRunner Prompt
```

只有用户显式选择面经，或当前会话有目标岗位且问题属于面试准备意图时，才执行自动检索，避免普通闲聊被无关语料污染。Embedding 默认可关闭；向量服务失败时透明降级为关键词检索。

### 7.6 实时面试助手

浏览器方案通过 Web Audio API / AudioWorklet 获取用户明确授权的系统音频和可选麦克风，使用业务 WebSocket 把 PCM 帧发给后端：

```text
系统音频 / 麦克风
  → AudioWorklet 分帧与重采样
  → FastAPI WebSocket
  → Realtime ASR Provider
  → TranscriptAssembler
  → RuleBasedQuestionDetector
  → LiveAnswerService
  → 流式回答事件
```

`LiveSessionManager` 为每个连接维护问题版本号。新问题到来时取消旧回答，前端也按 `question_version + attempt + delta_index` 忽略迟到或重复片段，避免上一题回答串入下一题。

会话、最终转写和回答会持久化；结束后可把本次识别到的问题去重归档到面经库。当前浏览器实时回答主要基于问题本身，追问时只使用最近两条对话理解指代；虽然契约预留了简历、岗位和面经字段，但 Web 构建的 `LiveAnswerContext` 仍为空，不能声称实时回答已经完成个性化上下文注入。

### 7.7 Skill 库

Skill 库把专业工作流从硬编码 Prompt 中拆出。求职 Agent 可以确定性挂载项目中的 SKILL.md，并在模型支持时执行 Registry 白名单 Tool。这样增加新工作流时不必改动主 Agent 的每个分支，但仍需为真正的外部动作实现受控 Tool，不能把“模型读到了 Skill 指令”误认为“工具已经执行”。

### 7.8 评测与可观测性

LangSmith 是旁路 Trace，默认只记录阶段、计数、耗时、Provider、模型等 metadata，不上传简历正文、问题正文、业务 ID 或密钥。观测失败不阻断业务。

项目内的评测中心使用独立 `evaluation` schema 保存数据集、冻结版本、案例、实验、Run 和指标。已经接通面经 RAG 的真实实验 Runner，支持 Recall@6、MRR、nDCG@6；求职 Agent 和简历优化的完整执行 Adapter 仍是后续工作。

## 8. 文档与图片材料处理

附件采用分层路由，而不是所有文件都调用昂贵模型：

| 输入 | 首选路径 | 增强或降级 |
| --- | --- | --- |
| 原生文本 PDF | `pypdf` | 文本覆盖不足时调用 Docling |
| 扫描 PDF | PDF Probe → Docling OCR | Docling 失败时保留可用原生文本并说明限制 |
| DOCX / XLSX | Docling Serve | 服务关闭时给出可操作提示 |
| DOC / XLS | Gotenberg/LibreOffice 转 PDF | 再复用 PDF + Docling 链路 |
| JPG / PNG / WebP 等 | Pillow 校验 → Docling OCR | 文本质量不足时用固定 Cloud Vision 复核 |

API 在附件解析成功并将完整解析文本写入 Turn Payload 后删除原始文件；Worker 不依赖共享附件目录。需要注意，解析文本本身仍属于敏感数据，会进入 PostgreSQL Payload，不能把“删除原文件”讲成“系统不保存任何简历内容”。

## 9. 数据模型

可以把核心表分为六组理解：

| 数据域 | 代表表 | 作用 |
| --- | --- | --- |
| 身份与模型 | organizations、actors、model_profiles、model_profile_credentials | 组织、用户、模型能力和加密凭据 |
| 对话 | conversations、messages、session_summaries | 会话历史和预留摘要 |
| Agent 执行 | agent_turns、agent_turn_payloads、agent_turn_events、agent_execution_slots | Turn、可恢复输入、SSE 事件和全局并发 |
| 职业上下文 | candidate_profiles、target_role_profiles、conversation_context_bindings | 简历、岗位和版本化绑定 |
| 专业结果 | job_match_assessments、resume_optimization_records | 岗位评估和简历优化产物 |
| 面试数据 | interview_experiences、interview_chunks、live_interview_sessions/utterances/answers | RAG 语料、实时会话与归档 |

数据按 `organization_id` 和/或 `actor_id` 做访问边界。Conversation 与 Message 的读取还会校验 actor 归属。

## 10. 主要技术栈及用途

| 层次 | 技术 | 在项目中的用途 |
| --- | --- | --- |
| Web 前端 | Vue 3、Vite、Element Plus | 求职工作台、上下文侧栏、队列恢复、面经和管理页面 |
| 浏览器能力 | Chrome MV3、Content Script、Service Worker | 复用 BOSS 登录态读取岗位和执行用户确认后的操作 |
| 桌面端 | Electron、Vue、TypeScript | Windows 双音轨实时面试备选方案 |
| API | FastAPI、Pydantic | REST、Multipart、SSE、WebSocket 和请求契约 |
| Agent 编排 | LangGraph | 固定 Intake 状态图和节点级进度 |
| 并发 | asyncio、`asyncio.to_thread` | Worker 本地调度、心跳和同步 Agent 适配 |
| 数据访问 | SQLAlchemy 2、Alembic、psycopg | PostgreSQL 事务、仓储和迁移 |
| 数据库 | PostgreSQL 16、JSONB、全文检索、pgvector | 业务事实、任务队列、事件和混合检索 |
| 模型协议 | OpenAI-compatible Chat Completions / Tools / Embeddings | 多 Provider 模型、Tool Calling 和向量化 |
| 文档 | pypdf、Pillow、Docling Serve、Gotenberg | PDF/图片/Office 解析与格式转换 |
| 实时语音 | Web Audio API、AudioWorklet、WebSocket、Qwen-Audio/OpenAI Realtime Adapter | 音频分帧、ASR 和实时回答 |
| 可观测性 | LangSmith | metadata-only Trace，不作为业务事实来源 |
| 质量评估 | 项目内 Evaluation Service | 冻结数据集、可复现实验和确定性指标 |
| 部署 | Docker Compose、Caddy、Nginx | HTTPS、静态页面、API、Worker、PostgreSQL 和文档服务 |

## 11. 最关键的技术取舍

### 11.1 PostgreSQL 权威队列，而不是只用 asyncio Queue

进程内 Queue 实现简单，但 API 重启、浏览器刷新和多 Worker 部署都会丢状态或破坏并发上限。使用 PostgreSQL 后，业务数据和调度事实可以在同一事务边界内管理，并利用行锁、`SKIP LOCKED`、Lease 和 Slot 实现可靠领取。

代价是 Event 表增长、数据库轮询和连接池压力，需要后续做归档、监控和容量规划。

### 11.2 服务端预检索，而不是让 Agent 自主决定是否 RAG

求职场景的面经证据有明确作用域和引用要求。服务端根据面试意图、会话岗位和显式选择确定检索范围，可以保证每次检索可复现、可限制、可评测。代价是 Agent 自主性较低，但事实边界更清楚。

### 11.3 LLM Judge 提取证据，服务端验证和计分

LLM 擅长理解 JD 与简历语义，但不适合作为分数和引用合法性的最终权威。模型只提交结构化判断，服务端检查 ID、枚举、证据和公式。这样兼顾语义能力与可解释性。

### 11.4 API 预解析附件，Worker 只处理持久化文本

这样 Worker 扩容不需要共享临时文件系统，原文件可以尽早清理，任务恢复也不依赖文件还存在。代价是 API 承担文档解析耗时和资源压力；大规模使用时应把解析进一步拆成持久化文档任务。

### 11.5 Provider Adapter 与能力标签

统一 OpenAI-compatible 协议降低了 Provider 替换成本，能力标签阻止 TEXT-only 模型被误用于 Tool 或 Embedding 任务。代价是非兼容 Provider 需要单独 Adapter，不能只填一个 URL 就接入。

## 12. 当前边界与改进方向

面试中主动说明边界，通常比把项目包装成“全都做完”更可信。

| 当前边界 | 影响 | 合理演进 |
| --- | --- | --- |
| 最近 6 条消息窗口，没有滚动摘要 | 长对话可能忘记早期细节 | Summary + User Facts + Retrieval Memory，并用版本/CAS 更新 |
| 岗位 Judge 用 FastAPI BackgroundTasks | API 重启时可靠性弱于聊天 Turn | 迁入统一持久化 Job/Worker |
| Semantic RAG 默认关闭 | 默认主要依赖 lexical retrieval | 验证 1024 维模型与索引后开启，并用评测门槛发布 |
| 运行中 Turn 不能硬中断 Provider | 取消存在延迟 | Provider 支持取消时增加安全检查点和请求级 cancellation |
| 普通用户可能争用全局 Slot | 缺少 actor 级公平性 | 用户/组织并发配额、限流和成本预算 |
| Tool 写操作影响项目级目录 | 多租户权限风险 | RBAC、只读 Tool、用户私有执行沙箱 |
| 实时回答未注入个人职业上下文 | 回答通用，个性化不足 | 构建受事实约束的 LiveAnswerContext，并做低延迟评测 |
| BOSS 内部接口和 DOM 可能变化 | 扩展维护成本高 | 协议版本、能力握手、契约测试和人工降级入口 |
| Turn Payload 保存完整解析文本 | 数据库含敏感求职材料 | 数据保留策略、字段级加密、访问审计和按用户删除 |
| 仓库保留旧直接流式接口 | 两条执行路径增加维护成本 | 迁移完成后逐步删除旧路径 |

## 13. 代码证据索引

| 主题 | 关键文件 |
| --- | --- |
| FastAPI 组装与依赖注入 | `src/career_assistant/web/router.py` |
| 持久 Turn API 与 SSE | `src/career_assistant/web/turn_router.py` |
| Agent 输入契约 | `src/career_assistant/contracts.py` |
| LangGraph Intake | `src/career_assistant/intake_graph.py` |
| Prompt、Memory、LLM 与 Tool Loop | `src/career_assistant/response_runner.py` |
| 模型路由 | `src/career_assistant/model_gateway.py` |
| Worker | `src/career_assistant/turn_worker.py` |
| PostgreSQL 队列 | `src/career_assistant/persistence/turn_job_repository.py` |
| Turn 序列化与恢复 | `src/career_assistant/turn_payloads.py` |
| Skill 挂载与 Tool Registry | `src/career_assistant/skill_runtime.py`、`skill_tools.py` |
| 文档处理 | `src/career_assistant/attachments.py`、`document_parsing.py` |
| 职业上下文 | `src/career_assistant/context_profiles.py`、`persistence/context_repository.py` |
| 岗位 Judge | `src/career_assistant/job_assessment.py` |
| 面经 RAG | `src/career_assistant/interview_library/` |
| 简历优化 | `src/career_assistant/resume_assistant/` |
| 实时面试 | `src/career_assistant/live_interview/` |
| 前端主页面 | `web-ui/src/components/CareerAssistantPage.vue` |
| 前端 Turn 恢复 | `web-ui/src/career-turn-client-queue.js`、`career-turn-observation.js` |
| BOSS 扩展 | `browser-extension/job-library/` |
| 数据库结构 | `migrations/versions/20260805_01_*` 至 `20260823_19_*` |
| 配置 | `config/career_assistant.yaml`、`docker-compose.production.yml` |

## 14. 验证结果

本次文档编写期间，基于当前未提交工作区实际执行：

- 后端核心模块：104 项测试通过；
- Vue WebUI：101 项测试通过；
- BOSS 职位库扩展：19 项测试通过；
- Electron 实时面试客户端：8 项测试通过；
- 后端出现 2 条 FastAPI `on_event` 弃用警告，不影响本次测试通过，但应逐步迁移到 lifespan。

这些结果验证了核心单元和契约，不等同于真实模型、BOSS 页面、实时音频和生产并发压测。

## 15. 面试讲解模板

### 15.1 30 秒版本

> 我做的是一个 AI 求职工作台，不只是聊天机器人。它以版本化的简历和目标岗位作为事实上下文，把求职对话、岗位证据评估、简历优化、面经 RAG 和实时面试串起来。架构上我用 LangGraph 管确定性输入流程，用 LLM 做语义分析和受控 Tool Calling，用 PostgreSQL 持久化 Turn 队列、事件和并发槽位，再由独立 Worker 执行耗时任务，所以浏览器刷新或服务重启也能恢复。对容易幻觉的岗位匹配，我让模型只提交带 JD/CV 原文编号的结构化判断，再由服务端校验引用并重新计分。

### 15.2 两分钟版本

> 这个项目的核心问题是：求职建议必须结合真实简历和岗位，但 LLM 天然容易丢上下文、编经历，而且文档解析和生成时间长。我的方案是先把简历和岗位做成版本化 Context，每个 Conversation 绑定一个当前版本。
>
> 用户发送消息后，FastAPI 先完成身份和会话校验，解析附件，并把完整的 Turn Payload 与 queued Event 原子写入 PostgreSQL，然后立即返回 202。独立 Worker 用 `FOR UPDATE SKIP LOCKED` 领取任务，同一会话严格 FIFO，不同会话按全局 Slot 并发。Worker 内部先跑 LangGraph 的固定 Intake Graph，完成校验、上下文构建、材料解析、脱敏、历史落库和清理，再由 ModelGateway 选择满足能力和策略的模型。
>
> Prompt 会组合最近对话、已确认简历、目标岗位和面经 RAG 证据。普通问题直接流式生成；显式激活 Skill 且模型支持 Tools 时，会进入最多 6 轮的白名单 Tool Calling。生成事件持久化到数据库，前端用 Last-Event-ID 恢复 SSE，所以 HTTP 连接和任务生命周期是解耦的。
>
> 在岗位评估上，我没有直接相信模型给的分数，而是给 JD/CV 原文编号，让模型返回结构化证据引用，服务端校验引用和判断，再重新计算指标。这是项目里我最想强调的设计：把 LLM 放在它擅长的语义判断位置，把一致性、权限、计分和恢复交给确定性系统。

### 15.3 面试官追问时的展开顺序

建议固定按以下顺序讲，避免跳跃：

1. 业务问题：为什么普通聊天机器人不够；
2. 数据边界：简历、岗位、Conversation、Turn 的关系；
3. Agent 主链：LangGraph、ModelGateway、Prompt、Tool；
4. 可靠性：PostgreSQL 队列、FIFO、Slot、Lease、SSE 恢复；
5. 专业能力：岗位 Judge、RAG、简历优化、实时面试；
6. 取舍：为什么这样设计，以及当前边界；
7. 结果：测试、评测和下一步量化指标。

### 15.4 高频追问与回答要点

#### 为什么使用 LangGraph，不直接调用模型？

输入校验、材料解析、脱敏、持久化和清理是不可跳过的稳定步骤，需要节点级状态和真实进度；开放式回答才交给模型。LangGraph 在这里是可观察的流程骨架，不是为了追求“Agent”概念。

#### 为什么不用 Celery 或 Redis？

当前项目已经以 PostgreSQL 保存会话和 Turn，队列需要和 Payload、Event、状态迁移共享事务。使用 PostgreSQL 可以先减少一套基础设施，并通过行锁、Lease 和 Slot 满足当前规模。更大规模下可再根据数据库压力评估专用队列。

#### 如何避免简历幻觉？

三层约束：Prompt 明确事实边界；简历和岗位由服务端按会话绑定注入，浏览器不能伪造系统上下文；关键岗位判断必须引用服务端编号的原文，并由服务端验证和计分。完整 Claim 级事实校验仍是后续方向。

#### RAG 为什么用 Hybrid + RRF？

关键词检索擅长公司名、岗位名和技术名词，向量检索擅长语义相似。两者分数尺度不同，RRF 只依赖排名，融合简单稳定；向量服务失败还能无缝降级到关键词召回。

#### 多用户并发如何保证顺序？

数据库按 `queue_sequence` 排序，用 `NOT EXISTS` 禁止同一 Conversation 的后续 Turn 越过前一轮；不同 Conversation 通过全局 Slot 并发。`SKIP LOCKED` 允许多个 Worker 安全领取不同任务。

#### 系统如何恢复断线？

任务和事件不在浏览器内存，而在 PostgreSQL。浏览器通过 Turn ID 查询状态，并携带 `Last-Event-ID` 重连 SSE；Worker 与 HTTP 连接解耦，最终消息和终态仍可从数据库恢复。

## 16. 讲解时不要说错的五件事

1. 不要说“这是 Multi-Agent 系统”；当前是混合式单 Agent + 专业服务。
2. 不要说“支持无限上下文”；当前只注入最近 6 条消息，长期摘要未接主链。
3. 不要说“所有问题都会 RAG”；只有显式选择或岗位相关的面试准备意图才检索。
4. 不要说“删除附件后不保存简历”；原始二进制会删除，但完整解析文本会进入 Turn Payload。
5. 不要说“实时面试已经结合个人简历回答”；当前实时回答的个性化 Context 尚未真正接入。

## 17. 最后的总结句

这个项目的核心价值不是堆叠了多少 AI 功能，而是把概率性的模型能力放进了一个有事实边界、状态边界、权限边界和恢复能力的工程系统：LangGraph 让过程可控，PostgreSQL 让任务可靠，证据引用和服务端校验让结果可解释，模块化上下文又让岗位、简历、面经和面试能够复用同一份求职事实。
