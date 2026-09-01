from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.config.config_manager import AppConfig
from src.observability.langsmith_runtime import trace_llm_call


class DoubaoTtsApiError(RuntimeError):
    """豆包语音 V3 API 调用失败。"""


@dataclass(frozen=True)
class DoubaoTtsResult:
    """豆包语音合成成功并落盘后的音频结果。"""

    output_path: Path
    reqid: str
    voice_type: str
    chunk_count: int
    raw_response: dict[str, Any]


class DoubaoTtsProvider:
    """封装豆包语音 V3 HTTP 单向流式合成接口。

    本项目的语音合成统一使用 ``X-Api-Key`` 鉴权，防止将方舟 ARK Key 误用于
    豆包语音 TTS 请求。
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def has_credentials(self) -> bool:
        """检查新版豆包语音 API Key 与音色配置是否齐全。"""
        return bool(self._read_api_key() and self._read_voice_type())

    def synthesize(self, text: str, output_path: Path) -> DoubaoTtsResult:
        """调用 V3 HTTP 单向流式接口，将旁白文本合成为音频文件。"""
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("豆包 TTS 文本不能为空")

        api_key = self._read_api_key()
        voice_type = self._read_voice_type()
        if not api_key:
            raise DoubaoTtsApiError(
                f"{self.config.audio_api_key_env} 未配置，无法调用新版豆包语音 TTS"
            )
        if not voice_type:
            raise DoubaoTtsApiError("豆包 TTS 音色未配置")

        segments = self._split_text_by_utf8_bytes(
            text=normalized_text,
            max_utf8_bytes=self.config.audio_max_input_utf8_bytes,
        )
        if len(segments) == 1:
            reqid, raw_response = self._synthesize_single(
                text=segments[0],
                output_path=output_path,
                api_key=api_key,
                voice_type=voice_type,
            )
            return DoubaoTtsResult(
                output_path=output_path,
                reqid=reqid,
                voice_type=voice_type,
                chunk_count=1,
                raw_response=raw_response,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="doubao_tts_chunks_") as temporary_directory:
            temporary_root = Path(temporary_directory)
            chunk_paths: list[Path] = []
            chunk_results: list[dict[str, Any]] = []
            for index, segment in enumerate(segments, start=1):
                chunk_path = temporary_root / f"{index:03d}.{self.config.audio_encoding}"
                reqid, raw_response = self._synthesize_single(
                    text=segment,
                    output_path=chunk_path,
                    api_key=api_key,
                    voice_type=voice_type,
                )
                chunk_paths.append(chunk_path)
                chunk_results.append(
                    {
                        "reqid": reqid,
                        "text_utf8_bytes": len(segment.encode("utf-8")),
                        "response": raw_response,
                    }
                )
            self._concat_audio_chunks(chunk_paths=chunk_paths, output_path=output_path)

        return DoubaoTtsResult(
            output_path=output_path,
            reqid=chunk_results[0]["reqid"],
            voice_type=voice_type,
            chunk_count=len(chunk_results),
            raw_response={"protocol": "v3_http_chunked", "chunk_count": len(chunk_results), "chunks": chunk_results},
        )

    def _synthesize_single(
        self,
        text: str,
        output_path: Path,
        api_key: str,
        voice_type: str,
    ) -> tuple[str, dict[str, Any]]:
        """执行一条 V3 单向流式合成请求，并保存 NDJSON 音频分片。"""
        return trace_llm_call(
            run_name="media.audio.doubao_tts.synthesize",
            provider="doubao-speech",
            model=voice_type,
            message_count=1,
            input_characters=len(text),
            execute=lambda: self._synthesize_single_without_trace(
                text=text,
                output_path=output_path,
                api_key=api_key,
                voice_type=voice_type,
            ),
            summarize=self._trace_summary,
        )

    def _synthesize_single_without_trace(
        self,
        *,
        text: str,
        output_path: Path,
        api_key: str,
        voice_type: str,
    ) -> tuple[str, dict[str, Any]]:
        """执行真实 TTS HTTP 流；音频拼接由调用方完成且不产生 LLM Trace。"""

        reqid = uuid.uuid4().hex
        payload = self._build_payload(text=text, voice_type=voice_type)
        headers = {
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": self.config.audio_resource_id,
            "X-Api-Request-Id": reqid,
            "X-Control-Require-Usage-Tokens-Return": "*",
            "Content-Type": "application/json",
        }
        try:
            with requests.post(
                self.config.audio_api_url,
                headers=headers,
                json=payload,
                timeout=self.config.audio_timeout_seconds,
                stream=True,
            ) as response:
                if response.status_code >= 400:
                    detail = response.text[:500].strip()
                    raise DoubaoTtsApiError(
                        f"豆包 TTS 返回 HTTP {response.status_code}" + (f"：{detail}" if detail else "")
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                raw_response = self._save_v3_streamed_audio(response=response, output_path=output_path)
        except DoubaoTtsApiError:
            raise
        except requests.RequestException as exc:
            raise DoubaoTtsApiError(f"豆包 TTS 请求失败：{exc}") from exc

        return reqid, raw_response

    @staticmethod
    def _trace_summary(result: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        """返回匿名音频规模与用量，不上传文本、reqid 或音频内容。"""

        _, raw_response = result
        usage = raw_response.get("usage")
        return {
            "response_chunk_count": raw_response.get("response_chunk_count", 0),
            "audio_bytes": raw_response.get("audio_bytes", 0),
            "usage": usage if isinstance(usage, dict) else {},
        }

    @staticmethod
    def _split_text_by_utf8_bytes(text: str, max_utf8_bytes: int) -> list[str]:
        """优先按句子切分文本，保证每段不超过配置的 UTF-8 字节上限。"""
        normalized = text.strip()
        if not normalized:
            return []
        if len(normalized.encode("utf-8")) <= max_utf8_bytes:
            return [normalized]

        sentence_endings = {"。", "！", "？", "；", "\n"}
        units: list[str] = []
        current = ""
        for character in normalized:
            current += character
            if character in sentence_endings:
                units.append(current)
                current = ""
        if current:
            units.append(current)

        chunks: list[str] = []
        buffer = ""
        for unit in units:
            if len(unit.encode("utf-8")) > max_utf8_bytes:
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                chunks.extend(DoubaoTtsProvider._split_oversized_unit(unit, max_utf8_bytes))
                continue
            if buffer and len((buffer + unit).encode("utf-8")) > max_utf8_bytes:
                chunks.append(buffer)
                buffer = unit
            else:
                buffer += unit
        if buffer:
            chunks.append(buffer)
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    @staticmethod
    def _split_oversized_unit(text: str, max_utf8_bytes: int) -> list[str]:
        """处理没有句末标点的超长文本，按字符边界安全切分。"""
        chunks: list[str] = []
        buffer = ""
        for character in text:
            if buffer and len((buffer + character).encode("utf-8")) > max_utf8_bytes:
                chunks.append(buffer)
                buffer = character
            else:
                buffer += character
        if buffer:
            chunks.append(buffer)
        return chunks

    def _concat_audio_chunks(self, chunk_paths: list[Path], output_path: Path) -> None:
        """使用 ffmpeg 无损拼接分段音频，避免直接拼接字节导致编码损坏。"""
        if not chunk_paths:
            raise DoubaoTtsApiError("豆包 TTS 没有可拼接的音频分段")
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise DoubaoTtsApiError("缺少 imageio-ffmpeg，无法拼接豆包 TTS 音频分段") from exc

        file_list_path = chunk_paths[0].parent / "concat.txt"
        entries = []
        for chunk_path in chunk_paths:
            normalized_path = str(chunk_path.resolve()).replace("\\", "/").replace("'", "'\\''")
            entries.append(f"file '{normalized_path}'")
        file_list_path.write_text("\n".join(entries), encoding="utf-8")

        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(file_list_path),
            "-c",
            "copy",
            "-y",
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.audio_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DoubaoTtsApiError("豆包 TTS 分段音频拼接超时") from exc
        if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            detail = (completed.stderr or completed.stdout or "ffmpeg 未输出错误信息").strip()[-800:]
            raise DoubaoTtsApiError(f"豆包 TTS 分段音频拼接失败：{detail}")

    def _build_payload(self, text: str, voice_type: str) -> dict[str, Any]:
        """构造新版 V3 HTTP 单向流式合成请求体。"""
        speech_rate = round((self.config.audio_speed_ratio - 1.0) * 100)
        return {
            "user": {"uid": "zhitan"},
            "req_params": {
                "text": text,
                "speaker": voice_type,
                "audio_params": {
                    "format": self.config.audio_encoding,
                    "sample_rate": self.config.audio_rate,
                    "speech_rate": speech_rate,
                },
            },
        }

    def _save_v3_streamed_audio(self, response: requests.Response, output_path: Path) -> dict[str, Any]:
        """读取 V3 NDJSON 响应，拼接每行 ``data`` 中的 Base64 音频分片。"""
        audio = bytearray()
        response_chunks = 0
        last_code: Any = None
        last_message = ""
        usage: dict[str, Any] | None = None

        for raw_line in response.iter_lines(decode_unicode=True):
            line = self._normalize_stream_line(raw_line)
            if not line or line == "[DONE]" or line.startswith("event:") or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
                if not line or line == "[DONE]":
                    continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DoubaoTtsApiError("豆包 TTS V3 返回了无法解析的流式数据") from exc
            if not isinstance(chunk, dict):
                continue

            response_chunks += 1
            if chunk.get("code") is not None:
                last_code = chunk.get("code")
            if isinstance(chunk.get("message"), str):
                last_message = str(chunk["message"])
            if isinstance(chunk.get("usage"), dict):
                usage = dict(chunk["usage"])

            audio_base64 = chunk.get("data")
            if not isinstance(audio_base64, str) or not audio_base64.strip():
                continue
            try:
                audio.extend(base64.b64decode(audio_base64, validate=True))
            except (ValueError, TypeError) as exc:
                raise DoubaoTtsApiError("豆包 TTS V3 返回的音频分片不是合法 Base64") from exc

        if not audio:
            reason = last_message or (f"code={last_code}" if last_code is not None else "空响应")
            raise DoubaoTtsApiError(f"豆包 TTS V3 未返回可用音频：{reason}")

        output_path.write_bytes(bytes(audio))
        return {
            "protocol": "v3_http_chunked",
            "response_chunk_count": response_chunks,
            "audio_bytes": len(audio),
            "last_code": last_code,
            "usage": usage,
        }

    @staticmethod
    def _normalize_stream_line(raw_line: str | bytes) -> str:
        """规范化 requests 返回的文本或字节流行。"""
        if isinstance(raw_line, bytes):
            return raw_line.decode("utf-8", errors="replace").strip()
        return raw_line.strip()

    def _read_api_key(self) -> str:
        """读取豆包语音控制台 API Key，不写入日志或任务元数据。"""
        return os.getenv(self.config.audio_api_key_env, "").strip()

    def _read_voice_type(self) -> str:
        """读取音色 ID；未配置时使用 config/app.yaml 的默认音色。"""
        return os.getenv(self.config.audio_voice_type_env, "").strip() or self.config.audio_default_voice_type
