"""模型档案费用分类回归测试。"""

from datetime import UTC, datetime
from uuid import uuid4

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.persistence.model_profile_repository import (
    CareerModelProfileRepository,
    ModelCostTier,
    ModelProfileDraft,
    normalize_model_cost_tier,
)


def test_deepseek_is_always_normalized_to_paid() -> None:
    """DeepSeek 不能因旧表单默认值而进入免费自动路由。"""

    assert (
        normalize_model_cost_tier(" DeepSeek ", ModelCostTier.FREE_QUOTA)
        is ModelCostTier.PAID
    )


def test_other_provider_keeps_declared_cost_tier() -> None:
    """其他服务商仍由免费目录或管理员提交的费用属性决定。"""

    assert (
        normalize_model_cost_tier("gemini", ModelCostTier.FREE_QUOTA)
        is ModelCostTier.FREE_QUOTA
    )


def test_deepseek_draft_is_normalized_before_write() -> None:
    """即使旧客户端仍提交 free_quota，写库前也必须纠正。"""

    repository = object.__new__(CareerModelProfileRepository)
    draft = ModelProfileDraft(
        profile_key="deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        provider_key="deepseek",
        model_id="deepseek-v4-pro",
        capabilities=frozenset({ModelCapability.TEXT}),
        cost_tier=ModelCostTier.FREE_QUOTA,
    )

    normalized = repository._normalize_draft(draft)

    assert normalized.cost_tier is ModelCostTier.PAID


def test_legacy_deepseek_row_is_normalized_when_read() -> None:
    """迁移执行前读取到的旧记录也不能进入免费路由。"""

    now = datetime.now(UTC)
    record = CareerModelProfileRepository._to_record(
        {
            "id": uuid4(),
            "organization_id": uuid4(),
            "profile_key": "deepseek-v4-flash",
            "display_name": "DeepSeek V4 Flash",
            "provider_key": "deepseek",
            "model_id": "deepseek-v4-flash",
            "api_base_url": "https://api.deepseek.com",
            "provider_website_url": "https://platform.deepseek.com",
            "capability_codes": ["text"],
            "cost_tier": "free_quota",
            "enabled": True,
            "priority": 100,
            "created_at": now,
            "updated_at": now,
        }
    )

    assert record.cost_tier is ModelCostTier.PAID
