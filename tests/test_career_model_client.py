"""OpenAI-compatible 模型客户端结束原因测试。"""

import httpx
import pytest

from src.career_assistant.model_clients import (
    ChatMessage,
    FunctionToolDefinition,
    ModelConnectionTarget,
    ModelInvocationError,
    OpenAICompatibleChatClient,
)


TARGET = ModelConnectionTarget(
    provider_key="custom",
    model_id="test-model",
    api_base_url="https://example.test/v1",
)


def test_non_stream_length_response_keeps_text_and_adds_notice() -> None:
    client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"content": "未完成正文"},
                            },
                        ],
                    },
                ),
            ),
        ),
    )

    result = client._request_completion(
        TARGET,
        "test-key",
        [ChatMessage("user", "请回答")],
        max_tokens=100,
    )

    assert result.startswith("未完成正文")
    assert "达到模型输出上限" in result


def test_stream_length_response_adds_notice_after_existing_chunks() -> None:
    client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=(
                        b'data: {"choices":[{"delta":{"content":"part"}}]}\n\n'
                        b'data: {"choices":[{"finish_reason":"length","delta":{}}]}\n\n'
                        b"data: [DONE]\n\n"
                    ),
                ),
            ),
        ),
    )

    result = "".join(
        client._stream_request_completion(
            TARGET,
            "test-key",
            [ChatMessage("user", "请回答")],
            max_tokens=100,
        ),
    )

    assert result.startswith("part")
    assert "达到模型输出上限" in result


def test_truncated_tool_call_is_rejected_before_execution() -> None:
    client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "function": {
                                                "name": "inspect",
                                                "arguments": '{"url":"unterminated',
                                            },
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                ),
            ),
        ),
    )

    with pytest.raises(ModelInvocationError, match="Tool Calling 达到输出上限"):
        client._request_tool_completion(
            TARGET,
            "test-key",
            [ChatMessage("user", "检查")],
            (FunctionToolDefinition("inspect", "检查", {"type": "object"}),),
            tool_choice="auto",
            max_tokens=100,
        )
