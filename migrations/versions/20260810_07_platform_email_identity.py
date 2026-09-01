"""为平台账号增加邮箱身份、可续期会话和验证码挑战。"""

from __future__ import annotations

from alembic import op


revision = "20260810_07"
down_revision = "20260810_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保留已有管理员账号，并为公开邮箱注册和会话策略增量建表。"""

    op.execute("ALTER TABLE career_assistant.platform_users ADD COLUMN email TEXT")
    op.execute("ALTER TABLE career_assistant.platform_users ADD COLUMN email_normalized TEXT")
    op.execute("ALTER TABLE career_assistant.platform_users ADD COLUMN email_verified_at TIMESTAMPTZ")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_platform_users_verified_email
        ON career_assistant.platform_users (organization_id, email_normalized)
        WHERE email_normalized IS NOT NULL
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.platform_sessions
        ADD COLUMN absolute_expires_at TIMESTAMPTZ
        """,
    )
    op.execute(
        """
        UPDATE career_assistant.platform_sessions
        SET absolute_expires_at = expires_at
        WHERE absolute_expires_at IS NULL
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.platform_sessions
        ALTER COLUMN absolute_expires_at SET NOT NULL
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.platform_email_challenges (
            id UUID PRIMARY KEY,
            email_normalized TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK (purpose IN ('register', 'bootstrap', 'bind_email', 'reset_password')),
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            code_digest CHAR(64) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            attempt_count SMALLINT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            send_count SMALLINT NOT NULL DEFAULT 1 CHECK (send_count > 0),
            last_sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_platform_email_challenges_active
        ON career_assistant.platform_email_challenges (email_normalized, purpose, expires_at)
        WHERE consumed_at IS NULL
        """,
    )


def downgrade() -> None:
    """按依赖逆序回滚本次身份增强字段。"""

    op.execute("DROP TABLE IF EXISTS career_assistant.platform_email_challenges")
    op.execute("ALTER TABLE career_assistant.platform_sessions DROP COLUMN IF EXISTS absolute_expires_at")
    op.execute("DROP INDEX IF EXISTS career_assistant.uq_platform_users_verified_email")
    op.execute("ALTER TABLE career_assistant.platform_users DROP COLUMN IF EXISTS email_verified_at")
    op.execute("ALTER TABLE career_assistant.platform_users DROP COLUMN IF EXISTS email_normalized")
    op.execute("ALTER TABLE career_assistant.platform_users DROP COLUMN IF EXISTS email")
