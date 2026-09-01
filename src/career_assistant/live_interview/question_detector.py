"""面试官 final 话语的低延迟问题检测与追问分类。"""

from __future__ import annotations

import time
from collections.abc import Callable
import re

from src.career_assistant.live_interview.contracts import (
    DetectedQuestion,
    QuestionIntent,
    SpeakerRole,
    TranscriptEvent,
)


_FILLER_UTTERANCES = frozenset(
    {
        "嗯",
        "嗯嗯",
        "哦",
        "噢",
        "啊",
        "好的",
        "好",
        "行",
        "可以",
        "知道了",
        "明白了",
        "okay",
        "ok",
    }
)
_EDGE_PUNCTUATION = re.compile(r"^[\s，。！？!?、,.；;：:~～…]+|[\s，。！？!?、,.；;：:~～…]+$")
_DUPLICATE_WINDOW_SECONDS = 3.0
_FOLLOW_UP = ("那如果", "进一步", "继续", "刚才", "那么", "除此之外", "具体呢", "why exactly")


class RuleBasedQuestionDetector:
    """确定性首层检测器；接口可替换为带结构化 LLM 的复合检测器。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_text: str | None = None
        self._last_detected_at: float | None = None

    def detect(
        self,
        event: TranscriptEvent,
        previous_question: str | None = None,
    ) -> DetectedQuestion | None:
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
        is_follow_up = previous_question is not None and any(
            marker.casefold() in lowered for marker in _FOLLOW_UP
        )
        intent = QuestionIntent.FOLLOW_UP if is_follow_up else self._classify(lowered)
        return DetectedQuestion(
            normalized_question=text,
            intent=intent,
            confidence=1.0,
            is_follow_up=is_follow_up,
        )

    @staticmethod
    def _classify(text: str) -> QuestionIntent:
        if any(marker in text for marker in ("项目", "经历", "负责", "你做", "your project")):
            return QuestionIntent.PROJECT_DEEP_DIVE
        if any(marker in text for marker in ("系统设计", "架构", "design a", "design the")):
            return QuestionIntent.SYSTEM_DESIGN
        if any(marker in text for marker in ("算法", "代码", "复杂度", "algorithm", "coding")):
            return QuestionIntent.CODING_OR_ALGORITHM
        if any(marker in text for marker in ("如果", "场景", "case", "scenario")):
            return QuestionIntent.CASE_OR_SCENARIO
        if any(marker in text for marker in ("冲突", "失败", "挑战", "行为", "behavioral")):
            return QuestionIntent.BEHAVIORAL
        return QuestionIntent.KNOWLEDGE
