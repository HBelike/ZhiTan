from src.career_assistant.model_clients import (
    ChatMessage,
    CompletionRequestOptions,
    ModelConnectionTarget,
    OpenAICompatibleChatClient,
)


def test_completion_options_override_only_one_request() -> None:
    payload = OpenAICompatibleChatClient._build_request_payload(
        ModelConnectionTarget(
            provider_key="deepseek",
            model_id="deepseek-v4-pro",
            api_base_url="https://api.deepseek.com",
        ),
        [ChatMessage(role="user", content="请生成招呼语")],
        max_tokens=12_288,
        response_format={"type": "json_object"},
        options=CompletionRequestOptions(
            temperature=0.2,
            max_tokens=800,
            thinking=False,
        ),
    )

    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 800
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}


def test_completion_options_keep_existing_defaults() -> None:
    payload = OpenAICompatibleChatClient._build_request_payload(
        ModelConnectionTarget(
            provider_key="openai",
            model_id="gpt-test",
            api_base_url="https://example.test",
        ),
        [ChatMessage(role="user", content="hello")],
        max_tokens=12_288,
    )

    assert payload["temperature"] == 0.25
    assert payload["max_tokens"] == 12_288
    assert "thinking" not in payload


def test_completion_options_can_enable_thinking_for_deepseek() -> None:
    payload = OpenAICompatibleChatClient._build_request_payload(
        ModelConnectionTarget(
            provider_key="deepseek",
            model_id="deepseek-v4-pro",
            api_base_url=None,
        ),
        [ChatMessage(role="user", content="分析")],
        max_tokens=800,
        options=CompletionRequestOptions(thinking=True),
    )

    assert payload["thinking"] == {"type": "enabled"}
