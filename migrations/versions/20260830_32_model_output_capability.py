"""把旧输出预留字段调整为模型最大输出能力。"""

from __future__ import annotations

from alembic import op


revision = "20260830_32"
down_revision = "20260830_31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """允许模型最大输出使用完整上下文，并回填已核对的 DeepSeek V4。"""

    op.execute(
        """
        ALTER TABLE career_assistant.model_profiles
          DROP CONSTRAINT chk_model_output_within_context,
          ADD CONSTRAINT chk_model_output_within_context
            CHECK (reserved_output_tokens <= context_window_tokens)
        """,
    )
    op.execute(
        """
        UPDATE career_assistant.model_profiles
        SET reserved_output_tokens = 384000,
            updated_at = NOW()
        WHERE LOWER(provider_key) = 'deepseek'
          AND LOWER(model_id) IN (
            'deepseek-v4-flash',
            'deepseek-v4-pro',
            'deepseek-v4-flash-vision-exp'
          )
          AND context_window_source <> 'admin'
        """,
    )


def downgrade() -> None:
    """恢复旧的一半上下文约束，并把超出值收敛到旧边界。"""

    op.execute(
        """
        UPDATE career_assistant.model_profiles
        SET reserved_output_tokens = LEAST(
              reserved_output_tokens,
              context_window_tokens / 2
            ),
            updated_at = NOW()
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.model_profiles
          DROP CONSTRAINT chk_model_output_within_context,
          ADD CONSTRAINT chk_model_output_within_context
            CHECK (reserved_output_tokens * 2 <= context_window_tokens)
        """,
    )
