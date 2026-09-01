"""按音频通道组装实时转写，阻止乱序 partial 覆盖 final。"""

from __future__ import annotations

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


class TranscriptAssembler:
    def __init__(self) -> None:
        self._last_sequence = {channel: -1 for channel in AudioChannel}
        self._final_sequences = {channel: set() for channel in AudioChannel}

    def accept(self, event: TranscriptEvent) -> TranscriptEvent | None:
        last = self._last_sequence[event.channel]
        if event.sequence < last:
            return None
        if event.sequence in self._final_sequences[event.channel] and not event.is_final:
            return None
        if event.is_final:
            self._final_sequences[event.channel].add(event.sequence)
        self._last_sequence[event.channel] = max(last, event.sequence)
        return event
