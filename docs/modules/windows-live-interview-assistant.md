# Windows 实时面试助手

## 设计目标

该模块作为现有“求职助手”中的“面试大师”工具运行，为 Windows 10/11 视频或语音通话提供本地桌面采集能力：分别采集 Windows 系统输出和麦克风，把前者固定标记为面试官、后者固定标记为应试者；实时转写中文、英文及中英混合对话，检测面试官的问题，并把当前问题文本直接交给所选回答模型，向应试者流式输出统一中文的回答建议。

模块只用于模拟面试或参与者明确允许使用 AI 的场景。不实现隐藏进程、规避屏幕共享、绕过监考、未经授权录音、自动代替用户发言、视频采集或 macOS 适配。

## 技术取舍

- 桌面端使用 Electron + Vue 3 + TypeScript。Electron 官方的 `setDisplayMediaRequestHandler` 支持在 Windows 使用 `audio: "loopback"` 捕获系统音频；麦克风继续使用独立 `getUserMedia`，因此不需要让 LLM 猜双方角色。
- 产品入口位于现有求职助手会话顶部的“面试大师”按钮。按钮调用本地 `/api/career/live-interviews/desktop/launch`，直接启动或聚焦 Electron 采集窗口，并把当前求职助手服务地址传给桌面端；首次 Electron 二进制尚未完成时会在无终端窗口的后台线程中准备，完成后自动打开，不要求用户手工执行 `npm start`。
- Electron preload 生产构建固定输出为 CommonJS `preload.cjs`。这是桌面桥接能否加载的必要条件；不能在 `type=module` 项目里把 ESM `preload.js` 直接交给 sandbox preload，否则只会出现空白深色窗口。
- 不对接腾讯会议、Teams、Zoom 等会议软件 API。系统中任何正在播放的对方声音都由 Windows loopback 捕获，用户麦克风作为另一条独立音轨。
- 音频由 `AudioWorklet` 读取，重采样为 24 kHz 单声道 PCM16，并以 100 ms 帧发送。系统音频和麦克风分别维护 `sequence`。
- 转文字不是由普通聊天 LLM 完成。默认 Provider 使用阿里云百炼 `qwen-audio-3.0-asr-flash-streaming`，通过 DashScope WebSocket 接收 24 kHz PCM 并流式输出中英混合文本；OpenAI transcription 仍作为兼容备用。普通 LLM 只负责问题识别和中文回答生成。
- `DeepSeek` 等仅声明 `text` 能力的模型不能承担语音转写。准备页只有在发现 `DASHSCOPE_API_KEY`、`OPENAI_API_KEY` 或可用的 transcription 模型档案时才允许开始；否则直接显示配置说明，不创建必然失败的会话。
- ASR 保留原始语言，不自动翻译；回答 Prompt 强制使用中文，并要求各行业专有名词保留原文。
- 当前问题识别先使用低延迟确定性规则，Provider/Detector 均保留替换接口。回答 Prompt 只包含当前面试官问题和输出规则，不读取简历、目标岗位、面经或历史对话；遇到个人经历和数字类问题时只给可替换的回答结构，不编造事实。
- 服务端只保存 session、final utterance 和 answer。PCM、WAV、partial 转写、API Key 和 Provider 原始错误正文都不进入业务表。

## 调用链

```text
Windows loopback ─┐
                  ├─ AudioWorklet → PCM16/24kHz → Electron 主进程 WebSocket
麦克风 ───────────┘                                  │
                                                    ▼
FastAPI /api/career/live-interviews/{id}/stream
  → organization_id + actor_id 会话所有权
  → 两个 AsrSession（interviewer / candidate）
  → TranscriptAssembler → TerminologyCorrector
  → RuleBasedQuestionDetector → question_version
  → 当前面试官问题文本（无资料检索、无历史对话注入）
  → ModelGateway → 中文回答流
  → final utterance / answer 写入 PostgreSQL
```

入口调用链：

```text
求职助手顶部“面试大师”
  → POST /api/career/live-interviews/desktop/launch
  → 携带当前 FastAPI 服务地址启动 Electron；已运行时刷新地址并聚焦现有窗口
  → 静默加载 Electron preload.cjs 与 Vue 生产页面
  → 用户开始面试后建立双通道采集与 WebSocket
```

新问题会递增 `question_version` 并取消旧回答；桌面端忽略旧版本迟到增量。用户也可以手动立即生成、停止和重新生成。WebSocket 断开、用户结束或窗口退出时，桌面端停止所有 MediaStream Track 并关闭 AudioContext，服务端关闭两个 ASR 会话并取消回答任务。

## 页面

- 求职助手会话顶部“面试大师”：统一产品入口，点击后启动或聚焦 Windows 采集器。
- `/live-interview/setup`：只选择 ASR 模型、回答模型和麦克风；不展示或提交简历、岗位及面经资料。
- `/live-interview/session/:id`：双声轨状态、双方转写、当前问题、中文回答流和手动控制。
- `/live-interview/history/:id`：final 对话时间线和各问题的回答尝试。

## 依赖与配置

