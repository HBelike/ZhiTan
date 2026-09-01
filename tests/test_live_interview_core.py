from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.career_assistant.live_interview.asr.dashscope_realtime import (
    DashScopeRealtimeAsrProvider,
    map_dashscope_event,
)
from src.career_assistant.live_interview.asr.openai_realtime import map_openai_event
from src.career_assistant.live_interview.contracts import (
    AnswerRequestEvent,
    AudioAppendEvent,
    AudioChannel,
    QuestionIntent,
    SpeakerRole,
    TranscriptEvent,
    parse_client_event,
)
from src.career_assistant.live_interview.question_detector import RuleBasedQuestionDetector
from src.career_assistant.live_interview.terminology import TerminologyCorrector, extract_terms
from src.career_assistant.live_interview.transcript_assembler import TranscriptAssembler


def test_audio_event_decodes_pcm_and_maps_roles() -> None:
    event = parse_client_event(
        {
            "type": "audio.append",
            "channel": "interviewer",
            "sequence": 3,
            "pcm_base64": base64.b64encode(b"\x01\x02").decode(),
        }
    )

    assert isinstance(event, AudioAppendEvent)
    assert event.pcm == b"\x01\x02"
    assert event.channel.role is SpeakerRole.INTERVIEWER
    assert AudioChannel.CANDIDATE.role is SpeakerRole.CANDIDATE


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "type": "audio.append",
                "channel": "interviewer",
                "sequence": -1,
                "pcm_base64": "AA==",
            },
            "sequence",
        ),
        (
            {
                "type": "audio.append",
                "channel": "candidate",
                "sequence": 0,
                "pcm_base64": "%%%",
            },
            "Base64",
        ),
    ],
)
def test_audio_event_rejects_invalid_payload(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_client_event(payload)


def test_manual_answer_request_contract() -> None:
    event = parse_client_event(
        {"type": "answer.request", "mode": "regenerate", "question": "请解释 CAP theorem"}
    )

    assert isinstance(event, AnswerRequestEvent)
    assert event.mode == "regenerate"
    assert event.question == "请解释 CAP theorem"


def test_assembler_keeps_channel_sequences_independent_and_drops_stale_partial() -> None:
    assembler = TranscriptAssembler()
    interviewer = TranscriptEvent(
        channel=AudioChannel.INTERVIEWER,
        sequence=4,
        text="Explain CAP theorem",
        is_final=True,
    )
    candidate = TranscriptEvent(
        channel=AudioChannel.CANDIDATE,
        sequence=1,
        text="我会从 consistency 开始",
        is_final=True,
    )

    assert assembler.accept(interviewer) == interviewer
    assert assembler.accept(candidate) == candidate
    assert (
        assembler.accept(
            TranscriptEvent(
                channel=AudioChannel.INTERVIEWER,
                sequence=3,
                text="Explain CAP",
                is_final=False,
            )
        )
        is None
    )


def test_terminology_correction_is_limited_to_confirmed_terms() -> None:
    terms = extract_terms("使用 PostgreSQL、Kafka 和 IEC 62304 交付医疗设备软件")
    result = TerminologyCorrector(terms).correct("使用 Postgres SQL 和 kafka 进行开发")

    assert "PostgreSQL" in result.corrected_text
    assert "Kafka" in result.corrected_text
    assert "IEC 62304" not in result.corrected_text
    assert result.raw_text == "使用 Postgres SQL 和 kafka 进行开发"


def test_detector_only_uses_interviewer_final_and_supports_mixed_language() -> None:
    detector = RuleBasedQuestionDetector()
    candidate = TranscriptEvent(
        channel=AudioChannel.CANDIDATE,
        sequence=1,
        text="I used Kafka 处理事件",
        is_final=True,
    )
    partial = TranscriptEvent(
        channel=AudioChannel.INTERVIEWER,
        sequence=2,
        text="Explain CAP",
        is_final=False,
    )
    question = TranscriptEvent(
        channel=AudioChannel.INTERVIEWER,
        sequence=3,
        text="请结合你的项目解释 CAP theorem？",
        is_final=True,
    )

    assert detector.detect(candidate) is None
    assert detector.detect(partial) is None
    detected = detector.detect(question)
    assert detected is not None
    assert detected.intent is QuestionIntent.PROJECT_DEEP_DIVE
    assert detected.normalized_question == "请结合你的项目解释 CAP theorem？"


def test_detector_marks_follow_up() -> None:
    detected = RuleBasedQuestionDetector().detect(
        TranscriptEvent(
            channel=AudioChannel.INTERVIEWER,
            sequence=9,
            text="那如果流量再增加十倍呢？",
            is_final=True,
        ),
        previous_question="你如何处理高并发？",
    )

    assert detected is not None
    assert detected.intent is QuestionIntent.FOLLOW_UP
    assert detected.is_follow_up


@pytest.mark.parametrize(
    "text",
    (
        "分析一下日股。",
        "评价一下这个方案。",
        "展开。",
        "Redis 为什么这么快",
    ),
)
def test_detector_answers_every_non_filler_interviewer_final(text: str) -> None:
    detected = RuleBasedQuestionDetector().detect(
        TranscriptEvent(
            channel=AudioChannel.INTERVIEWER,
            sequence=1,
            text=text,
            is_final=True,
        )
    )

    assert detected is not None
    assert detected.confidence == 1.0


@pytest.mark.parametrize("text", ("嗯。", "哦", "好的。", "OK!"))
def test_detector_ignores_exact_filler_utterances(text: str) -> None:
    detected = RuleBasedQuestionDetector().detect(
        TranscriptEvent(
            channel=AudioChannel.INTERVIEWER,
            sequence=1,
            text=text,
            is_final=True,
        )
    )

    assert detected is None


def test_detector_does_not_treat_filler_prefix_as_whole_utterance() -> None:
    detected = RuleBasedQuestionDetector().detect(
        TranscriptEvent(
            channel=AudioChannel.INTERVIEWER,
            sequence=1,
            text="好的，请继续说明缓存一致性。",
            is_final=True,
        )
    )

    assert detected is not None


def test_detector_only_deduplicates_inside_three_second_window() -> None:
    timestamps = iter((10.0, 11.0, 14.1))
    detector = RuleBasedQuestionDetector(clock=lambda: next(timestamps))

    def event(sequence: int) -> TranscriptEvent:
        return TranscriptEvent(
            channel=AudioChannel.INTERVIEWER,
            sequence=sequence,
            text="解释 CAP。",
            is_final=True,
        )

    assert detector.detect(event(1)) is not None
    assert detector.detect(event(2)) is None
    assert detector.detect(event(3)) is not None


def test_openai_transcription_messages_map_to_domain_events() -> None:
    partial = map_openai_event(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "delta": "Explain CAP",
        },
        AudioChannel.INTERVIEWER,
        7,
    )
    final = map_openai_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "Explain CAP theorem",
        },
        AudioChannel.INTERVIEWER,
        7,
    )

    assert partial is not None and not partial.is_final
    assert final is not None and final.is_final
    assert final.text == "Explain CAP theorem"


