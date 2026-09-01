# Windows Live Interview Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个仅支持 Windows 10/11 的 Electron 实时面试助手，分离采集系统音频与麦克风，实时双语转写面试对话，并向应试者流式输出有材料事实约束的中文回答建议。

**Architecture:** Electron + Vue 3 桌面端仅负责授权、双音轨 PCM16 采集、会话控制和展示；现有 FastAPI 服务通过一个复用事件的 WebSocket 管理两个 ASR 通道、问题版本状态机、RAG 上下文和流式回答。服务端只持久化会话、final 话语和答案，不保存 PCM 或 partial 转写；真实 ASR 首个实现使用 OpenAI Realtime transcription，测试使用可重复的 Fake Provider。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy/Alembic、PostgreSQL、OpenAI Realtime WebSocket、Electron、Vue 3、TypeScript、Vite、Vitest

**Spec:** `docs/superpowers/specs/2026-08-23-windows-live-interview-assistant-design.md`

**Execution Status (2026-08-23):** Task 1～8 的 Windows MVP 已实施并完成自动测试、构建、依赖审计和三页视觉验收。Task 9 的真实双端会议、P95、双会议软件和 60 分钟稳定性验证需要有效 OpenAI 凭据与第二通话端，未伪报通过；ASR 自动重连/10 秒缓冲和轻量 LLM JSON 二次分类记录为下一阶段边界。

## Global Constraints

- 仅支持 Windows 10/11 x64，不实现 macOS、Linux、iPad 或手机端。
- 系统音频固定映射为 `interviewer`，麦克风固定映射为 `candidate`，不依赖 LLM 猜角色。
- 转写支持中文、英文和中英混合，回答统一使用中文并保留各行业专有名词原文。
- 个人经历、职责、业绩和数字只能来自用户确认材料；缺少证据时必须提示替换为真实经历。
- 用户明确开始后才能采集；断开、结束或退出后 2 秒内停止；不保存 PCM、WAV、partial 转写或临时凭据。
- 不实现隐藏窗口、规避屏幕共享、规避监考、未经授权录音或自动代替用户发言。
- 同一会话最多一个活动业务 WebSocket 和一个活动回答流；新问题版本取消旧回答。
- PC 桌面端优先，不进行平板或移动端专项适配；所有页面标题和操作标题使用中文。

---

## File Map

- `src/career_assistant/live_interview/contracts.py`：角色、意图、会话、转写、问题、答案与 WebSocket 事件合同。
- `src/career_assistant/live_interview/transcript_assembler.py`：双通道序号、partial/final 去重与乱序保护。
- `src/career_assistant/live_interview/terminology.py`：从已确认材料抽取术语并进行受约束纠错。
- `src/career_assistant/live_interview/question_detector.py`：确定性预筛、追问识别和可替换分类接口。
- `src/career_assistant/live_interview/context_builder.py`：最近对话、简历、岗位和面经证据的最小 Prompt 上下文。
- `src/career_assistant/live_interview/answer_service.py`：中文回答边界、流式生成和取消。
- `src/career_assistant/live_interview/persistence.py`：组织与 Actor 隔离的会话、话语和答案仓储。
- `src/career_assistant/live_interview/session_manager.py`：会话内存状态、问题版本、答案任务和幂等关闭。
- `src/career_assistant/live_interview/asr/`：Provider 接口、Fake Provider 和 OpenAI Realtime Provider。
- `src/career_assistant/live_interview/web.py`：REST setup/session/history 和业务 WebSocket。
- `migrations/versions/20260823_18_live_interview_assistant.py`：三张实时面试表及索引。
- `desktop-interview-assistant/`：Electron 主进程、preload、Vue 三路由、双音轨 AudioWorklet、协议客户端与测试。
- `docs/modules/windows-live-interview-assistant.md`：目标、技术取舍、调用链、依赖、验证结果和后续边界。

### Task 1: 领域合同与数据库迁移

**Files:**
- Create: `src/career_assistant/live_interview/__init__.py`
- Create: `src/career_assistant/live_interview/contracts.py`
- Create: `migrations/versions/20260823_18_live_interview_assistant.py`
- Test: `tests/test_live_interview_contracts.py`

