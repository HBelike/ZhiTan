"""按模型统一识别并回填上下文容量。"""

from __future__ import annotations

from alembic import op


revision = "20260827_27"
down_revision = "20260827_26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """已知 DeepSeek V4 使用官方 1 Mi Token，未知模型默认使用 1M Token。"""

    op.execute(
        "ALTER TABLE career_assistant.model_profiles "
        "ALTER COLUMN context_window_tokens SET DEFAULT 1000000",
    )
    op.execute(
        """
        UPDATE career_assistant.model_profiles
        SET context_window_tokens = CASE
              WHEN LOWER(provider_key) = 'deepseek'
               AND LOWER(model_id) IN (
                 'deepseek-v4-flash',
                 'deepseek-v4-pro',
                 'deepseek-v4-flash-vision-exp'
               )
              THEN 1048576
              ELSE 1000000
            END,
            context_window_source = CASE
              WHEN LOWER(provider_key) = 'deepseek'
               AND LOWER(model_id) IN (
                 'deepseek-v4-flash',
                 'deepseek-v4-pro',
                 'deepseek-v4-flash-vision-exp'
               )
              THEN 'built_in'
              ELSE 'fallback'
            END,
            updated_at = NOW()
        WHERE context_window_source <> 'admin'
        """,
    )


def downgrade() -> None:
    """恢复旧版统一 8K 兜底策略，管理员设置保持不变。"""

    op.execute(
        "ALTER TABLE career_assistant.model_profiles "
        "ALTER COLUMN context_window_tokens SET DEFAULT 8192",
    )
    op.execute(
        """
        UPDATE career_assistant.model_profiles
        SET context_window_tokens = 8192,
            reserved_output_tokens = LEAST(reserved_output_tokens, 4096),
            context_window_source = 'fallback',
            updated_at = NOW()
        WHERE context_window_source <> 'admin'
        """,
    )
