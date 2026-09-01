"""模型上下文策略测试。"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.persistence.model_profile_repository import (
    CareerModelProfileRepository,
    ModelContextPolicy,
    ModelCostTier,
    ModelProfileDraft,
    infer_model_context_policy,
)


def test_deepseek_v4_uses_verified_one_mebi_token_context() -> None:
    policy = infer_model_context_policy("deepseek", "deepseek-v4-flash")

    assert policy.context_window_tokens == 1_048_576
    assert policy.reserved_output_tokens == 384_000
    assert policy.context_window_source == "built_in"


def test_unknown_model_defaults_to_one_million_context_tokens() -> None:
    policy = infer_model_context_policy("custom-provider", "future-model")

    assert policy.context_window_tokens == 1_000_000
    assert policy.reserved_output_tokens == 4_096
    assert policy.context_window_source == "fallback"


def test_profile_upsert_writes_inferred_context_policy() -> None:
    now = datetime.now(UTC)
    organization_id = uuid4()
    profile_id = uuid4()
    connection = MagicMock()
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value = connection
    connection.execute.return_value.mappings.return_value.one.return_value = {
        "id": profile_id,
        "organization_id": organization_id,
        "profile_key": "deepseek-v4-flash",
        "display_name": "DeepSeek V4 Flash",
        "provider_key": "deepseek",
        "model_id": "deepseek-v4-flash",
        "api_base_url": "https://api.deepseek.com",
        "provider_website_url": "https://platform.deepseek.com",
        "capability_codes": ["text"],
        "cost_tier": "paid",
        "enabled": True,
        "priority": 100,
        "created_at": now,
        "updated_at": now,
        "context_window_tokens": 1_048_576,
        "reserved_output_tokens": 4_096,
        "compression_trigger_percent": 80,
        "compression_target_percent": 60,
        "context_window_source": "built_in",
    }
    repository = CareerModelProfileRepository(database)

    repository.upsert_profile(
        organization_id,
        ModelProfileDraft(
            profile_key="deepseek-v4-flash",
            display_name="DeepSeek V4 Flash",
            provider_key="deepseek",
            model_id="deepseek-v4-flash",
            capabilities=frozenset({ModelCapability.TEXT}),
            cost_tier=ModelCostTier.PAID,
            api_base_url="https://api.deepseek.com",
            provider_website_url="https://platform.deepseek.com",
        ),
    )

    statement, parameters = connection.execute.call_args.args
    assert parameters["context_window_tokens"] == 1_048_576
    assert parameters["context_window_source"] == "built_in"
    assert "current_profile.context_window_source = 'admin'" in str(statement)


def test_policy_rejects_target_not_below_trigger() -> None:
    policy = ModelContextPolicy(
        context_window_tokens=64_000,
        reserved_output_tokens=4_096,
        compression_trigger_percent=80,
        compression_target_percent=80,
        context_window_source="admin",
    )

    with pytest.raises(ValueError, match="目标比例"):
        policy.validate()


def test_policy_allows_model_output_above_half_context() -> None:
    ModelContextPolicy(
        context_window_tokens=128_000,
        reserved_output_tokens=96_000,
    ).validate()


def test_policy_rejects_model_output_above_context() -> None:
    with pytest.raises(ValueError, match="模型最大输出"):
        ModelContextPolicy(
            context_window_tokens=128_000,
            reserved_output_tokens=128_001,
        ).validate()


def test_record_reads_context_policy_with_backward_compatible_defaults() -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "profile_key": "test-model",
        "display_name": "测试模型",
        "provider_key": "openai-compatible",
        "model_id": "test-model",
        "api_base_url": None,
        "provider_website_url": None,
        "capability_codes": [ModelCapability.TEXT.value],
        "cost_tier": "paid",
        "enabled": True,
        "priority": 100,
        "created_at": now,
        "updated_at": now,
    }

    record = CareerModelProfileRepository._to_record(row)

    assert record.context_policy == ModelContextPolicy()
    assert record.context_policy.context_window_tokens == 1_000_000


def test_admin_context_policy_is_read_from_row() -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "profile_key": "large-model",
        "display_name": "大上下文模型",
        "provider_key": "openai-compatible",
        "model_id": "large-model",
        "api_base_url": None,
        "provider_website_url": None,
        "capability_codes": [ModelCapability.TEXT.value],
        "cost_tier": "paid",
        "enabled": True,
        "priority": 100,
        "created_at": now,
        "updated_at": now,
        "context_window_tokens": 128_000,
        "reserved_output_tokens": 8_192,
        "compression_trigger_percent": 80,
        "compression_target_percent": 60,
        "context_window_source": "admin",
    }

    record = CareerModelProfileRepository._to_record(row)

    assert record.context_policy.context_window_tokens == 128_000
    assert record.context_policy.context_window_source == "admin"