后端新增 `websockets>=15,<16`，数据库迁移 head 为 `20260823_18`。桌面端依赖锁定在 `desktop-interview-assistant/package-lock.json`，当前 `npm audit` 为 0 个已知漏洞。

后端最低配置：

```text
CAREER_DATABASE_URL=postgresql+psycopg://...
DASHSCOPE_API_KEY=sk-...  # 阿里云百炼华北2（北京）API Key
DASHSCOPE_ASR_MODEL=qwen-audio-3.0-asr-flash-streaming  # 可选，代码默认值
DASHSCOPE_ASR_WEBSOCKET_URL=wss://dashscope.aliyuncs.com/api-ws/v1/inference  # 可选
DASHSCOPE_WORKSPACE_ID=  # 默认业务空间留空；子业务空间按控制台填写
```

申请与配置入口：

- [阿里云百炼 API Key（华北2·北京）](https://bailian.console.aliyun.com/cn-beijing/?tab=app#/api-key)
- [官方获取与配置 API Key 指南](https://help.aliyun.com/zh/model-studio/get-api-key)
- [Qwen-Audio 实时语音识别文档](https://help.aliyun.com/zh/model-studio/real-time-speech-recognition-user-guide)

在 `DASHSCOPE_API_KEY` 留空时，也可以继续使用 `OPENAI_API_KEY` 与 `OPENAI_TRANSCRIPTION_MODEL`，或在现有模型档案中创建能力包含 `transcription` 的 DashScope/OpenAI ASR 档案。回答继续使用能力包含 `text` 的现有 DeepSeek 模型档案。`LIVE_INTERVIEW_FAKE_ASR=1` 只用于本地协议联调，不能作为真实转写验收结果。

首次安装与运行：

```powershell
cd desktop-interview-assistant
npm ci
npm run build
npm start
```

首次下载 Electron 若访问 GitHub 较慢，可仅在本机安装阶段设置 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/` 后重新执行 Electron 安装脚本；该变量不进入产品运行配置。

## 验证结果

截至 2026-08-23 已完成：

- Python 核心、服务、桌面启动、WebSocket、Actor 隔离及相关 Career 回归：37 项通过。
- 原求职助手前端：39 项测试通过，Vite 生产构建通过；“面试大师”位于用户指定的会话顶部工具区。
- 双通道映射、乱序 partial、final 限制、混合语言、术语纠错、追问、手动生成和版本过滤均有自动测试。
- Electron：8 项 Vitest 通过，`vue-tsc` 与 Electron TypeScript 检查通过，Vite 和 Electron 主/预加载脚本生产构建通过。
- 依赖审计：0 个已知漏洞。
- 本地视觉验收：准备页、1280×720 实时双栏页和历史页无横向溢出，界面控制台无错误。
- Electron 43.4.1 本机启动成功，主进程确认 renderer 已加载生产 `index.html`。
- 已在 Windows 桌面实机验收入口链路：点击/调用入口不会打开 PowerShell，Electron 能渲染准备界面，自动使用当前 `http://127.0.0.1:18080` 服务并加载可用转写模型、回答模型和麦克风。
- 纯问题模式已移除准备页的简历、岗位和面经选择；会话创建只提交 ASR/回答模型 ID，避免 Vue 响应式资料数组跨 IPC 时触发 `An object could not be cloned`。
- 实时连接现在等待服务端发送 `session.ready` 后才开始系统音频与麦克风采集；连接提前关闭或发送失败只在界面内显示可操作错误，不再从 `ipcMain` 抛出 JavaScript 主进程崩溃弹窗。
- 默认云端 ASR 已切换为 DashScope Qwen-Audio Streaming：等待 `task-started` 后发送二进制 PCM，映射 `result-generated` partial/final，并在结束时发送 `finish-task`；API Key 只从服务端环境读取。
- 已使用当前本机百炼凭据完成真实 `qwen-audio-3.0-asr-flash-streaming` 握手，以及 FastAPI 会话创建、双 ASR Session 就绪和正常结束链路验证；验证过程不输出 API Key，也不保存原始音频。
- Alembic 只有一个 head：`20260823_18`。
- 数据库迁移及仓储没有 PCM、WAV 或 partial 持久化字段。

一键自动验收：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify-live-interview.ps1
```

## 后续边界

以下项目需要真实外部条件，当前没有伪报通过：

- 使用 DashScope 有效凭据完成中文、英文、中英混合和非 IT 专有名词的真实流式 ASR。
- 在 Zoom、Teams、腾讯会议或 Google Meet 中至少选择两个进行双端通话，分别验证耳机和扬声器。
- 验证真实问题结束到首个回答骨架的 P95 是否不超过 2 秒，并校准规则检测 Precision。
- 连续运行 60 分钟，验证网络波动、切换默认播放设备和重复追问。当前断线会立即安全停止采集；ASR 自动重连与最多 10 秒音频缓冲尚未实现。
- 当前自动问题检测使用确定性规则；轻量 LLM JSON 二次分类接口是后续精度增强项，不影响手动“立即生成”兜底。

真实验收前还应确认所有参与者知道并允许录音与 AI 辅助。macOS、Linux、移动端、多面试官声纹识别、离线 ASR、自动发言和综合会后复盘不在本模块范围内。
