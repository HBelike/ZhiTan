# Windows 实时面试助手设计

> 日期：2026-08-23
> 状态：MVP 已实施并完成自动验收；真实会议稳定性验收待外部条件
> 产品边界：仅支持 Windows 10/11；仅用于模拟面试或明确允许使用 AI 的面试场景。

## 1. 目标

在现有求职助手中新增 Windows 桌面实时面试助手。用户开始视频或语音通话后，桌面伴侣同时采集系统输出音频和麦克风音频，将双方对话实时转写，识别面试官的问题与追问，并在独立区域流式生成统一中文的回答建议。

首版成功标准：

1. 系统音频稳定映射为“面试官”，麦克风稳定映射为“应试者”。
2. 支持中文、英文和中英混合原文转写；回答统一使用中文，专有名词保留原文。
3. 自动识别面试问题，并提供“立即生成”“停止生成”“重新生成”手动兜底。
4. 个人经历、职责、业绩和数字只使用用户已确认的材料；通用知识题允许使用模型知识。
5. 默认不保存原始音频，不实现隐藏、规避检测或无提示录音能力。
6. 问题结束到首个可用回答骨架的目标 P95 不超过 2 秒。

## 2. 已确认范围

### 2.1 本次实现

- Windows 10/11 x64 桌面伴侣。
- Zoom、Microsoft Teams、腾讯会议、Google Meet 等播放到 Windows 系统输出设备的通话。
- 系统音频与麦克风双通道采集。
- 云端流式 ASR，首个实现为 OpenAI Realtime transcription，并保留 Provider 接口。
- 中文、英文及中英混合转写。
- 原始转写、受约束术语纠正、问题检测、追问识别。
- 简历、目标岗位、技能资料和面经 RAG 上下文。
- 中文流式回答、版本取消、手动触发和会后历史。
- 音频、ASR、模型和网络状态提示。

### 2.2 明确不做

- macOS、Linux、iPad 或移动端适配。
- 虚拟摄像头、会议平台插件或自动加入会议机器人。
- 录制或上传视频画面。
- 默认保存原始 PCM、WAV 或其他音频文件。
- 隐藏进程、隐藏窗口、规避屏幕共享、规避监考或未经参与者授权的录音。
- 首版离线 ASR；只预留 FunASR 等本地 Provider 扩展点。
- 多面试官声纹身份识别；首版角色由独立音频通道确定。
- 自动代替用户发言或生成语音。

## 3. 方案选择

### 3.1 采用 Electron 桌面伴侣

首版使用 Electron + Vue 3 + TypeScript。Electron 负责 Windows 桌面窗口、系统音频授权、麦克风设备和音频工作线程；业务处理继续由现有 FastAPI 服务承担。

选择理由：

- 当前前端已经使用 Vue 3、Vite 和 TypeScript 生态，复用成本低。
- 只支持 Windows，不需要为跨平台原生采音提前承担 Tauri/Rust 的实现和发布成本。
- 音频通道可以在桌面端保持分离，角色识别无需先依赖 diarization。
- 桌面端只负责采集和展示，API Key、RAG、模型调用和组织边界仍由服务端管理。

### 3.2 未采用的方案

- **Tauri + Rust**：体积和资源占用较低，但 Windows 单平台首版的投入收益不足。
- **纯浏览器**：`getDisplayMedia()` 的系统音频支持取决于浏览器、操作系统和用户选择的共享对象，无法作为跨会议软件的稳定主路径。
- **会议机器人**：会以显式参会者身份进入会议，部署和平台兼容成本高，不符合本地伴侣定位。

## 4. 总体架构

```text
Electron 桌面伴侣
  ├── 系统音频捕获 ── channel=interviewer
  ├── 麦克风捕获   ── channel=candidate
  ├── AudioWorklet：重采样、PCM16 分帧、音量状态
  └── 单个业务 WebSocket：事件复用与 UI 状态
                 │
                 ▼
FastAPI Live Interview WebSocket
  ├── 登录身份、组织边界和会话所有权
  ├── LiveSessionManager
  ├── AsrProvider × 2
  │    └── OpenAI Realtime transcription
  ├── TranscriptAssembler
  ├── TerminologyCorrector
  ├── QuestionDetector
  ├── LiveContextBuilder
  │    ├── 当前简历与目标岗位
  │    ├── 最近实时对话
  │    ├── 用户确认的技能与项目事实
  │    └── InterviewRetrievalService
  ├── LiveAnswerService
  │    └── ModelGateway + 流式 Chat Client
  └── PostgreSQL：会话、最终话语和回答
```

