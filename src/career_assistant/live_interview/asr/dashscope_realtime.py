"""阿里云百炼 Qwen-Audio 实时语音识别 WebSocket 适配器。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


DEFAULT_DASHSCOPE_ASR_MODEL = "qwen-audio-3.0-asr-flash-streaming"
DEFAULT_DASHSCOPE_ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def map_dashscope_event(
    payload: dict[str, object],
    channel: AudioChannel,
    sequence: int,
) -> TranscriptEvent | None:
    """把 DashScope result-generated 事件转换为稳定领域事件。"""

    header = payload.get("header")
    if not isinstance(header, dict) or header.get("event") != "result-generated":
        return None
    event_payload = payload.get("payload")
    if not isinstance(event_payload, dict):
        return None
    output = event_payload.get("output")
    if not isinstance(output, dict):
        return None
    sentence = output.get("sentence")
    if not isinstance(sentence, dict):
        return None
    text = str(sentence.get("text", "")).strip()
    if not text:
        return None
    return TranscriptEvent(
        channel=channel,
        sequence=sequence,
        text=text,
        is_final=bool(sentence.get("sentence_end", False)),
        provider="dashscope",
    )


class DashScopeRealtimeAsrSession:
    def __init__(self, websocket: object, channel: AudioChannel, task_id: str) -> None:
        self._websocket = websocket
        self._channel = channel
        self._task_id = task_id
        self._sequence = 0
        self._closed = False

    async def append_audio(self, pcm: bytes, sequence: int) -> None:
        if self._closed:
            raise RuntimeError("DashScope ASR 会话已关闭")
        self._sequence = sequence
        await self._websocket.send(pcm)

    async def commit(self) -> None:
        # Qwen-Audio Streaming 使用服务端 VAD 自动输出 sentence_end，无需逐句提交。
        return None

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        async for message in self._websocket:
            if not isinstance(message, str):
                continue
            payload = json.loads(message)
            if not isinstance(payload, dict):
                continue
            header = payload.get("header")
            event_type = header.get("event") if isinstance(header, dict) else None
            if event_type == "task-failed":
                raise RuntimeError("DashScope 实时转写任务失败")
            if event_type == "task-finished":
                return
            event = map_dashscope_event(payload, self._channel, self._sequence)
            if event is not None:
                yield event

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(Exception):
            await self._websocket.send(
                json.dumps(
                    {
                        "header": {
                            "action": "finish-task",
                            "task_id": self._task_id,
                            "streaming": "duplex",
                        },
                        "payload": {"input": {}},
                    }
                )
            )
        await self._websocket.close()


class DashScopeRealtimeAsrProvider:
    sample_rate = 24_000

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_DASHSCOPE_ASR_MODEL,
        base_url: str = DEFAULT_DASHSCOPE_ASR_URL,
        workspace_id: str = "",
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip() or DEFAULT_DASHSCOPE_ASR_MODEL
        self._base_url = base_url.strip() or DEFAULT_DASHSCOPE_ASR_URL
        self._workspace_id = workspace_id.strip()
        if not self._api_key:
            raise ValueError("DashScope ASR API Key 不能为空")

    async def start(
        self,
        channel: AudioChannel,
        *,
        language_hint: str | None = None,
        prompt: str = "",
    ) -> DashScopeRealtimeAsrSession:
        from websockets.asyncio.client import connect

        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._workspace_id:
            headers["X-DashScope-WorkSpace"] = self._workspace_id
        websocket = await connect(
            self._base_url,
            additional_headers=headers,
            max_size=2**20,
            ping_interval=20,
            ping_timeout=20,
        )
        task_id = str(uuid4())
        parameters: dict[str, object] = {
            "format": "pcm",
            "sample_rate": self.sample_rate,
            "heartbeat": True,
            "max_sentence_silence": 800,
        }
        parameters["language_hints"] = (
            [language_hint] if language_hint in {"zh", "en"} else ["zh", "en"]
        )
        input_payload: dict[str, object] = {}
        if prompt.strip():
            input_payload["context"] = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt.strip()[:2_000]}],
                }
            ]
        await websocket.send(
            json.dumps(
                {
                    "header": {
                        "action": "run-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {
                        "task_group": "audio",
                        "task": "asr",
                        "function": "recognition",
                        "model": self._model,
                        "parameters": parameters,
                        "input": input_payload,
                    },
                }
            )
        )
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await websocket.recv()
                    if not isinstance(message, str):
                        continue
                    payload = json.loads(message)
                    header = payload.get("header") if isinstance(payload, dict) else None
                    event_type = header.get("event") if isinstance(header, dict) else None
                    if event_type == "task-started":
                        break
                    if event_type == "task-failed":
                        raise RuntimeError("DashScope 实时转写任务启动失败")
        except Exception:
            await websocket.close()
            raise
        return DashScopeRealtimeAsrSession(websocket, channel, task_id)
