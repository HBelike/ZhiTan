"""新增平台账号、会话和版本化运行配置。"""

from __future__ import annotations

from alembic import op


revision = "20260810_06"
down_revision = "20260809_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建不影响现有业务表的平台级访问与配置表。"""

    op.execute(
        """
        CREATE TABLE career_assistant.platform_users (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            username TEXT NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, username)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.platform_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL
                REFERENCES career_assistant.platform_users(id) ON DELETE CASCADE,
            token_digest CHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_platform_sessions_active
        ON career_assistant.platform_sessions (user_id, expires_at)
        WHERE revoked_at IS NULL
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.pipeline_config_versions (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK (version > 0),
            config_json JSONB NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            created_by UUID
                REFERENCES career_assistant.platform_users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, version)
        )
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pipeline_config_active_per_organization
        ON career_assistant.pipeline_config_versions (organization_id)
        WHERE is_active = TRUE
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.pipeline_execution_requests (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            config_version_id UUID
                REFERENCES career_assistant.pipeline_config_versions(id) ON DELETE SET NULL,
            requested_by UUID
                REFERENCES career_assistant.platform_users(id) ON DELETE SET NULL,
            trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual', 'scheduled')),
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            idempotency_key TEXT NOT NULL,
            error_message TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, idempotency_key)
        )
        """,
    )


def downgrade() -> None:
    """按依赖逆序删除本次新增表和索引。"""

    op.execute("DROP TABLE IF EXISTS career_assistant.pipeline_execution_requests")
    op.execute("DROP TABLE IF EXISTS career_assistant.pipeline_config_versions")
    op.execute("DROP TABLE IF EXISTS career_assistant.platform_sessions")
    op.execute("DROP TABLE IF EXISTS career_assistant.platform_users")