**Interfaces:**
- Consumes: `UUID`、UTC `datetime` 和现有 Alembic `20260823_17` revision。
- Produces: `AudioChannel`、`SpeakerRole`、`QuestionIntent`、`LiveInterviewStatus`、`AnswerStatus`、`TranscriptEvent`、`DetectedQuestion`、`parse_client_event(payload: dict) -> ClientEvent`。

- [ ] **Step 1: 写协议校验失败测试**

```python
def test_audio_event_rejects_negative_sequence():
    with pytest.raises(ValueError, match="sequence"):
        parse_client_event({"type": "audio.append", "channel": "interviewer", "sequence": -1, "pcm_base64": "AA=="})
```

- [ ] **Step 2: 运行单测并确认因模块不存在而失败**

Run: `python -m pytest tests/test_live_interview_contracts.py -q`
Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现枚举、不可变事件合同和严格解析**

```python
@dataclass(frozen=True)
class AudioAppendEvent:
    channel: AudioChannel
    sequence: int
    pcm: bytes

def parse_client_event(payload: dict[str, object]) -> ClientEvent:
    if payload.get("type") == "audio.append":
        sequence = int(payload["sequence"])
        if sequence < 0:
            raise ValueError("sequence 必须大于等于 0")
        return AudioAppendEvent(AudioChannel(str(payload["channel"])), sequence, base64.b64decode(str(payload["pcm_base64"]), validate=True))
    ...
```

- [ ] **Step 4: 新增三表迁移，明确不包含音频与 partial 字段**

```python
op.create_table("live_interview_sessions", ...)
op.create_table("live_interview_utterances", sa.Column("raw_text", sa.Text(), nullable=False), ...)
op.create_table("live_interview_answers", sa.Column("answer_text", sa.Text(), nullable=False, server_default=""), ...)
```

- [ ] **Step 5: 运行合同测试和迁移链检查**

Run: `python -m pytest tests/test_live_interview_contracts.py -q && python -m alembic heads`
Expected: PASS；只显示一个 head `20260823_18`。

- [ ] **Step 6: 提交独立变更**

```bash
git add src/career_assistant/live_interview/__init__.py src/career_assistant/live_interview/contracts.py migrations/versions/20260823_18_live_interview_assistant.py tests/test_live_interview_contracts.py
git commit -m "feat: add live interview domain contracts"
```

### Task 2: 转写组装、术语纠错与问题检测

**Files:**
- Create: `src/career_assistant/live_interview/transcript_assembler.py`
- Create: `src/career_assistant/live_interview/terminology.py`
- Create: `src/career_assistant/live_interview/question_detector.py`
- Test: `tests/test_live_interview_transcript_pipeline.py`

**Interfaces:**
- Consumes: `TranscriptEvent`、`AudioChannel`、`SpeakerRole`、已确认简历/JD 文本。
- Produces: `TranscriptAssembler.accept(event) -> TranscriptEvent | None`、`TerminologyCorrector.correct(raw_text) -> CorrectionResult`、`RuleBasedQuestionDetector.detect(event, previous_question) -> DetectedQuestion | None`。

- [ ] **Step 1: 写双通道、乱序、混合语言、追问和非问题测试**

```python
def test_candidate_final_never_triggers_question():
    event = TranscriptEvent.final(channel=AudioChannel.CANDIDATE, sequence=2, text="I used Kafka 处理事件")
    assert RuleBasedQuestionDetector().detect(event) is None
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_live_interview_transcript_pipeline.py -q`
Expected: FAIL with missing implementations。

- [ ] **Step 3: 实现每通道独立序号、final 优先和重复片段消除**

```python
def accept(self, event: TranscriptEvent) -> TranscriptEvent | None:
    if event.sequence < self._last_sequence[event.channel]:
        return None
    self._last_sequence[event.channel] = event.sequence
    return event
```

- [ ] **Step 4: 实现只允许候选术语替换的纠错与问题检测**

```python
if event.role is not SpeakerRole.INTERVIEWER or not event.is_final:
    return None
if self._looks_like_question(event.text):
    return DetectedQuestion(intent=self._classify(event.text), normalized_question=event.text.strip(), is_follow_up=self._is_follow_up(event.text))
```

