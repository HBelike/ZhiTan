# Chrome 浏览器面试大师设计

## 1. 目标

在现有求职助手中新增一套纯 Web 的“面试大师”，专门服务于桌面版 Chrome 中运行的网页面试平台。用户从求职助手点击入口后，打开独立浏览器窗口，手动选择正在面试的 Chrome 标签页并共享标签页音频。系统将面试官语音实时转写为中英混合文本，识别完整问题，再使用求职助手已配置的文本模型流式生成统一中文的回答建议。

该方案不是 Chrome 插件，不安装浏览器扩展，不依赖 Electron。已有 Windows Electron 采集器和服务端启动接口保留不变，但求职助手中的默认“面试大师”入口改为打开浏览器版。

## 2. 已确认的产品决策

- 正式支持桌面版 Chrome，不对 Firefox、Safari、移动端和其他浏览器作可用性承诺。
- 仅采集用户明确选择的 Chrome 面试标签页音频；不尝试无感知监听整台电脑。
- 面试官标签页音频始终是待回答问题来源。应试者麦克风默认关闭，用户可在准备页开启“同时理解我的回答”。
- 应试者转写只用于当前内存中的追问消歧，不触发新答案，不作为事实来源，不持久化。
- ASR 默认固定使用服务端 `qwen-audio-3.0-asr-flash-streaming`；回答模型继承当前求职助手会话选中的文本模型，并允许在准备页临时切换。
- 回答只以当前面试官问题为核心，不读取简历、目标岗位或面经资料。对“刚才那个方案”类追问，仅允许使用当前会话内存中的最近问答消歧。
- 产品定位为模拟面试和经允许的实时辅助，不宣传隐蔽、不可检测或规避面试规则。

## 3. 非目标

- 不开发 Chrome Extension、Side Panel 或 `chrome.tabCapture` 链路。
- 不让会议机器人加入 Zoom、Teams 或 Google Meet。
- 不采集摄像头画面，不保存屏幕画面。`getDisplayMedia()` 所需的视频轨只用于获得用户选定的标签页音频，开始后立即停止该视频轨。
- 不使用 Web Speech API 或浏览器厂商不可控的语音服务。
- 不将 WebGPU Whisper 作为首版生产 ASR。
- 不捕获微信桌面版、腾讯会议客户端等非浏览器软件的声音。
- 不保存原始音频、屏幕画面、ASR partial 或应试者转写。

## 4. 用户流程

### 4.1 入口

1. 用户在求职助手点击“面试大师”。
2. 前端使用固定窗口名打开 `/career/interview-master`，同一浏览器会话重复点击时聚焦已有窗口。
3. Chrome 阻止弹窗时，降级为新标签页，不调用 Electron 启动接口。
4. 页面与 API 同源，直接使用现有 HttpOnly Cookie 完成 REST 和 WebSocket 鉴权。

### 4.2 准备

1. 检查桌面 Chrome、HTTPS、`getDisplayMedia`、`AudioWorklet`、WebSocket 和可选 `documentPictureInPicture` 能力。
2. 加载 Qwen ASR 就绪状态和可用文本回答模型。
3. 默认选中从求职助手传入的当前文本模型；该模型不可用时，只选中网关返回的可用文本模型，不构造假模型。
4. 默认关闭应试者麦克风，用户可选择开启。
5. 用户必须确认“我已了解采集范围，并确认当前场景允许使用实时辅助”，否则不得启动。

### 4.3 开始采集

1. 用户点击“开始面试”，该点击直接触发 `navigator.mediaDevices.getDisplayMedia()`。
2. 页面提示用户选择面试标签页并保持“共享标签页音频”开启。网站不绕过 Chrome 的每次授权选择器。
3. 如返回流没有 audio track，立即停止所有 track，不创建付费 ASR 会话，并要求重新选择。
4. 如开启应试者麦克风，在标签页音频成功后再调用 `getUserMedia()`；麦克风失败时允许用户改为仅面试官模式。
5. 媒体流就绪后创建服务端会话、打开业务 WebSocket，收到 `session.ready` 后才向 Qwen 发送 PCM。

### 4.4 面试进行中

- 主页面展示当前会话时长、音频轨状态、实时转写、当前问题、回答流和本场问答历史。
- 用户可点击“打开置顶小窗”。支持 Document Picture-in-Picture 时，在小窗中展示当前问题、30 秒回答提纲和正在流式生成的完整答案；不支持时保留独立普通窗口。
- 默认在 Qwen 返回句子结束后触发问题识别和回答，不根据半句 partial 投机生成。
- 用户可使用“立即回答”将最新面试官转写手动提交，或使用“重新生成”替换当前答案。
- 答案展示顺序是“直接结论 → 3～5 个可立即开口的短要点 → 简短表达示例 → 可能追问”。
- 问题类型决定组织方式：技术原理题使用机制与实例，系统设计题使用目标、约束、架构、组件和权衡，算法题使用思路、复杂度、伪代码或代码及边界，行为题使用 STAR，非 IT 专业题使用结论、分析维度和解决步骤。

