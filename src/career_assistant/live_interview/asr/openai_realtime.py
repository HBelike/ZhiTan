"""OpenAI Realtime transcription 服务端 WebSocket 适配器。"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from urllib.parse import quote

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


def map_openai_event(
    payload: dict[str, object],
    channel: AudioChannel,
    sequence: int,
) -> TranscriptEvent | None:
    event_type = str(payload.get("type", ""))
    if event_type == "conversation.item.input_audio_transcription.delta":
        text = str(payload.get("delta", "")).strip()
        return TranscriptEvent(channel, sequence, text, False, provider="openai") if text else None
    if event_type == "conversation.item.input_audio_transcription.completed":
        text = str(payload.get("transcript", "")).strip()
        return TranscriptEvent(channel, sequence, text, True, provider="openai") if text else None
    return None


class OpenAIRealtimeAsrSession:
    def __init__(self, websocket: object, channel: AudioChannel) -> None:
        self._websocket = websocket
        self._channel = channel
        self._sequence = 0
        self._closed = False

    async def append_audio(self, pcm: bytes, sequence: int) -> None:
        self._sequence = sequence
        await self._websocket.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode("ascii"),
                }
            )
        )

    async def commit(self) -> None:
        await self._websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        async for message in self._websocket:
            payload = json.loads(message)
            event = map_openai_event(payload, self._channel, self._sequence)
            if event is not None:
                yield event

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._websocket.close()


class OpenAIRealtimeAsrProvider:
    sample_rate = 24_000

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-transcribe",
        base_url: str = "wss://api.openai.com/v1/realtime",
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        if not self._api_key:
            raise ValueError("OpenAI ASR API Key 不能为空")

    async def start(
        self,
        channel: AudioChannel,
        *,
        language_hint: str | None = None,
        prompt: str = "",
    ) -> OpenAIRealtimeAsrSession:
        from websockets.asyncio.client import connect

        websocket = await connect(
            f"{self._base_url}?model={quote(self._model)}",
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
            max_size=2**20,
        )
        transcription: dict[str, object] = {"model": self._model, "prompt": prompt[:2_000]}
        if language_hint in {"zh", "en"}:
            transcription["language"] = language_hint
        await websocket.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": self.sample_rate},
                                "transcription": transcription,
                                "turn_detection": {"type": "server_vad"},
                            }
                        },
                    },
                }
            )
        )
        await asyncio.sleep(0)
        return OpenAIRealtimeAsrSession(websocket, channel)