服务端为每个实时会话维护短生命周期内存状态，但最终话语、问题版本和回答状态写入 PostgreSQL。原始音频只在桌面端、网络传输缓冲和 ASR Provider 会话中短暂存在。

## 5. 页面与交互

遵循“单页单任务”，桌面伴侣拆为三个路由：

### 5.1 `/live-interview/setup`

只承担开场准备：

- 选择当前简历和目标岗位。
- 选择需要使用的面经资料。
- 选择麦克风和系统输出设备。
- 展示双通道音量测试。
- 选择已由管理员配置的 ASR 与回答模型。
- 显示录音授权和数据处理说明。
- 通过预检后创建实时会话。

### 5.2 `/live-interview/session/:id`

只承担实时辅助：

- 左栏按时间显示面试官和应试者对话。
- partial 转写使用弱化样式，final 转写固定显示。
- 原文发生受约束纠正时，保留可查看的原文。
- 右栏显示当前问题、中文回答骨架、展开建议、材料证据和潜在追问。
- 顶部显示系统音频、麦克风、ASR、回答模型和网络状态。
- 底部提供“立即生成”“停止生成”“重新生成”“结束面试”。

### 5.3 `/live-interview/history/:id`

只承担会后查看：

- 最终对话时间线。
- 每个问题及其最终有效回答。
- 被后续补充替代的旧回答状态。
- 材料证据来源。
- 首版不自动生成新的综合复盘；复盘作为后续独立模块。

## 6. 双通道音频设计

### 6.1 角色归属

- `system` 通道固定映射为 `interviewer`。
- `microphone` 通道固定映射为 `candidate`。
- 不把混合后的单路音频交给 LLM 猜角色。
- 串音或回声只作为音频质量问题处理，不在首版引入声纹识别。

### 6.2 音频格式

- 桌面端内部使用浮点音频帧处理。
- 发送服务端前统一转为单声道 PCM16。
- 首个 OpenAI Realtime Provider 使用 24 kHz；Provider 接口负责声明目标采样率。
- 每个音频事件必须包含 `session_id`、`channel`、`sequence` 和音频负载。
- 两个通道分别维护单调递增序号，不把跨通道到达顺序当作真实发言顺序。

### 6.3 生命周期

音频只有在用户明确点击“开始面试”后采集。WebSocket 断开、用户结束会话或桌面进程退出时，立即停止两个采集源并释放设备。

## 7. ASR 与术语处理

### 7.1 Provider 合同

`AsrProvider` 对业务层暴露：

- `start(session_context)`
- `append_audio(channel, pcm)`
- `commit(channel)`
- `events()`
- `close()`

统一事件包含 `partial`、`final`、`speech_started`、`speech_stopped`、`warning` 和 `error`。首个真实实现连接 OpenAI Realtime transcription；测试实现根据脚本产生可重复事件。

### 7.2 双语策略

- ASR 输出保持原始语言，不自动翻译。
- 允许中文、英文和中英混合。
- 回答层始终输出中文。
- 技术、医疗、金融、法律、制造、教育等领域的专有名词按原文保留。

### 7.3 动态热词

会话开始前从下列已确认材料提取候选热词：

- 姓名、公司、岗位和组织名称。
- 简历与项目中的产品、技术、设备、药品、法规、指标和缩写。
- JD 中的领域术语。
- 用户选中的面经标题与高频关键词。
- 用户手工维护的词表。

支持热词提示的 ASR Provider 优先在识别阶段使用；不支持时交给 `TerminologyCorrector`。

### 7.4 受约束纠错

- `raw_text` 永久保留。
- `corrected_text` 单独保存，不能覆盖原文。
- 纠错只允许用候选热词替换发音或拼写相近片段。
- 不允许补充原话没有的事实、数字或句子。
- 无足够置信度时保持原文。

## 8. 问题检测与版本状态机

### 8.1 触发条件

