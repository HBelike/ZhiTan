# 面试大师逐句触发与连续追问 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每条有效的面试官 final 转写按顺序获得回答，并让追问携带当前问题之前的最近 4 条转写。

**Architecture:** 把现有问题检测器从“问句关键词门禁”改成“角色/final 门禁 + 精确填充词过滤 + 3 秒完全重复保护”，问题类型仅用于回答风格。`LiveSessionManager` 在问题入队时捕获历史快照，由单个 FIFO worker 依次发送现有 WebSocket 事件并流式生成；前端仍只处理一个活动问题。

**Tech Stack:** Python 3.12、asyncio、pytest、FastAPI WebSocket、Vue 3/Node test runner

**Spec:** `docs/superpowers/specs/2026-09-01-live-interview-final-utterance-answering-design.md`

## Global Constraints

- 所有 `interviewer + final` 默认触发回答；`candidate` 与 `partial` 永不触发。
- 只过滤整句填充词和 3 秒内完全重复文本，不增加 LLM 意图识别。
- 自动问题 FIFO 处理，新问题不得自动取消旧问题。
- `FOLLOW_UP` 只携带当前问题之前的最近 4 条转写；当前问题单独传入。
- 不修改前端页面结构、WebSocket 事件名称或数据库结构。
- 浏览器候选人转写继续仅存在于当前连接内存，不持久化。
- 仅本地修改和验证，不部署生产。

---

### Task 1: 将问题检测改成 final 触发策略

**Files:**
- Modify: `src/career_assistant/live_interview/question_detector.py`
- Test: `tests/test_live_interview_core.py`

**Interfaces:**
- Consumes: `TranscriptEvent`、可选 `previous_question: str | None`
- Produces: `RuleBasedQuestionDetector.detect(...) -> DetectedQuestion | None`，公开签名保持兼容；构造器新增可注入 `clock` 和重复窗口

- [ ] **Step 1: 写入陈述式任务、填充词与重复保护失败测试**

```python
def test_detector_answers_every_non_filler_interviewer_final() -> None:
    detector = RuleBasedQuestionDetector()
    for sequence, text in enumerate(("分析一下日股。", "评价一下这个方案。", "展开。"), 1):
        detected = detector.detect(TranscriptEvent(AudioChannel.INTERVIEWER, sequence, text, True))
        assert detected is not None
        assert detected.confidence == 1.0


def test_detector_ignores_exact_fillers_but_not_longer_requests() -> None:
    detector = RuleBasedQuestionDetector()
    assert detector.detect(TranscriptEvent(AudioChannel.INTERVIEWER, 1, "嗯。", True)) is None
    assert detector.detect(TranscriptEvent(AudioChannel.INTERVIEWER, 2, "好的。", True)) is None
    assert detector.detect(
        TranscriptEvent(AudioChannel.INTERVIEWER, 3, "好的，请继续说明。", True)
    ) is not None


def test_detector_deduplicates_only_inside_three_second_window() -> None:
    times = iter((10.0, 11.0, 14.1))
    detector = RuleBasedQuestionDetector(clock=lambda: next(times))
    event = lambda sequence: TranscriptEvent(AudioChannel.INTERVIEWER, sequence, "解释 CAP。", True)
    assert detector.detect(event(1)) is not None
    assert detector.detect(event(2)) is None
    assert detector.detect(event(3)) is not None
```

- [ ] **Step 2: 运行定向测试并确认旧实现失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_core.py -q`

Expected: 新增陈述式任务和重复保护测试失败。

- [ ] **Step 3: 实现确定性门禁、填充词规范化和重复窗口**

```python
_FILLER_UTTERANCES = frozenset(
    {"嗯", "嗯嗯", "哦", "噢", "啊", "好的", "好", "行", "可以", "知道了", "明白了", "okay", "ok"}
)
_EDGE_PUNCTUATION = re.compile(r"^[\s，。！？!?、,.；;：:~～…]+|[\s，。！？!?、,.；;：:~～…]+$")
_DUPLICATE_WINDOW_SECONDS = 3.0