### 4.5 暂停、恢复与结束

- 用户停止共享、关闭被采集标签页或音频 track 异常结束时，页面立即停止发送音频，保留当前转写和答案，显示“重新选择面试标签页”。
- 原 WebSocket 尚存活时，重新授权后继续原服务端 session；若 WebSocket 已关闭且后端已将 session 标记结束，则创建新的服务端 session。前端可以在本次浏览器窗口中连续展示两段结果，但历史记录分别保存，不篡改已结束的 session。
- 单场最长 120 分钟，115 分钟时提示，120 分钟自动结束。主页面持续显示已用时和按实际开启轨道数估算的 ASR 费用。
- 结束时停止所有 MediaStreamTrack、AudioContext、AudioWorklet、WebSocket 和定时器，并清空应试者转写内存。

## 5. 架构与数据流

```text
CareerAssistantPage
  -> window.open('/career/interview-master', fixedWindowName)
  -> BrowserInterviewMasterPage
       -> getDisplayMedia(): 面试标签页 audio track
       -> getUserMedia(): 可选应试者 microphone track
       -> AudioWorklet + PCM framer: 24 kHz / mono / PCM16
       -> POST /api/career/live-interviews/sessions
       -> WebSocket /api/career/live-interviews/{sessionId}/stream
            -> DashScope Qwen ASR
            -> TranscriptAssembler
            -> QuestionDetector
            -> LiveAnswerService
            -> 已选文本模型
       <- transcript / question / answer / error 事件
       -> 主页面和 Document Picture-in-Picture 共享状态
```

浏览器对每条轨道使用独立的 `AudioWorkletNode` 和递增 sequence。帧格式继续沿用现有 `audio.append` JSON 协议与 96 KB 限制，不再新建一套媒体后端。当前 Electron 客户端与浏览器客户端共享后端领域协议，但各自保留独立的采集和传输适配器。

## 6. 会话模型与向后兼容

现有 Electron 创建会话时不传新字段，必须继续保持当前行为。新的浏览器客户端在创建会话时显式传入：

```json
{
  "asr_model_profile_id": null,
  "answer_model_profile_id": "<selected-profile-id-or-null>",
  "client_kind": "browser",
  "candidate_audio_enabled": false
}
```

- `client_kind` 默认为 `desktop`，以保留已有 Electron 调用行为。
- `candidate_audio_enabled` 默认为 `true`，浏览器客户端必须显式传入用户在准备页的选择。
- 后端仅为启用的轨道创建 ASR Session，`session.ready` 返回 `active_channels`，避免默认关闭的麦克风轨道产生连接和费用。
- 浏览器会话的应试者 final transcript 不调用持久化 hook；只放入当前 `LiveSessionManager` 的有界内存队列。
- 浏览器会话只持久化面试官 final transcript、检测出的问题和生成答案。
- 答案 Prompt 对普通问题仍只使用当前问题。只有当 `is_follow_up=true` 时，才向 Prompt 增加最近一轮面试官问题和可选应试者回答，仅用于代词与指代消歧，不允许编造经历。

这两个字段属于会话可观测状态，需要通过新的 Alembic 迁移持久化，使历史回放和生产排障能够区分浏览器与桌面会话。

## 7. 前端边界

新增的浏览器实时面试代码位于 `web-ui`，不从 `desktop-interview-assistant` 直接引用 Electron Bridge。可以复用其中经过拆分后的纯 TypeScript 逻辑，但不在两个 Vite 工程之间建立源码跨目录耦合。

计划中的职责单元：

- `BrowserInterviewMasterPage.vue`：单页主任务，组装准备、进行中和结束状态。
- `BrowserInterviewHistoryPage.vue`：独立历史路由，只读已持久化的面试官问题与 AI 答案。
- `browser-live-interview/capture.js`：标签页音频、可选麦克风、track 生命周期和能力检测。
- `browser-live-interview/pcm-framer.js`：浮点采样重采样、PCM16 转换和有界分帧。
- `browser-live-interview/pcm-processor.js`：运行在 AudioWorkletGlobalScope 的最小处理器。
- `browser-live-interview/socket.js`：同源原生 WebSocket 传输、事件验证、心跳和显式重连。
- `browser-live-interview/session-state.js`：纯状态机，同时供主页面和置顶小窗消费。
- `browser-live-interview/picture-in-picture.js`：Document Picture-in-Picture 的能力检测、DOM/样式同步和关闭清理。

## 8. 部署调整

