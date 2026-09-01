"""增加模型上下文策略与分操作模型用量。"""

from __future__ import annotations

from alembic import op


revision = "20260826_22"
down_revision = "20260825_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为模型档案增加管理员可配置的上下文预算。"""

    op.execute(
        """
        ALTER TABLE career_assistant.model_profiles
          ADD COLUMN context_window_tokens INTEGER NOT NULL DEFAULT 8192
            CHECK (context_window_tokens >= 4096),
          ADD COLUMN reserved_output_tokens INTEGER NOT NULL DEFAULT 4096
            CHECK (reserved_output_tokens > 0),
          ADD COLUMN compression_trigger_percent INTEGER NOT NULL DEFAULT 80
            CHECK (compression_trigger_percent BETWEEN 50 AND 90),
          ADD COLUMN compression_target_percent INTEGER NOT NULL DEFAULT 60
            CHECK (compression_target_percent BETWEEN 30 AND 75),
          ADD COLUMN context_window_source TEXT NOT NULL DEFAULT 'fallback'
            CHECK (context_window_source IN ('fallback', 'built_in', 'admin')),
          ADD CONSTRAINT chk_model_context_policy_order
            CHECK (compression_target_percent < compression_trigger_percent),
          ADD CONSTRAINT chk_model_output_within_context
            CHECK (reserved_output_tokens * 2 <= context_window_tokens)
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.model_usage
          DROP CONSTRAINT model_usage_turn_id_key,
          ADD COLUMN operation_kind TEXT NOT NULL DEFAULT 'career_response',
          ADD CONSTRAINT chk_model_usage_operation_kind CHECK (
            operation_kind IN (
              'career_response',
              'conversation_memory_compaction',
              'career_memory_extraction'
            )
          ),
          ADD CONSTRAINT uq_model_usage_turn_operation UNIQUE (turn_id, operation_kind)
        """,
    )


def downgrade() -> None:
    """恢复每个 Turn 仅一条模型用量的旧结构。"""

    op.execute(
        """
        ALTER TABLE career_assistant.model_usage
          DROP CONSTRAINT uq_model_usage_turn_operation,
          DROP CONSTRAINT chk_model_usage_operation_kind,
          DROP COLUMN operation_kind,
          ADD CONSTRAINT model_usage_turn_id_key UNIQUE (turn_id)
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.model_profiles
          DROP CONSTRAINT chk_model_output_within_context,
          DROP CONSTRAINT chk_model_context_policy_order,
          DROP COLUMN context_window_source,
          DROP COLUMN compression_target_percent,
          DROP COLUMN compression_trigger_percent,
          DROP COLUMN reserved_output_tokens,
          DROP COLUMN context_window_tokens
        """,
    )