class RuleBasedQuestionDetector:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_text: str | None = None
        self._last_detected_at: float | None = None

    def detect(self, event: TranscriptEvent, previous_question: str | None = None) -> DetectedQuestion | None:
        if event.role is not SpeakerRole.INTERVIEWER or not event.is_final:
            return None
        text = re.sub(r"\s+", " ", event.text).strip()
        lowered = text.casefold()
        filler_key = _EDGE_PUNCTUATION.sub("", lowered)
        if not text or filler_key in _FILLER_UTTERANCES:
            return None
        now = self._clock()
        if (
            self._last_text == lowered
            and self._last_detected_at is not None
            and now - self._last_detected_at <= _DUPLICATE_WINDOW_SECONDS
        ):
            return None
        self._last_text = lowered
        self._last_detected_at = now
```

保留 `_FOLLOW_UP` 和 `_classify`，删除 `_QUESTION_MARKERS` 门禁；所有接受项返回 `confidence=1.0`。

- [ ] **Step 4: 运行核心测试并确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_core.py -q`

Expected: PASS。

- [ ] **Step 5: 提交检测器改动**

```bash
git add src/career_assistant/live_interview/question_detector.py tests/test_live_interview_core.py
git commit -m "fix: answer every interviewer final utterance"
```

### Task 2: 把追问上下文扩展为前面 4 条

**Files:**
- Modify: `src/career_assistant/live_interview/answer_service.py`
- Test: `tests/test_live_interview_services.py`

**Interfaces:**
- Consumes: `LiveAnswerContext.recent_conversation`，其内容由会话管理器保证不含当前问题
- Produces: `build_answer_prompt(...)` 在 `FOLLOW_UP` 时使用最多 4 条历史

- [ ] **Step 1: 写入四条历史和当前问题不重复的失败测试**

```python
def test_follow_up_prompt_uses_four_prior_utterances_without_repeating_current() -> None:
    current = "那它在分区恢复后怎么收敛？"
    history = (
        "interviewer: 请解释 CAP。",
        "candidate: CAP 需要在一致性和可用性之间取舍。",
        "interviewer: 重点讲讲 AP。",
        "candidate: AP 优先保证可用性。",
    )
    prompt = build_answer_prompt(
        current,
        QuestionIntent.FOLLOW_UP,
        LiveAnswerContext(recent_conversation=history),
    )
    assert all(item in prompt for item in history)
    assert prompt.count(current) == 1
```

- [ ] **Step 2: 运行服务测试并确认新增测试失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_services.py -q`

Expected: 现有 `[-2:]` 只保留两条，新增测试失败。

- [ ] **Step 3: 将追问历史切片调整为最近 4 条**

```python
if intent is QuestionIntent.FOLLOW_UP and context.recent_conversation:
    recent = "\n".join(context.recent_conversation[-4:])
```

当前问题继续只通过 `面试官问题：{question.strip()}` 传入。

- [ ] **Step 4: 运行服务测试并确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_services.py -q`

Expected: PASS。

- [ ] **Step 5: 提交 Prompt 改动**

```bash
git add src/career_assistant/live_interview/answer_service.py tests/test_live_interview_services.py
git commit -m "fix: include four prior utterances in follow-ups"
```

### Task 3: 用 FIFO worker 保证每条自动问题都完成

**Files:**
- Modify: `src/career_assistant/live_interview/session_manager.py`
- Test: `tests/test_live_interview_services.py`

**Interfaces:**
- Produces: 私有不可变 `_QueuedAnswer(detected, recent_conversation, manual=False, version=None, attempt=1, emit_detected=True)`
- Produces: `_answer_queue: deque[_QueuedAnswer]` 和单个 `_answer_task` worker
- Preserves: `question.detected`、`answer.started`、`answer.delta`、`answer.completed`、`answer.cancelled` WebSocket 合同

- [ ] **Step 1: 扩展脚本 ASR 测试工具并写入 FIFO 失败测试**

```python
async def emit(self, transcript: TranscriptEvent) -> None:
    await self.queue.put(transcript)


async def _assert_session_manager_answers_rapid_finals_in_fifo_order() -> None:
    first_release = asyncio.Event()
    prompts: list[str] = []

    async def answer(prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            await first_release.wait()
        yield f"回答{len(prompts)}"

    # 连续发送“分析一下日股。”和“评价一下这个方案。”；第一题阻塞期间第二题只能入队。
    # 释放后断言两个 answer.completed 按顺序出现，且没有 superseded 取消。
```

