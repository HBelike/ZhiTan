"""将求职助手模型连接凭据切换为应用层 Fernet 加密。

迁移只扩展 ``career_assistant.model_profile_credentials``。Fernet 主密钥不进入
数据库迁移，也不写入任何数据表；它始终由运行时环境变量
``CAREER_CREDENTIAL_MASTER_KEY`` 提供。

旧 ``plaintext_api_key`` 记录保留为 ``legacy_plaintext``，由部署者在主密钥配置
完成后使用迁移脚本转换。历史 ``encrypted_api_key`` 没有可验证的格式元数据，因此
标为 ``legacy_unknown``，禁止直接作为 API Key 使用，必须重新填写。
"""

from __future__ import annotations

from alembic import op


revision = "20260810_08"
down_revision = "20260810_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加凭据格式元数据，并为 Fernet 新写入建立数据库级约束。"""

    op.execute(
        """
        ALTER TABLE career_assistant.model_profile_credentials
        ADD COLUMN IF NOT EXISTS encryption_scheme TEXT NOT NULL DEFAULT 'legacy_plaintext'
        """,
    )
    op.execute(
        """
        UPDATE career_assistant.model_profile_credentials
        SET encryption_scheme = CASE
            WHEN plaintext_api_key IS NOT NULL AND BTRIM(plaintext_api_key) <> ''
                THEN 'legacy_plaintext'
            WHEN encrypted_api_key IS NOT NULL AND OCTET_LENGTH(encrypted_api_key) > 0
                THEN 'legacy_unknown'
            ELSE 'legacy_unknown'
        END
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.model_profile_credentials
        ADD CONSTRAINT chk_career_model_credentials_fernet_payload
        CHECK (
            encryption_scheme <> 'fernet_v1'
            OR (
                encrypted_api_key IS NOT NULL
                AND OCTET_LENGTH(encrypted_api_key) > 0
                AND plaintext_api_key IS NULL
            )
        )
        """,
    )


def downgrade() -> None:
    """移除格式元数据；不会把 Fernet 密文还原成明文。"""

    op.execute(
        """
        ALTER TABLE career_assistant.model_profile_credentials
        DROP CONSTRAINT IF EXISTS chk_career_model_credentials_fernet_payload
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.model_profile_credentials
        DROP COLUMN IF EXISTS encryption_scheme
        """,
    )
