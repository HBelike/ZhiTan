from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from src.career_assistant.live_interview.answer_service import (
    LiveAnswerContext,
    LiveAnswerService,
    build_answer_prompt,
)
from src.career_assistant.live_interview.contracts import (
    AnswerRequestEvent,
    AudioAppendEvent,
    AudioChannel,
    AudioCommitEvent,
    QuestionIntent,
    TranscriptEvent,
)
from src.career_assistant.live_interview.session_manager import LiveSessionManager
from src.career_assistant.live_interview.persistence import LiveInterviewRepository


def test_prompt_requires_chinese_and_forbids_personal_fabrication() -> None:
    prompt = build_answer_prompt(
        "你的业绩是多少？",
        QuestionIntent.BEHAVIORAL,
        LiveAnswerContext(candidate_facts="", target_role="医疗器械产品经理"),
    )

    assert "统一使用中文" in prompt
    assert "不得编造" in prompt
    assert "可替换占位提示" in prompt
    assert "专有名词保留原文" in prompt


def test_prompt_only_uses_transcribed_interviewer_question() -> None:
    prompt = build_answer_prompt(
        "请解释 Kafka consumer group？",
        QuestionIntent.KNOWLEDGE,
        LiveAnswerContext(
            candidate_facts="不应进入提示词的简历内容",
            target_role="不应进入提示词的岗位内容",
            recent_conversation=("不应进入提示词的历史对话",),
            interview_evidence=("不应进入提示词的面经",),
        ),
    )

    assert "请解释 Kafka consumer group？" in prompt
    assert "不应进入提示词" not in prompt
    assert "最近对话：" not in prompt
    assert "已确认个人材料：" not in prompt
    assert "面经检索证据" not in prompt


def test_repository_rejects_partial_before_opening_database_transaction() -> None:
    class DatabaseThatMustNotBeUsed:
        def transaction(self):
            raise AssertionError("partial 不应接触数据库")

    repository = LiveInterviewRepository(DatabaseThatMustNotBeUsed())
    event = TranscriptEvent(AudioChannel.INTERVIEWER, 1, "尚未结束", False)

    try:
        repository.append_final_utterance(uuid4(), uuid4(), uuid4(), event)
    except ValueError as exc:
        assert "final" in str(exc)
    else:
        raise AssertionError("partial 转写被错误持久化")


def test_answer_service_streams_without_buffering_entire_answer() -> None:
    asyncio.run(_assert_answer_service_streams_without_buffering_entire_answer())


async def _assert_answer_service_streams_without_buffering_entire_answer() -> None:
    async def generator(prompt: str) -> AsyncIterator[str]:
        assert "CAP theorem" in prompt
        yield "直接结论："
        yield "CAP 需要权衡。"

    chunks = [
        chunk
        async for chunk in LiveAnswerService(generator).stream(
            "Explain CAP theorem",
            QuestionIntent.KNOWLEDGE,
            LiveAnswerContext(),
        )
    ]

    assert chunks == ["直接结论：", "CAP 需要权衡。"]


class ScriptedAsrSession:
    def __init__(self, transcript: TranscriptEvent | None = None) -> None:
        self.transcript = transcript
        self.queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        self.closed = False

    async def append_audio(self, pcm: bytes, sequence: int) -> None:
        assert pcm and sequence >= 0

    async def commit(self) -> None:
        if self.transcript is not None:
            await self.queue.put(self.transcript)

    async def emit(self, transcript: TranscriptEvent) -> None:
        await self.queue.put(transcript)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self.closed = True
        await self.queue.put(None)


def test_session_manager_detects_question_and_streams_answer() -> None:
    asyncio.run(_assert_session_manager_detects_question_and_streams_answer())


