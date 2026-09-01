"""验证模型连接的加密凭据持久化与动态 Provider 路由。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.contracts import (
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.model_gateway import ModelGateway, ModelReadiness
from src.career_assistant.persistence import (
    CareerDatabase,
    CareerModelProfileRepository,
    ModelCostTier,
    ModelProfileDraft,
)
from src.career_assistant.persistence.credential_cipher import (
    CredentialCipher,
    CredentialCipherError,
)
from src.career_assistant.persistence.conversation_repository import DEFAULT_ORGANIZATION_ID
from src.career_assistant.settings import load_model_gateway_settings


def main() -> None:
    """写入临时 Fernet 凭据、验证迁移后删除，确保不遗留测试模型。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    database = CareerDatabase(database_url)
    repository = CareerModelProfileRepository(
        database,
        credential_cipher=CredentialCipher.from_master_key(Fernet.generate_key()),
    )
    profile_ids = []
    api_key = "verification-only-api-key"
    try:
        profile = repository.upsert_profile(
            DEFAULT_ORGANIZATION_ID,
            ModelProfileDraft(
                profile_key="verification-custom-connection",
                display_name="验证自定义连接",
                provider_key="custom",
                model_id="verification-model",
                api_base_url="https://mock-provider.example/v1",
                capabilities=frozenset({ModelCapability.TEXT}),
                cost_tier=ModelCostTier.FREE_QUOTA,
            ),
            api_key=api_key,
        )
        profile_ids.append(profile.id)
        assert repository.has_stored_credential(DEFAULT_ORGANIZATION_ID, profile.id)
        with database.transaction() as connection:
            credential_row = connection.execute(
                text(
                    """
                    SELECT encrypted_api_key, plaintext_api_key, encryption_scheme
                    FROM career_assistant.model_profile_credentials
                    WHERE profile_id = :profile_id
                    """,
                ),
                {"profile_id": profile.id},
            ).mappings().one()
        assert credential_row["encryption_scheme"] == "fernet_v1"
        assert credential_row["plaintext_api_key"] is None
        assert bytes(credential_row["encrypted_api_key"]) != api_key.encode("utf-8")
        assert (
            repository.read_stored_credential(
                DEFAULT_ORGANIZATION_ID,
                profile.id,
            )
            == api_key
        )

        gateway = ModelGateway(
            repository,
            load_model_gateway_settings(),
            environment={},
        )
        resolution = gateway.resolve(
            DEFAULT_ORGANIZATION_ID,
            ModelSelectionRequest(
                mode=ModelSelectionMode.SPECIFIC_PROFILE,
                profile_id=profile.id,
                required_capabilities=frozenset({ModelCapability.TEXT}),
            ),
        )
        assert resolution.readiness is ModelReadiness.READY
        assert resolution.credential == api_key

        legacy_profile = repository.upsert_profile(
            DEFAULT_ORGANIZATION_ID,
            ModelProfileDraft(
                profile_key="verification-legacy-credential",
                display_name="验证旧明文凭据迁移",
                provider_key="custom",
                model_id="verification-legacy-model",
                api_base_url="https://mock-provider.example/v1",
                capabilities=frozenset({ModelCapability.TEXT}),
                cost_tier=ModelCostTier.FREE_QUOTA,
            ),
        )
        profile_ids.append(legacy_profile.id)
        with database.transaction() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.model_profile_credentials (
                        profile_id, organization_id, encrypted_api_key, plaintext_api_key,
                        encryption_scheme, last_verified_at
                    )
                    VALUES (
                        :profile_id, :organization_id, NULL, :api_key,
                        'legacy_plaintext', NOW()
                    )
                    ON CONFLICT (profile_id) DO UPDATE
                    SET encrypted_api_key = NULL,
                        plaintext_api_key = EXCLUDED.plaintext_api_key,
                        encryption_scheme = 'legacy_plaintext',
                        updated_at = NOW()
                    """,
                ),
                {
                    "profile_id": legacy_profile.id,
                    "organization_id": DEFAULT_ORGANIZATION_ID,
                    "api_key": api_key,
                },
            )
        try:
            repository.read_stored_credential(DEFAULT_ORGANIZATION_ID, legacy_profile.id)
        except CredentialCipherError as exc:
            assert "旧版明文" in str(exc)
        else:
            raise AssertionError("默认配置不得读取历史明文凭据")

        assert repository.migrate_legacy_plaintext_credentials(profile_id=legacy_profile.id) == 1
        assert (
            repository.read_stored_credential(DEFAULT_ORGANIZATION_ID, legacy_profile.id)
            == api_key
        )
        with database.transaction() as connection:
            migrated_row = connection.execute(
                text(
                    """
                    SELECT encrypted_api_key, plaintext_api_key, encryption_scheme
                    FROM career_assistant.model_profile_credentials
                    WHERE profile_id = :profile_id
                    """,
                ),
                {"profile_id": legacy_profile.id},
            ).mappings().one()
        assert migrated_row["encryption_scheme"] == "fernet_v1"
        assert migrated_row["plaintext_api_key"] is None
        assert bytes(migrated_row["encrypted_api_key"]) != api_key.encode("utf-8")
        print("career_model_connection_credentials_real_database_ok")
    finally:
        for profile_id in reversed(profile_ids):
            deleted = repository.delete_profile_permanently(
                DEFAULT_ORGANIZATION_ID,
                profile_id,
            )
            if not deleted:
                raise RuntimeError("模型连接凭据验证数据清理失败，请检查数据库")
        database.close()


if __name__ == "__main__":
    main()
