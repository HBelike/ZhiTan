"""可替换的实时 ASR Provider 协议。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


class AsrSession(Protocol):
    async def append_audio(self, pcm: bytes, sequence: int) -> None: ...

    async def commit(self) -> None: ...

    def events(self) -> AsyncIterator[TranscriptEvent]: ...

    async def close(self) -> None: ...


class AsrProvider(Protocol):
    sample_rate: int

    async def start(
        self,
        channel: AudioChannel,
        *,
        language_hint: str | None = None,
        prompt: str = "",
    ) -> AsrSession: ...
