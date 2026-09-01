# Chrome 浏览器面试大师 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有求职助手中交付桌面 Chrome 可直接使用的标签页音频实时转写与流式回答页面，同时保持 Electron 方案可独立继续运行。

**Architecture:** 浏览器使用 `getDisplayMedia()` 获取用户选择的面试标签页音频，通过 `AudioWorklet` 转换为 24 kHz 单声道 PCM16，并沿用现有 FastAPI WebSocket 协议发送。后端按会话配置只启动面试官轨道或双轨 Qwen ASR，并将最终问题交给当前文本模型流式回答；Vue 页面负责准备、采集中、暂停和结束状态，Document Picture-in-Picture 仅作渐进增强。

**Tech Stack:** Vue 3、Vite 7、Web Audio API、Media Capture and Streams、原生 WebSocket、FastAPI、Pydantic、PostgreSQL、Alembic、pytest、Node test runner。

**Spec:** `docs/superpowers/specs/2026-08-23-chrome-browser-interview-master-design.md`

## Global Constraints

- 正式支持桌面版 Chrome；不承诺 Firefox、Safari、移动端、macOS 或 Linux 真机验收。
- 浏览器版不是 Chrome Extension，不依赖 Electron，现有 Electron 代码与启动接口保持可用。
- 只采集用户主动选择的 Chrome 标签页音频；麦克风默认关闭且必须单独授权。
- ASR 使用服务端 Qwen `qwen-audio-3.0-asr-flash-streaming`，API Key 不下发浏览器。
- 回答模型继承求职助手当前选择并允许准备页切换；回答统一中文且技术名词保留原文。
- 普通问题只使用当前面试官问题；候选人转写只在追问消歧时进入内存上下文，不持久化。
- 不保存原始音频、屏幕画面、ASR partial 或候选人转写。
- 页面遵守 PC 端“单页单任务”，标题和操作文案统一中文。

---

### Task 1: 浏览器会话合同与数据库模型

**Files:**
- Create: `migrations/versions/20260823_19_browser_live_interview.py`
- Modify: `src/career_assistant/live_interview/contracts.py`
- Modify: `src/career_assistant/live_interview/persistence.py`
- Modify: `src/career_assistant/live_interview/web.py`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Consumes: 现有 `POST /api/career/live-interviews/sessions` 与 `LiveInterviewSessionRecord`。
- Produces: `client_kind: Literal["desktop", "browser"]`、`candidate_audio_enabled: bool`，以及 `session.ready.active_channels: string[]`。

- [ ] **Step 1: 写出会话默认值与浏览器显式配置的失败测试**

```python
def test_browser_session_records_capture_policy() -> None:
    response = client.post("/api/career/live-interviews/sessions", json={
        "client_kind": "browser",
        "candidate_audio_enabled": False,
    })
    assert response.json()["session"]["client_kind"] == "browser"
    assert response.json()["session"]["candidate_audio_enabled"] is False

def test_desktop_session_keeps_legacy_defaults() -> None:
    response = client.post("/api/career/live-interviews/sessions", json={})
    assert response.json()["session"]["client_kind"] == "desktop"
    assert response.json()["session"]["candidate_audio_enabled"] is True
```

- [ ] **Step 2: 运行测试确认新字段尚不存在**

Run: `pytest tests/test_live_interview_web.py -q`
Expected: FAIL，响应或记录缺少 `client_kind`、`candidate_audio_enabled`。

- [ ] **Step 3: 增加迁移、请求校验、仓储字段和响应字段**

```python
class CreateLiveInterviewRequest(BaseModel):
    asr_model_profile_id: UUID | None = None
    answer_model_profile_id: UUID | None = None
    client_kind: Literal["desktop", "browser"] = "desktop"
    candidate_audio_enabled: bool = True
```

迁移为 `live_interview_sessions` 增加 `client_kind TEXT NOT NULL DEFAULT 'desktop'` 与 `candidate_audio_enabled BOOLEAN NOT NULL DEFAULT TRUE`，并为 `client_kind` 增加取值检查。

- [ ] **Step 4: 运行后端 Web 测试与迁移 head 检查**

