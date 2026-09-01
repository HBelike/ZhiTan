"""回复链路的上下文和真实用量契约测试。"""

from pathlib import Path

from src.career_assistant.model_clients import CompletionUsage
from src.career_assistant.response_runner import ModelUsageAccumulator


def test_usage_accumulator_sums_tool_rounds() -> None:
    accumulator = ModelUsageAccumulator()
    accumulator.observe(CompletionUsage(100, 20))
    accumulator.observe(CompletionUsage(80, 30))
    assert accumulator.value() == CompletionUsage(180, 50)


def test_usage_accumulator_keeps_unknown_provider_usage_unknown() -> None:
    accumulator = ModelUsageAccumulator()
    accumulator.observe(CompletionUsage(100, 20))
    accumulator.observe(CompletionUsage(None, None))
    assert accumulator.value() == CompletionUsage(None, None)


def test_response_uses_prepared_runtime_output_for_all_answer_paths() -> None:
    source = Path("src/career_assistant/response_runner.py").read_text(encoding="utf-8")
    assert source.count("max_tokens=prepared.context_usage.reserved_output_tokens") == 2
    assert "max_tokens=resolution.profile.context_policy.reserved_output_tokens" not in source
    assert "TOOL_CALL_MAX_TOKENS" in source
    assert source.count("usage_callback=usage_accumulator.observe") >= 3
    assert '"context_hard_limit_reached"' in source


def test_turn_event_exposes_only_approximate_usage() -> None:
    source = Path("src/career_assistant/turn_worker.py").read_text(encoding="utf-8")
    assert '"remaining_percent"' in source
    assert '"approximate": True' in source
    assert '"context_window_tokens"' not in source
