"""验证 OpenAI-compatible 模型调用器的请求结构，不发起外网调用。"""

from __future__ import annotations

import os
import sys
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.model_clients import (
    ChatMessage,
    ModelConnectionTarget,
    ModelInvocationError,
    OpenAICompatibleChatClient,
)
from src.career_assistant.persistence.model_profile_repository import (
    ModelCostTier,
    ModelProfileRecord,
)


def main() -> None:
    """确认文本与图片数据 URI 会按 ChatCompletions 规范交给已选 Provider。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://mock-provider.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-free-token"
        payload = json.loads(request.content)
        assert payload["model"] == "mock-vision-model"
        assert payload["max_tokens"] == 4_096
        assert payload["messages"][1]["content"][1]["type"] == "image_url"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "已完成匹配分析。"}}]},
        )

    now = datetime.now(UTC)
    profile = ModelProfileRecord(
        id=uuid4(),
        organization_id=uuid4(),
        profile_key="mock-vision",
        display_name="Mock Vision",
        provider_key="openrouter",
        model_id="mock-vision-model",
        capabilities=frozenset({ModelCapability.TEXT, ModelCapability.VISION}),
        cost_tier=ModelCostTier.FREE_QUOTA,
        priority=1,
        enabled=True,
        api_base_url="https://mock-provider.example/v1",
        created_at=now,
        updated_at=now,
    )
    os.environ["CAREER_MODEL_CLIENT_TEST_KEY"] = "test-free-token"
    try:
        client = OpenAICompatibleChatClient(httpx.Client(transport=httpx.MockTransport(handler)))
        result = client.complete(
            profile,
            "CAREER_MODEL_CLIENT_TEST_KEY",
            [
                ChatMessage(role="system", content="系统指令"),
                ChatMessage(
                    role="user",
                    content=[
                        {"type": "text", "text": "分析附件"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                ),
            ],
        )
        assert result == "已完成匹配分析。"

        def deepseek_handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://api.deepseek.com/chat/completions"
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

        deepseek_client = OpenAICompatibleChatClient(
            httpx.Client(transport=httpx.MockTransport(deepseek_handler)),
        )
        deepseek_reply = deepseek_client.test_connection(
            ModelConnectionTarget(
                provider_key="deepseek",
                model_id="deepseek-v4-pro",
                api_base_url="https://platform.deepseek.com",
            ),
            "test-free-token",
        )
        assert deepseek_reply == "OK"

        def mistral_handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://api.mistral.ai/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-free-token"
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

        mistral_client = OpenAICompatibleChatClient(
            httpx.Client(transport=httpx.MockTransport(mistral_handler)),
        )
        mistral_reply = mistral_client.test_connection(
            ModelConnectionTarget(
                provider_key="mistral",
                model_id="mistral-small-latest",
                api_base_url=None,
            ),
            "test-free-token",
        )
        assert mistral_reply == "OK"

        def stream_handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://mock-provider.example/v1/chat/completions"
            payload = json.loads(request.content)
            assert payload["stream"] is True
            assert payload["max_tokens"] == 4_096
            return httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"content":"\xe6\xb5\x81\xe5\xbc\x8f"}}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":"\xe5\x9b\x9e\xe5\xa4\x8d"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )

        stream_client = OpenAICompatibleChatClient(
            httpx.Client(transport=httpx.MockTransport(stream_handler)),
        )
        stream_reply = "".join(
            stream_client.stream_complete(
                profile,
                None,
                [ChatMessage(role="user", content="请流式回答")],
                api_key="test-free-token",
            ),
        )
        assert stream_reply == "流式回复"

        rejected_client = OpenAICompatibleChatClient(
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(401, json={"error": "invalid key"}),
                ),
            ),
        )
        try:
            rejected_client.test_connection(
                ModelConnectionTarget(
                    provider_key="openrouter",
                    model_id="mock-model",
                    api_base_url="https://mock-provider.example/v1",
                ),
                "test-free-token",
            )
        except ModelInvocationError as exc:
            assert "API Key 无效" in str(exc)
        else:
            raise AssertionError("401 响应应转成可操作的 API Key 提示")
        print("career_model_client_mock_transport_ok")
    finally:
        os.environ.pop("CAREER_MODEL_CLIENT_TEST_KEY", None)


if __name__ == "__main__":
    main()
