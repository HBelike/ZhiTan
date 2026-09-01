"""增加面经公开资料发现任务和候选资料队列。

该迁移把“关键词检索/页面提取”和“最终面经入库”拆成两个可观测阶段：候选资料
必须先被用户选择，再转换为 Markdown 面经并进入 RAG 索引。表中不保存平台账号、
密码、Cookie、网页原始 HTML 或上传原件。
"""

from __future__ import annotations

from alembic import op


revision = "20260809_05"
down_revision = "20260809_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建采集任务和候选网页的轻量元数据表。"""

    op.execute(
        """
        CREATE TABLE career_assistant.interview_collection_jobs (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            platform_key TEXT NOT NULL,
            keyword TEXT NOT NULL,
            requested_limit INTEGER NOT NULL CHECK (requested_limit BETWEEN 1 AND 50),
            connector_kind TEXT NOT NULL CHECK (
                connector_kind IN ('public_api', 'user_authorized_browser',
                                  'url_import', 'manual_text')
            ),
            status TEXT NOT NULL DEFAULT 'queued' CHECK (
                status IN ('queued', 'running', 'needs_user_interaction',
                           'succeeded', 'failed', 'cancelled')
            ),
            policy_decision TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.interview_collection_candidates (
            id UUID PRIMARY KEY,
            collection_job_id UUID NOT NULL
                REFERENCES career_assistant.interview_collection_jobs(id) ON DELETE CASCADE,
            source_url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            source_platform TEXT NOT NULL,
            title TEXT,
            snippet TEXT,
            published_at TIMESTAMPTZ,
            extracted_markdown TEXT,
            content_hash TEXT,
            status TEXT NOT NULL DEFAULT 'discovered' CHECK (
                status IN ('discovered', 'fetched', 'empty', 'blocked',
                           'selected', 'imported', 'failed')
            ),
            error_code TEXT,
            error_message TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (collection_job_id, canonical_url)
        )
        """,
    )
    op.execute(
        "CREATE INDEX idx_interview_collection_jobs_status_created "
        "ON career_assistant.interview_collection_jobs "
        "(organization_id, status, created_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_interview_collection_candidates_status "
        "ON career_assistant.interview_collection_candidates "
        "(collection_job_id, status, created_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_interview_collection_candidates_platform_url "
        "ON career_assistant.interview_collection_candidates "
        "(source_platform, canonical_url)",
    )


def downgrade() -> None:
    """回滚采集队列表，不影响已入库的面经与 RAG 索引。"""

    op.execute("DROP TABLE IF EXISTS career_assistant.interview_collection_candidates")
    op.execute("DROP TABLE IF EXISTS career_assistant.interview_collection_jobs")
