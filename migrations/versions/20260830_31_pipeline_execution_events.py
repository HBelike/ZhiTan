"""为管理员手动工作流保存可续传的结构化日志事件。"""

from __future__ import annotations

from alembic import op


revision = "20260830_31"
down_revision = "20260828_30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增按工作流运行与事件游标查询的日志事件表。"""

    op.execute(
        """
        CREATE TABLE career_assistant.pipeline_execution_events (
            id BIGSERIAL PRIMARY KEY,
            execution_request_id UUID NOT NULL
                REFERENCES career_assistant.pipeline_execution_requests(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            level TEXT NOT NULL,
            task_name TEXT,
            task_run_id TEXT,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX ix_pipeline_execution_events_request_cursor
        ON career_assistant.pipeline_execution_events (execution_request_id, id)
        """,
    )


def downgrade() -> None:
    """移除工作流结构化日志事件。"""

    op.execute("DROP TABLE IF EXISTS career_assistant.pipeline_execution_events")
