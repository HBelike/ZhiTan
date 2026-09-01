"""为求职助手增加加密模型凭据与服务商官网信息。

本迁移只扩展 ``career_assistant`` schema，不触碰公众号工作流、技能库或既有 SQLite 数据。
API Key 使用 PostgreSQL ``pgcrypto`` 的 AES-256 对称加密结果保存；解密口令始终由
服务端环境变量 ``CAREER_CREDENTIAL_MASTER_KEY`` 提供，数据库中不保存该口令。
"""

from __future__ import annotations

from alembic import op


revision = "20260806_02"
down_revision = "20260805_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加连接凭据表和官网链接字段。"""

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        ALTER TABLE career_assistant.model_profiles
        ADD COLUMN IF NOT EXISTS provider_website_url TEXT
        """,
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS career_assistant.model_profile_credentials (
            profile_id UUID PRIMARY KEY
                REFERENCES career_assistant.model_profiles(id) ON DELETE CASCADE,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            encrypted_api_key BYTEA NOT NULL,
            last_verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_career_model_credentials_organization
        ON career_assistant.model_profile_credentials (organization_id)
        """,
    )


def downgrade() -> None:
    """移除本迁移新增的连接凭据能力。"""

    op.execute("DROP TABLE IF EXISTS career_assistant.model_profile_credentials")
    op.execute(
        """
        ALTER TABLE career_assistant.model_profiles
        DROP COLUMN IF EXISTS provider_website_url
        """,
    )
