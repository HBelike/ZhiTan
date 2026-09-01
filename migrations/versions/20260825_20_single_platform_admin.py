"""收敛平台角色，并限制平台最多存在一个管理员。"""

from __future__ import annotations

from alembic import op


revision = "20260825_20"
down_revision = "20260823_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """把旧角色归一化为 user/admin，并按既有角色保留一个管理员。"""

    op.execute(
        """
        ALTER TABLE career_assistant.platform_users
        DROP CONSTRAINT platform_users_role_check
        """
    )
    op.execute(
        """
        UPDATE career_assistant.platform_users
        SET role = 'user', updated_at = NOW()
        WHERE role <> 'admin'
        """
    )
    op.execute(
        """
        WITH ranked_admins AS (
            SELECT id,
                   ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS position
            FROM career_assistant.platform_users
            WHERE role = 'admin'
        )
        UPDATE career_assistant.platform_users AS users
        SET role = 'user', updated_at = NOW()
        FROM ranked_admins
        WHERE users.id = ranked_admins.id
          AND ranked_admins.position > 1
        """
    )
    op.execute(
        """
        ALTER TABLE career_assistant.platform_users
        ADD CONSTRAINT platform_users_role_check
        CHECK (role IN ('user', 'admin'))
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_platform_users_single_admin
        ON career_assistant.platform_users (role)
        WHERE role = 'admin'
        """
    )


def downgrade() -> None:
    """恢复旧三级角色约束；已归一化普通账号统一回退为 viewer。"""

    op.execute("DROP INDEX IF EXISTS career_assistant.uq_platform_users_single_admin")
    op.execute(
        """
        ALTER TABLE career_assistant.platform_users
        DROP CONSTRAINT platform_users_role_check
        """
    )
    op.execute(
        """
        UPDATE career_assistant.platform_users
        SET role = 'viewer', updated_at = NOW()
        WHERE role = 'user'
        """
    )
    op.execute(
        """
        ALTER TABLE career_assistant.platform_users
        ADD CONSTRAINT platform_users_role_check
        CHECK (role IN ('viewer', 'operator', 'admin'))
        """
    )