- [ ] **Step 5: 运行管线测试**

Run: `python -m pytest tests/test_live_interview_transcript_pipeline.py -q`
Expected: PASS。

- [ ] **Step 6: 提交独立变更**

```bash
git add src/career_assistant/live_interview/transcript_assembler.py src/career_assistant/live_interview/terminology.py src/career_assistant/live_interview/question_detector.py tests/test_live_interview_transcript_pipeline.py
git commit -m "feat: detect interview questions from transcripts"
```

### Task 3: ASR Provider 与 OpenAI Realtime 适配

**Files:**
- Create: `src/career_assistant/live_interview/asr/__init__.py`
- Create: `src/career_assistant/live_interview/asr/base.py`
- Create: `src/career_assistant/live_interview/asr/fake.py`
- Create: `src/career_assistant/live_interview/asr/openai_realtime.py`
- Modify: `requirements-career-assistant.txt`
- Test: `tests/test_live_interview_asr.py`

**Interfaces:**
- Consumes: 24 kHz、单声道 PCM16 和独立 `AudioChannel`。
- Produces: `AsrSession.append_audio(pcm)`, `commit()`, async `events()`, `close()`；`AsrProvider.start(channel, context) -> AsrSession`。

- [ ] **Step 1: 写 Fake 脚本事件和 OpenAI 消息映射测试**

```python
async def test_openai_completed_maps_to_final():
    event = map_openai_event({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Explain CAP theorem"}, AudioChannel.INTERVIEWER, 7)
    assert event.is_final and event.text == "Explain CAP theorem"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_live_interview_asr.py -q`
Expected: FAIL with missing Provider classes。

- [ ] **Step 3: 实现 Provider 协议、Fake 会话和 OpenAI 事件映射**

```python
class AsrSession(Protocol):
    async def append_audio(self, pcm: bytes) -> None: ...
    async def commit(self) -> None: ...
    def events(self) -> AsyncIterator[TranscriptEvent]: ...
    async def close(self) -> None: ...
```

- [ ] **Step 4: 实现服务端 OpenAI WebSocket 生命周期**

```python
websocket = await connect(f"{base_url}?model={quote(model)}", additional_headers={"Authorization": f"Bearer {api_key}", "OpenAI-Beta": "realtime=v1"})
await websocket.send(json.dumps({"type": "session.update", "session": {"type": "transcription", "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}, "transcription": {"model": model}}}}}))
```

- [ ] **Step 5: 运行 ASR 测试，确认不需要真实 API Key**

Run: `python -m pytest tests/test_live_interview_asr.py -q`
Expected: PASS。

- [ ] **Step 6: 提交独立变更**

```bash
git add requirements-career-assistant.txt src/career_assistant/live_interview/asr tests/test_live_interview_asr.py
git commit -m "feat: add realtime transcription providers"
```

### Task 4: 持久化、上下文和中文回答服务

**Files:**
- Create: `src/career_assistant/live_interview/persistence.py`
- Create: `src/career_assistant/live_interview/context_builder.py`
- Create: `src/career_assistant/live_interview/answer_service.py`
- Test: `tests/test_live_interview_services.py`

**Interfaces:**
- Consumes: 现有 `CareerDatabase`、`CareerContextRepository`、`InterviewRetrievalService`、`ModelGateway`、`OpenAICompatibleChatClient`。
- Produces: `LiveInterviewRepository`、`LiveContextBuilder.build(...) -> LiveAnswerContext`、`LiveAnswerService.stream(...) -> AsyncIterator[str]`。

- [ ] **Step 1: 写 Actor 隔离、只存 final、事实边界与取消测试**

```python
def test_prompt_forbids_invented_personal_metrics():
    prompt = build_answer_prompt(question="你的业绩是多少？", context=LiveAnswerContext(candidate_facts=""))
    assert "不得编造" in prompt and "替换为真实经历" in prompt
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_live_interview_services.py -q`
Expected: FAIL with missing repository and prompt builder。

