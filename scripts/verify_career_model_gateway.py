"""验证模型网关的 PostgreSQL 档案管理与免费优先路由。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.contracts import (
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.model_gateway import (
    ModelGateway,
    ModelReadiness,
    ModelResolutionReason,
)
from src.career_assistant.persistence import (
    CareerDatabase,
    CareerModelProfileRepository,
    ModelCostTier,
    ModelProfileDraft,
)
from src.career_assistant.persistence.credential_cipher import CredentialCipher
from src.career_assistant.persistence.conversation_repository import DEFAULT_ORGANIZATION_ID
from src.career_assistant.settings import load_model_gateway_settings


def main() -> None:
    """创建临时免费/付费档案，验证路由策略与自动清理。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    database = CareerDatabase(database_url)
    repository = CareerModelProfileRepository(
        database,
        credential_cipher=CredentialCipher.from_master_key(Fernet.generate_key()),
    )
    created_profile_ids = []

    try:
        # 已保存的个人连接不应依赖“公共免费模型目录”是否收录该服务商。
        # 该用例覆盖 DeepSeek 这类用户自行配置、但目录未必展示的连接。
        personal_profile = repository.upsert_profile(
            DEFAULT_ORGANIZATION_ID,
            ModelProfileDraft(
                profile_key="verification-personal-deepseek-free",
                display_name="验证用个人 DeepSeek 连接",
                provider_key="deepseek",
                model_id="verification-deepseek-model",
                capabilities=frozenset({ModelCapability.TEXT}),
                cost_tier=ModelCostTier.FREE_QUOTA,
                priority=1,
            ),
            api_key="verification-only-key",
        )
        created_profile_ids.append(personal_profile.id)
        free_profile = repository.upsert_profile(
            DEFAULT_ORGANIZATION_ID,
            ModelProfileDraft(
                profile_key="verification-groq-free",
                display_name="验证用免费文本模型",
                provider_key="groq",
                model_id="verification-free-model",
                capabilities=frozenset({ModelCapability.TEXT}),
                cost_tier=ModelCostTier.FREE_QUOTA,
                priority=10,
            ),
        )
        created_profile_ids.append(free_profile.id)
        paid_profile = repository.upsert_profile(
            DEFAULT_ORGANIZATION_ID,
            ModelProfileDraft(
                profile_key="verification-paid-model",
                display_name="验证用付费模型",
                provider_key="openrouter",
                model_id="verification-paid-model",
                capabilities=frozenset({ModelCapability.TEXT}),
                cost_tier=ModelCostTier.PAID,
                priority=0,
            ),
        )
        created_profile_ids.append(paid_profile.id)

        gateway = ModelGateway(
            repository,
            load_model_gateway_settings(),
            environment={},
        )
        resolution = gateway.resolve(
            DEFAULT_ORGANIZATION_ID,
            ModelSelectionRequest(),
        )
        availability = gateway.list_availability(DEFAULT_ORGANIZATION_ID)
        paid_availability = next(item for item in availability if item.profile.id == paid_profile.id)

        assert resolution.profile.id == personal_profile.id
        assert resolution.reason is ModelResolutionReason.FREE_QUOTA_PRIORITY
        assert resolution.readiness is ModelReadiness.READY
        assert paid_availability.readiness is ModelReadiness.POLICY_BLOCKED

        selected_resolution = gateway.resolve(
            DEFAULT_ORGANIZATION_ID,
            ModelSelectionRequest(
                mode=ModelSelectionMode.SPECIFIC_PROFILE,
                profile_id=free_profile.id,
                required_capabilities=frozenset({ModelCapability.TEXT}),
            ),
        )
        assert selected_resolution.reason is ModelResolutionReason.USER_SELECTED
        print("career_model_gateway_real_database_ok")
    finally:
        for profile_id in reversed(created_profile_ids):
            deleted = repository.delete_profile_permanently(
                DEFAULT_ORGANIZATION_ID,
                profile_id,
            )
            if not deleted:
                raise RuntimeError("模型网关验证档案清理失败，请人工检查数据库")
        database.close()


if __name__ == "__main__":
    main()
