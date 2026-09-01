"""Prompt 上下文预算与裁剪测试。"""

from dataclasses import dataclass

import pytest

from src.career_assistant.context_budget import (
    ContextBudgetService,
    ContextHardLimitError,
    ContextUsageState,
    ConservativeTokenEstimator,
    PromptComponent,
    estimate_text_tokens,
)
from src.career_assistant.model_clients import ChatMessage
from src.career_assistant.persistence import ModelContextPolicy


@dataclass
class FixedEstimator:
    tokens: int

    def estimate_message(self, message: ChatMessage) -> int:
        del message
        return self.tokens


@dataclass
class MappingEstimator:
    mapping: dict[str, int]

    def estimate_message(self, message: ChatMessage) -> int:
        return self.mapping[str(message.content)]


def policy(window: int = 10_000, output: int = 2_000) -> ModelContextPolicy:
    return ModelContextPolicy(
        context_window_tokens=window,
        reserved_output_tokens=output,
        compression_trigger_percent=80,
        compression_target_percent=60,
        context_window_source="admin",
    )


def test_measure_includes_provider_overhead_output_and_safety_factor() -> None:
    usage = ContextBudgetService(FixedEstimator(1_000)).measure(
        (PromptComponent.pinned("system", (ChatMessage("system", "规则"),)),),
        policy(),
    )

    assert usage.estimated_input_tokens == 1_178
    assert usage.projected_total_tokens == 3_178
    assert usage.used_percent == 32
    assert usage.remaining_percent == 68
    assert usage.approximate is True


@pytest.mark.parametrize(
    ("raw_tokens", "expected_state"),
    [
        (3_100, ContextUsageState.NORMAL),
        (3_500, ContextUsageState.WARNING),
        (5_000, ContextUsageState.WARNING),
        (5_200, ContextUsageState.COMPACTING),
        (6_200, ContextUsageState.COMPACTING),
        (6_500, ContextUsageState.HARD_BLOCKED),
    ],
)
def test_measure_resolves_boundary_states(
    raw_tokens: int,
    expected_state: ContextUsageState,
) -> None:
    usage = ContextBudgetService(FixedEstimator(raw_tokens)).measure(
        (PromptComponent.pinned("input", (ChatMessage("user", "问题"),)),),
        policy(),
    )

    assert usage.state is expected_state


def test_fit_drops_optional_components_in_rank_order() -> None:
    components = (
        PromptComponent.pinned("current_input", (ChatMessage("user", "问题"),)),
        PromptComponent.optional(
            "old_interview",
            (ChatMessage("system", "旧面经"),),
            drop_rank=0,
        ),
        PromptComponent.optional(
            "weak_advantage",
            (ChatMessage("system", "弱优势"),),
            drop_rank=30,
        ),
    )
    result = ContextBudgetService(
        MappingEstimator({"问题": 1_000, "旧面经": 3_000, "弱优势": 1_000}),
    ).fit(components, policy(), target_percent=60)

    assert result.dropped_component_keys == ("old_interview",)
    assert tuple(item.key for item in result.components) == (
        "current_input",
        "weak_advantage",
    )


def test_fit_never_drops_required_current_input() -> None:
    components = (
        PromptComponent.pinned("current_input", (ChatMessage("user", "问题"),)),
        PromptComponent.optional(
            "old_interview",
            (ChatMessage("system", "旧面经"),),
            drop_rank=0,
        ),
    )
    service = ContextBudgetService(MappingEstimator({"问题": 8_000, "旧面经": 1_000}))

    with pytest.raises(ContextHardLimitError) as error:
        service.fit(components, policy(), target_percent=60)

    assert error.value.usage.state is ContextUsageState.HARD_BLOCKED


def test_estimator_counts_chinese_english_json_and_image_payload() -> None:
    assert estimate_text_tokens("中文abcd") == 3
    message = ChatMessage(
        "user",
        [
            {"type": "text", "text": "请看图片"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abcd"}},
        ],
    )
    assert ConservativeTokenEstimator().estimate_message(message) > 20


def test_tool_schema_estimate_is_included() -> None:
    usage = ContextBudgetService(FixedEstimator(0)).measure(
        (PromptComponent.pinned("skills", (), extra_token_estimate=1_000),),
        policy(),
    )

    assert usage.estimated_input_tokens == 1_178


def test_runtime_policy_caps_output_by_safe_remaining_space() -> None:
    service = ContextBudgetService(FixedEstimator(70_000))
    components = (PromptComponent.pinned("input", (ChatMessage("user", "问题"),)),)
    source = ModelContextPolicy(
        context_window_tokens=100_000,
        reserved_output_tokens=40_000,
        context_window_source="admin",
    )

    runtime = service.runtime_policy(
        components,
        source,
        system_max_output_tokens=100_000,
    )

    assert runtime.reserved_output_tokens == 14_472
    assert source.reserved_output_tokens == 40_000


def test_runtime_policy_uses_model_limit_when_it_is_lower() -> None:
    service = ContextBudgetService(FixedEstimator(1_000))
    components = (PromptComponent.pinned("input", (ChatMessage("user", "问题"),)),)
    source = ModelContextPolicy(
        context_window_tokens=100_000,
        reserved_output_tokens=8_192,
        context_window_source="admin",
    )

    runtime = service.runtime_policy(
        components,
        source,
        system_max_output_tokens=100_000,
    )

    assert runtime.reserved_output_tokens == 8_192


def test_runtime_policy_uses_system_limit_when_it_is_lower() -> None:
    service = ContextBudgetService(FixedEstimator(1_000))
    components = (PromptComponent.pinned("input", (ChatMessage("user", "问题"),)),)
    source = ModelContextPolicy(
        context_window_tokens=1_000_000,
        reserved_output_tokens=384_000,
        context_window_source="admin",
    )

    runtime = service.runtime_policy(
        components,
        source,
        system_max_output_tokens=100_000,
    )

    assert runtime.reserved_output_tokens == 100_000
