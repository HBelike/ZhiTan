"""简历助手文件路径字段兼容新格式。"""

from __future__ import annotations

from alembic import op


revision = "20260817_11"
down_revision = "20260817_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "ADD COLUMN original_preview_pdf_path TEXT",
    )
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "RENAME COLUMN optimized_file_path TO optimized_text_path",
    )
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "ADD COLUMN optimized_docx_path TEXT",
    )
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "ADD COLUMN optimized_pdf_path TEXT",
    )
    op.execute(
        "UPDATE career_assistant.resume_optimization_records "
        "SET original_preview_pdf_path = original_file_path",
    )
    op.execute(
        "UPDATE career_assistant.resume_optimization_records "
        "SET optimized_text_path = COALESCE(optimized_text_path, '')",
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "DROP COLUMN IF EXISTS optimized_pdf_path",
    )
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "DROP COLUMN IF EXISTS optimized_docx_path",
    )
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "RENAME COLUMN optimized_text_path TO optimized_file_path",
    )
    op.execute(
        "ALTER TABLE career_assistant.resume_optimization_records "
        "DROP COLUMN IF EXISTS original_preview_pdf_path",
    )
