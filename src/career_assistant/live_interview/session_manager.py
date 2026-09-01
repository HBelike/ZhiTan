"""单个实时面试 WebSocket 的内存状态与问题版本状态机。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.career_assistant.live_interview.answer_service import LiveAnswerContext, LiveAnswerService
from src.career_assistant.live_interview.asr.base import AsrSession
from src.career_assistant.live_interview.contracts import (
    AnswerCancelEvent,
    AnswerRequestEvent,
    AudioAppendEvent,
    AudioChannel,
    AudioCommitEvent,
    ClientEvent,
    DetectedQuestion,
    PingEvent,
    QuestionIntent,
    ServerEvent,
    SessionEndEvent,
    SessionStartEvent,
    TranscriptEvent,
)
from src.career_assistant.live_interview.question_detector import RuleBasedQuestionDetector
from src.career_assistant.live_interview.transcript_assembler import TranscriptAssembler


TranscriptHook = Callable[[TranscriptEvent], Awaitable[None]]
AnswerHook = Callable[[int, int, str, QuestionIntent, str, str], Awaitable[None]]


async def _noop_transcript(event: TranscriptEvent) -> None:
    return None


async def _noop_answer(
    version: int,
    attempt: int,
    question: str,
    intent: QuestionIntent,
    status: str,
    text: str,
) -> None:
    return None


@dataclass(frozen=True)
class _QueuedAnswer:
    detected: DetectedQuestion
    recent_conversation: tuple[str, ...]
    manual: bool = False
    version: int | None = None
    attempt: int = 1
    emit_detected: bool = True


class LiveSessionManager:
    """每个连接一个实例；负责释放 ASR 与取消迟到回答。"""

    def __init__(
        self,
        *,
        asr_sessions: dict[AudioChannel, AsrSession],
        answer_service: LiveAnswerService,
        answer_context: LiveAnswerContext | None = None,
        transcript_hook: TranscriptHook = _noop_transcript,
        answer_hook: AnswerHook = _noop_answer,
    ) -> None:
        self._asr_sessions = asr_sessions
        self._answer_service = answer_service
        self._answer_context = answer_context or LiveAnswerContext()
        self._transcript_hook = transcript_hook
        self._answer_hook = answer_hook
        self._assembler = TranscriptAssembler()
        self._detector = RuleBasedQuestionDetector()
        self._outgoing: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=1_000)
        self._reader_tasks: list[asyncio.Task[None]] = []
        self._answer_task: asyncio.Task[None] | None = None
        self._answer_queue: deque[_QueuedAnswer] = deque()
        self._answer_running = False
        self._closed = False
        self._started = False
        self._question_version = 0
        self._attempt = 0
        self._active_question: str | None = None
        self._active_intent = QuestionIntent.KNOWLEDGE
        self._active_recent_conversation: tuple[str, ...] = ()
        self._last_detected_question: str | None = None
        self._last_interviewer_final: str | None = None
        self._recent_conversation: list[str] = []

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for channel, session in self._asr_sessions.items():
            self._reader_tasks.append(
                asyncio.create_task(self._read_asr(channel, session), name=f"live-asr-{channel.value}")
            )
        await self._emit(
            "session.ready",
            sample_rate=24_000,
            active_channels=[channel.value for channel in self._asr_sessions],
        )

    async def next_event(self) -> ServerEvent:
        return await self._outgoing.get()

    async def handle(self, event: ClientEvent) -> None:
        if self._closed:
            return
        if isinstance(event, SessionStartEvent):
            await self.start()
        elif isinstance(event, AudioAppendEvent):
            if event.channel not in self._asr_sessions:
                raise ValueError(f"当前会话未启用 {event.channel.value} 音频轨道")
            await self._asr_sessions[event.channel].append_audio(event.pcm, event.sequence)
        elif isinstance(event, AudioCommitEvent):
            if event.channel not in self._asr_sessions:
                raise ValueError(f"当前会话未启用 {event.channel.value} 音频轨道")
            await self._asr_sessions[event.channel].commit()
        elif isinstance(event, AnswerRequestEvent):
            await self._handle_answer_request(event)
        elif isinstance(event, AnswerCancelEvent):
            await self._cancel_answer("user")
            self._ensure_answer_worker()
        elif isinstance(event, PingEvent):
            await self._emit("pong")
        elif isinstance(event, SessionEndEvent):
            await self.close("user")

    async def _read_asr(self, channel: AudioChannel, session: AsrSession) -> None:
        try:
            async for event in session.events():
                if self._closed:
                    return
                accepted = self._assembler.accept(event)
                if accepted is not None:
                    await self._handle_transcript(accepted)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit("error", code="asr_stream_failed", message=f"{channel.value} 转写中断")

    async def _handle_transcript(self, event: TranscriptEvent) -> None:
        await self._emit(
            "transcript.final" if event.is_final else "transcript.partial",
            channel=event.channel.value,
            role=event.role.value,
            sequence=event.sequence,
            text=event.text,
        )
        if not event.is_final:
            return
        prior_conversation = tuple(self._recent_conversation[-4:])
        self._recent_conversation.append(f"{event.role.value}: {event.text}")
        self._recent_conversation = self._recent_conversation[-12:]
        await self._transcript_hook(event)
        if event.channel is AudioChannel.INTERVIEWER:
            self._last_interviewer_final = event.text
        detected = self._detector.detect(event, self._last_detected_question)
        if detected is not None:
            self._last_detected_question = detected.normalized_question
            self._answer_queue.append(
                _QueuedAnswer(
                    detected=detected,
                    recent_conversation=prior_conversation,
                )
            )
            self._ensure_answer_worker()

    async def _activate_job(self, job: _QueuedAnswer) -> None:
        if job.version is None:
            self._question_version += 1
        else:
            self._question_version = job.version
        self._attempt = job.attempt
        self._active_question = job.detected.normalized_question
        self._active_intent = job.detected.intent
        self._active_recent_conversation = job.recent_conversation
        if job.emit_detected:
            await self._emit(
                "question.detected",
                question_version=self._question_version,
                question=self._active_question,
                intent=job.detected.intent.value,
                confidence=job.detected.confidence,
                is_follow_up=job.detected.is_follow_up,
                manual=job.manual,
            )

    async def _handle_answer_request(self, event: AnswerRequestEvent) -> None:
        if event.mode == "regenerate":
            if self._active_question is None:
                raise ValueError("当前没有可重新生成的问题")
            question = self._active_question
            intent = self._active_intent
            recent_conversation = self._active_recent_conversation
            version = self._question_version
            attempt = self._attempt + 1
            await self._cancel_answer("regenerate")
            self._answer_queue.appendleft(
                _QueuedAnswer(
                    detected=DetectedQuestion(
                        normalized_question=question,
                        intent=intent,
                        confidence=1.0,
                        is_follow_up=intent is QuestionIntent.FOLLOW_UP,
                    ),
                    recent_conversation=recent_conversation,
                    version=version,
                    attempt=attempt,
                    emit_detected=False,
                )
            )
        else:
            question = event.question or self._last_interviewer_final
            if not question:
                raise ValueError("尚未收到可用于生成的面试官话语")
            await self._cancel_answer("manual_replaced")
            self._answer_queue = deque(
                job
                for job in self._answer_queue
                if job.detected.normalized_question != question
            )
            detected = DetectedQuestion(
                normalized_question=question,
                intent=QuestionIntent.KNOWLEDGE,
                confidence=1.0,
                is_follow_up=False,
            )
            self._last_detected_question = question
            self._answer_queue.appendleft(
                _QueuedAnswer(
                    detected=detected,
                    recent_conversation=tuple(self._recent_conversation[-4:]),
                    manual=True,
                )
            )
        self._ensure_answer_worker()

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
        worker = asyncio.current_task()
        try:
            while self._answer_queue and not self._closed:
                job = self._answer_queue.popleft()
                await self._activate_job(job)
                question = self._active_question
                assert question is not None
                self._answer_running = True
                try:
                    await self._run_answer(
                        self._question_version,
                        self._attempt,
                        question,
                        self._active_intent,
                        job.recent_conversation,
                    )
                finally:
                    self._answer_running = False
        finally:
            if self._answer_task is worker:
                self._answer_task = None

    async def _run_answer(
        self,
        version: int,
        attempt: int,
        question: str,
        intent: QuestionIntent,
        recent_conversation: tuple[str, ...],
    ) -> None:
        text = ""
        try:
            await self._answer_hook(version, attempt, question, intent, "generating", text)
            await self._emit("answer.started", question_version=version, attempt=attempt)
            context = LiveAnswerContext(
                candidate_facts=self._answer_context.candidate_facts,
                target_role=self._answer_context.target_role,
                recent_conversation=recent_conversation,
                interview_evidence=self._answer_context.interview_evidence,
                terminology=self._answer_context.terminology,
            )
            delta_index = 0
            async for chunk in self._answer_service.stream(question, intent, context):
                if version != self._question_version or attempt != self._attempt:
                    return
                text += chunk
                delta_index += 1
                await self._emit(
                    "answer.delta",
                    question_version=version,
                    attempt=attempt,
                    delta_index=delta_index,
                    delta=chunk,
                )
            await self._answer_hook(version, attempt, question, intent, "completed", text)
            await self._emit(
                "answer.completed",
                question_version=version,
                attempt=attempt,
                answer_text=text,
            )
        except asyncio.CancelledError:
            await self._answer_hook(version, attempt, question, intent, "cancelled", text)
            raise
        except Exception:
            await self._answer_hook(version, attempt, question, intent, "failed", text)
            await self._emit(
                "error",
                code="answer_failed",
                message="回答生成失败，可点击重新生成",
                question_version=version,
            )

    async def _cancel_answer(self, reason: str) -> None:
        task = self._answer_task
        if task is None or task.done():
            return
        version, attempt = self._question_version, self._attempt
        had_active_answer = self._answer_running and version > 0 and attempt > 0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if had_active_answer:
            await self._emit(
                "answer.cancelled",
                question_version=version,
                attempt=attempt,
                reason=reason,
            )
        if self._answer_task is task:
            self._answer_task = None

    async def _emit(self, event_type: str, **payload: object) -> None:
        await self._outgoing.put(ServerEvent(event_type, dict(payload)))

    async def close(self, reason: str = "closed") -> None:
        if self._closed:
            return
        self._closed = True
        self._answer_queue.clear()
        await self._cancel_answer(reason)
        for task in self._reader_tasks:
            task.cancel()
        await asyncio.gather(*self._reader_tasks, return_exceptions=True)
        await asyncio.gather(
            *(session.close() for session in self._asr_sessions.values()),
            return_exceptions=True,
        )
