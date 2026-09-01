"""创建面经库、采集任务与 RAG 索引的独立数据结构。

面经库只持久化经解析、清洗后的 Markdown 和检索索引，不保存用户上传的原始
图片、PDF、Word 或 Excel 文件。第三方平台登录态也只允许保存到外部 Secret
Store 的引用，禁止把账号、密码、Cookie 明文写进 PostgreSQL。
"""

from __future__ import annotations

from alembic import op


revision = "20260809_04"
down_revision = "20260806_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建面经树、入库任务、可追溯切片及检索反馈表。"""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        CREATE TABLE career_assistant.interview_companies (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, normalized_name)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.interview_experiences (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            company_id UUID NOT NULL
                REFERENCES career_assistant.interview_companies(id) ON DELETE CASCADE,
            job_name TEXT NOT NULL,
            role_name TEXT NOT NULL,
            normalized_role_name TEXT NOT NULL,
            interview_date DATE,
            source_type TEXT NOT NULL CHECK (
                source_type IN ('manual_upload', 'manual_text', 'public_url',
                                'authenticated_session', 'official_api')
            ),
            source_platform TEXT,
            source_url TEXT,
            source_content_hash TEXT NOT NULL,
            markdown_content TEXT NOT NULL,
            normalized_markdown TEXT NOT NULL,
            summary_text TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'parsed' CHECK (
                status IN ('queued', 'parsing', 'parsed', 'indexing', 'indexed', 'failed')
            ),
            failure_code TEXT,
            failure_message TEXT,
            chunking_version TEXT,
            indexed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, company_id, job_name)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.interview_ingestion_jobs (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            experience_id UUID
                REFERENCES career_assistant.interview_experiences(id) ON DELETE SET NULL,
            trigger_type TEXT NOT NULL CHECK (
                trigger_type IN ('manual_upload', 'manual_url', 'scheduled_scan', 'api_sync')
            ),
            source_url TEXT,
            source_platform TEXT,
            status TEXT NOT NULL DEFAULT 'queued' CHECK (
                status IN ('queued', 'fetching', 'parsing', 'normalizing', 'indexing',
                                'succeeded', 'failed', 'cancelled')
            ),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
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
        CREATE TABLE career_assistant.interview_chunks (
            id UUID PRIMARY KEY,
            experience_id UUID NOT NULL
                REFERENCES career_assistant.interview_experiences(id) ON DELETE CASCADE,
            parent_heading TEXT,
            heading_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
            content_text TEXT NOT NULL,
            contextual_content TEXT NOT NULL,
            token_estimate INTEGER NOT NULL CHECK (token_estimate > 0),
            chunk_hash TEXT NOT NULL,
            chunking_version TEXT NOT NULL,
            embedding_model TEXT,
            embedding vector(1024),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (experience_id, chunk_index),
            UNIQUE (experience_id, chunk_hash)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.interview_source_connections (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            platform_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            auth_kind TEXT NOT NULL CHECK (
                auth_kind IN ('official_api', 'authenticated_session', 'manual')
            ),
            secret_reference TEXT,
            session_state_reference TEXT,
            status TEXT NOT NULL DEFAULT 'unconfigured' CHECK (
                status IN ('unconfigured', 'available', 'expired', 'disabled', 'failed')
            ),
            last_verified_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, platform_key, display_name)
        )
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.interview_retrieval_feedback (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            conversation_id UUID
                REFERENCES career_assistant.conversations(id) ON DELETE SET NULL,
            experience_id UUID
                REFERENCES career_assistant.interview_experiences(id) ON DELETE SET NULL,
            query_text TEXT NOT NULL,
            retrieval_strategy TEXT NOT NULL,
            rank_position INTEGER CHECK (rank_position > 0),
            useful BOOLEAN,
            feedback_text TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )

    op.execute(
        "CREATE INDEX idx_interview_companies_organization_name "
        "ON career_assistant.interview_companies (organization_id, display_name)",
    )
    op.execute(
        "CREATE INDEX idx_interview_experiences_company_date "
        "ON career_assistant.interview_experiences (company_id, interview_date DESC, created_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_interview_experiences_search "
        "ON career_assistant.interview_experiences USING GIN "
        "(normalized_markdown gin_trgm_ops)",
    )
    op.execute(
        "CREATE INDEX idx_interview_jobs_status_created "
        "ON career_assistant.interview_ingestion_jobs (organization_id, status, created_at DESC)",
    )
    op.execute(
        "CREATE INDEX idx_interview_chunks_experience_index "
        "ON career_assistant.interview_chunks (experience_id, chunk_index)",
    )
    op.execute(
        "CREATE INDEX idx_interview_chunks_context_search "
        "ON career_assistant.interview_chunks USING GIN "
        "(contextual_content gin_trgm_ops)",
    )
    op.execute(
        "CREATE INDEX idx_interview_chunks_embedding "
        "ON career_assistant.interview_chunks USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL",
    )


def downgrade() -> None:
    """回滚面经库独立表，不影响既有求职会话和公众号模块。"""

    op.execute("DROP TABLE IF EXISTS career_assistant.interview_retrieval_feedback")
    op.execute("DROP TABLE IF EXISTS career_assistant.interview_source_connections")
    op.execute("DROP TABLE IF EXISTS career_assistant.interview_chunks")
    op.execute("DROP TABLE IF EXISTS career_assistant.interview_ingestion_jobs")
    op.execute("DROP TABLE IF EXISTS career_assistant.interview_experiences")
    op.execute("DROP TABLE IF EXISTS career_assistant.interview_companies")
