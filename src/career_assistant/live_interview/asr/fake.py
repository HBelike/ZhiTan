"""无需外部服务的可重复 ASR Provider，用于协议联调和自动验收。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


class FakeAsrSession:
    def __init__(self, channel: AudioChannel, script: tuple[TranscriptEvent, ...]) -> None:
        self.channel = channel
        self._queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        self._script = script
        self._closed = False

    async def append_audio(self, pcm: bytes, sequence: int) -> None:
        if self._closed:
            raise RuntimeError("ASR 会话已关闭")

    async def commit(self) -> None:
        if self._closed:
            return
        for event in self._script:
            if event.channel is self.channel:
                await self._queue.put(event)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._queue.put(None)


class FakeAsrProvider:
    sample_rate = 24_000

    def __init__(self, script: tuple[TranscriptEvent, ...] = ()) -> None:
        self._script = script

    async def start(
        self,
        channel: AudioChannel,
        *,
        language_hint: str | None = None,
        prompt: str = "",
    ) -> FakeAsrSession:
        return FakeAsrSession(channel, self._script)
