"""增加职业空间、六类长期求职记忆与来源追踪。"""

from __future__ import annotations

from alembic import op


revision = "20260826_24"
down_revision = "20260826_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE career_assistant.career_spaces (
          id UUID PRIMARY KEY,
          organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
          actor_id UUID NOT NULL REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          normalized_name TEXT NOT NULL,
          is_default BOOLEAN NOT NULL DEFAULT FALSE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE (organization_id, actor_id, normalized_name),
          CHECK (length(trim(name)) BETWEEN 1 AND 120)
        )
        """,
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_career_spaces_actor_default "
        "ON career_assistant.career_spaces (organization_id, actor_id) WHERE is_default",
    )
    op.execute(
        """
        INSERT INTO career_assistant.career_spaces
          (id, organization_id, actor_id, name, normalized_name, is_default)
        SELECT md5(organization_id::text || ':' || id::text || ':' || 'career-space')::uuid,
               organization_id, id, '默认求职方向', '默认求职方向', TRUE
        FROM career_assistant.actors
        """,
    )
    op.execute(
        "ALTER TABLE career_assistant.conversations ADD COLUMN career_space_id UUID "
        "REFERENCES career_assistant.career_spaces(id) ON DELETE RESTRICT",
    )
    op.execute(
        """
        UPDATE career_assistant.conversations AS conversation
        SET career_space_id = space.id
        FROM career_assistant.career_spaces AS space
        WHERE space.organization_id = conversation.organization_id
          AND space.actor_id = conversation.actor_id
          AND space.is_default
        """,
    )
    op.execute(
        "ALTER TABLE career_assistant.conversations ALTER COLUMN career_space_id SET NOT NULL",
    )
    op.execute(
        """
        CREATE TABLE career_assistant.career_memory_items (
          id UUID PRIMARY KEY,
          organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
          actor_id UUID NOT NULL REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
          career_space_id UUID REFERENCES career_assistant.career_spaces(id) ON DELETE CASCADE,
          memory_type TEXT NOT NULL CHECK (memory_type IN (
            'job_intention','work_experience','education','award','publication','personal_advantage'
          )),
          normalized_value_json JSONB NOT NULL,
          display_text TEXT NOT NULL CHECK (length(display_text) BETWEEN 1 AND 500),
          source_kind TEXT NOT NULL CHECK (source_kind IN (
            'explicit_user_statement','explicit_user_correction','confirmed_resume','user_confirmed_candidate'
          )),
          source_message_id UUID REFERENCES career_assistant.messages(id) ON DELETE SET NULL,
          source_conversation_id UUID REFERENCES career_assistant.conversations(id) ON DELETE SET NULL,
          candidate_profile_id UUID REFERENCES career_assistant.candidate_profiles(id) ON DELETE SET NULL,
          candidate_profile_version INTEGER CHECK (candidate_profile_version > 0),
          status TEXT NOT NULL CHECK (status IN ('candidate','active','disabled','superseded')),
          supersedes_memory_id UUID REFERENCES career_assistant.career_memory_items(id) ON DELETE SET NULL,
          valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          valid_to TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CHECK (memory_type <> 'job_intention' OR career_space_id IS NOT NULL)
        )
        """,
    )
    op.execute(
        "CREATE INDEX idx_career_memory_scope_status ON career_assistant.career_memory_items "
        "(organization_id, actor_id, career_space_id, status, memory_type)",
    )
    op.execute(
        """
        CREATE TABLE career_assistant.career_memory_jobs (
          id UUID PRIMARY KEY,
          organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
          actor_id UUID NOT NULL REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
          conversation_id UUID REFERENCES career_assistant.conversations(id) ON DELETE CASCADE,
          turn_id UUID REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
          candidate_profile_id UUID REFERENCES career_assistant.candidate_profiles(id) ON DELETE CASCADE,
          candidate_profile_version INTEGER CHECK (candidate_profile_version > 0),
          job_kind TEXT NOT NULL CHECK (job_kind IN ('turn_extraction','resume_indexing')),
          requested_profile_id UUID REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
          status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','superseded')),
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
        "CREATE UNIQUE INDEX uq_career_memory_jobs_turn ON career_assistant.career_memory_jobs (turn_id) "
        "WHERE turn_id IS NOT NULL",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_career_memory_jobs_resume ON career_assistant.career_memory_jobs "
        "(candidate_profile_id, candidate_profile_version) WHERE candidate_profile_id IS NOT NULL",
    )
    op.execute(
        "CREATE INDEX idx_career_memory_jobs_claim ON career_assistant.career_memory_jobs (status, created_at)",
    )
    op.execute(
        """
        CREATE TABLE career_assistant.turn_memory_usages (
          id UUID PRIMARY KEY,
          organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
          actor_id UUID NOT NULL REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
          turn_id UUID NOT NULL REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
          memory_id UUID REFERENCES career_assistant.career_memory_items(id) ON DELETE SET NULL,
          memory_type TEXT NOT NULL CHECK (memory_type IN (
            'job_intention','work_experience','education','award','publication','personal_advantage'
          )),
          source_kind TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE (turn_id, memory_id)
        )
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.model_usage
          DROP CONSTRAINT uq_model_usage_turn_operation,
          ALTER COLUMN turn_id DROP NOT NULL,
          ADD COLUMN memory_job_id UUID REFERENCES career_assistant.career_memory_jobs(id) ON DELETE CASCADE,
          ADD CONSTRAINT chk_model_usage_owner CHECK (
            (turn_id IS NOT NULL AND memory_job_id IS NULL)
            OR (turn_id IS NULL AND memory_job_id IS NOT NULL)
          )
        """,
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_model_usage_turn_operation ON career_assistant.model_usage "
        "(turn_id, operation_kind) WHERE turn_id IS NOT NULL",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_model_usage_memory_job_operation ON career_assistant.model_usage "
        "(memory_job_id, operation_kind) WHERE memory_job_id IS NOT NULL",
    )


def downgrade() -> None:
    op.execute("DROP INDEX career_assistant.uq_model_usage_memory_job_operation")
    op.execute("DROP INDEX career_assistant.uq_model_usage_turn_operation")
    op.execute(
        """
        ALTER TABLE career_assistant.model_usage
          DROP CONSTRAINT chk_model_usage_owner,
          DROP COLUMN memory_job_id,
          ALTER COLUMN turn_id SET NOT NULL,
          ADD CONSTRAINT uq_model_usage_turn_operation UNIQUE (turn_id, operation_kind)
        """,
    )
    op.execute("DROP TABLE career_assistant.turn_memory_usages")
    op.execute("DROP TABLE career_assistant.career_memory_jobs")
    op.execute("DROP TABLE career_assistant.career_memory_items")
    op.execute("ALTER TABLE career_assistant.conversations DROP COLUMN career_space_id")
    op.execute("DROP TABLE career_assistant.career_spaces")