async def _assert_session_manager_detects_question_and_streams_answer() -> None:
    interviewer = ScriptedAsrSession(
        TranscriptEvent(
            channel=AudioChannel.INTERVIEWER,
            sequence=1,
            text="请解释 CAP theorem？",
            is_final=True,
        )
    )
    candidate = ScriptedAsrSession(
        TranscriptEvent(
            channel=AudioChannel.CANDIDATE,
            sequence=1,
            text="我先思考一下",
            is_final=True,
        )
    )

    async def answer(prompt: str):
        assert "CAP theorem" in prompt
        yield "CAP 的核心是分布式权衡。"

    manager = LiveSessionManager(
        asr_sessions={
            AudioChannel.INTERVIEWER: interviewer,
            AudioChannel.CANDIDATE: candidate,
        },
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    await manager.handle(AudioCommitEvent(AudioChannel.INTERVIEWER))

    event_types: list[str] = []
    for _ in range(6):
        event = await asyncio.wait_for(manager.next_event(), timeout=1)
        event_types.append(event.type)
        if event.type == "answer.completed":
            break

    assert event_types[:2] == ["session.ready", "transcript.final"]
    assert "question.detected" in event_types
    assert "answer.delta" in event_types
    assert event_types[-1] == "answer.completed"
    await manager.close("test")
    assert interviewer.closed and candidate.closed


def test_session_manager_reports_only_active_channels() -> None:
    asyncio.run(_assert_session_manager_reports_only_active_channels())


async def _assert_session_manager_reports_only_active_channels() -> None:
    interviewer = ScriptedAsrSession(
        TranscriptEvent(AudioChannel.INTERVIEWER, 1, "请介绍限流算法？", True)
    )

    async def answer(prompt: str):
        yield "令牌桶适合允许突发流量。"

    manager = LiveSessionManager(
        asr_sessions={AudioChannel.INTERVIEWER: interviewer},
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    ready = await manager.next_event()

    assert ready.type == "session.ready"
    assert ready.payload["active_channels"] == ["interviewer"]
    try:
        await manager.handle(AudioAppendEvent(AudioChannel.CANDIDATE, 0, b"pcm"))
    except ValueError as exc:
        assert "未启用 candidate" in str(exc)
    else:
        raise AssertionError("未启用的 candidate 音频轨道不应被接收")
    await manager.close("test")


def test_follow_up_prompt_uses_recent_conversation_for_reference_resolution() -> None:
    prompt = build_answer_prompt(
        "那它的缺点是什么？",
        QuestionIntent.FOLLOW_UP,
        LiveAnswerContext(
            recent_conversation=(
                "interviewer: 请介绍令牌桶？",
                "candidate: 我会先说明令牌补充与突发流量。",
            )
        ),
    )

    assert "请介绍令牌桶" in prompt
    assert "令牌补充与突发流量" in prompt


def test_follow_up_prompt_uses_four_prior_utterances_without_repeating_current() -> None:
    current = "那如果分区恢复，它怎么收敛？"
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


def test_session_manager_answers_rapid_finals_in_fifo_order() -> None:
    asyncio.run(_assert_session_manager_answers_rapid_finals_in_fifo_order())


async def _assert_session_manager_answers_rapid_finals_in_fifo_order() -> None:
    interviewer = ScriptedAsrSession()
    first_release = asyncio.Event()
    prompts: list[str] = []

    async def answer(prompt: str):
        prompts.append(prompt)
        if "分析一下日股。" in prompt:
            await first_release.wait()
            yield "日股回答"
            return
        yield "方案回答"

    manager = LiveSessionManager(
        asr_sessions={AudioChannel.INTERVIEWER: interviewer},
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    await _wait_for_type(manager, "session.ready")
    await interviewer.emit(TranscriptEvent(AudioChannel.INTERVIEWER, 1, "分析一下日股。", True))
    first_started = await _wait_for_type(manager, "answer.started")
    await interviewer.emit(TranscriptEvent(AudioChannel.INTERVIEWER, 2, "评价一下这个方案。", True))
    await _wait_for_type(manager, "transcript.final")
    first_release.set()

    event_types: list[str] = []
    detected_questions = ["分析一下日股。"]
    completed_answers: list[str] = []
    while len(completed_answers) < 2:
        event = await asyncio.wait_for(manager.next_event(), timeout=1)
        event_types.append(event.type)
        if event.type == "question.detected":
            detected_questions.append(str(event.payload["question"]))
        if event.type == "answer.completed":
            completed_answers.append(str(event.payload["answer_text"]))

    assert first_started.payload["question_version"] == 1
    assert detected_questions == ["分析一下日股。", "评价一下这个方案。"]
    assert completed_answers == ["日股回答", "方案回答"]
    assert "answer.cancelled" not in event_types
    assert len(prompts) == 2
    await manager.close("test")


def test_session_manager_continues_queue_after_answer_failure() -> None:
    asyncio.run(_assert_session_manager_continues_queue_after_answer_failure())


async def _assert_session_manager_continues_queue_after_answer_failure() -> None:
    interviewer = ScriptedAsrSession()
    first_release = asyncio.Event()

    async def answer(prompt: str):
        if "第一题" in prompt:
            await first_release.wait()
            raise RuntimeError("模型失败")
        yield "第二题回答"

    manager = LiveSessionManager(
        asr_sessions={AudioChannel.INTERVIEWER: interviewer},
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    await _wait_for_type(manager, "session.ready")
    await interviewer.emit(TranscriptEvent(AudioChannel.INTERVIEWER, 1, "第一题。", True))
    await _wait_for_type(manager, "answer.started")
    await interviewer.emit(TranscriptEvent(AudioChannel.INTERVIEWER, 2, "第二题。", True))
    await _wait_for_type(manager, "transcript.final")
    first_release.set()

    saw_failure = False
    completed = None
    while completed is None:
        event = await asyncio.wait_for(manager.next_event(), timeout=1)
        if event.type == "error" and event.payload.get("code") == "answer_failed":
            saw_failure = True
        if event.type == "answer.completed":
            completed = event

    assert saw_failure
    assert completed.payload["answer_text"] == "第二题回答"
    await manager.close("test")


def test_session_manager_snapshots_four_prior_utterances_for_follow_up() -> None:
    asyncio.run(_assert_session_manager_snapshots_four_prior_utterances_for_follow_up())


async def _assert_session_manager_snapshots_four_prior_utterances_for_follow_up() -> None:
    interviewer = ScriptedAsrSession()
    candidate = ScriptedAsrSession()
    prompts: list[str] = []

    async def answer(prompt: str):
        prompts.append(prompt)
        yield "回答"

    manager = LiveSessionManager(
        asr_sessions={
            AudioChannel.INTERVIEWER: interviewer,
            AudioChannel.CANDIDATE: candidate,
        },
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    await _wait_for_type(manager, "session.ready")

    prior_events = (
        TranscriptEvent(AudioChannel.INTERVIEWER, 1, "请解释 CAP。", True),
        TranscriptEvent(AudioChannel.CANDIDATE, 1, "CAP 需要在一致性和可用性之间取舍。", True),
        TranscriptEvent(AudioChannel.INTERVIEWER, 2, "重点讲讲 AP。", True),
        TranscriptEvent(AudioChannel.CANDIDATE, 2, "AP 优先保证可用性。", True),
    )
    await interviewer.emit(prior_events[0])
    await _wait_for_type(manager, "answer.completed")
    await candidate.emit(prior_events[1])
    await _wait_for_type(manager, "transcript.final")
    await interviewer.emit(prior_events[2])
    await _wait_for_type(manager, "answer.completed")
    await candidate.emit(prior_events[3])
    await _wait_for_type(manager, "transcript.final")

    current = "那如果分区恢复，它怎么收敛？"
    await interviewer.emit(TranscriptEvent(AudioChannel.INTERVIEWER, 3, current, True))
    await _wait_for_type(manager, "answer.completed")
    follow_up_prompt = prompts[-1]

    assert all(f"{event.role.value}: {event.text}" in follow_up_prompt for event in prior_events)
    assert follow_up_prompt.count(current) == 1
    await manager.close("test")


def test_manual_regenerate_increments_attempt_and_close_is_idempotent() -> None:
    asyncio.run(_assert_manual_regenerate_increments_attempt_and_close_is_idempotent())


async def _assert_manual_regenerate_increments_attempt_and_close_is_idempotent() -> None:
    interviewer = ScriptedAsrSession(
        TranscriptEvent(AudioChannel.INTERVIEWER, 1, "背景说明。", True)
    )
    candidate = ScriptedAsrSession(
        TranscriptEvent(AudioChannel.CANDIDATE, 1, "收到。", True)
    )

    async def answer(prompt: str):
        yield "回答"

    manager = LiveSessionManager(
        asr_sessions={
            AudioChannel.INTERVIEWER: interviewer,
            AudioChannel.CANDIDATE: candidate,
        },
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    await manager.handle(AnswerRequestEvent(mode="manual", question="请介绍你自己"))
    first_started = await _wait_for_type(manager, "answer.started")
    await _wait_for_type(manager, "answer.completed")
    await manager.handle(AnswerRequestEvent(mode="regenerate"))
    second_started = await _wait_for_type(manager, "answer.started")

    assert first_started.payload["question_version"] == second_started.payload["question_version"]
    assert first_started.payload["attempt"] == 1
    assert second_started.payload["attempt"] == 2
    await manager.close("first")
    await manager.close("second")


async def _wait_for_type(manager: LiveSessionManager, expected: str):
    while True:
        event = await asyncio.wait_for(manager.next_event(), timeout=1)
        if event.type == expected:
            return event
