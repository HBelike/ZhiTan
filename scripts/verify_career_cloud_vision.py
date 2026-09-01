"""验证平台可配置图片理解适配器，不访问真实云端服务。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.cloud_vision import (  # noqa: E402
    CloudVisionError,
    CloudVisionRouter,
    OpenAICompatibleCloudVisionClient,
)
from src.career_assistant.settings import (  # noqa: E402
    CloudVisionConnectionSettings,
    CloudVisionSettings,
)


def _connection() -> CloudVisionConnectionSettings:
    """构造无真实凭证的单条图片理解连接。"""

    return CloudVisionConnectionSettings(
        connection_key="qwen-primary",
        enabled=True,
        provider_key="qwen",
        model_id="qwen3.6-flash",
        api_key_env="CAREER_TEST_VISION_KEY",
        api_base_url_env="CAREER_TEST_VISION_BASE_URL",
        request_timeout_seconds=5,
        max_completion_tokens=800,
    )


def main() -> None:
    """断言视觉服务可按服务端连接配置调用，而不依赖用户模型档案。"""

    os.environ["CAREER_TEST_VISION_KEY"] = "test-secret"
    os.environ["CAREER_TEST_VISION_BASE_URL"] = "https://workspace.example.com/v1"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://workspace.example.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.6-flash"
        content = payload["messages"][0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "图片类型：简历\n技能：Python、FastAPI",
                        },
                    },
                ],
            },
        )

    client = OpenAICompatibleCloudVisionClient(
        _connection(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = client.analyze_image("image/png", b"image-bytes")
        assert result.provider_key == "qwen"
        assert result.model_id == "qwen3.6-flash"
        assert "Python" in result.analysis_text
    finally:
        client.close()

    os.environ.pop("CAREER_TEST_VISION_BASE_URL", None)
    unavailable_client = OpenAICompatibleCloudVisionClient(
        _connection(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        try:
            unavailable_client.analyze_image("image/png", b"image-bytes")
        except CloudVisionError as exc:
            assert "API Host" in str(exc)
        else:
            raise AssertionError("缺失 API Host 时必须给出安全错误")
    finally:
        unavailable_client.close()
        os.environ.pop("CAREER_TEST_VISION_KEY", None)

    router = CloudVisionRouter(
        CloudVisionSettings(
            enabled=True,
            active_connection_key="qwen-primary",
            fallback_connection_keys=(),
            connections=(_connection(),),
        ),
    )
    try:
        assert router._connections[0].model_id == "qwen3.6-flash"
    finally:
        router.close()

    print("career_cloud_vision_ok")


if __name__ == "__main__":
    main()