同时增加：第一题失败后第二题继续、后续话语不泄漏进更早问题的历史快照、候选人 final 只进入历史不入队。

- [ ] **Step 2: 运行服务测试并确认旧状态机失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_services.py -q`

Expected: 旧 `_activate_question` 会以 `superseded` 取消第一题，FIFO 测试失败。

- [ ] **Step 3: 新增队列项和入队时历史快照**

```python
@dataclass(frozen=True)
class _QueuedAnswer:
    detected: DetectedQuestion
    recent_conversation: tuple[str, ...]
    manual: bool = False
    version: int | None = None
    attempt: int = 1
    emit_detected: bool = True
```

在处理 final 时先捕获 `prior_conversation = tuple(self._recent_conversation[-4:])`，再追加当前话语。自动检测使用 `_last_detected_question`，接受后更新该字段并把快照随问题入队。

- [ ] **Step 4: 实现单 worker FIFO 调度**

```python
def _ensure_answer_worker(self) -> None:
    if self._closed or not self._answer_queue:
        return
    if self._answer_task is not None and not self._answer_task.done():
        return
    self._answer_task = asyncio.create_task(
        self._drain_answer_queue(),
        name="live-answer-worker",
    )


async def _drain_answer_queue(self) -> None:
    try:
        while self._answer_queue and not self._closed:
            job = self._answer_queue.popleft()
            await self._activate_job(job)
            await self._run_answer(
                self._question_version,
                self._attempt,
                self._active_question,
                self._active_intent,
                job.recent_conversation,
            )
    finally:
        if asyncio.current_task() is self._answer_task:
            self._answer_task = None
```

自动入队不得调用 `_cancel_answer("superseded")`。`question.detected` 只在 `_activate_job` 取出队列项时发送。

- [ ] **Step 5: 保持手动回答与重新生成语义**

显式手动回答先取消当前 worker，把同文本待处理项移除，再插入队首并重启 worker；被用户主动替换的当前问题不重新入队。重新生成复用当前 version、递增 attempt，并排在原待处理自动问题之前。关闭会话时清空队列。

- [ ] **Step 6: 运行核心与服务测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_core.py tests/test_live_interview_services.py -q`

Expected: PASS。

- [ ] **Step 7: 提交 FIFO 状态机改动**

```bash
git add src/career_assistant/live_interview/session_manager.py tests/test_live_interview_services.py
git commit -m "fix: queue live interview answers in order"
```

### Task 4: 记录模块变更并完成回归验证

**Files:**
- Create: `docs/fixes/2026-09-01-live-interview-final-trigger-and-followup.md`
- Verify: `tests/test_live_interview_web.py`
- Verify: `web-ui/src/browser-live-interview-core.test.js`
- Verify: `web-ui/src/browser-live-interview-view.test.js`

**Interfaces:**
- Consumes: Task 1～3 的最终行为与测试结果
- Produces: 可交接的设计目标、调用链、技术取舍、验证结果和后续边界记录

- [ ] **Step 1: 写变更记录**

文档必须记录：旧关键词门禁根因、final 触发策略、填充词/重复边界、FIFO 事件顺序、四条历史快照、手动覆盖语义、未改前端/数据库/部署，以及实际测试命令和结果。

- [ ] **Step 2: 运行实时面试后端回归**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_core.py tests/test_live_interview_services.py tests/test_live_interview_web.py -q`

Expected: PASS。

- [ ] **Step 3: 运行前端实时面试测试**

Run: `npm test -- --runInBand`（若项目脚本不接受该参数，则使用 `npm test`）

Workdir: `web-ui`

Expected: PASS。

- [ ] **Step 4: 运行前端生产构建**

Run: `npm run build`

Workdir: `web-ui`

Expected: PASS。

- [ ] **Step 5: 检查改动范围**

Run: `git diff --check`

Expected: 无空白错误；不包含数据库迁移、前端页面改版或生产部署文件。

- [ ] **Step 6: 提交验证记录**

```bash
git add docs/fixes/2026-09-01-live-interview-final-trigger-and-followup.md
git commit -m "docs: record live interview trigger verification"
```
