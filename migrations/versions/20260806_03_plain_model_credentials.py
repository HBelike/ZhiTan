"""将个人本机模式的模型 API Key 改为直接保存。

求职助手当前按个人本机使用场景部署。连接 Key 直接保存在独立 PostgreSQL 表中，
不再依赖环境变量主密钥；Web API 仍绝不回传 Key。旧的加密列保留为可空列，
历史加密记录需在编辑模型连接时重新填写并保存一次。
"""

from __future__ import annotations

from alembic import op


revision = "20260806_03"
down_revision = "20260806_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为本机明文凭据增加字段，并允许废弃的加密字段为空。"""

    op.execute(
        """
        ALTER TABLE career_assistant.model_profile_credentials
        ALTER COLUMN encrypted_api_key DROP NOT NULL
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.model_profile_credentials
        ADD COLUMN IF NOT EXISTS plaintext_api_key TEXT
        """,
    )


def downgrade() -> None:
    """移除本机明文凭据字段。"""

    op.execute(
        """
        ALTER TABLE career_assistant.model_profile_credentials
        DROP COLUMN IF EXISTS plaintext_api_key
        """,
    )
