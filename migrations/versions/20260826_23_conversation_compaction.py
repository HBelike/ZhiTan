"""增加滚动摘要游标与会话压缩任务。"""

from __future__ import annotations

from alembic import op


revision = "20260826_23"
down_revision = "20260826_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE career_assistant.session_summaries
          ADD COLUMN covered_through_message_id UUID
            REFERENCES career_assistant.messages(id) ON DELETE SET NULL,
          ADD COLUMN summary_schema_version TEXT NOT NULL
            DEFAULT 'career-conversation-summary-v2',
          ADD COLUMN compacted_with_profile_id UUID
            REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
          ADD COLUMN compacted_input_tokens INTEGER CHECK (compacted_input_tokens >= 0),
          ADD COLUMN compacted_output_tokens INTEGER CHECK (compacted_output_tokens >= 0)
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.conversation_compaction_jobs (
          id UUID PRIMARY KEY,
          organization_id UUID NOT NULL
            REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
          actor_id UUID NOT NULL
            REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
          conversation_id UUID NOT NULL
            REFERENCES career_assistant.conversations(id) ON DELETE CASCADE,
          trigger_turn_id UUID NOT NULL UNIQUE
            REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
          requested_profile_id UUID
            REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
          status TEXT NOT NULL
            CHECK (status IN ('queued','running','succeeded','failed','superseded')),
          expected_summary_version INTEGER NOT NULL CHECK (expected_summary_version >= 0),
          lease_owner TEXT,
          lease_expires_at TIMESTAMPTZ,
          attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
          error_code TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          completed_at TIMESTAMPTZ
        )
        """,
    )
    op.execute(
        "CREATE INDEX idx_compaction_jobs_status_created "
        "ON career_assistant.conversation_compaction_jobs (status, created_at)",
    )
    op.execute(
        "CREATE INDEX idx_compaction_jobs_conversation_status "
        "ON career_assistant.conversation_compaction_jobs (conversation_id, status)",
    )


def downgrade() -> None:
    op.execute("DROP TABLE career_assistant.conversation_compaction_jobs")
    op.execute(
        """
        ALTER TABLE career_assistant.session_summaries
          DROP COLUMN compacted_output_tokens,
          DROP COLUMN compacted_input_tokens,
          DROP COLUMN compacted_with_profile_id,
          DROP COLUMN summary_schema_version,
          DROP COLUMN covered_through_message_id
        """,
    )
