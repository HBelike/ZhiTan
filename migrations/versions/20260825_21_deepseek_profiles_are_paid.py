"""将 DeepSeek 模型档案纠正为付费分类。"""

from __future__ import annotations

from alembic import op


revision = "20260825_21"
down_revision = "20260825_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """清理旧表单默认值产生的 DeepSeek 免费额度误分类。"""

    op.execute(
        """
        UPDATE career_assistant.model_profiles
        SET cost_tier = 'paid', updated_at = NOW()
        WHERE LOWER(TRIM(provider_key)) = 'deepseek'
          AND cost_tier IS DISTINCT FROM 'paid'
        """
    )


def downgrade() -> None:
    """不恢复已确认错误的免费分类。"""

    return None
