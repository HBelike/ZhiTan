"""记录模型服务商响应中回报的模型标识。"""

from __future__ import annotations

from alembic import op


revision = "20260828_30"
down_revision = "20260828_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为模型调用留存 Provider 响应中的 model 字段。"""

    op.execute(
        "ALTER TABLE career_assistant.model_usage "
        "ADD COLUMN provider_reported_model_id TEXT",
    )


def downgrade() -> None:
    """移除 Provider 回报模型字段。"""

    op.execute(
        "ALTER TABLE career_assistant.model_usage "
        "DROP COLUMN provider_reported_model_id",
    )
