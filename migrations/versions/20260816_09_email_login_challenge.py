"""允许邮箱验证码挑战用于直接登录。"""

from __future__ import annotations

from alembic import op


revision = "20260816_09"
down_revision = "20260810_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """扩展验证码用途约束，使已验证邮箱可以申请一次性登录验证码。"""

    op.execute(
        """
        ALTER TABLE career_assistant.platform_email_challenges
        DROP CONSTRAINT platform_email_challenges_purpose_check
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.platform_email_challenges
        ADD CONSTRAINT platform_email_challenges_purpose_check
        CHECK (purpose IN ('login', 'register', 'bootstrap', 'bind_email', 'reset_password'))
        """,
    )


def downgrade() -> None:
    """恢复原用途集合；存在登录挑战时拒绝回滚，避免静默删除业务记录。"""

    op.execute(
        """
        ALTER TABLE career_assistant.platform_email_challenges
        DROP CONSTRAINT platform_email_challenges_purpose_check
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.platform_email_challenges
        ADD CONSTRAINT platform_email_challenges_purpose_check
        CHECK (purpose IN ('register', 'bootstrap', 'bind_email', 'reset_password'))
        """,
    )
