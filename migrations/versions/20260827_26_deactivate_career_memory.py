"""停用求职记忆，并允许新会话不再绑定职业空间。"""

from __future__ import annotations

from alembic import op


revision = "20260827_26"
down_revision = "20260827_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保留历史职业空间和记忆数据，只解除新会话的强制绑定。"""

    op.execute(
        "ALTER TABLE career_assistant.conversations "
        "ALTER COLUMN career_space_id DROP NOT NULL",
    )


def downgrade() -> None:
    """恢复旧约束前，为升级后创建的会话补齐默认职业空间。"""

    op.execute(
        """
        INSERT INTO career_assistant.career_spaces
          (id, organization_id, actor_id, name, normalized_name, is_default)
        SELECT md5(actor.organization_id::text || ':' || actor.id::text || ':' || 'career-space')::uuid,
               actor.organization_id, actor.id, '默认求职方向', '默认求职方向', TRUE
        FROM career_assistant.actors AS actor
        WHERE NOT EXISTS (
          SELECT 1
          FROM career_assistant.career_spaces AS space
          WHERE space.organization_id = actor.organization_id
            AND space.actor_id = actor.id
            AND space.is_default
        )
        """,
    )
    op.execute(
        """
        UPDATE career_assistant.conversations AS conversation
        SET career_space_id = space.id
        FROM career_assistant.career_spaces AS space
        WHERE conversation.career_space_id IS NULL
          AND space.organization_id = conversation.organization_id
          AND space.actor_id = conversation.actor_id
          AND space.is_default
        """,
    )
    op.execute(
        "ALTER TABLE career_assistant.conversations "
        "ALTER COLUMN career_space_id SET NOT NULL",
    )
