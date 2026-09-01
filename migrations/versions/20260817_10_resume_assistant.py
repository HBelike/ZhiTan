"""新增简历助手不可变优化历史。"""

from __future__ import annotations

from alembic import op


revision = "20260817_10"
down_revision = "20260816_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE career_assistant.resume_optimization_records (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            owner_id UUID NOT NULL REFERENCES career_assistant.platform_users(id) ON DELETE CASCADE,
            creator_name TEXT NOT NULL,
            job_title TEXT NOT NULL,
            model_profile_id UUID REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
            model_display_name TEXT NOT NULL,
            provider_key TEXT NOT NULL,
            model_id TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            source_media_type TEXT NOT NULL,
            original_file_path TEXT NOT NULL,
            original_preview_markdown TEXT NOT NULL,
            job_description_text TEXT NOT NULL,
            extra_prompt TEXT,
            suggestions_json JSONB NOT NULL,
            optimized_markdown TEXT NOT NULL,
            optimized_file_path TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        "CREATE INDEX idx_resume_optimization_org_created "
        "ON career_assistant.resume_optimization_records (organization_id, created_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_resume_optimization_owner_created "
        "ON career_assistant.resume_optimization_records (owner_id, created_at DESC)",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS career_assistant.resume_optimization_records")
