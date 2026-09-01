"""为浏览器实时面试记录客户端类型与候选人音频策略。"""

from __future__ import annotations

from alembic import op


revision = "20260823_19"
down_revision = "20260823_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE career_assistant.live_interview_sessions
        ADD COLUMN client_kind TEXT NOT NULL DEFAULT 'desktop',
        ADD COLUMN candidate_audio_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        ADD CONSTRAINT ck_live_interview_client_kind
            CHECK (client_kind IN ('desktop', 'browser'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE career_assistant.live_interview_sessions
        DROP CONSTRAINT IF EXISTS ck_live_interview_client_kind,
        DROP COLUMN IF EXISTS candidate_audio_enabled,
        DROP COLUMN IF EXISTS client_kind
        """
    )
