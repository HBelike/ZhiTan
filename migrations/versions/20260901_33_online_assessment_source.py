"""允许面经记录标记为线上笔试助手来源。"""

from alembic import op


revision = "20260901_33"
down_revision = "20260830_32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE career_assistant.interview_experiences
        DROP CONSTRAINT IF EXISTS interview_experiences_source_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE career_assistant.interview_experiences
        ADD CONSTRAINT interview_experiences_source_type_check CHECK (
            source_type IN ('manual_upload', 'manual_text', 'public_url',
                            'authenticated_session', 'official_api', 'online_assessment')
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE career_assistant.interview_experiences
        SET source_type = 'manual_text'
        WHERE source_type = 'online_assessment'
        """
    )
    op.execute(
        """
        ALTER TABLE career_assistant.interview_experiences
        DROP CONSTRAINT IF EXISTS interview_experiences_source_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE career_assistant.interview_experiences
        ADD CONSTRAINT interview_experiences_source_type_check CHECK (
            source_type IN ('manual_upload', 'manual_text', 'public_url',
                            'authenticated_session', 'official_api')
        )
        """
    )