- [ ] **Step 3: 实现所有 SQL 都带 `organization_id + actor_id` 的仓储**

```python
SELECT * FROM live_interview_sessions
WHERE id = :session_id AND organization_id = :organization_id AND actor_id = :actor_id
```

- [ ] **Step 4: 实现最小上下文和中文流式回答 Prompt**

```python
SYSTEM_RULES = "回答统一使用中文；专有名词保留原文；个人经历与数字仅可引用已确认材料；材料缺失时给出可表达思路并提示替换为真实经历。"
```

- [ ] **Step 5: 运行服务测试**

Run: `python -m pytest tests/test_live_interview_services.py -q`
Expected: PASS。

- [ ] **Step 6: 提交独立变更**

```bash
git add src/career_assistant/live_interview/persistence.py src/career_assistant/live_interview/context_builder.py src/career_assistant/live_interview/answer_service.py tests/test_live_interview_services.py
git commit -m "feat: persist and answer live interviews"
```

### Task 5: 会话管理器和版本取消状态机

**Files:**
- Create: `src/career_assistant/live_interview/session_manager.py`
- Test: `tests/test_live_interview_session_manager.py`

**Interfaces:**
- Consumes: 两个 `AsrSession`、`TranscriptAssembler`、`QuestionDetector`、仓储与回答服务。
- Produces: `LiveSessionManager.open(session_id, actor)`, `handle(event) -> AsyncIterator[ServerEvent]`, `close(reason)`；每个答案事件携带 `question_version` 和 `attempt`。

- [ ] **Step 1: 写新问题取消旧答案、手动生成、重生成和幂等关闭测试**

```python
async def test_new_question_cancels_previous_answer():
    manager = build_manager(answer_chunks=["第一版", "迟到内容"])
    await manager.accept_question("问题一")
    events = await manager.accept_question("问题二")
    assert any(e.type == "answer.cancelled" and e.question_version == 1 for e in events)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_live_interview_session_manager.py -q`
Expected: FAIL with missing manager。

- [ ] **Step 3: 实现单连接、单答案任务、递增问题版本和迟到增量过滤**

```python
if version != self.active_question_version:
    return
await self._emit(ServerEvent.answer_delta(version=version, delta=chunk))
```

- [ ] **Step 4: 实现最大帧、10 秒缓冲和幂等资源关闭**

```python
if self._closed:
    return
self._closed = True
await asyncio.gather(*(session.close() for session in self._asr_sessions.values()), return_exceptions=True)
```

- [ ] **Step 5: 运行状态机测试**

Run: `python -m pytest tests/test_live_interview_session_manager.py -q`
Expected: PASS。

- [ ] **Step 6: 提交独立变更**

```bash
git add src/career_assistant/live_interview/session_manager.py tests/test_live_interview_session_manager.py
git commit -m "feat: manage versioned live interview sessions"
```

### Task 6: FastAPI REST 与 WebSocket 接入

**Files:**
- Create: `src/career_assistant/live_interview/web.py`
- Modify: `src/career_assistant/web/router.py`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Consumes: 平台 `platform_session` Cookie、`CareerRequestActor`、服务容器、`LiveSessionManager`。
- Produces: `GET /api/career/live-interviews/setup-options`、`POST /api/career/live-interviews/sessions`、`GET /api/career/live-interviews/sessions/{id}`、`WS /api/career/live-interviews/{id}/stream`。

- [ ] **Step 1: 写未登录拒绝、Actor 越权 404 和 Fake WebSocket 端到端测试**

```python
with client.websocket_connect(f"/api/career/live-interviews/{session_id}/stream") as ws:
    ws.send_json({"type": "session.start"})
    assert ws.receive_json()["type"] == "session.ready"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_live_interview_web.py -q`
Expected: FAIL because routes do not exist。

- [ ] **Step 3: 实现 REST、WebSocket 显式 Cookie 身份解析和错误分类**

```python
cookie = websocket.cookies.get("platform_session")
actor = resolve_websocket_actor(websocket.app, cookie)
if actor is None:
    await websocket.close(code=4401)
```

- [ ] **Step 4: 在 `install_career_assistant_api` 安装新 Router 并注册关闭钩子**

