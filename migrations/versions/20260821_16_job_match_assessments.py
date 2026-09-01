"""新增 LLM Judge 岗位匹配评估缓存。"""

from __future__ import annotations

from alembic import op


revision = "20260821_16"
down_revision = "20260821_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE career_assistant.job_match_assessments (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            actor_id UUID NOT NULL
                REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
            candidate_profile_id UUID NOT NULL
                REFERENCES career_assistant.candidate_profiles(id) ON DELETE CASCADE,
            target_role_profile_id UUID NOT NULL
                REFERENCES career_assistant.target_role_profiles(id) ON DELETE CASCADE,
            judge_model_profile_id UUID
                REFERENCES career_assistant.model_profiles(id) ON DELETE RESTRICT,
            judge_provider_key TEXT NOT NULL,
            judge_model_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            schema_version TEXT NOT NULL DEFAULT 'career-job-assessment-v1',
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'analyzing', 'ready', 'fallback_ready', 'failed')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
            result JSONB,
            error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (
                candidate_profile_id,
                target_role_profile_id,
                judge_provider_key,
                judge_model_id,
                prompt_version
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_job_match_assessments_actor_updated
        ON career_assistant.job_match_assessments (actor_id, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS career_assistant.job_match_assessments")