Run: `pytest tests/test_live_interview_web.py -q`
Expected: PASS。

Run: `python -m alembic heads`
Expected: 仅 `20260823_19 (head)`。

- [ ] **Step 5: 提交会话合同**

```bash
git add migrations/versions/20260823_19_browser_live_interview.py src/career_assistant/live_interview/contracts.py src/career_assistant/live_interview/persistence.py src/career_assistant/live_interview/web.py tests/test_live_interview_web.py
git commit -m "feat: add browser live interview session policy"
```

### Task 2: 按轨道启动 ASR 与候选人隐私边界

**Files:**
- Modify: `src/career_assistant/live_interview/session_manager.py`
- Modify: `src/career_assistant/live_interview/web.py`
- Modify: `src/career_assistant/live_interview/answer_service.py`
- Test: `tests/test_live_interview_services.py`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Consumes: `LiveInterviewSessionRecord.candidate_audio_enabled`。
- Produces: `LiveSessionManager.start()` 返回的 `session.ready` 含 `active_channels`；未启用轨道收到 `audio.append` 时返回明确协议错误；追问 Prompt 可读取最近一轮内存对话。

- [ ] **Step 1: 写出单轨启动、候选人不持久化和追问上下文测试**

```python
assert ready.payload["active_channels"] == ["interviewer"]
assert set(started_channels) == {AudioChannel.INTERVIEWER}
assert all(event.role.value != "candidate" for event in persisted_events)
assert "candidate: 我负责限流" in follow_up_prompt
```

- [ ] **Step 2: 运行服务测试确认现有实现会启动双轨并保存候选人文本**

Run: `pytest tests/test_live_interview_services.py tests/test_live_interview_web.py -q`
Expected: FAIL，现有 `_build_live_manager` 遍历所有 `AudioChannel`，保存 hook 未区分角色。

- [ ] **Step 3: 只创建启用轨道并限制持久化 hook**

```python
active_channels = [AudioChannel.INTERVIEWER]
if record.candidate_audio_enabled:
    active_channels.append(AudioChannel.CANDIDATE)

async def save_transcript(event: TranscriptEvent) -> None:
    if record.client_kind == "browser" and event.channel is AudioChannel.CANDIDATE:
        return
```

`LiveSessionManager.handle()` 在字典中不存在目标轨道时抛出 `ValueError("当前会话未启用 candidate 音频轨道")`；`session.ready` 通过实际字典键生成 `active_channels`。

- [ ] **Step 4: 仅在追问中构建最近一轮消歧 Prompt**

```python
if intent is QuestionIntent.FOLLOW_UP and context.recent_conversation:
    follow_up_context = "\n".join(context.recent_conversation[-2:])
```

普通问题继续不读取 `candidate_facts`、`target_role` 与 `interview_evidence`。

- [ ] **Step 5: 运行实时面试后端测试**

Run: `pytest tests/test_live_interview_core.py tests/test_live_interview_services.py tests/test_live_interview_web.py -q`
Expected: PASS。

- [ ] **Step 6: 提交按轨道运行逻辑**

```bash
git add src/career_assistant/live_interview/session_manager.py src/career_assistant/live_interview/web.py src/career_assistant/live_interview/answer_service.py tests/test_live_interview_services.py tests/test_live_interview_web.py
git commit -m "feat: enforce browser interview audio boundaries"
```

### Task 3: 浏览器 PCM、状态机与 WebSocket 客户端

**Files:**
- Create: `web-ui/src/browser-live-interview/pcm-framer.js`
- Create: `web-ui/src/browser-live-interview/pcm-processor.js`
- Create: `web-ui/src/browser-live-interview/session-state.js`
- Create: `web-ui/src/browser-live-interview/socket.js`
- Create: `web-ui/src/browser-live-interview-core.test.js`

**Interfaces:**
- Consumes: 后端 `audio.append`、`audio.commit`、`answer.request`、`session.end` 和服务端事件。
- Produces: `resampleToPcm16(samples, inputRate, outputRate): Int16Array`、`createInterviewState()`、`reduceInterviewEvent(state, event)`、`LiveInterviewSocket`。