def test_dashscope_transcription_messages_map_to_domain_events() -> None:
    partial = map_dashscope_event(
        {
            "header": {"event": "result-generated"},
            "payload": {
                "output": {
                    "sentence": {"text": "解释 CAP", "sentence_end": False}
                }
            },
        },
        AudioChannel.INTERVIEWER,
        12,
    )
    final = map_dashscope_event(
        {
            "header": {"event": "result-generated"},
            "payload": {
                "output": {
                    "sentence": {"text": "解释 CAP theorem？", "sentence_end": True}
                }
            },
        },
        AudioChannel.INTERVIEWER,
        13,
    )

    assert partial is not None and not partial.is_final
    assert final is not None and final.is_final
    assert final.provider == "dashscope"
    assert final.text == "解释 CAP theorem？"


class _FakeDashScopeSocket:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, payload: str | bytes) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        return json.dumps({"header": {"event": "task-started"}, "payload": {}})

    async def close(self) -> None:
        self.closed = True


def test_dashscope_provider_waits_for_task_start_and_streams_pcm() -> None:
    async def scenario() -> None:
        socket = _FakeDashScopeSocket()
        provider = DashScopeRealtimeAsrProvider("dashscope-test-key")

        with patch(
            "websockets.asyncio.client.connect",
            new=AsyncMock(return_value=socket),
        ) as connect:
            session = await provider.start(
                AudioChannel.INTERVIEWER,
                prompt="LangGraph、Kafka",
            )

        connect.assert_awaited_once()
        assert connect.await_args.kwargs["additional_headers"]["Authorization"] == (
            "Bearer dashscope-test-key"
        )
        run_task = json.loads(str(socket.sent[0]))
        assert run_task["header"]["action"] == "run-task"
        assert run_task["payload"]["model"] == "qwen-audio-3.0-asr-flash-streaming"
        assert run_task["payload"]["parameters"]["sample_rate"] == 24_000
        assert run_task["payload"]["parameters"]["language_hints"] == ["zh", "en"]
        assert run_task["payload"]["input"]["context"][0]["content"][0]["text"] == (
            "LangGraph、Kafka"
        )

        await session.append_audio(b"\x01\x02", 1)
        assert socket.sent[-1] == b"\x01\x02"
        await session.close()
        assert socket.closed

    asyncio.run(scenario())
