"""实时面试助手的稳定领域合同和 WebSocket 协议解析。"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeAlias


MAX_AUDIO_FRAME_BYTES = 96_000


class SpeakerRole(StrEnum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


class AudioChannel(StrEnum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"

    @property
    def role(self) -> SpeakerRole:
        return SpeakerRole(self.value)


class LiveInterviewStatus(StrEnum):
    PREPARING = "preparing"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class AnswerStatus(StrEnum):
    GENERATING = "generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class QuestionIntent(StrEnum):
    BEHAVIORAL = "behavioral"
    PROJECT_DEEP_DIVE = "project_deep_dive"
    KNOWLEDGE = "knowledge"
    CASE_OR_SCENARIO = "case_or_scenario"
    SYSTEM_DESIGN = "system_design"
    CODING_OR_ALGORITHM = "coding_or_algorithm"
    FOLLOW_UP = "follow_up"
    STATEMENT = "statement"


@dataclass(frozen=True)
class TranscriptEvent:
    channel: AudioChannel
    sequence: int
    text: str
    is_final: bool
    provider: str = "unknown"
    confidence: float | None = None

    @property
    def role(self) -> SpeakerRole:
        return self.channel.role

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence 必须大于等于 0")
        if not self.text.strip():
            raise ValueError("转写文本不能为空")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")


@dataclass(frozen=True)
class CorrectionResult:
    raw_text: str
    corrected_text: str
    replacements: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DetectedQuestion:
    normalized_question: str
    intent: QuestionIntent
    confidence: float
    is_follow_up: bool = False


@dataclass(frozen=True)
class SessionStartEvent:
    type: str = "session.start"


@dataclass(frozen=True)
class AudioAppendEvent:
    channel: AudioChannel
    sequence: int
    pcm: bytes = field(repr=False)
    type: str = "audio.append"


@dataclass(frozen=True)
class AudioCommitEvent:
    channel: AudioChannel
    type: str = "audio.commit"


@dataclass(frozen=True)
class AnswerRequestEvent:
    mode: str
    question: str | None = None
    type: str = "answer.request"


@dataclass(frozen=True)
class AnswerCancelEvent:
    type: str = "answer.cancel"


@dataclass(frozen=True)
class SessionEndEvent:
    type: str = "session.end"


@dataclass(frozen=True)
class PingEvent:
    type: str = "ping"


ClientEvent: TypeAlias = (
    SessionStartEvent
    | AudioAppendEvent
    | AudioCommitEvent
    | AnswerRequestEvent
    | AnswerCancelEvent
    | SessionEndEvent
    | PingEvent
)


@dataclass(frozen=True)
class ServerEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


def parse_client_event(payload: dict[str, object]) -> ClientEvent:
    """解析并限制桌面端事件，避免任意字段进入会话状态机。"""

    event_type = str(payload.get("type", ""))
    if event_type == "session.start":
        return SessionStartEvent()
    if event_type == "audio.append":
        try:
            channel = AudioChannel(str(payload["channel"]))
            sequence = int(payload["sequence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("audio.append 的 channel 或 sequence 无效") from exc
        if sequence < 0:
            raise ValueError("sequence 必须大于等于 0")
        try:
            pcm = base64.b64decode(str(payload["pcm_base64"]), validate=True)
        except (KeyError, binascii.Error, ValueError) as exc:
            raise ValueError("pcm_base64 必须是有效 Base64") from exc
        if not pcm or len(pcm) > MAX_AUDIO_FRAME_BYTES:
            raise ValueError(f"PCM 帧大小必须在 1 到 {MAX_AUDIO_FRAME_BYTES} 字节之间")
        return AudioAppendEvent(channel=channel, sequence=sequence, pcm=pcm)
    if event_type == "audio.commit":
        try:
            return AudioCommitEvent(channel=AudioChannel(str(payload["channel"])))
        except (KeyError, ValueError) as exc:
            raise ValueError("audio.commit 的 channel 无效") from exc
    if event_type == "answer.request":
        mode = str(payload.get("mode", "manual"))
        if mode not in {"manual", "regenerate"}:
            raise ValueError("answer.request mode 只能是 manual 或 regenerate")
        question_value = payload.get("question")
        question = None if question_value is None else str(question_value).strip()
        if question is not None and not question:
            raise ValueError("手动问题不能为空")
        return AnswerRequestEvent(mode=mode, question=question)
    if event_type == "answer.cancel":
        return AnswerCancelEvent()
    if event_type == "session.end":
        return SessionEndEvent()
    if event_type == "ping":
        return PingEvent()
    raise ValueError(f"不支持的客户端事件：{event_type or '<empty>'}")