只有 `interviewer` 的 final 话语进入自动问题检测。`candidate` 话语只进入会话上下文，不自动触发答案。

检测采用两层策略：

1. 确定性预筛：最小长度、停顿边界、疑问结构、祈使式任务和重复片段。
2. 轻量 LLM JSON 分类：是否需要回答、问题类型、是否追问、规范化问题和置信度。

问题类型首版固定为：

- `behavioral`
- `project_deep_dive`
- `knowledge`
- `case_or_scenario`
- `system_design`
- `coding_or_algorithm`
- `follow_up`
- `statement`

### 8.2 版本规则

- 每个检测成功的问题分配递增 `question_version`。
- 生成请求和所有答案事件必须携带该版本。
- 面试官继续补充、纠正或追问时创建新版本。
- 新版本确认后取消旧版本的回答任务。
- 已取消答案保留历史，但 UI 标记“已被后续问题替代”。
- 客户端忽略版本小于当前活动版本的迟到增量。

### 8.3 手动控制

- “立即生成”使用最近一个完整面试官话语，即使自动分类未触发。
- “停止生成”取消当前版本但不删除已出现内容。
- “重新生成”沿用同一问题文本并创建新的回答尝试。

## 9. 上下文与回答生成

### 9.1 上下文顺序

`LiveContextBuilder` 按以下顺序构造最小上下文：

1. 回答规则和统一中文要求。
2. 当前规范化问题、意图与追问关系。
3. 最近双方对话窗口。
4. 当前简历、目标岗位和用户确认的项目事实。
5. 面经 RAG 证据。
6. 动态热词和必须保留原文的专有名词。

实时链路不直接复用普通聊天 Intake Graph，以避免附件解析、Skill Tool Loop 和消息持久化流程增加延迟；只复用领域仓储、RAG、模型网关和流式 Client。

### 9.2 事实边界

- 概念、行业知识、分析方法和通用解决方案可使用模型知识。
- 个人经历、职责、业绩、时间、规模和数字只能来自用户确认材料。
- 缺少个人证据时输出“可表达思路”，明确要求用户替换为真实经历。
- 不把面经中的他人经历改写成用户自己的经历。

### 9.3 两阶段输出

回答流分为：

1. **回答骨架**：直接结论和 3～5 个要点，目标尽快可读。
2. **展开建议**：表达示例、真实证据、风险和可能追问。

服务端使用结构化内部合同，并把用户可读字段分别流式推送：

```json
{
  "intent": "project_deep_dive",
  "direct_answer": "先给出直接结论",
  "answer_points": ["要点一", "要点二"],
  "personal_evidence": ["来自简历或技能卡的事实"],
  "follow_up_risks": ["可能继续追问的内容"]
}
```

## 10. WebSocket 协议

### 10.1 客户端事件

- `session.start`
- `audio.append`
- `audio.commit`
- `answer.request`
- `answer.cancel`
- `session.end`
- `ping`

### 10.2 服务端事件

- `session.ready`
- `transcript.partial`
- `transcript.final`
- `question.detected`
- `answer.started`
- `answer.delta`
- `answer.completed`
- `answer.cancelled`
- `warning`
- `error`
- `pong`

控制事件使用 JSON。音频首版也使用带 Base64 PCM 的 JSON，以便先完成协议和测试；真实性能验收若表明编码开销影响 P95，再在同一事件合同下切换为二进制帧，不提前引入第二套协议。

## 11. 数据模型

### 11.1 `live_interview_sessions`

保存：

- 组织、Actor、会话 ID。
- 绑定的候选人档案、目标岗位和面经 ID 快照。
- ASR 与回答模型档案标识。
- `preparing`、`active`、`completed`、`failed` 状态。
- 开始、结束和更新时间。

### 11.2 `live_interview_utterances`

保存：

- 所属会话和角色。
- `raw_text`、可选 `corrected_text`。
- 开始、结束时间和通道序号。
- ASR Provider 与置信度元数据。

只保存 final 话语，不保存 partial。

### 11.3 `live_interview_answers`

保存：

- 所属会话、问题版本和回答尝试序号。
- 原始问题、规范化问题和意图。
- `generating`、`completed`、`cancelled`、`failed` 状态。
- 最终回答正文、证据引用和错误类别。

