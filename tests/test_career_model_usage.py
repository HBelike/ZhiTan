"""模型用量采集测试。"""

from src.career_assistant.model_clients import CompletionUsage, OpenAICompatibleChatClient


def test_completion_usage_reads_provider_tokens() -> None:
    usage = OpenAICompatibleChatClient._completion_usage(
        {"prompt_tokens": 123, "completion_tokens": 45},
    )
    assert usage == CompletionUsage(123, 45)


def test_completion_usage_keeps_missing_values_unknown() -> None:
    assert OpenAICompatibleChatClient._completion_usage(None) == CompletionUsage(None, None)
    assert OpenAICompatibleChatClient._completion_usage(
        {"prompt_tokens": "123", "completion_tokens": -1},
    ) == CompletionUsage(None, None)


def test_usage_repository_uses_operation_unique_key() -> None:
    from pathlib import Path

    source = Path("src/career_assistant/persistence/model_usage_repository.py").read_text(
        encoding="utf-8",
    )
    assert "ON CONFLICT (turn_id, operation_kind)" in source
    assert "WHERE id = :usage_id" in source
