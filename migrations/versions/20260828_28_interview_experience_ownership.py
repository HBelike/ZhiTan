"""增加面经创建者与采集任务发起人归属。"""

from __future__ import annotations

from alembic import op


revision = "20260828_28"
down_revision = "20260827_27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """历史记录保持无归属；新记录由应用层写入真实发起人。"""

    op.execute(
        """
        ALTER TABLE career_assistant.interview_experiences
        ADD COLUMN created_by_actor_id UUID
            REFERENCES career_assistant.actors(id) ON DELETE SET NULL
        """,
    )
    op.execute(
        """
        ALTER TABLE career_assistant.interview_collection_jobs
        ADD COLUMN created_by_actor_id UUID
            REFERENCES career_assistant.actors(id) ON DELETE SET NULL
        """,
    )
    op.execute(
        "CREATE INDEX idx_interview_experiences_creator "
        "ON career_assistant.interview_experiences (created_by_actor_id, updated_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_interview_collection_jobs_creator "
        "ON career_assistant.interview_collection_jobs (created_by_actor_id, created_at DESC)",
    )


def downgrade() -> None:
    """移除归属字段，不删除任何面经或采集任务。"""

    op.execute("DROP INDEX IF EXISTS career_assistant.idx_interview_collection_jobs_creator")
    op.execute("DROP INDEX IF EXISTS career_assistant.idx_interview_experiences_creator")
    op.execute(
        "ALTER TABLE career_assistant.interview_collection_jobs "
        "DROP COLUMN IF EXISTS created_by_actor_id",
    )
    op.execute(
        "ALTER TABLE career_assistant.interview_experiences "
        "DROP COLUMN IF EXISTS created_by_actor_id",
    )