### 11.4 不持久化内容

- PCM 音频帧和原始录音。
- ASR 临时密钥或 WebSocket 凭证。
- partial 转写。
- Provider 原始错误响应正文。

## 12. 认证与配置

- WebSocket 沿用当前平台登录身份和组织边界。
- 创建会话、读取上下文和查看历史均校验 `organization_id + actor_id`。
- ASR Key 只从服务端加密凭据或部署环境读取，不发给 Electron。
- 模型档案新增实时转写能力标记；现有聊天模型继续由 `ModelGateway` 选择。
- Electron 只接收可展示的 Provider 名称、状态和错误分类。

## 13. 容错与资源边界

- ASR 短暂断线时，每通道最多缓存约 10 秒音频。
- 缓存溢出时丢弃最旧帧并发送明确 warning，不伪装完整转写。
- 音频设备变化时暂停对应通道，提示用户重新选择。
- LLM 超时时保留已生成内容并允许重新生成。
- WebSocket 断开后立即停止桌面采集；服务端取消活动答案并关闭两个 ASR 会话。
- 同一实时会话只允许一个活动业务 WebSocket和一个活动答案流。
- 服务端限制单帧大小、事件频率、会话时长和每个 Actor 的活动会话数。
- 结束会话时以幂等方式关闭音频、ASR、答案任务和数据库状态。

## 14. 代码边界

新增独立桌面工程：

```text
desktop-interview-assistant/
  electron/
  src/
    pages/
    components/
    audio/
    protocol/
    stores/
```

新增后端模块：

```text
src/career_assistant/live_interview/
  contracts.py
  session_manager.py
  transcript_assembler.py
  terminology.py
  question_detector.py
  context_builder.py
  answer_service.py
  persistence.py
  web.py
  asr/
    base.py
    fake.py
    openai_realtime.py
```

新增 Alembic 迁移和独立测试文件。现有 `CareerAssistantPage.vue`、普通聊天 Intake Graph 和公众号媒体流水线不承担实时面试职责。

## 15. 验证与验收

### 15.1 自动化测试

- 双通道到角色的确定性映射。
- PCM 分帧序号和乱序处理。
- partial/final 合并、重复片段消除和 final 持久化。
- 中文、英文及中英混合事件。
- 热词受约束纠错不新增事实。
- 问题、陈述、追问、补充和打断分类。
- 新问题取消旧答案，客户端忽略迟到增量。
- 手动生成、停止和重新生成。
- 个人经历回答不得脱离材料。
- 面经检索继续保留来源标识。
- WebSocket 断开、ASR 重连、缓冲溢出和幂等结束。
- 组织与 Actor 隔离。
- Electron 单元测试、后端 Python 回归和两个生产构建。

### 15.2 Windows 真实验收

在真实 Windows 设备上完成：

1. 耳机和扬声器各验证一次双通道采集。
2. 至少选择 Zoom、Teams、腾讯会议或 Google Meet 中两个进行通话测试。
3. 中文、英文、中英混合以及包含非 IT 领域专有名词的语料各一组。
4. 面试官补充问题时验证旧答案被取消。
5. 断网、切换输出设备和关闭窗口时验证明确停止采集。
6. 连续运行 60 分钟，无失控重连、重复回答或上下文无限增长。

### 15.3 指标目标

- 问题结束到回答骨架首段：P95 ≤ 2 秒。
- 自动问题检测 Precision ≥ 90%。
- 正常双通道条件下角色归属 ≥ 98%。
- 用户点击结束或连接断开后 2 秒内停止本地音频采集。
- 原始音频持久化数量为 0。

## 16. 实施顺序

1. 先建立协议、领域合同、迁移和 Fake ASR，完成纯后端可重复测试。
2. 实现问题状态机、上下文组装和版本化流式回答。
3. 建立 Electron 单任务页面和 Fake Audio/ASR 联调。
4. 实现 Windows 系统音频、麦克风和 AudioWorklet。
5. 接入 OpenAI Realtime transcription，完成双语真实验证。
6. 补齐历史页、故障提示、构建和 60 分钟稳定性验收。

每个阶段同步更新模块文档，记录设计目标、技术取舍、调用链、依赖、验证结果和后续边界。
