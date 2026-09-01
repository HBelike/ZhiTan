"""增加全网公开面经永久抓取账本与来源别名。"""

from __future__ import annotations

from alembic import op


revision = "20260827_25"
down_revision = "20260826_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建组织隔离的 URL/正文去重账本和可追溯来源表。"""

    op.execute(
        """
        CREATE TABLE career_assistant.interview_web_documents (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            canonical_url TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_platform TEXT NOT NULL,
            title TEXT,
            search_snippet TEXT,
            status TEXT NOT NULL DEFAULT 'discovered' CHECK (
                status IN ('discovered', 'fetching', 'fetched', 'duplicate',
                           'imported', 'filtered', 'retryable_failed', 'permanent_failed')
            ),
            normalized_markdown TEXT,
            content_hash TEXT,
            imported_experience_id UUID
                REFERENCES career_assistant.interview_experiences(id) ON DELETE SET NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            error_code TEXT,
            error_message TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_fetched_at TIMESTAMPTZ,
            next_retry_at TIMESTAMPTZ,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, canonical_url),
            CHECK (content_hash IS NULL OR length(content_hash) = 64)
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_interview_web_documents_content_hash
        ON career_assistant.interview_web_documents (organization_id, content_hash)
        WHERE content_hash IS NOT NULL
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_interview_web_documents_retry
        ON career_assistant.interview_web_documents (organization_id, status, next_retry_at)
        """,
    )
    op.execute(
        """
        CREATE TABLE career_assistant.interview_experience_sources (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL
                REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
            experience_id UUID NOT NULL
                REFERENCES career_assistant.interview_experiences(id) ON DELETE CASCADE,
            canonical_url TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_platform TEXT NOT NULL,
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            discovery_keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (organization_id, canonical_url),
            CHECK (jsonb_typeof(discovery_keywords_json) = 'array')
        )
        """,
    )
    op.execute(
        """
        CREATE INDEX idx_interview_experience_sources_experience
        ON career_assistant.interview_experience_sources
            (organization_id, experience_id, first_seen_at)
        """,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_interview_experience_sources_primary
        ON career_assistant.interview_experience_sources (organization_id, experience_id)
        WHERE is_primary
        """,
    )
    # 历史来源不在迁移中猜测 canonical 规则；原地址作为首次 canonical，后续新发现
    # 地址统一由应用层纯函数规范化。确定性 UUID 使重复执行回填保持幂等。
    op.execute(
        """
        INSERT INTO career_assistant.interview_experience_sources (
            id, organization_id, experience_id, canonical_url, source_url,
            source_platform, is_primary, discovery_keywords_json,
            first_seen_at, last_seen_at, created_at, updated_at
        )
        SELECT md5(experience.organization_id::text || ':' || experience.id::text ||
                   '-interview-source')::uuid,
               experience.organization_id,
               experience.id,
               trim(experience.source_url),
               trim(experience.source_url),
               COALESCE(NULLIF(trim(experience.source_platform), ''), '公开网页'),
               TRUE,
               '[]'::jsonb,
               experience.created_at,
               experience.updated_at,
               NOW(),
               NOW()
        FROM career_assistant.interview_experiences AS experience
        WHERE experience.source_url IS NOT NULL
          AND trim(experience.source_url) <> ''
        ON CONFLICT (organization_id, canonical_url) DO NOTHING
        """,
    )


def downgrade() -> None:
    """仅移除新增来源和账本，不删除已入库面经。"""

    op.execute("DROP TABLE IF EXISTS career_assistant.interview_experience_sources")
    op.execute("DROP TABLE IF EXISTS career_assistant.interview_web_documents")
