"""离线验证豆包语音 V3 API Key 请求协议，不会调用任何云端接口或消耗额度。"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.providers.doubao_tts_provider as doubao_tts_module
from src.config.config_manager import ConfigManager
from src.providers.doubao_tts_provider import DoubaoTtsProvider


class FakeV3Response:
    """模拟 V3 HTTP 单向流式接口返回的 NDJSON 响应。"""

    status_code = 200
    text = ""

    def __enter__(self) -> "FakeV3Response":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        return False

    def iter_lines(self, decode_unicode: bool = False) -> list[str]:
        del decode_unicode
        return [
            json.dumps({"data": base64.b64encode(b"audio-").decode("ascii"), "code": 0}),
            json.dumps(
                {
                    "data": base64.b64encode(b"contract").decode("ascii"),
                    "code": 0,
                    "usage": {"input_tokens": 2, "output_tokens": 3},
                }
            ),
        ]


def main() -> None:
    """验证 V3 请求格式与流式音频写入结果。"""
    config = ConfigManager(project_root=PROJECT_ROOT).load()
    provider = DoubaoTtsProvider(config=config)
    captured: dict[str, Any] = {}

    def fake_post(*args: Any, **kwargs: Any) -> FakeV3Response:
        captured["url"] = args[0] if args else kwargs.get("url")
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        captured["stream"] = kwargs.get("stream")
        return FakeV3Response()

    api_key_env = config.audio_api_key_env
    previous_api_key = os.environ.get(api_key_env)
    original_post = doubao_tts_module.requests.post
    os.environ[api_key_env] = "offline-contract-key"
    doubao_tts_module.requests.post = fake_post
    try:
        with tempfile.TemporaryDirectory(prefix="verify_doubao_tts_v3_") as temporary_directory:
            output_path = Path(temporary_directory) / f"voiceover.{config.audio_encoding}"
            result = provider.synthesize("这是新版豆包语音协议离线验证。", output_path)
            if output_path.read_bytes() != b"audio-contract":
                raise RuntimeError("V3 NDJSON 音频分片未按顺序写入")
            if result.raw_response.get("protocol") != "v3_http_chunked":
                raise RuntimeError("未记录 V3 HTTP 单向流式协议")
    finally:
        doubao_tts_module.requests.post = original_post
        if previous_api_key is None:
            os.environ.pop(api_key_env, None)
        else:
            os.environ[api_key_env] = previous_api_key

    headers = captured.get("headers", {})
    required_headers = {"X-Api-Key", "X-Api-Resource-Id", "X-Api-Request-Id"}
    if not required_headers.issubset(headers):
        raise RuntimeError("V3 请求缺少新版 API Key 鉴权头")
    if "Authorization" in headers or "X-Api-App-Id" in headers or "X-Api-Access-Key" in headers:
        raise RuntimeError("V3 请求仍包含旧版鉴权头")
    req_params = captured.get("json", {}).get("req_params", {})
    if not req_params.get("text") or not req_params.get("speaker"):
        raise RuntimeError("V3 请求体缺少文本或音色")
    if not captured.get("stream"):
        raise RuntimeError("V3 请求未启用流式读取")

    print(
        json.dumps(
            {
                "status": "ok",
                "protocol": "v3_http_chunked",
                "api_key_header": "present",
                "resource_id": headers["X-Api-Resource-Id"],
                "audio_bytes": len(b"audio-contract"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
