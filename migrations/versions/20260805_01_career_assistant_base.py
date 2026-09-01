"""创建求职助手独立 PostgreSQL 基础结构。

本迁移仅创建 career_assistant schema 下的表，不读取、不迁移、不修改现有 SQLite
数据库，也不涉及技能库和微信公众号任务数据。
"""

from __future__ import annotations

from alembic import op


revision = "20260805_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建历史、摘要、模型档案和 Agent 运行追踪表。"""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS career_assistant")

    op.execute(
        """
        CREATE TABLE career_assistant.organizations (
            id UUID PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.actors (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL,
            actor_type TEXT NOT NULL CHECK (actor_type IN ('local', 'user', 'service')),
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.model_profiles (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            profile_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            provider_key TEXT NOT NULL,
            model_id TEXT NOT NULL,
            api_base_url TEXT,
            capability_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            cost_tier TEXT NOT NULL CHECK (cost_tier IN ('free_quota', 'local', 'paid')),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            priority INTEGER NOT NULL DEFAULT 100 CHECK (priority >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, profile_key)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.conversations (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            actor_id UUID NOT NULL
                REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted')),
            last_model_profile_id UUID
                REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            archived_at TIMESTAMPTZ
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.session_summaries (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL UNIQUE
                REFERENCES career_assistant.conversations(id) ON DELETE CASCADE,
            summary_text TEXT NOT NULL,
            summary_version INTEGER NOT NULL DEFAULT 1 CHECK (summary_version > 0),
            contains_sensitive_data BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.agent_turns (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
                REFERENCES career_assistant.conversations(id) ON DELETE CASCADE,
            actor_id UUID NOT NULL
                REFERENCES career_assistant.actors(id) ON DELETE CASCADE,
            requested_selection_mode TEXT NOT NULL,
            requested_model_profile_id UUID
                REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
            input_kind_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            error_code TEXT,
            error_message TEXT,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
                REFERENCES career_assistant.conversations(id) ON DELETE CASCADE,
            turn_id UUID
                REFERENCES career_assistant.agent_turns(id) ON DELETE SET NULL,
            role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
            content_text TEXT NOT NULL,
            is_redacted BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.model_usage (
            id UUID PRIMARY KEY,
            turn_id UUID NOT NULL UNIQUE
                REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
            requested_profile_id UUID
                REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
            resolved_profile_id UUID
                REFERENCES career_assistant.model_profiles(id) ON DELETE SET NULL,
            resolved_provider_key TEXT,
            resolved_model_id TEXT,
            status TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'rate_limited', 'failed')),
            input_tokens INTEGER CHECK (input_tokens >= 0),
            output_tokens INTEGER CHECK (output_tokens >= 0),
            retry_after_seconds INTEGER CHECK (retry_after_seconds >= 0),
            fallback_reason TEXT,
            error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.agent_steps (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            turn_id UUID NOT NULL
                REFERENCES career_assistant.agent_turns(id) ON DELETE CASCADE,
            step_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
            attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_code TEXT,
            error_message TEXT,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )

    op.execute(
        """
        INSERT INTO career_assistant.organizations (id, slug, display_name)
        VALUES ('00000000-0000-0000-0000-000000000001', 'local-default', '本地默认组织')
        ON CONFLICT (id) DO NOTHING
        """,
    )
    op.execute(
        """
        INSERT INTO career_assistant.actors (id, organization_id, display_name, actor_type)
        VALUES (
            '00000000-0000-0000-0000-000000000002',
            '00000000-0000-0000-0000-000000000001',
            '本地默认用户',
            'local'
        )
        ON CONFLICT (id) DO NOTHING
        """,
    )

    op.execute(
        "CREATE INDEX idx_career_conversations_actor_updated "
        "ON career_assistant.conversations (actor_id, updated_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_career_messages_conversation_created "
        "ON career_assistant.messages (conversation_id, created_at ASC)",
    )
    op.execute(
        "CREATE INDEX idx_career_agent_turns_conversation_created "
        "ON career_assistant.agent_turns (conversation_id, created_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_career_agent_steps_turn_created "
        "ON career_assistant.agent_steps (turn_id, created_at ASC)",
    )
    op.execute(
        "CREATE INDEX idx_career_model_profiles_enabled_priority "
        "ON career_assistant.model_profiles (organization_id, enabled, priority ASC)",
    )


def downgrade() -> None:
    """仅在明确执行 Alembic 回滚时删除求职助手独立 schema。"""

    op.execute("DROP SCHEMA IF EXISTS career_assistant CASCADE")