- 生产 Caddy 当前禁止 `microphone`。需将响应头调整为 `Permissions-Policy: camera=(), microphone=(self), geolocation=()`，只允许同源页面发起麦克风授权请求；用户仍需在 Chrome 中主动授权。
- 生产保持 HTTPS，页面、REST 与 WebSocket 都使用 `your-domain.example` 同源路径。
- Vite `/api` 代理需启用 WebSocket upgrade，使本地浏览器页能通过同一开发 origin 连接 FastAPI。
- `.env.production.example` 继续保留 `DASHSCOPE_API_KEY` 占位。生产不将 API Key 下发浏览器，Qwen WebSocket 始终由 FastAPI 服务端建立。

## 9. 费用、隐私与存储

- Qwen 北京区当前标准价是 `0.00033 元/秒`，即约 `1.188 元/小时/轨道`。页面费用仅作估算，明确标注“以服务商账单为准”。
- 默认只启动面试官轨道。开启应试者麦克风时，费用按两条 ASR 轨道估算。
- 原始音频只在浏览器内存、网络传输和 Qwen 实时 ASR 中短暂流转，不写入项目数据库、文件系统或对象存储。
- 数据库只保存最终面试官转写、标准化问题、问题类型、回答状态和最终答案。
- 应试者转写、ASR partial、实时费用估算和轨道电平只存在当前浏览器/服务端内存中，会话结束后清空。

## 10. 错误处理

- 非 Chrome 或缺失必需 Web API：准备页显示不支持原因，禁用开始按钮。
- 用户拒绝标签页共享：保留准备页，不创建服务端会话。
- 共享流没有音频轨：停止共享并告知用户重新选择标签页和音频复选框。
- 麦克风被拒绝或被占用：允许降级为仅面试官轨道。
- ASR 连接失败：停止音频发送，保留已有答案，不使用 fake ASR 伪装成功。
- 文本模型失败：标记当前答案失败，允许重新生成，不重复转写原始问题。
- WebSocket 短暂中断：暂停发帧，停止当前 ASR 会话，保留 UI 状态并引导显式重连；不缓存无界原始音频。
- 达到 120 分钟：向服务端发送 `session.end`，完整清理所有浏览器媒体资源。

## 11. 验证与验收

### 11.1 自动化验证

- PCM 重采样、饱和转换、分帧、递增 sequence 和轨道隔离的纯单元测试。
- 无 audio track、用户拒绝、track `ended`、麦克风降级、超时结束和资源释放测试。
- 原生 WebSocket 协议事件、心跳、无界缓冲防护、重连与事件去重测试。
- 主页面与 Picture-in-Picture 状态同步、问题切换、旧答案取消、立即回答和重新生成测试。
- 会话创建中新字段的默认值、向后兼容、浏览器仅面试官 ASR Session 和应试者不持久化测试。
- 路由、弹窗降级、当前回答模型继承、历史页和生产构建测试。
- Caddy Permissions Policy、Vite WebSocket proxy、Alembic 单 head 和生产 Compose 展开检查。

### 11.2 真实验收

首版必须在当前可用的 Windows + 桌面 Chrome 上完成：

1. 使用可播放的中文、英文和中英混合面试音频标签页，确认 Qwen partial 和 final 转写。
2. 开启可选麦克风，确认应试者转写不触发答案，会话结束后数据库不存在应试者文本。
3. 确认技术、行为、系统设计和非 IT 问题都能生成对应结构的统一中文答案。
4. 确认置顶小窗、手动立即回答、重新生成、停止共享和重新选择。
5. 确认原始音频、partial 和应试者转写不落盘。
6. 发布到正式域名后，以真实登录态完成同源 REST、WSS、Qwen ASR 和文本模型的全链路冒烟。

不宣称 macOS 或 Linux 真机已验收。页面使用标准 Web API 和能力检测，但跨系统可用性只能在后续获得设备后单独记录。

### 11.3 延迟目标

从 Qwen 确认面试官句子结束到回答首字到达浏览器：

- 中位数不超过 3 秒。
- P95 不超过 6 秒。

该指标在前端以 `question.detected` 和首个 `answer.delta` 的单调时钟记录，仅记录延迟数值，不记录问题内容。

## 12. 参考实现与复用边界

- [MDN `getDisplayMedia`](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)：权限、audio track 与兼容性边界。
- [W3C Screen Capture](https://www.w3.org/TR/screen-capture/)：每次用户选择、不可持久化授权和安全要求。
- [Chrome Document Picture-in-Picture](https://developer.chrome.com/docs/web-platform/document-picture-in-picture)：可渐进增强的任意 HTML 置顶小窗。
- [patrikx3/meet-assistant](https://github.com/patrikx3/meet-assistant)：MIT，参考标签页音频授权产品流程，不复制其 Python 服务端。
- [AssemblyAI/realtime-transcription-browser-js-example](https://github.com/AssemblyAI/realtime-transcription-browser-js-example)：MIT，参考 AudioWorklet 与 WebSocket 拆分方式，不引入 AssemblyAI SDK。
- [addpipe/getDisplayMedia-demo](https://github.com/addpipe/getDisplayMedia-demo)：只作能力参考；仓库未标注可复用许可证，不复制源码。