- [ ] **Step 1: 写出 PCM 饱和、降采样、事件去重与答案替换测试**

```js
assert.deepEqual([...floatToPcm16(new Float32Array([-2, -1, 0, 1, 2]))], [-32768, -32768, 0, 32767, 32767])
assert.equal(resampleToPcm16(new Float32Array(480), 48000, 24000).length, 240)
assert.equal(reduceInterviewEvent(state, duplicateDelta).answerText, '结论')
```

- [ ] **Step 2: 运行 Node 测试确认模块不存在**

Run: `npm test -- --test-name-pattern="browser live interview"`
Expected: FAIL，找不到新模块。

- [ ] **Step 3: 实现纯函数与有界状态机**

PCM 使用线性窗口平均降采样与 `Int16Array` 饱和转换；状态机最多保留 100 条 transcript 展示记录和 50 组问答，按 `question_version + attempt` 忽略迟到 delta。

- [ ] **Step 4: 实现原生 WebSocket 封装**

```js
new LiveInterviewSocket({
  sessionId,
  onEvent,
  onStateChange,
  WebSocketImpl: window.WebSocket
})
```

连接地址由当前 origin 派生 `ws:`/`wss:`；只在 `session.ready` 后允许发帧；每 20 秒 ping；`bufferedAmount > 1_000_000` 时丢弃当前帧并报告背压，不缓存无界音频。

- [ ] **Step 5: 运行浏览器核心测试**

Run: `npm test -- --test-name-pattern="browser live interview"`
Expected: PASS。

- [ ] **Step 6: 提交浏览器协议核心**

```bash
git add web-ui/src/browser-live-interview web-ui/src/browser-live-interview-core.test.js
git commit -m "feat: add browser interview streaming core"
```

### Task 4: Chrome 标签页和可选麦克风采集

**Files:**
- Create: `web-ui/src/browser-live-interview/capture.js`
- Create: `web-ui/src/browser-live-interview-capture.test.js`

**Interfaces:**
- Consumes: `navigator.mediaDevices`、`AudioContext`、Task 3 的 PCM 转换函数。
- Produces: `detectBrowserInterviewSupport()` 与 `BrowserAudioCapture.start({ candidateEnabled, onFrame, onEnded })`、`stop()`。

- [ ] **Step 1: 写出能力检测、无音轨清理、麦克风降级和停止幂等测试**

```js
assert.equal(detectBrowserInterviewSupport({ userAgent: 'Firefox', mediaDevices: {} }).supported, false)
await assert.rejects(() => capture.start(...), /共享标签页音频/)
assert.equal(videoTrack.stop.mock.calls.length, 1)
assert.equal(result.candidateEnabled, false)
```

- [ ] **Step 2: 运行采集测试确认模块不存在**

Run: `npm test -- --test-name-pattern="browser audio capture"`
Expected: FAIL。

- [ ] **Step 3: 实现用户手势内的媒体授权与 AudioWorklet 管线**

`getDisplayMedia({ video: true, audio: true, preferCurrentTab: false, selfBrowserSurface: 'exclude', surfaceSwitching: 'exclude', systemAudio: 'exclude' })` 后立即停止视频轨；无音频轨时停止整个流。麦克风失败返回降级状态，不终止面试官轨道。

- [ ] **Step 4: 实现 track ended、AudioContext 和节点清理**

所有 track、source、worklet node、AudioContext 与事件监听器统一登记；`stop()` 可重复调用且不抛错。

- [ ] **Step 5: 运行采集测试**

Run: `npm test -- --test-name-pattern="browser audio capture"`
Expected: PASS。

- [ ] **Step 6: 提交浏览器采集层**

```bash
git add web-ui/src/browser-live-interview/capture.js web-ui/src/browser-live-interview-capture.test.js
git commit -m "feat: capture Chrome interview tab audio"
```

### Task 5: 面试大师单页与置顶回答小窗

