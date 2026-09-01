"""新增 Windows 实时面试会话、最终话语与答案记录。"""

from __future__ import annotations

from alembic import op


revision = "20260823_18"
down_revision = "20260823_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE career_assistant.live_interview_sessions (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            actor_id UUID NOT NULL,
            candidate_profile_id UUID
                REFERENCES career_assistant.candidate_profiles(id) ON DELETE SET NULL,
            target_role_profile_id UUID
                REFERENCES career_assistant.target_role_profiles(id) ON DELETE SET NULL,
            interview_experience_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            asr_provider TEXT NOT NULL,
            asr_model_profile_id UUID
                REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
            answer_model_profile_id UUID
                REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'preparing'
                CHECK (status IN ('preparing', 'active', 'completed', 'failed')),
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.live_interview_utterances (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL
                REFERENCES career_assistant.live_interview_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('interviewer', 'candidate')),
            channel_sequence BIGINT NOT NULL CHECK (channel_sequence >= 0),
            raw_text TEXT NOT NULL,
            corrected_text TEXT,
            provider TEXT NOT NULL,
            confidence DOUBLE PRECISION,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_live_interview_utterance_sequence
                UNIQUE (session_id, role, channel_sequence)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.live_interview_answers (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL
                REFERENCES career_assistant.live_interview_sessions(id) ON DELETE CASCADE,
            question_version INTEGER NOT NULL CHECK (question_version > 0),
            attempt INTEGER NOT NULL CHECK (attempt > 0),
            original_question TEXT NOT NULL,
            normalized_question TEXT NOT NULL,
            intent TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'generating'
                CHECK (status IN ('generating', 'completed', 'cancelled', 'failed')),
            answer_text TEXT NOT NULL DEFAULT '',
            evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_live_interview_answer_attempt
                UNIQUE (session_id, question_version, attempt)
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_live_interview_sessions_owner
        ON career_assistant.live_interview_sessions
            (organization_id, actor_id, created_at DESC)
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_live_interview_utterances_timeline
        ON career_assistant.live_interview_utterances (session_id, created_at)
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_live_interview_answers_timeline
        ON career_assistant.live_interview_answers
            (session_id, question_version, attempt)
        """,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS career_assistant.idx_live_interview_answers_timeline")
    op.execute("DROP INDEX IF EXISTS career_assistant.idx_live_interview_utterances_timeline")
    op.execute("DROP INDEX IF EXISTS career_assistant.idx_live_interview_sessions_owner")
    op.execute("DROP TABLE career_assistant.live_interview_answers")
    op.execute("DROP TABLE career_assistant.live_interview_utterances")
    op.execute("DROP TABLE career_assistant.live_interview_sessions")
