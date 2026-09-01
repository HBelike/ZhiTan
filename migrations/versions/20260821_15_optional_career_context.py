"""允许求职会话只绑定简历或只绑定目标岗位。"""

from __future__ import annotations

from alembic import op


revision = "20260821_15"
down_revision = "20260820_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE career_assistant.conversation_context_bindings
            ALTER COLUMN candidate_profile_id DROP NOT NULL,
            ALTER COLUMN target_role_profile_id DROP NOT NULL,
            ADD CONSTRAINT chk_context_binding_has_material
                CHECK (candidate_profile_id IS NOT NULL OR target_role_profile_id IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM career_assistant.conversation_context_bindings
        WHERE candidate_profile_id IS NULL OR target_role_profile_id IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE career_assistant.conversation_context_bindings
            DROP CONSTRAINT IF EXISTS chk_context_binding_has_material,
            ALTER COLUMN candidate_profile_id SET NOT NULL,
            ALTER COLUMN target_role_profile_id SET NOT NULL
        """
    )