**Files:**
- Create: `web-ui/src/components/BrowserInterviewMasterPage.vue`
- Create: `web-ui/src/browser-live-interview/picture-in-picture.js`
- Create: `web-ui/src/browser-live-interview-view.test.js`
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/styles.css`

**Interfaces:**
- Consumes: setup-options/session REST、Task 3 WebSocket、Task 4 capture、URL 参数 `answer_model_profile_id`。
- Produces: `/career/interview-master` 独立页面，准备、采集、暂停、结束四阶段和可选 Document Picture-in-Picture。

- [ ] **Step 1: 写出路由识别、模型选择、费用和时长纯函数测试**

```js
assert.equal(normalizeInterviewRoute('/career/interview-master'), '/career/interview-master')
assert.equal(estimateAsrCost(3600, 1), 1.188)
assert.equal(formatDuration(125), '02:05')
```

- [ ] **Step 2: 运行前端测试确认页面辅助函数不存在**

Run: `npm test -- --test-name-pattern="interview master view"`
Expected: FAIL。

- [ ] **Step 3: 实现“实时提词台”PC 页面**

页面包含：采集状态和问题脉冲轨、当前问题、3～5 个短要点与完整回答、面试官/候选人实时转写、本场历史、115 分钟提醒、120 分钟结束、当前费用、立即回答、重新生成、重新选择和结束面试。准备页只显示模型、可选麦克风、授权说明与同意复选框，不显示简历/岗位/面经资料。

- [ ] **Step 4: 实现 Document Picture-in-Picture 渐进增强**

支持时调用 `documentPictureInPicture.requestWindow({ width: 460, height: 680 })`，复制必要样式并渲染当前问题和答案；关闭时清理 watcher 与 DOM。不支持时提示用户继续使用当前独立窗口。

- [ ] **Step 5: 接入 App 路由并让该路由继承求职助手权限**

`normalizeRoute('/career/interview-master')` 返回自身；`navItemForRoute()` 将其映射到 `career_assistant`；App 在此路由只渲染面试页，不显示全局侧栏和顶栏，保持单页单任务。

- [ ] **Step 6: 运行单元测试和生产构建**

Run: `npm test`
Expected: PASS。

Run: `npm run build`
Expected: PASS，无 Vue 编译错误。

- [ ] **Step 7: 提交面试页面**

```bash
git add web-ui/src/components/BrowserInterviewMasterPage.vue web-ui/src/browser-live-interview/picture-in-picture.js web-ui/src/browser-live-interview-view.test.js web-ui/src/App.vue web-ui/src/styles.css
git commit -m "feat: add Chrome interview master page"
```

### Task 6: 求职助手入口迁移

**Files:**
- Create: `web-ui/src/browser-interview-launcher.js`
- Create: `web-ui/src/browser-interview-launcher.test.js`
- Modify: `web-ui/src/components/CareerAssistantPage.vue`

**Interfaces:**
- Consumes: 当前 `selectedProfileId` 与浏览器 `window.open`。
- Produces: `openBrowserInterviewMaster({ answerModelProfileId, openWindow })`，固定窗口名 `career-interview-master`，弹窗失败时当前页新标签降级。

- [ ] **Step 1: 写出固定窗口名、模型参数与弹窗降级测试**

```js
assert.equal(call.name, 'career-interview-master')
assert.equal(new URL(call.url).searchParams.get('answer_model_profile_id'), modelId)
assert.equal(fallbackTarget, '_blank')
```

- [ ] **Step 2: 运行测试确认仍调用桌面启动 API**

Run: `npm test -- --test-name-pattern="browser interview launcher"`
Expected: FAIL，新 launcher 尚不存在。

- [ ] **Step 3: 修改按钮为同步打开浏览器窗口**

按钮不再请求 `/desktop/launch`，文案与 tooltip 改为“选择面试标签页音频，实时生成回答建议”；已有 Electron endpoint 和工程文件不删除。

- [ ] **Step 4: 运行入口测试和构建**

Run: `npm test && npm run build`
Expected: PASS。

- [ ] **Step 5: 提交入口迁移**

```bash
git add web-ui/src/browser-interview-launcher.js web-ui/src/browser-interview-launcher.test.js web-ui/src/components/CareerAssistantPage.vue
git commit -m "feat: open browser interview master from career assistant"
```

### Task 7: 本地与生产 WebSocket/权限配置

**Files:**
- Modify: `web-ui/vite.config.js`
- Modify: `docker/caddy/Caddyfile`
- Modify: `docker/nginx/default.conf`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Consumes: `/api/career/live-interviews/{sessionId}/stream`。
- Produces: Vite 开发代理、Caddy 与 Nginx 均可完成 WebSocket Upgrade；Caddy 允许同源麦克风授权。

- [ ] **Step 1: 写出配置文件断言测试**

```python
assert "microphone=(self)" in caddyfile
assert 'proxy_set_header Upgrade $http_upgrade;' in nginx_config
assert "ws: true" in vite_config
```

- [ ] **Step 2: 运行测试确认配置缺口**

Run: `pytest tests/test_live_interview_web.py -q`
Expected: FAIL，Vite 未启用 `ws`，Caddy 禁止麦克风，Nginx 清空 Connection。

- [ ] **Step 3: 修改代理与权限头**

Vite `/api` 设置 `ws: true`；Caddy 使用 `Permissions-Policy "camera=(), microphone=(self), geolocation=()"`；Nginx `/api/career/` 透传 `$http_upgrade` 与 `upgrade` connection map，同时保持 SSE buffering 关闭。

- [ ] **Step 4: 运行配置测试与 Compose 展开检查**

Run: `pytest tests/test_live_interview_web.py -q`
Expected: PASS。

Run: `docker compose -f docker-compose.prod.yml config --quiet`
Expected: exit code 0。

- [ ] **Step 5: 提交部署配置**

```bash
git add web-ui/vite.config.js docker/caddy/Caddyfile docker/nginx/default.conf tests/test_live_interview_web.py
git commit -m "fix: proxy browser interview WebSocket"
```

### Task 8: 全链路验收、文档与生产发布

**Files:**
- Modify: `docs/superpowers/specs/2026-08-23-chrome-browser-interview-master-design.md`
- Create: `docs/acceptance/2026-08-23-chrome-browser-interview-master.md`

**Interfaces:**
- Consumes: Tasks 1–7 的浏览器和后端实现。
- Produces: 自动化验收记录、Windows Chrome 真机记录、生产同源冒烟结果和明确的未验证边界。

- [ ] **Step 1: 运行完整实时面试测试与前端测试**

Run: `pytest tests/test_live_interview_core.py tests/test_live_interview_services.py tests/test_live_interview_web.py tests/test_live_interview_desktop_launcher.py -q`
Expected: PASS。

Run: `npm test && npm run build`
Expected: PASS。

- [ ] **Step 2: 运行仓库相关回归和迁移检查**

Run: `pytest tests/test_navigation_config.py tests/test_career_turn_api.py -q`
Expected: PASS。

Run: `python -m alembic heads`
Expected: 单一 head。

- [ ] **Step 3: 在本地 Windows Chrome 验收真实标签页音频**

启动本地 API 与 Web UI，登录后从求职助手点击“面试大师”，选择播放中文、英文及中英混合音频的 Chrome 标签页，确认 Qwen final、问题检测和答案首字流式到达；开启麦克风后确认候选人转写不触发答案且数据库未保存该文本；停止共享后确认页面进入暂停并可重新选择。

- [ ] **Step 4: 发布生产并执行同源冒烟**

按仓库现有生产发布流程构建并更新服务，执行 Alembic 升级；在 `https://your-domain.example/career/interview-master` 验证登录 Cookie、setup-options、session REST、WSS upgrade、Chrome 标签页授权、Qwen ASR 与文本模型首字流。

- [ ] **Step 5: 写入验收证据与边界**

验收文档记录 commit、命令结果、浏览器版本、测试音频类型、问题结束到首个 `answer.delta` 的中位数/P95、单/双轨费用估算、生产 URL；明确“不宣称 macOS/Linux 真机验证”。

- [ ] **Step 6: 提交验收文档**

```bash
git add docs/superpowers/specs/2026-08-23-chrome-browser-interview-master-design.md docs/acceptance/2026-08-23-chrome-browser-interview-master.md
git commit -m "docs: record browser interview master acceptance"
```
