"""新增求职助手 PostgreSQL Turn 权威队列与 Worker 租约。"""

from __future__ import annotations

from alembic import op


revision = "20260823_17"
down_revision = "20260821_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE career_assistant.agent_turns
            ADD COLUMN queue_sequence BIGINT GENERATED ALWAYS AS IDENTITY,
            ADD COLUMN available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0
                CHECK (attempt_count >= 0),
            ADD COLUMN lease_owner TEXT,
            ADD COLUMN lease_expires_at TIMESTAMPTZ,
            ADD COLUMN heartbeat_at TIMESTAMPTZ,
            ADD COLUMN cancel_requested_at TIMESTAMPTZ,
            ADD CONSTRAINT uq_career_agent_turns_queue_sequence
                UNIQUE (queue_sequence)
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.agent_turn_payloads (
            turn_id UUID PRIMARY KEY
                REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
            input_text TEXT NOT NULL,
            effective_text TEXT NOT NULL,
            job_url TEXT,
            attachment_payloads_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            request_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.agent_turn_events (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            turn_id UUID NOT NULL
                REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.agent_execution_slots (
            slot_number INTEGER PRIMARY KEY CHECK (slot_number > 0),
            turn_id UUID
                REFERENCES career_assistant.agent_turns(id) ON DELETE SET NULL,
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT chk_career_agent_execution_slot_lease
                CHECK (
                    (turn_id IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL)
                    OR
                    (turn_id IS NOT NULL AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
                )
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_career_agent_turns_queue_claim
        ON career_assistant.agent_turns (status, available_at, queue_sequence)
        WHERE status IN ('queued', 'running')
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_career_agent_turns_conversation_queue
        ON career_assistant.agent_turns (conversation_id, queue_sequence)
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_career_agent_turn_events_turn_id
        ON career_assistant.agent_turn_events (turn_id, id)
        """,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS career_assistant.idx_career_agent_turn_events_turn_id")
    op.execute("DROP INDEX IF EXISTS career_assistant.idx_career_agent_turns_conversation_queue")
    op.execute("DROP INDEX IF EXISTS career_assistant.idx_career_agent_turns_queue_claim")
    op.execute("DROP TABLE career_assistant.agent_execution_slots")
    op.execute("DROP TABLE career_assistant.agent_turn_events")
    op.execute("DROP TABLE career_assistant.agent_turn_payloads")
    op.execute(
        """
        ALTER TABLE career_assistant.agent_turns
            DROP CONSTRAINT uq_career_agent_turns_queue_sequence,
            DROP COLUMN cancel_requested_at,
            DROP COLUMN heartbeat_at,
            DROP COLUMN lease_expires_at,
            DROP COLUMN lease_owner,
            DROP COLUMN attempt_count,
            DROP COLUMN available_at,
            DROP COLUMN queue_sequence
        """,
    )