```python
from src.career_assistant.live_interview.web import router as live_interview_router
app.include_router(live_interview_router)
```

- [ ] **Step 5: 运行 API 测试和现有 Career 回归**

Run: `python -m pytest tests/test_live_interview_web.py tests/test_career_turn_api.py tests/test_career_optional_context_repository.py -q`
Expected: PASS。

- [ ] **Step 6: 提交独立变更**

```bash
git add src/career_assistant/live_interview/web.py src/career_assistant/web/router.py tests/test_live_interview_web.py
git commit -m "feat: expose live interview websocket api"
```

### Task 7: Windows Electron 壳与双音轨采集

**Files:**
- Create: `desktop-interview-assistant/package.json`
- Create: `desktop-interview-assistant/electron/main.ts`
- Create: `desktop-interview-assistant/electron/preload.ts`
- Create: `desktop-interview-assistant/src/audio/pcm-worklet.ts`
- Create: `desktop-interview-assistant/src/audio/capture.ts`
- Create: `desktop-interview-assistant/src/protocol/client.ts`
- Test: `desktop-interview-assistant/src/audio/capture.test.ts`

**Interfaces:**
- Consumes: Windows loopback display-media stream、麦克风 `MediaStream`、服务端事件合同。
- Produces: `startCapture(devices) -> CaptureController`，24 kHz PCM16 帧及 interviewer/candidate 独立 sequence，`stop()` 释放所有 Track 和 AudioContext。

- [ ] **Step 1: 写浮点转 PCM16、独立序号和停止释放测试**

```ts
expect(floatToPcm16(new Float32Array([-1, 0, 1]))).toEqual(new Int16Array([-32768, 0, 32767]))
```

- [ ] **Step 2: 安装依赖并运行测试确认失败**

Run: `cd desktop-interview-assistant && npm install && npm test -- --run`
Expected: FAIL with missing audio modules。

- [ ] **Step 3: 实现 Electron Windows 平台保护与 loopback 授权**

```ts
if (process.platform !== "win32") throw new Error("实时面试助手仅支持 Windows")
session.defaultSession.setDisplayMediaRequestHandler(async (_request, callback) => callback({ video: sources[0], audio: "loopback" }))
```

- [ ] **Step 4: 实现 AudioWorklet 重采样、PCM16 分帧和业务 WebSocket 客户端**

```ts
socket.send(JSON.stringify({ type: "audio.append", channel, sequence: nextSequence(channel), pcm_base64: bytesToBase64(frame) }))
```

- [ ] **Step 5: 运行单测和 TypeScript 检查**

Run: `cd desktop-interview-assistant && npm test -- --run && npm run typecheck`
Expected: PASS。

- [ ] **Step 6: 提交独立变更**

```bash
git add desktop-interview-assistant
git commit -m "feat: capture Windows interview audio"
```

### Task 8: 三个单任务页面与实时交互

**Files:**
- Create: `desktop-interview-assistant/src/main.ts`
- Create: `desktop-interview-assistant/src/App.vue`
- Create: `desktop-interview-assistant/src/router.ts`
- Create: `desktop-interview-assistant/src/pages/SetupPage.vue`
- Create: `desktop-interview-assistant/src/pages/SessionPage.vue`
- Create: `desktop-interview-assistant/src/pages/HistoryPage.vue`
- Create: `desktop-interview-assistant/src/stores/session.ts`
- Create: `desktop-interview-assistant/src/style.css`
- Test: `desktop-interview-assistant/src/stores/session.test.ts`

**Interfaces:**
- Consumes: setup REST、`InterviewSocketClient`、音频控制器和版本化服务端事件。
- Produces: `/live-interview/setup`、`/live-interview/session/:id`、`/live-interview/history/:id`，以及开始、立即生成、停止、重新生成和结束操作。

- [ ] **Step 1: 写迟到答案过滤、角色展示和结束清理测试**

```ts
store.activeQuestionVersion = 2
store.apply({ type: "answer.delta", question_version: 1, delta: "迟到" })
expect(store.answerText).toBe("")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd desktop-interview-assistant && npm test -- --run`
Expected: FAIL with missing store and pages。

