"""新增求职助手基准简历、目标岗位与会话上下文版本。"""

from __future__ import annotations

from alembic import op


revision = "20260820_14"
down_revision = "20260820_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE career_assistant.candidate_profiles (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            actor_id UUID NOT NULL
                REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            resume_outline TEXT NOT NULL,
            version INTEGER NOT NULL CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (length(trim(display_name)) > 0),
            CHECK (length(trim(resume_outline)) > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE career_assistant.target_role_profiles (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            actor_id UUID NOT NULL
                REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
            company_name TEXT NOT NULL,
            role_name TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_label TEXT NOT NULL DEFAULT '',
            job_text TEXT NOT NULL,
            requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
            version INTEGER NOT NULL CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (length(trim(company_name)) > 0),
            CHECK (length(trim(role_name)) > 0),
            CHECK (length(trim(job_text)) > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE career_assistant.conversation_context_bindings (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
                REFERENCES career_assistant.conversations(id) ON DELETE CASCADE,
            candidate_profile_id UUID NOT NULL
                REFERENCES career_assistant.candidate_profiles(id) ON DELETE RESTRICT,
            target_role_profile_id UUID NOT NULL
                REFERENCES career_assistant.target_role_profiles(id) ON DELETE RESTRICT,
            binding_version INTEGER NOT NULL CHECK (binding_version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (conversation_id, binding_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_candidate_profiles_actor_created "
        "ON career_assistant.candidate_profiles (actor_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_target_role_profiles_actor_created "
        "ON career_assistant.target_role_profiles (actor_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_context_bindings_conversation_version "
        "ON career_assistant.conversation_context_bindings "
        "(conversation_id, binding_version DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS career_assistant.conversation_context_bindings")
    op.execute("DROP TABLE IF EXISTS career_assistant.target_role_profiles")
    op.execute("DROP TABLE IF EXISTS career_assistant.candidate_profiles")