- [ ] **Step 3: 实现三路由和中文界面状态**

```ts
const routes = [
  { path: "/live-interview/setup", component: SetupPage },
  { path: "/live-interview/session/:id", component: SessionPage },
  { path: "/live-interview/history/:id", component: HistoryPage },
]
```

- [ ] **Step 4: 实现 partial/final、问题版本、流式回答和手动控制**

```ts
if (event.question_version < activeQuestionVersion.value) return
```

- [ ] **Step 5: 运行桌面测试、TypeScript 检查和生产构建**

Run: `cd desktop-interview-assistant && npm test -- --run && npm run typecheck && npm run build`
Expected: PASS and creates `dist/` plus Electron output。

- [ ] **Step 6: 提交独立变更**

```bash
git add desktop-interview-assistant
git commit -m "feat: add live interview desktop experience"
```

### Task 9: 文档、回归与 Windows 验收脚本

**Files:**
- Create: `docs/modules/windows-live-interview-assistant.md`
- Create: `scripts/verify-live-interview.ps1`
- Modify: `docs/superpowers/specs/2026-08-23-windows-live-interview-assistant-design.md`
- Modify: `docs/superpowers/plans/2026-08-23-windows-live-interview-assistant.md`

**Interfaces:**
- Consumes: 全部后端/桌面测试与构建命令、真实 Windows 人工场景。
- Produces: 一条自动验收入口、模块维护文档和明确的凭据/设备实测边界。

- [ ] **Step 1: 编写自动验收脚本**

```powershell
python -m pytest tests/test_live_interview_*.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Push-Location desktop-interview-assistant
npm test -- --run
npm run typecheck
npm run build
Pop-Location
```

- [ ] **Step 2: 编写模块文档，记录目标、取舍、调用链、依赖、验证和边界**

Run: `rg -n "设计目标|技术取舍|调用链|依赖|验证结果|后续边界" docs/modules/windows-live-interview-assistant.md`
Expected: 六个章节均命中。

- [ ] **Step 3: 运行完整自动验收**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify-live-interview.ps1`
Expected: Python tests、Vitest、typecheck、Vite/Electron build 全部 PASS。

- [ ] **Step 4: 检查原始音频零持久化与迁移唯一 head**

Run: `rg -n "pcm_base64|WAV|\.wav|partial" migrations src/career_assistant/live_interview/persistence.py; python -m alembic heads`
Expected: 数据库 schema/repository 不存在 PCM/WAV/partial 持久化字段；唯一 head 为 `20260823_18`。

- [ ] **Step 5: 在可用凭据环境执行受控真实验收**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify-live-interview.ps1 -Live -MeetingApps Teams,腾讯会议 -DurationMinutes 60`
Expected: 中文、英文、中英混合和非 IT 术语均有 final 转写；新问题取消旧答案；结束后 2 秒内停止；若没有 OpenAI API Key 或第二通话端，则明确记录为“外部条件未满足”，不伪报通过。

- [ ] **Step 6: 更新 spec/plan 状态并提交文档**

```bash
git add docs/modules/windows-live-interview-assistant.md scripts/verify-live-interview.ps1 docs/superpowers/specs/2026-08-23-windows-live-interview-assistant-design.md docs/superpowers/plans/2026-08-23-windows-live-interview-assistant.md
git commit -m "docs: verify Windows live interview assistant"
```

## Self-Review Result

- Spec coverage：双通道、双语、统一中文、术语、问题版本、手动控制、事实边界、持久化、认证、断开清理、三路由、OpenAI/Fake Provider 和 Windows 验收均映射到 Task 1～9。
- Placeholder scan：所有步骤均包含具体文件、接口、测试命令和通过标准；外部凭据不可替代的真实调用单独列为受控验收边界。
- Type consistency：前后端统一使用 `channel`、`sequence`、`question_version`、`attempt`；客户端只发送七类事件，服务端只输出设计文档中的事件族；Provider 与 Manager 均使用异步生命周期。
- Scope note：本计划保留一个端到端可运行项目，但以九个独立可验收任务切分后端、桌面端和最终验收，任何任务都能单独回归和提交。
