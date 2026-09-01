"""面经库的 PostgreSQL 仓储。

仓储是唯一允许拼写面经库 SQL 的位置。它只写入 Markdown、元数据、任务状态和切片，
不接触原始附件、Cookie 明文或模型 API Key。
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Iterable, Mapping
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.exc import IntegrityError

from src.career_assistant.interview_library.chunking import CHUNKING_VERSION, ChunkDraft
from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    IngestionJobStatus,
    IngestionTriggerType,
    InterviewCollectionCandidateRecord,
    InterviewCollectionJobRecord,
    InterviewChunkRecord,
    InterviewChunkCandidate,
    InterviewCompanyRecord,
    InterviewExperienceRecord,
    InterviewExperienceSourceRecord,
    InterviewExperienceStatus,
    InterviewIngestionJobRecord,
    InterviewSourceType,
    InterviewWebDocumentRecord,
    InterviewWebDocumentStatus,
)
from src.career_assistant.persistence.database import CareerDatabase


class InterviewLibraryRepository:
    """管理公司树、面经正文、切片和入库任务的事务边界。"""

    _MAX_COLLECTION_METADATA_BYTES = 64 * 1024

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def upsert_company(
        self,
        organization_id: UUID,
        display_name: str,
        *,
        aliases: Iterable[str] = (),
    ) -> InterviewCompanyRecord:
        """创建或更新面经树根节点；同一组织内按规范化名称去重。"""

        normalized_display_name = self._normalize_text(display_name, "公司名称", 120)
        normalized_name = self._normalize_key(normalized_display_name)
        normalized_aliases = self._normalize_tags(aliases, maximum_items=20, maximum_length=80)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_companies
                        (id, organization_id, display_name, normalized_name, aliases)
                    VALUES
                        (:id, :organization_id, :display_name, :normalized_name,
                         CAST(:aliases AS jsonb))
                    ON CONFLICT (organization_id, normalized_name) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        aliases = EXCLUDED.aliases,
                        updated_at = NOW()
                    RETURNING id, organization_id, display_name, normalized_name, aliases,
                              created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "display_name": normalized_display_name,
                    "normalized_name": normalized_name,
                    "aliases": json.dumps(normalized_aliases),
                },
            ).mappings().one()
        return self._to_company(row)

    def create_experience(
        self,
        *,
        organization_id: UUID,
        created_by_actor_id: UUID | None,
        company_id: UUID,
        job_name: str,
        role_name: str,
        interview_date: date | None,
        source_type: InterviewSourceType,
        markdown_content: str,
        source_content_hash: str,
        normalized_markdown: str | None = None,
        source_platform: str | None = None,
        source_url: str | None = None,
        summary_text: str | None = None,
        tags: Iterable[str] = (),
        status: InterviewExperienceStatus = InterviewExperienceStatus.PARSED,
        can_manage_all: bool = False,
    ) -> InterviewExperienceRecord:
        """写入一份已解析的面经正文，拒绝无归属公司或空 Markdown。"""

        normalized_job_name = self._normalize_text(job_name, "面经名称", 220)
        normalized_role_name = self._normalize_text(role_name, "岗位名称", 160)
        normalized_content = self._normalize_markdown(markdown_content, "面经 Markdown", 300_000)
        normalized_hash = self._normalize_hash(source_content_hash)
        normalized_summary = self._normalize_optional_text(summary_text, "面经摘要", 12_000)
        normalized_platform = self._normalize_optional_text(source_platform, "来源平台", 80)
        normalized_url = self._normalize_optional_url(source_url)
        normalized_markdown_value = self._normalize_optional_markdown(
            normalized_markdown,
            "规范化 Markdown",
            300_000,
        ) or normalized_content
        normalized_tags = self._normalize_tags(tags, maximum_items=30, maximum_length=60)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_experiences (
                        id, organization_id, created_by_actor_id, company_id, job_name, role_name,
                        normalized_role_name, interview_date, source_type, source_platform,
                        source_url, source_content_hash, markdown_content, normalized_markdown,
                        summary_text, tags, status
                    )
                    SELECT
                        :id, :organization_id, :created_by_actor_id, company.id, :job_name, :role_name,
                        :normalized_role_name, :interview_date, :source_type,
                        :source_platform, :source_url, :source_content_hash,
                        :markdown_content, :normalized_markdown, :summary_text,
                        CAST(:tags AS jsonb), :status
                    FROM career_assistant.interview_companies AS company
                    WHERE company.id = :company_id
                      AND company.organization_id = :organization_id
                    ON CONFLICT (organization_id, company_id, job_name) DO UPDATE
                    SET role_name = EXCLUDED.role_name,
                        normalized_role_name = EXCLUDED.normalized_role_name,
                        interview_date = EXCLUDED.interview_date,
                        source_type = EXCLUDED.source_type,
                        source_platform = EXCLUDED.source_platform,
                        source_url = EXCLUDED.source_url,
                        source_content_hash = EXCLUDED.source_content_hash,
                        markdown_content = EXCLUDED.markdown_content,
                        normalized_markdown = EXCLUDED.normalized_markdown,
                        summary_text = EXCLUDED.summary_text,
                        tags = EXCLUDED.tags,
                        status = 'parsed',
                        failure_code = NULL,
                        failure_message = NULL,
                        chunking_version = NULL,
                        indexed_at = NULL,
                        updated_at = NOW()
                    WHERE interview_experiences.created_by_actor_id = :created_by_actor_id
                       OR :can_manage_all
                    RETURNING id, organization_id, created_by_actor_id, company_id, job_name, role_name,
                              normalized_role_name, interview_date, source_type,
                              source_platform, source_url, source_content_hash,
                              markdown_content, normalized_markdown, summary_text, tags,
                              status, chunking_version, indexed_at, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "created_by_actor_id": created_by_actor_id,
                    "can_manage_all": can_manage_all,
                    "company_id": company_id,
                    "job_name": normalized_job_name,
                    "role_name": normalized_role_name,
                    "normalized_role_name": self._normalize_key(normalized_role_name),
                    "interview_date": interview_date,
                    "source_type": source_type.value,
                    "source_platform": normalized_platform,
                    "source_url": normalized_url,
                    "source_content_hash": normalized_hash,
                    "markdown_content": normalized_content,
                    "normalized_markdown": normalized_markdown_value,
                    "summary_text": normalized_summary,
                    "tags": json.dumps(normalized_tags),
                    "status": status.value,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("公司不存在，或同名面经不属于当前用户")
        return self._to_experience(row, company_name=self._company_name_for(company_id, organization_id))

    def update_experience_markdown(
        self,
        organization_id: UUID,
        experience_id: UUID,
        *,
        actor_id: UUID,
        can_manage_all: bool,
        company_id: UUID,
        role_name: str,
        markdown_content: str,
        normalized_markdown: str,
        source_content_hash: str,
        summary_text: str | None,
        tags: Iterable[str],
    ) -> InterviewExperienceRecord:
        """保存人工编辑后的归属与 Markdown，并标记为待重新建立索引。"""

        normalized_role_name = self._normalize_text(role_name, "岗位名称", 160)
        normalized_content = self._normalize_markdown(markdown_content, "面经 Markdown", 300_000)
        normalized_markdown_value = self._normalize_markdown(normalized_markdown, "规范化 Markdown", 300_000)
        normalized_hash = self._normalize_hash(source_content_hash)
        normalized_summary = self._normalize_optional_text(summary_text, "面经摘要", 12_000)
        normalized_tags = self._normalize_tags(tags, maximum_items=30, maximum_length=60)
        try:
            with self._database.transaction() as connection:
                row = connection.execute(
                    text(
                        """
                    UPDATE career_assistant.interview_experiences AS experience
                    SET company_id = :company_id,
                        role_name = :role_name,
                        normalized_role_name = :normalized_role_name,
                        markdown_content = :markdown_content,
                        normalized_markdown = :normalized_markdown,
                        source_content_hash = :source_content_hash,
                        summary_text = :summary_text,
                        tags = CAST(:tags AS jsonb),
                        status = 'parsed',
                        chunking_version = NULL,
                        indexed_at = NULL,
                        updated_at = NOW()
                    WHERE experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                      AND (
                          experience.created_by_actor_id = :actor_id
                          OR :can_manage_all
                      )
                    RETURNING experience.id, experience.organization_id,
                              experience.created_by_actor_id, experience.company_id,
                              experience.job_name, experience.role_name,
                              experience.normalized_role_name, experience.interview_date,
                              experience.source_type, experience.source_platform,
                              experience.source_url, experience.source_content_hash,
                              experience.markdown_content, experience.normalized_markdown,
                              experience.summary_text, experience.tags, experience.status,
                              experience.chunking_version, experience.indexed_at,
                              experience.created_at, experience.updated_at
                    """,
                    ),
                    {
                        "experience_id": experience_id,
                        "organization_id": organization_id,
                        "actor_id": actor_id,
                        "can_manage_all": can_manage_all,
                        "company_id": company_id,
                        "role_name": normalized_role_name,
                        "normalized_role_name": self._normalize_key(normalized_role_name),
                        "markdown_content": normalized_content,
                        "normalized_markdown": normalized_markdown_value,
                        "source_content_hash": normalized_hash,
                        "summary_text": normalized_summary,
                        "tags": json.dumps(normalized_tags),
                    },
                ).mappings().one_or_none()
        except IntegrityError as exc:
            raise ValueError("目标公司下已存在同名面经，不能重复归档") from exc
        if row is None:
            raise LookupError("面经不存在或无访问权限")
        return self._to_experience(row, company_name=self._company_name_for(row["company_id"], organization_id))

    def delete_experience(
        self,
        experience_id: UUID,
        *,
        actor_id: UUID,
        can_manage_all: bool,
    ) -> bool:
        """仅创建者或管理员可永久删除；关联记录由外键规则同步清理。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    DELETE FROM career_assistant.interview_experiences
                    WHERE id = :experience_id
                      AND (
                          created_by_actor_id = :actor_id
                          OR :can_manage_all
                      )
                    RETURNING id
                    """,
                ),
                {
                    "experience_id": experience_id,
                    "actor_id": actor_id,
                    "can_manage_all": can_manage_all,
                },
            ).mappings().one_or_none()
        return row is not None

    def replace_chunks(
        self,
        organization_id: UUID,
        experience_id: UUID,
        chunks: Iterable[ChunkDraft],
        *,
        embedding_model: str | None = None,
    ) -> list[InterviewChunkRecord]:
        """原子替换一份面经的文本切片；向量由后续 Embedding Job 回填。"""

        normalized_chunks = list(chunks)
        if not normalized_chunks:
            raise ValueError("至少需要一条面经切片")
        if len(normalized_chunks) > 2_000:
            raise ValueError("单份面经切片数量不能超过 2000")
        chunking_version = CHUNKING_VERSION
        normalized_embedding_model = self._normalize_optional_text(embedding_model, "Embedding 模型", 200)
        with self._database.transaction() as connection:
            exists = connection.execute(
                text(
                    """
                    SELECT 1
                    FROM career_assistant.interview_experiences
                    WHERE id = :experience_id
                      AND organization_id = :organization_id
                    FOR UPDATE
                    """,
                ),
                {"experience_id": experience_id, "organization_id": organization_id},
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError("面经不存在或无访问权限")
            connection.execute(
                text("DELETE FROM career_assistant.interview_chunks WHERE experience_id = :experience_id"),
                {"experience_id": experience_id},
            )
            inserted_rows: list[RowMapping] = []
            for chunk in normalized_chunks:
                row = connection.execute(
                    text(
                        """
                        INSERT INTO career_assistant.interview_chunks (
                            id, experience_id, parent_heading, heading_path, chunk_index,
                            content_text, contextual_content, token_estimate, chunk_hash,
                            chunking_version, embedding_model
                        ) VALUES (
                            :id, :experience_id, :parent_heading, :heading_path,
                            :chunk_index, :content_text, :contextual_content,
                            :token_estimate, :chunk_hash, :chunking_version, :embedding_model
                        )
                        RETURNING id, experience_id, parent_heading, heading_path, chunk_index,
                                  content_text, contextual_content, token_estimate, chunk_hash,
                                  chunking_version, embedding_model, created_at, updated_at
                        """,
                    ),
                    {
                        "id": uuid4(),
                        "experience_id": experience_id,
                        "parent_heading": chunk.parent_heading,
                        "heading_path": chunk.heading_path,
                        "chunk_index": chunk.chunk_index,
                        "content_text": chunk.content_text,
                        "contextual_content": chunk.contextual_content,
                        "token_estimate": chunk.token_estimate,
                        "chunk_hash": chunk.chunk_hash,
                        "chunking_version": chunking_version,
                        "embedding_model": normalized_embedding_model,
                    },
                ).mappings().one()
                inserted_rows.append(row)
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_experiences
                    SET status = 'indexed',
                        chunking_version = :chunking_version,
                        indexed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :experience_id
                    """,
                ),
                {"experience_id": experience_id, "chunking_version": chunking_version},
            )
        return [self._to_chunk(row) for row in inserted_rows]

    def list_chunks(
        self,
        organization_id: UUID,
        experience_id: UUID,
    ) -> list[InterviewChunkRecord]:
        """读取一份面经的现有切片，供异步或同步向量索引器重建 embedding。

        切片正文已经是可持久化的 Markdown 派生文本，因此该方法不访问也不恢复任何
        原始附件；多个并发索引任务只会读取同一稳定快照。
        """

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    WHERE chunk.experience_id = :experience_id
                      AND experience.organization_id = :organization_id
                    ORDER BY chunk.chunk_index ASC
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "experience_id": experience_id,
                },
            ).mappings().all()
        return [self._to_chunk(row) for row in rows]

    def replace_chunk_embeddings(
        self,
        organization_id: UUID,
        *,
        embedding_model: str,
        embeddings_by_chunk_id: dict[UUID, list[float]],
        expected_dimensions: int,
    ) -> int:
        """事务性写入一批向量，并验证每条向量维度与数值边界。

        ``pgvector`` 不接受维度不一致的数组。先在应用层拦截错误，避免一批索引任务
        因单个上游模型异常输出而留下部分写入；数据库事务会在任一失败时整体回滚。
        """

        normalized_model = self._normalize_text(embedding_model, "Embedding 模型", 200)
        if not embeddings_by_chunk_id:
            return 0
        if not 1 <= expected_dimensions <= 8_192:
            raise ValueError("Embedding 维度必须在 1 到 8192 之间")

        prepared_embeddings: list[tuple[UUID, str]] = []
        for chunk_id, vector in embeddings_by_chunk_id.items():
            if len(vector) != expected_dimensions:
                raise ValueError(
                    f"Embedding 维度不匹配：期望 {expected_dimensions}，实际 {len(vector)}",
                )
            if not all(isinstance(item, int | float) and math.isfinite(float(item)) for item in vector):
                raise ValueError("Embedding 包含非法数值")
            vector_literal = "[" + ",".join(f"{float(item):.9g}" for item in vector) + "]"
            prepared_embeddings.append((chunk_id, vector_literal))

        updated_count = 0
        with self._database.transaction() as connection:
            for chunk_id, vector_literal in prepared_embeddings:
                result = connection.execute(
                    text(
                        """
                        UPDATE career_assistant.interview_chunks AS chunk
                        SET embedding = CAST(:embedding AS vector),
                            embedding_model = :embedding_model,
                            updated_at = NOW()
                        FROM career_assistant.interview_experiences AS experience
                        WHERE chunk.id = :chunk_id
                          AND experience.id = chunk.experience_id
                          AND experience.organization_id = :organization_id
                        """,
                    ),
                    {
                        "chunk_id": chunk_id,
                        "embedding": vector_literal,
                        "embedding_model": normalized_model,
                        "organization_id": organization_id,
                    },
                )
                updated_count += result.rowcount
        return updated_count

    def search_lexical_chunks(
        self,
        organization_id: UUID,
        query: str,
        *,
        limit: int = 24,
        experience_ids: tuple[UUID, ...] = (),
    ) -> list[InterviewChunkCandidate]:
        """用 PostgreSQL trigram 做无需模型凭证的中文关键词兜底召回。

        该路径对公司名、岗位、标题和正文同时检索。向量服务未配置、限流或维护时，
        面经库仍然可用，是上线后降级策略的一部分。
        """

        normalized_query = self._normalize_text(query, "检索关键词", 240).lower()
        if not 1 <= limit <= 100:
            raise ValueError("检索数量必须在 1 到 100 之间")
        scope_clause, scope_parameters = self._experience_scope_filter(experience_ids)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at,
                           company.display_name AS company_name,
                           experience.job_name, experience.role_name,
                           experience.interview_date, experience.source_url,
                           GREATEST(
                               similarity(LOWER(chunk.contextual_content), :query),
                               similarity(LOWER(company.display_name), :query),
                               similarity(LOWER(experience.job_name), :query),
                               similarity(LOWER(experience.role_name), :query)
                           ) AS lexical_score
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE TRUE
                      {scope_clause}
                      AND (
                          LOWER(chunk.contextual_content) LIKE :like_query
                          OR LOWER(company.display_name) LIKE :like_query
                          OR LOWER(experience.job_name) LIKE :like_query
                          OR LOWER(experience.role_name) LIKE :like_query
                          OR similarity(LOWER(chunk.contextual_content), :query) >= 0.06
                          OR similarity(LOWER(experience.job_name), :query) >= 0.06
                      )
                    ORDER BY lexical_score DESC, chunk.updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "query": normalized_query,
                    "like_query": f"%{normalized_query}%",
                    "limit": limit,
                    **scope_parameters,
                },
            ).mappings().all()
        return [self._to_candidate(row, lexical_score=float(row["lexical_score"])) for row in rows]

    def search_semantic_chunks(
        self,
        organization_id: UUID,
        query_embedding: list[float],
        *,
        expected_dimensions: int,
        limit: int = 24,
        experience_ids: tuple[UUID, ...] = (),
    ) -> list[InterviewChunkCandidate]:
        """在已完成向量化的切片中执行余弦近邻召回。"""

        if len(query_embedding) != expected_dimensions:
            raise ValueError("查询向量维度与面经索引不一致")
        if not all(isinstance(item, int | float) and math.isfinite(float(item)) for item in query_embedding):
            raise ValueError("查询向量包含非法数值")
        if not 1 <= limit <= 100:
            raise ValueError("检索数量必须在 1 到 100 之间")
        vector_literal = "[" + ",".join(f"{float(item):.9g}" for item in query_embedding) + "]"
        scope_clause, scope_parameters = self._experience_scope_filter(experience_ids)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at,
                           company.display_name AS company_name,
                           experience.job_name, experience.role_name,
                           experience.interview_date, experience.source_url,
                           (1 - (chunk.embedding <=> CAST(:query_embedding AS vector)))
                               AS semantic_score
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE chunk.embedding IS NOT NULL
                      {scope_clause}
                    ORDER BY chunk.embedding <=> CAST(:query_embedding AS vector) ASC,
                             chunk.updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "query_embedding": vector_literal,
                    "limit": limit,
                    **scope_parameters,
                },
            ).mappings().all()
        return [self._to_candidate(row, semantic_score=float(row["semantic_score"])) for row in rows]

    def list_chunks_for_experiences(
        self,
        organization_id: UUID,
        experience_ids: tuple[UUID, ...],
        *,
        limit: int = 12,
    ) -> list[InterviewChunkCandidate]:
        """为用户显式选择的面经提供稳定兜底证据。

        当关键词或向量召回均未命中时，不能因为标题写法差异而让 ``@面经`` 失效。
        此方法只返回已授权资料的前若干结构化切片，不参与全库检索排序。
        """

        if not experience_ids:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("检索数量必须在 1 到 100 之间")
        scope_clause, scope_parameters = self._experience_scope_filter(experience_ids)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at,
                           company.display_name AS company_name,
                           experience.job_name, experience.role_name,
                           experience.interview_date, experience.source_url
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE TRUE
                      {scope_clause}
                    ORDER BY experience.updated_at DESC, chunk.chunk_index ASC
                    LIMIT :limit
                    """,
                ),
                {
                    "limit": limit,
                    **scope_parameters,
                },
            ).mappings().all()
        return [self._to_candidate(row) for row in rows]

    def create_ingestion_job(
        self,
        *,
        organization_id: UUID,
        trigger_type: IngestionTriggerType,
        source_url: str | None = None,
        source_platform: str | None = None,
    ) -> InterviewIngestionJobRecord:
        """为上传、URL 或未来扫描器创建可追踪任务；不带原始文件内容。"""

        normalized_url = self._normalize_optional_url(source_url)
        normalized_platform = self._normalize_optional_text(source_platform, "来源平台", 80)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_ingestion_jobs (
                        id, organization_id, trigger_type, source_url, source_platform
                    ) VALUES (
                        :id, :organization_id, :trigger_type, :source_url, :source_platform
                    )
                    RETURNING id, organization_id, experience_id, trigger_type, source_url,
                              source_platform, status, attempt_count, error_code,
                              error_message, started_at, completed_at, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "trigger_type": trigger_type.value,
                    "source_url": normalized_url,
                    "source_platform": normalized_platform,
                },
            ).mappings().one()
        return self._to_ingestion_job(row)

    def create_collection_job(
        self,
        *,
        organization_id: UUID,
        created_by_actor_id: UUID | None,
        platform_key: str,
        keyword: str,
        requested_limit: int,
        connector_kind: CollectionConnectorKind,
        policy_decision: str,
        metadata_json: Mapping[str, object] | None = None,
    ) -> InterviewCollectionJobRecord:
        """创建资料发现任务，不执行外部抓取也不保存任何第三方登录凭证。"""

        normalized_platform = self._normalize_text(platform_key, "平台标识", 50).lower()
        # 关键词任务通常很短，但用户主动粘贴的公开 URL 最长可达 2000 字符；
        # 该列为 PostgreSQL TEXT，不能因为复用同一任务表而截断可访问的来源链接。
        normalized_keyword = self._normalize_text(keyword, "检索关键词或来源链接", 2_000)
        normalized_policy = self._normalize_text(policy_decision, "采集策略说明", 500)
        normalized_metadata = self._normalize_metadata_json(
            metadata_json,
            "采集任务元数据",
        ) or {}
        if not 1 <= requested_limit <= 50:
            raise ValueError("候选资料数量必须在 1 到 50 之间")

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_collection_jobs (
                        id, organization_id, created_by_actor_id, platform_key, keyword, requested_limit,
                        connector_kind, policy_decision, metadata_json
                    ) VALUES (
                        :id, :organization_id, :created_by_actor_id, :platform_key, :keyword, :requested_limit,
                        :connector_kind, :policy_decision, CAST(:metadata_json AS jsonb)
                    )
                    RETURNING id, organization_id, created_by_actor_id, platform_key, keyword, requested_limit,
                              connector_kind, status, policy_decision, error_code,
                              error_message, metadata_json, started_at, completed_at,
                              created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "created_by_actor_id": created_by_actor_id,
                    "platform_key": normalized_platform,
                    "keyword": normalized_keyword,
                    "requested_limit": requested_limit,
                    "connector_kind": connector_kind.value,
                    "policy_decision": normalized_policy,
                    "metadata_json": self._serialize_metadata_json(normalized_metadata),
                },
            ).mappings().one()
        return self._to_collection_job(row)

    def get_collection_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord | None:
        """读取单个采集任务，强制校验组织边界。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, created_by_actor_id, platform_key, keyword, requested_limit,
                           connector_kind, status, policy_decision, error_code,
                           error_message, metadata_json, started_at, completed_at,
                           created_at, updated_at
                    FROM career_assistant.interview_collection_jobs
                    WHERE id = :job_id AND organization_id = :organization_id
                    """,
                ),
                {"job_id": job_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_collection_job(row) if row is not None else None

    def claim_collection_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord | None:
        """原子抢占一个排队中的采集任务，避免同一任务被多个后台调用重复执行。

        FastAPI ``BackgroundTasks`` 可能因为重试、重复请求或多 worker 部署而重复触发同一个
        ``job_id``。仅允许从 ``queued`` 进入 ``running`` 的调用取得任务；已经被抢占或已终态
        的任务返回 ``None``，调用方应直接退出而不是重新抓取、重新 OCR 或重复入库。
        """

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_collection_jobs
                    SET status = 'running',
                        error_code = NULL,
                        error_message = NULL,
                        started_at = COALESCE(started_at, NOW()),
                        completed_at = NULL,
                        updated_at = NOW()
                    WHERE id = :job_id
                      AND organization_id = :organization_id
                      AND status = 'queued'
                    RETURNING id, organization_id, created_by_actor_id, platform_key, keyword, requested_limit,
                              connector_kind, status, policy_decision, error_code,
                              error_message, metadata_json, started_at, completed_at,
                              created_at, updated_at
                    """,
                ),
                {"job_id": job_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_collection_job(row) if row is not None else None

    def update_collection_job_status(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        status: CollectionJobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata_json: Mapping[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionJobRecord:
        """原子更新采集任务状态，并在结束状态记录完成时间。"""

        normalized_code = self._normalize_optional_text(error_code, "错误代码", 80)
        normalized_message = self._normalize_optional_text(error_message, "错误说明", 1_000)
        normalized_metadata = self._normalize_metadata_json(
            metadata_json,
            "采集任务元数据",
        )
        terminal = status in {
            CollectionJobStatus.SUCCEEDED,
            CollectionJobStatus.FAILED,
            CollectionJobStatus.CANCELLED,
        }
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_collection_jobs
                    SET status = :status,
                        error_code = :error_code,
                        error_message = :error_message,
                        metadata_json = CASE
                            WHEN CAST(:metadata_json AS text) IS NULL THEN metadata_json
                            WHEN :merge_metadata THEN metadata_json || CAST(:metadata_json AS jsonb)
                            ELSE CAST(:metadata_json AS jsonb)
                        END,
                        started_at = CASE
                            WHEN :status = 'running' AND started_at IS NULL THEN NOW()
                            ELSE started_at
                        END,
                        completed_at = CASE WHEN :terminal THEN NOW() ELSE NULL END,
                        updated_at = NOW()
                    WHERE id = :job_id AND organization_id = :organization_id
                    RETURNING id, organization_id, created_by_actor_id, platform_key, keyword, requested_limit,
                              connector_kind, status, policy_decision, error_code,
                              error_message, metadata_json, started_at, completed_at,
                              created_at, updated_at
                    """,
                ),
                {
                    "job_id": job_id,
                    "organization_id": organization_id,
                    "status": status.value,
                    "error_code": normalized_code,
                    "error_message": normalized_message,
                    "metadata_json": self._serialize_metadata_json(normalized_metadata),
                    "merge_metadata": merge_metadata,
                    "terminal": terminal,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("采集任务不存在或无访问权限")
        return self._to_collection_job(row)

    def create_collection_candidate(
        self,
        organization_id: UUID,
        *,
        collection_job_id: UUID,
        source_url: str,
        canonical_url: str | None = None,
        source_platform: str,
        title: str | None = None,
        snippet: str | None = None,
        published_at: datetime | None = None,
        extracted_markdown: str | None = None,
        content_hash: str | None = None,
        status: CollectionCandidateStatus = CollectionCandidateStatus.DISCOVERED,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata_json: Mapping[str, object] | None = None,
    ) -> InterviewCollectionCandidateRecord:
        """保存候选的必要文本与元数据，拒绝写入原始 HTML 或会话凭证。"""

        normalized_source_url = self._normalize_optional_url(source_url)
        normalized_canonical_url = self._normalize_optional_url(canonical_url or source_url)
        if normalized_source_url is None or normalized_canonical_url is None:
            raise ValueError("候选资料必须提供有效的 http 或 https 地址")
        normalized_platform = self._normalize_text(source_platform, "来源平台", 80)
        normalized_title = self._normalize_optional_text(title, "候选标题", 300)
        normalized_snippet = self._normalize_optional_text(snippet, "候选摘要", 4_000)
        if published_at is not None and not isinstance(published_at, datetime):
            raise ValueError("候选发布时间必须是 datetime 或 None")
        normalized_markdown = self._normalize_optional_markdown(
            extracted_markdown,
            "候选正文",
            300_000,
        )
        normalized_hash = self._normalize_hash(content_hash) if content_hash else None
        normalized_metadata = self._normalize_metadata_json(
            metadata_json,
            "候选资料元数据",
        ) or {}

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_collection_candidates (
                        id, collection_job_id, source_url, canonical_url, source_platform,
                        title, snippet, published_at, extracted_markdown, content_hash, status,
                        error_code, error_message, metadata_json
                    )
                    SELECT :id, :collection_job_id, :source_url, :canonical_url,
                           :source_platform, :title, :snippet, :published_at, :extracted_markdown,
                           :content_hash, :status, :error_code, :error_message,
                           CAST(:metadata_json AS jsonb)
                    WHERE EXISTS (
                        SELECT 1 FROM career_assistant.interview_collection_jobs
                        WHERE id = :collection_job_id AND organization_id = :organization_id
                    )
                    ON CONFLICT (collection_job_id, canonical_url) DO UPDATE
                    SET source_url = EXCLUDED.source_url,
                        source_platform = EXCLUDED.source_platform,
                        title = COALESCE(EXCLUDED.title, interview_collection_candidates.title),
                        snippet = COALESCE(EXCLUDED.snippet, interview_collection_candidates.snippet),
                        published_at = COALESCE(
                            EXCLUDED.published_at,
                            interview_collection_candidates.published_at
                        ),
                        extracted_markdown = COALESCE(
                            EXCLUDED.extracted_markdown,
                            interview_collection_candidates.extracted_markdown
                        ),
                        content_hash = COALESCE(
                            EXCLUDED.content_hash,
                            interview_collection_candidates.content_hash
                        ),
                        status = EXCLUDED.status,
                        error_code = EXCLUDED.error_code,
                        error_message = EXCLUDED.error_message,
                        metadata_json = CASE
                            WHEN EXCLUDED.metadata_json = '{}'::jsonb
                                THEN interview_collection_candidates.metadata_json
                            ELSE interview_collection_candidates.metadata_json || EXCLUDED.metadata_json
                        END,
                        updated_at = NOW()
                    RETURNING id, collection_job_id, source_url, canonical_url, source_platform,
                              title, snippet, published_at, extracted_markdown, content_hash,
                              status, error_code, error_message, metadata_json,
                              created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "collection_job_id": collection_job_id,
                    "source_url": normalized_source_url,
                    "canonical_url": normalized_canonical_url,
                    "source_platform": normalized_platform,
                    "title": normalized_title,
                    "snippet": normalized_snippet,
                    "published_at": published_at,
                    "extracted_markdown": normalized_markdown,
                    "content_hash": normalized_hash,
                    "status": status.value,
                    "error_code": self._normalize_optional_text(error_code, "错误代码", 80),
                    "error_message": self._normalize_optional_text(error_message, "错误说明", 1_000),
                    "metadata_json": self._serialize_metadata_json(normalized_metadata),
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("采集任务不存在或无访问权限")
        return self._to_collection_candidate(row)

    def list_collection_candidates(
        self,
        organization_id: UUID,
        collection_job_id: UUID,
    ) -> list[InterviewCollectionCandidateRecord]:
        """列出任务候选项，读取时再次用组织边界隔离。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT candidate.id, candidate.collection_job_id, candidate.source_url,
                           candidate.canonical_url, candidate.source_platform, candidate.title,
                           candidate.snippet, candidate.published_at,
                           candidate.extracted_markdown, candidate.content_hash,
                           candidate.status, candidate.error_code, candidate.error_message,
                           candidate.metadata_json, candidate.created_at, candidate.updated_at
                    FROM career_assistant.interview_collection_candidates AS candidate
                    INNER JOIN career_assistant.interview_collection_jobs AS job
                        ON job.id = candidate.collection_job_id
                    WHERE candidate.collection_job_id = :collection_job_id
                      AND job.organization_id = :organization_id
                    ORDER BY candidate.created_at ASC
                    """,
                ),
                {
                    "collection_job_id": collection_job_id,
                    "organization_id": organization_id,
                },
            ).mappings().all()
        return [self._to_collection_candidate(row) for row in rows]

    def get_collection_candidate(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> InterviewCollectionCandidateRecord | None:
        """读取一个候选正文，为“选择后入库”重新校验归属。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT candidate.id, candidate.collection_job_id, candidate.source_url,
                           candidate.canonical_url, candidate.source_platform, candidate.title,
                           candidate.snippet, candidate.published_at,
                           candidate.extracted_markdown, candidate.content_hash,
                           candidate.status, candidate.error_code, candidate.error_message,
                           candidate.metadata_json, candidate.created_at, candidate.updated_at
                    FROM career_assistant.interview_collection_candidates AS candidate
                    INNER JOIN career_assistant.interview_collection_jobs AS job
                        ON job.id = candidate.collection_job_id
                    WHERE candidate.id = :candidate_id
                      AND job.organization_id = :organization_id
                    """,
                ),
                {"candidate_id": candidate_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_collection_candidate(row) if row is not None else None

    def get_collection_candidate_by_canonical_url(
        self,
        organization_id: UUID,
        collection_job_id: UUID,
        canonical_url: str,
    ) -> InterviewCollectionCandidateRecord | None:
        """按任务和规范来源读取候选，供浏览器采集恢复时跳过已完成项。"""

        normalized_url = self._normalize_optional_url(canonical_url)
        if normalized_url is None:
            raise ValueError("候选资料必须提供有效的规范来源地址")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT candidate.id, candidate.collection_job_id, candidate.source_url,
                           candidate.canonical_url, candidate.source_platform, candidate.title,
                           candidate.snippet, candidate.published_at,
                           candidate.extracted_markdown, candidate.content_hash,
                           candidate.status, candidate.error_code, candidate.error_message,
                           candidate.metadata_json, candidate.created_at, candidate.updated_at
                    FROM career_assistant.interview_collection_candidates AS candidate
                    INNER JOIN career_assistant.interview_collection_jobs AS job
                        ON job.id = candidate.collection_job_id
                    WHERE candidate.collection_job_id = :collection_job_id
                      AND candidate.canonical_url = :canonical_url
                      AND job.organization_id = :organization_id
                    """,
                ),
                {
                    "collection_job_id": collection_job_id,
                    "canonical_url": normalized_url,
                    "organization_id": organization_id,
                },
            ).mappings().one_or_none()
        return self._to_collection_candidate(row) if row is not None else None

    def update_collection_candidate(
        self,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        title: str | None = None,
        snippet: str | None = None,
        published_at: datetime | None = None,
        extracted_markdown: str | None = None,
        content_hash: str | None = None,
        status: CollectionCandidateStatus | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        clear_errors: bool = False,
        metadata_json: Mapping[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionCandidateRecord:
        """更新候选的提取结果和轻量元数据，空值默认不覆盖既有内容。

        远程图文解析会分多步补齐标题、正文、发布时间、哈希和提取元数据；这些
        字段必须在同一组织边界内原子更新。需要清理上一次失败信息时，调用方应
        显式传入 ``clear_errors=True``，避免把遗漏参数误当作清空动作。
        """

        normalized_title = self._normalize_optional_text(title, "候选标题", 300)
        normalized_snippet = self._normalize_optional_text(snippet, "候选摘要", 4_000)
        if published_at is not None and not isinstance(published_at, datetime):
            raise ValueError("候选发布时间必须是 datetime 或 None")
        normalized_markdown = self._normalize_optional_markdown(
            extracted_markdown,
            "候选正文",
            300_000,
        )
        normalized_hash = self._normalize_hash(content_hash) if content_hash else None
        normalized_code = self._normalize_optional_text(error_code, "错误代码", 80)
        normalized_message = self._normalize_optional_text(error_message, "错误说明", 1_000)
        normalized_metadata = self._normalize_metadata_json(
            metadata_json,
            "候选资料元数据",
        )

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_collection_candidates AS candidate
                    SET title = COALESCE(:title, candidate.title),
                        snippet = COALESCE(:snippet, candidate.snippet),
                        published_at = COALESCE(:published_at, candidate.published_at),
                        extracted_markdown = COALESCE(
                            :extracted_markdown,
                            candidate.extracted_markdown
                        ),
                        content_hash = COALESCE(:content_hash, candidate.content_hash),
                        status = COALESCE(:status, candidate.status),
                        error_code = CASE
                            WHEN :clear_errors THEN NULL
                            ELSE COALESCE(:error_code, candidate.error_code)
                        END,
                        error_message = CASE
                            WHEN :clear_errors THEN NULL
                            ELSE COALESCE(:error_message, candidate.error_message)
                        END,
                        metadata_json = CASE
                            WHEN CAST(:metadata_json AS text) IS NULL THEN candidate.metadata_json
                            WHEN :merge_metadata THEN candidate.metadata_json || CAST(:metadata_json AS jsonb)
                            ELSE CAST(:metadata_json AS jsonb)
                        END,
                        updated_at = NOW()
                    FROM career_assistant.interview_collection_jobs AS job
                    WHERE candidate.id = :candidate_id
                      AND job.id = candidate.collection_job_id
                      AND job.organization_id = :organization_id
                    RETURNING candidate.id, candidate.collection_job_id, candidate.source_url,
                              candidate.canonical_url, candidate.source_platform,
                              candidate.title, candidate.snippet, candidate.published_at,
                              candidate.extracted_markdown, candidate.content_hash,
                              candidate.status, candidate.error_code, candidate.error_message,
                              candidate.metadata_json, candidate.created_at, candidate.updated_at
                    """,
                ),
                {
                    "candidate_id": candidate_id,
                    "organization_id": organization_id,
                    "title": normalized_title,
                    "snippet": normalized_snippet,
                    "published_at": published_at,
                    "extracted_markdown": normalized_markdown,
                    "content_hash": normalized_hash,
                    "status": status.value if status is not None else None,
                    "error_code": normalized_code,
                    "error_message": normalized_message,
                    "clear_errors": clear_errors,
                    "metadata_json": self._serialize_metadata_json(normalized_metadata),
                    "merge_metadata": merge_metadata,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("候选资料不存在或无访问权限")
        return self._to_collection_candidate(row)

    def set_collection_candidate_status(
        self,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        status: CollectionCandidateStatus,
        metadata_json: Mapping[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionCandidateRecord:
        """标记候选状态，并可追加该阶段的轻量处理元数据。"""

        return self.update_collection_candidate(
            organization_id,
            candidate_id,
            status=status,
            metadata_json=metadata_json,
            merge_metadata=merge_metadata,
        )

    def update_collection_candidate_status(
        self,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        status: CollectionCandidateStatus,
        metadata_json: Mapping[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionCandidateRecord:
        """新采集链路使用的状态更新别名，保持旧 ``set`` 接口兼容。"""

        return self.set_collection_candidate_status(
            organization_id,
            candidate_id,
            status=status,
            metadata_json=metadata_json,
            merge_metadata=merge_metadata,
        )

    def complete_ingestion_job(
        self,
        organization_id: UUID,
        job_id: UUID,
        experience_id: UUID,
    ) -> InterviewIngestionJobRecord:
        """将成功入库任务绑定到面经并收口为 succeeded。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_ingestion_jobs AS job
                    SET experience_id = experience.id,
                        status = 'succeeded',
                        attempt_count = job.attempt_count + 1,
                        completed_at = NOW(),
                        updated_at = NOW(),
                        error_code = NULL,
                        error_message = NULL
                    FROM career_assistant.interview_experiences AS experience
                    WHERE job.id = :job_id
                      AND job.organization_id = :organization_id
                      AND experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                    RETURNING job.id, job.organization_id, job.experience_id,
                              job.trigger_type, job.source_url, job.source_platform,
                              job.status, job.attempt_count, job.error_code,
                              job.error_message, job.started_at, job.completed_at,
                              job.created_at, job.updated_at
                    """,
                ),
                {"job_id": job_id, "organization_id": organization_id, "experience_id": experience_id},
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("入库任务或面经不存在，无法收口")
        return self._to_ingestion_job(row)

    def list_tree(self, *, query: str | None = None) -> list[dict[str, object]]:
        """跨组织读取公开公司→面经树，并合并规范化名称相同的公司。"""

        normalized_query = (query or "").strip().lower()
        parameters: dict[str, object] = {}
        filter_clause = ""
        if normalized_query:
            parameters["query"] = f"%{normalized_query}%"
            filter_clause = """
                AND (
                    LOWER(company.display_name) LIKE :query
                    OR LOWER(experience.job_name) LIKE :query
                    OR LOWER(experience.role_name) LIKE :query
                )
            """
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT company.normalized_name, company.display_name AS company_name,
                           experience.id AS experience_id, experience.job_name,
                           experience.role_name, experience.interview_date,
                           experience.status, experience.updated_at
                    FROM career_assistant.interview_companies AS company
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.company_id = company.id
                    WHERE TRUE
                    {filter_clause}
                    ORDER BY company.display_name ASC,
                             experience.role_name ASC,
                             experience.interview_date DESC NULLS LAST,
                             experience.updated_at DESC
                    """,
                ),
                parameters,
            ).mappings().all()

        companies: dict[str, dict[str, object]] = {}
        for row in rows:
            company_key = str(row["normalized_name"])
            company = companies.setdefault(
                company_key,
                {
                    "id": f"company:{company_key}",
                    "node_type": "company",
                    "label": row["company_name"],
                    "children": [],
                },
            )
            company["children"].append(
                {
                    "id": str(row["experience_id"]),
                    "node_type": "experience",
                    "label": row["job_name"],
                    "role_name": row["role_name"],
                    "interview_date": row["interview_date"].isoformat() if row["interview_date"] else None,
                    "status": row["status"],
                    "updated_at": row["updated_at"].isoformat(),
                },
            )
        return list(companies.values())

    def get_public_experience(
        self,
        experience_id: UUID,
    ) -> InterviewExperienceRecord | None:
        """按 ID 跨组织读取公开面经，不执行读权限判断。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id,
                           experience.created_by_actor_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.id = :experience_id
                    """,
                ),
                {"experience_id": experience_id},
            ).mappings().one_or_none()
        return self._to_experience(row) if row is not None else None

    def list_public_experience_sources(
        self,
        experience_id: UUID,
    ) -> list[InterviewExperienceSourceRecord]:
        """跨组织读取公开面经来源，不执行读权限判断。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, organization_id, experience_id, canonical_url,
                           source_url, source_platform, is_primary,
                           discovery_keywords_json, first_seen_at, last_seen_at,
                           created_at, updated_at
                    FROM career_assistant.interview_experience_sources
                    WHERE experience_id = :experience_id
                    ORDER BY is_primary DESC, first_seen_at ASC
                    """,
                ),
                {"experience_id": experience_id},
            ).mappings().all()
        return [self._to_experience_source(row) for row in rows]

    def register_web_document(
        self,
        organization_id: UUID,
        *,
        canonical_url: str,
        source_url: str,
        source_platform: str,
        title: str | None,
        search_snippet: str | None,
        keyword: str,
        search_rank: int,
        metadata_json: Mapping[str, object] | None = None,
    ) -> InterviewWebDocumentRecord:
        """登记或刷新一个规范 URL，既有终态绝不退回待抓取。"""

        normalized_canonical = self._normalize_optional_url(canonical_url)
        normalized_source = self._normalize_optional_url(source_url)
        if normalized_canonical is None or normalized_source is None:
            raise ValueError("公开网页来源地址不能为空")
        normalized_platform = self._normalize_text(source_platform, "来源平台", 80)
        normalized_title = self._normalize_optional_text(title, "网页标题", 500)
        normalized_snippet = self._normalize_optional_text(search_snippet, "搜索摘要", 2_000)
        normalized_keyword = self._normalize_text(keyword, "搜索关键词", 120)
        if not 1 <= search_rank <= 100:
            raise ValueError("搜索排名必须在 1 到 100 之间")
        extra_metadata = self._normalize_metadata_json(metadata_json, "公开网页元数据") or {}
        document_metadata = {
            **extra_metadata,
            "latest_keyword": normalized_keyword,
            "search_rank": search_rank,
            "discovery_keywords": [normalized_keyword],
        }
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_web_documents (
                        id, organization_id, canonical_url, source_url, source_platform,
                        title, search_snippet, metadata_json
                    ) VALUES (
                        :id, :organization_id, :canonical_url, :source_url, :source_platform,
                        :title, :search_snippet, CAST(:metadata_json AS jsonb)
                    )
                    ON CONFLICT (organization_id, canonical_url) DO UPDATE
                    SET title = COALESCE(EXCLUDED.title, interview_web_documents.title),
                        search_snippet = COALESCE(
                            EXCLUDED.search_snippet,
                            interview_web_documents.search_snippet
                        ),
                        status = interview_web_documents.status,
                        last_seen_at = NOW(),
                        metadata_json = interview_web_documents.metadata_json
                            || EXCLUDED.metadata_json
                            || jsonb_build_object(
                                'discovery_keywords',
                                (
                                    SELECT COALESCE(jsonb_agg(keyword ORDER BY keyword), '[]'::jsonb)
                                    FROM (
                                        SELECT DISTINCT keyword
                                        FROM jsonb_array_elements_text(
                                            COALESCE(
                                                interview_web_documents.metadata_json
                                                    -> 'discovery_keywords',
                                                '[]'::jsonb
                                            ) || COALESCE(
                                                EXCLUDED.metadata_json -> 'discovery_keywords',
                                                '[]'::jsonb
                                            )
                                        ) AS items(keyword)
                                    ) AS unique_keywords
                                )
                            ),
                        updated_at = NOW()
                    RETURNING id, organization_id, canonical_url, source_url,
                              source_platform, title, search_snippet, status,
                              normalized_markdown, content_hash, imported_experience_id,
                              attempt_count, error_code, error_message, first_seen_at,
                              last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                              created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "canonical_url": normalized_canonical,
                    "source_url": normalized_source,
                    "source_platform": normalized_platform,
                    "title": normalized_title,
                    "search_snippet": normalized_snippet,
                    "metadata_json": self._serialize_metadata_json(document_metadata),
                },
            ).mappings().one()
        return self._to_web_document(row)

    def get_web_document(
        self,
        organization_id: UUID,
        document_id: UUID,
    ) -> InterviewWebDocumentRecord | None:
        """按组织读取一条永久抓取账本。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, canonical_url, source_url,
                           source_platform, title, search_snippet, status,
                           normalized_markdown, content_hash, imported_experience_id,
                           attempt_count, error_code, error_message, first_seen_at,
                           last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                           created_at, updated_at
                    FROM career_assistant.interview_web_documents
                    WHERE id = :document_id AND organization_id = :organization_id
                    """,
                ),
                {"document_id": document_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_web_document(row) if row is not None else None

    def claim_web_document(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        now: datetime,
    ) -> InterviewWebDocumentRecord | None:
        """原子认领一个新地址或到期的临时失败地址。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_web_documents
                    SET status = 'fetching',
                        attempt_count = attempt_count + 1,
                        error_code = NULL,
                        error_message = NULL,
                        next_retry_at = NULL,
                        updated_at = NOW()
                    WHERE id = :document_id
                      AND organization_id = :organization_id
                      AND status IN ('discovered', 'retryable_failed')
                      AND (next_retry_at IS NULL OR next_retry_at <= :now)
                    RETURNING id, organization_id, canonical_url, source_url,
                              source_platform, title, search_snippet, status,
                              normalized_markdown, content_hash, imported_experience_id,
                              attempt_count, error_code, error_message, first_seen_at,
                              last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                              created_at, updated_at
                    """,
                ),
                {"document_id": document_id, "organization_id": organization_id, "now": now},
            ).mappings().one_or_none()
        return self._to_web_document(row) if row is not None else None

    def mark_web_document_fetched(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        normalized_markdown: str,
        content_hash: str,
        metadata_json: Mapping[str, object] | None = None,
    ) -> InterviewWebDocumentRecord:
        """保存唯一一份清洗正文，后续 Agent/OCR 重试不再抓取网页。"""

        normalized_content = self._normalize_markdown(
            normalized_markdown,
            "公开网页规范正文",
            300_000,
        )
        normalized_hash = self._normalize_hash(content_hash)
        metadata = self._normalize_metadata_json(metadata_json, "公开网页元数据") or {}
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_web_documents
                    SET status = 'fetched',
                        normalized_markdown = :normalized_markdown,
                        content_hash = :content_hash,
                        last_fetched_at = NOW(),
                        next_retry_at = NULL,
                        error_code = NULL,
                        error_message = NULL,
                        metadata_json = metadata_json || CAST(:metadata_json AS jsonb),
                        updated_at = NOW()
                    WHERE id = :document_id AND organization_id = :organization_id
                    RETURNING id, organization_id, canonical_url, source_url,
                              source_platform, title, search_snippet, status,
                              normalized_markdown, content_hash, imported_experience_id,
                              attempt_count, error_code, error_message, first_seen_at,
                              last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                              created_at, updated_at
                    """,
                ),
                {
                    "document_id": document_id,
                    "organization_id": organization_id,
                    "normalized_markdown": normalized_content,
                    "content_hash": normalized_hash,
                    "metadata_json": self._serialize_metadata_json(metadata),
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("公开网页账本不存在或无访问权限")
        return self._to_web_document(row)

    def link_web_document_experience(
        self,
        organization_id: UUID,
        document_id: UUID,
        experience_id: UUID,
        *,
        status: InterviewWebDocumentStatus,
    ) -> InterviewWebDocumentRecord:
        """把同正文账本统一关联到一份面经。"""

        if status not in {
            InterviewWebDocumentStatus.IMPORTED,
            InterviewWebDocumentStatus.DUPLICATE,
        }:
            raise ValueError("关联面经时账本状态只能是 imported 或 duplicate")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_web_documents AS document
                    SET status = :status,
                        imported_experience_id = experience.id,
                        next_retry_at = NULL,
                        error_code = NULL,
                        error_message = NULL,
                        updated_at = NOW()
                    FROM career_assistant.interview_experiences AS experience
                    WHERE document.id = :document_id
                      AND document.organization_id = :organization_id
                      AND experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                    RETURNING document.id, document.organization_id,
                              document.canonical_url, document.source_url,
                              document.source_platform, document.title,
                              document.search_snippet, document.status,
                              document.normalized_markdown, document.content_hash,
                              document.imported_experience_id, document.attempt_count,
                              document.error_code, document.error_message,
                              document.first_seen_at, document.last_seen_at,
                              document.last_fetched_at, document.next_retry_at,
                              document.metadata_json, document.created_at,
                              document.updated_at
                    """,
                ),
                {
                    "document_id": document_id,
                    "organization_id": organization_id,
                    "experience_id": experience_id,
                    "status": status.value,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("公开网页账本或面经不存在或无访问权限")
        return self._to_web_document(row)

    def mark_web_document_imported(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        imported_experience_id: UUID,
    ) -> InterviewWebDocumentRecord:
        return self.link_web_document_experience(
            organization_id,
            document_id,
            imported_experience_id,
            status=InterviewWebDocumentStatus.IMPORTED,
        )

    def mark_web_document_duplicate(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        imported_experience_id: UUID,
    ) -> InterviewWebDocumentRecord:
        return self.link_web_document_experience(
            organization_id,
            document_id,
            imported_experience_id,
            status=InterviewWebDocumentStatus.DUPLICATE,
        )

    def mark_web_document_duplicate_pending(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        metadata_json: Mapping[str, object] | None = None,
    ) -> InterviewWebDocumentRecord:
        """正文重复但主记录尚未入库时，保留别名等待后续统一关联。"""

        metadata = self._normalize_metadata_json(metadata_json, "重复来源元数据") or {}
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_web_documents
                    SET status = 'duplicate',
                        next_retry_at = NULL,
                        error_code = NULL,
                        error_message = NULL,
                        metadata_json = metadata_json || CAST(:metadata_json AS jsonb),
                        updated_at = NOW()
                    WHERE id = :document_id AND organization_id = :organization_id
                    RETURNING id, organization_id, canonical_url, source_url,
                              source_platform, title, search_snippet, status,
                              normalized_markdown, content_hash, imported_experience_id,
                              attempt_count, error_code, error_message, first_seen_at,
                              last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                              created_at, updated_at
                    """,
                ),
                {
                    "document_id": document_id,
                    "organization_id": organization_id,
                    "metadata_json": self._serialize_metadata_json(metadata),
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("公开网页账本不存在或无访问权限")
        return self._to_web_document(row)

    def mark_web_document_filtered(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        metadata_json: Mapping[str, object] | None = None,
    ) -> InterviewWebDocumentRecord:
        """标记非面经或字段不足页面，同时保留已解析正文供人工核对。"""

        metadata = self._normalize_metadata_json(metadata_json, "过滤元数据") or {}
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_web_documents
                    SET status = 'filtered',
                        next_retry_at = NULL,
                        error_code = NULL,
                        error_message = NULL,
                        metadata_json = metadata_json || CAST(:metadata_json AS jsonb),
                        updated_at = NOW()
                    WHERE id = :document_id AND organization_id = :organization_id
                    RETURNING id, organization_id, canonical_url, source_url,
                              source_platform, title, search_snippet, status,
                              normalized_markdown, content_hash, imported_experience_id,
                              attempt_count, error_code, error_message, first_seen_at,
                              last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                              created_at, updated_at
                    """,
                ),
                {
                    "document_id": document_id,
                    "organization_id": organization_id,
                    "metadata_json": self._serialize_metadata_json(metadata),
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("公开网页账本不存在或无访问权限")
        return self._to_web_document(row)

    def mark_web_document_failed(
        self,
        organization_id: UUID,
        document_id: UUID,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: datetime,
    ) -> InterviewWebDocumentRecord:
        """按 1 小时、6 小时、24 小时安排最多三次重试。"""

        normalized_code = self._normalize_text(error_code, "错误代码", 80)
        normalized_message = self._normalize_text(error_message, "错误说明", 1_000)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_web_documents
                    SET status = CASE
                            WHEN :retryable AND attempt_count <= 3
                                THEN 'retryable_failed'
                            ELSE 'permanent_failed'
                        END,
                        attempt_count = attempt_count,
                        error_code = :error_code,
                        error_message = :error_message,
                        next_retry_at = CASE
                            WHEN NOT :retryable OR attempt_count > 3 THEN NULL
                            WHEN attempt_count = 1 THEN :now + INTERVAL '1 hour'
                            WHEN attempt_count = 2 THEN :now + INTERVAL '6 hours'
                            ELSE :now + INTERVAL '24 hours'
                        END,
                        updated_at = NOW()
                    WHERE id = :document_id AND organization_id = :organization_id
                    RETURNING id, organization_id, canonical_url, source_url,
                              source_platform, title, search_snippet, status,
                              normalized_markdown, content_hash, imported_experience_id,
                              attempt_count, error_code, error_message, first_seen_at,
                              last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                              created_at, updated_at
                    """,
                ),
                {
                    "document_id": document_id,
                    "organization_id": organization_id,
                    "error_code": normalized_code,
                    "error_message": normalized_message,
                    "retryable": bool(retryable),
                    "now": now,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("公开网页账本不存在或无访问权限")
        return self._to_web_document(row)

    def find_imported_document_by_content_hash(
        self,
        organization_id: UUID,
        content_hash: str,
    ) -> InterviewWebDocumentRecord | None:
        """按正文哈希查找已经归属面经的最早账本。"""

        normalized_hash = self._normalize_hash(content_hash)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, canonical_url, source_url,
                           source_platform, title, search_snippet, status,
                           normalized_markdown, content_hash, imported_experience_id,
                           attempt_count, error_code, error_message, first_seen_at,
                           last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                           created_at, updated_at
                    FROM career_assistant.interview_web_documents
                    WHERE organization_id = :organization_id
                      AND content_hash = :content_hash
                      AND imported_experience_id IS NOT NULL
                      AND status IN ('imported', 'duplicate')
                    ORDER BY first_seen_at ASC
                    LIMIT 1
                    """,
                ),
                {"organization_id": organization_id, "content_hash": normalized_hash},
            ).mappings().one_or_none()
        return self._to_web_document(row) if row is not None else None

    def list_web_documents_by_content_hash(
        self,
        organization_id: UUID,
        content_hash: str,
    ) -> list[InterviewWebDocumentRecord]:
        """读取同正文全部来源，供一次性建立主来源和别名。"""

        normalized_hash = self._normalize_hash(content_hash)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, organization_id, canonical_url, source_url,
                           source_platform, title, search_snippet, status,
                           normalized_markdown, content_hash, imported_experience_id,
                           attempt_count, error_code, error_message, first_seen_at,
                           last_seen_at, last_fetched_at, next_retry_at, metadata_json,
                           created_at, updated_at
                    FROM career_assistant.interview_web_documents
                    WHERE organization_id = :organization_id
                      AND content_hash = :content_hash
                    ORDER BY first_seen_at ASC, created_at ASC
                    """,
                ),
                {"organization_id": organization_id, "content_hash": normalized_hash},
            ).mappings().all()
        return [self._to_web_document(row) for row in rows]

    def get_experience_by_source_content_hash(
        self,
        organization_id: UUID,
        content_hash: str,
    ) -> InterviewExperienceRecord | None:
        """按历史来源哈希只读查重，不触发 upsert 或覆盖人工编辑。"""

        normalized_hash = self._normalize_hash(content_hash)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id,
                           experience.created_by_actor_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.organization_id = :organization_id
                      AND experience.source_content_hash = :content_hash
                    ORDER BY experience.created_at ASC
                    LIMIT 1
                    """,
                ),
                {"organization_id": organization_id, "content_hash": normalized_hash},
            ).mappings().one_or_none()
        return self._to_experience(row) if row is not None else None

    def add_experience_source(
        self,
        organization_id: UUID,
        experience_id: UUID,
        *,
        canonical_url: str,
        source_url: str,
        source_platform: str,
        keyword: str,
        is_primary: bool,
    ) -> InterviewExperienceSourceRecord:
        """新增来源或合并发现关键词，冲突时不改变既有主来源归属。"""

        normalized_canonical = self._normalize_optional_url(canonical_url)
        normalized_source = self._normalize_optional_url(source_url)
        if normalized_canonical is None or normalized_source is None:
            raise ValueError("面经来源地址不能为空")
        normalized_platform = self._normalize_text(source_platform, "来源平台", 80)
        normalized_keyword = self._normalize_text(keyword, "发现关键词", 120)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_experience_sources (
                        id, organization_id, experience_id, canonical_url, source_url,
                        source_platform, is_primary, discovery_keywords_json
                    )
                    SELECT :id, :organization_id, experience.id, :canonical_url,
                           :source_url, :source_platform, :is_primary,
                           jsonb_build_array(CAST(:keyword AS text))
                    FROM career_assistant.interview_experiences AS experience
                    WHERE experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                    ON CONFLICT (organization_id, canonical_url) DO UPDATE
                    SET last_seen_at = NOW(),
                        updated_at = NOW(),
                        discovery_keywords_json = (
                            SELECT COALESCE(jsonb_agg(keyword ORDER BY keyword), '[]'::jsonb)
                            FROM (
                                SELECT DISTINCT keyword
                                FROM jsonb_array_elements_text(
                                    interview_experience_sources.discovery_keywords_json
                                    || jsonb_build_array(CAST(:keyword AS text))
                                ) AS items(keyword)
                            ) AS unique_keywords
                        ),
                        is_primary = CASE
                            WHEN interview_experience_sources.is_primary THEN TRUE
                            WHEN CAST(:is_primary AS boolean)
                                 AND interview_experience_sources.experience_id = :experience_id
                                 AND NOT EXISTS (
                                     SELECT 1
                                     FROM career_assistant.interview_experience_sources
                                          AS existing_primary
                                     WHERE existing_primary.organization_id = :organization_id
                                       AND existing_primary.experience_id = :experience_id
                                       AND existing_primary.is_primary
                                 )
                                THEN TRUE
                            ELSE FALSE
                        END
                    RETURNING id, organization_id, experience_id, canonical_url,
                              source_url, source_platform, is_primary,
                              discovery_keywords_json, first_seen_at, last_seen_at,
                              created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "experience_id": experience_id,
                    "canonical_url": normalized_canonical,
                    "source_url": normalized_source,
                    "source_platform": normalized_platform,
                    "keyword": normalized_keyword,
                    "is_primary": bool(is_primary),
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("面经不存在或无访问权限")
        return self._to_experience_source(row)

    def list_experience_sources(
        self,
        organization_id: UUID,
        experience_id: UUID,
    ) -> list[InterviewExperienceSourceRecord]:
        """读取一份面经的主来源和全部同正文来源别名。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, organization_id, experience_id, canonical_url,
                           source_url, source_platform, is_primary,
                           discovery_keywords_json, first_seen_at, last_seen_at,
                           created_at, updated_at
                    FROM career_assistant.interview_experience_sources
                    WHERE organization_id = :organization_id
                      AND experience_id = :experience_id
                    ORDER BY is_primary DESC, first_seen_at ASC
                    """,
                ),
                {"organization_id": organization_id, "experience_id": experience_id},
            ).mappings().all()
        return [self._to_experience_source(row) for row in rows]

    def get_experience(
        self,
        organization_id: UUID,
        experience_id: UUID,
    ) -> InterviewExperienceRecord | None:
        """读取一份面经 Markdown，用于预览和人工编辑。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id,
                           experience.created_by_actor_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                    """,
                ),
                {"experience_id": experience_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_experience(row) if row is not None else None

    def get_experience_by_source_url(
        self,
        organization_id: UUID,
        source_url: str,
    ) -> InterviewExperienceRecord | None:
        """按规范来源读取已入库面经，避免同一平台笔记跨关键词重复写入。"""

        normalized_url = self._normalize_optional_url(source_url)
        if normalized_url is None:
            raise ValueError("面经来源地址不正确")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id,
                           experience.created_by_actor_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.organization_id = :organization_id
                      AND experience.source_platform = '小红书'
                      AND experience.source_url = :source_url
                    ORDER BY experience.updated_at DESC
                    LIMIT 1
                    """,
                ),
                {"organization_id": organization_id, "source_url": normalized_url},
            ).mappings().one_or_none()
        return self._to_experience(row) if row is not None else None

    def get_experience_by_company_and_job_name(
        self,
        organization_id: UUID,
        company_name: str,
        job_name: str,
    ) -> InterviewExperienceRecord | None:
        """按面经树的唯一业务键读取正文，供追加合并而不是覆盖使用。"""

        normalized_company_name = self._normalize_key(
            self._normalize_text(company_name, "公司名称", 120)
        )
        normalized_job_name = self._normalize_text(job_name, "面经名称", 220)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id,
                           experience.created_by_actor_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.organization_id = :organization_id
                      AND company.normalized_name = :normalized_company_name
                      AND experience.job_name = :job_name
                    """
                ),
                {
                    "organization_id": organization_id,
                    "normalized_company_name": normalized_company_name,
                    "job_name": normalized_job_name,
                },
            ).mappings().one_or_none()
        return self._to_experience(row) if row is not None else None

    def search_experiences(
        self,
        organization_id: UUID,
        query: str,
        *,
        limit: int = 8,
    ) -> list[InterviewExperienceRecord]:
        """跨组织提供 @面经 的公开名称/公司模糊匹配。"""

        normalized_query = self._normalize_text(query, "检索关键词", 120).lower()
        if not 1 <= limit <= 30:
            raise ValueError("检索数量必须在 1 到 30 之间")
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id,
                           experience.created_by_actor_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE (
                        LOWER(company.display_name) LIKE :query
                        OR LOWER(experience.job_name) LIKE :query
                        OR LOWER(experience.role_name) LIKE :query
                      )
                    ORDER BY experience.updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "query": f"%{normalized_query}%",
                    "limit": limit,
                },
            ).mappings().all()
        return [self._to_experience(row) for row in rows]

    def _company_name_for(self, company_id: UUID, organization_id: UUID) -> str:
        with self._database.transaction() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT display_name
                    FROM career_assistant.interview_companies
                    WHERE id = :company_id AND organization_id = :organization_id
                    """,
                ),
                {"company_id": company_id, "organization_id": organization_id},
            ).scalar_one()
        return str(value)

    @staticmethod
    def _normalize_text(value: str, field_name: str, maximum_length: int) -> str:
        normalized = " ".join(value.replace("\x00", "").split()).strip()
        if not normalized:
            raise ValueError(f"{field_name}不能为空")
        if len(normalized) > maximum_length:
            raise ValueError(f"{field_name}不能超过 {maximum_length} 个字符")
        return normalized

    @staticmethod
    def _experience_scope_filter(experience_ids: tuple[UUID, ...]) -> tuple[str, dict[str, object]]:
        """把可选面经范围转换为参数化 SQL，避免拼接用户输入。"""

        normalized_ids = tuple(dict.fromkeys(experience_ids))
        if not normalized_ids:
            return "", {}
        return (
            "AND experience.id = ANY(CAST(:experience_ids AS uuid[]))",
            {"experience_ids": [str(experience_id) for experience_id in normalized_ids]},
        )

    @classmethod
    def _normalize_optional_text(cls, value: str | None, field_name: str, maximum_length: int) -> str | None:
        if value is None or not value.strip():
            return None
        return cls._normalize_text(value, field_name, maximum_length)

    @staticmethod
    def _normalize_markdown(value: str, field_name: str, maximum_length: int) -> str:
        """清除危险空字符但保留 Markdown 换行、列表和表格结构。"""

        normalized = "\n".join(
            line.rstrip()
            for line in value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").split("\n")
        ).strip()
        if not normalized:
            raise ValueError(f"{field_name}不能为空")
        if len(normalized) > maximum_length:
            raise ValueError(f"{field_name}不能超过 {maximum_length} 个字符")
        return normalized

    @classmethod
    def _normalize_optional_markdown(cls, value: str | None, field_name: str, maximum_length: int) -> str | None:
        if value is None or not value.strip():
            return None
        return cls._normalize_markdown(value, field_name, maximum_length)

    @staticmethod
    def _normalize_key(value: str) -> str:
        return "".join(value.lower().split())

    @staticmethod
    def _normalize_hash(value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("正文摘要指纹必须是 SHA-256 十六进制值")
        return normalized

    @staticmethod
    def _normalize_optional_url(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if len(normalized) > 2_000 or not normalized.startswith(("http://", "https://")):
            raise ValueError("来源链接必须是长度不超过 2000 的 http 或 https 地址")
        return normalized

    @classmethod
    def _normalize_metadata_json(
        cls,
        value: Mapping[str, object] | None,
        field_name: str,
    ) -> dict[str, object] | None:
        """校验并深拷贝 JSON 元数据，阻断不可序列化对象进入 JSONB。"""

        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name}必须是 JSON 对象")
        try:
            serialized = json.dumps(
                dict(value),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            normalized = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}必须只包含 JSON 可序列化的数据") from exc
        if not isinstance(normalized, dict):
            raise ValueError(f"{field_name}必须是 JSON 对象")
        if len(serialized.encode("utf-8")) > cls._MAX_COLLECTION_METADATA_BYTES:
            raise ValueError(
                f"{field_name}不能超过 {cls._MAX_COLLECTION_METADATA_BYTES} 字节",
            )
        return normalized

    @staticmethod
    def _serialize_metadata_json(value: Mapping[str, object] | None) -> str | None:
        """为 PostgreSQL JSONB 参数生成稳定的 JSON 字符串。"""

        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _read_metadata_json(value: object) -> dict[str, object]:
        """兼容 PostgreSQL 驱动返回 dict 或 JSON 文本的两种形态。"""

        if value is None:
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError("采集元数据不是有效 JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("采集元数据必须是 JSON 对象")
        return dict(value)

    @staticmethod
    def _normalize_tags(values: Iterable[str], *, maximum_items: int, maximum_length: int) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = " ".join(str(value).split()).strip()
            if not normalized or normalized in result:
                continue
            if len(normalized) > maximum_length:
                raise ValueError("标签长度超过允许范围")
            result.append(normalized)
        if len(result) > maximum_items:
            raise ValueError("标签数量超过允许范围")
        return result

    @staticmethod
    def _to_company(row: RowMapping) -> InterviewCompanyRecord:
        return InterviewCompanyRecord(
            id=row["id"], organization_id=row["organization_id"],
            display_name=row["display_name"], normalized_name=row["normalized_name"],
            aliases=tuple(row["aliases"]), created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_experience(row: RowMapping, company_name: str | None = None) -> InterviewExperienceRecord:
        return InterviewExperienceRecord(
            id=row["id"], organization_id=row["organization_id"],
            created_by_actor_id=row.get("created_by_actor_id"), company_id=row["company_id"],
            company_name=company_name or row["company_name"], job_name=row["job_name"],
            role_name=row["role_name"], normalized_role_name=row["normalized_role_name"],
            interview_date=row["interview_date"], source_type=InterviewSourceType(row["source_type"]),
            source_platform=row["source_platform"], source_url=row["source_url"],
            summary_text=row["summary_text"], markdown_content=row["markdown_content"],
            normalized_markdown=row["normalized_markdown"], tags=tuple(row["tags"]),
            status=InterviewExperienceStatus(row["status"]), chunking_version=row["chunking_version"],
            indexed_at=row["indexed_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_chunk(row: RowMapping) -> InterviewChunkRecord:
        return InterviewChunkRecord(
            id=row["id"], experience_id=row["experience_id"], parent_heading=row["parent_heading"],
            heading_path=row["heading_path"], chunk_index=row["chunk_index"],
            content_text=row["content_text"], contextual_content=row["contextual_content"],
            token_estimate=row["token_estimate"], chunk_hash=row["chunk_hash"],
            chunking_version=row["chunking_version"], embedding_model=row["embedding_model"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @classmethod
    def _to_candidate(
        cls,
        row: RowMapping,
        *,
        lexical_score: float | None = None,
        semantic_score: float | None = None,
    ) -> InterviewChunkCandidate:
        """把联表检索结果收敛为不暴露敏感输入的引用候选。"""

        return InterviewChunkCandidate(
            chunk=cls._to_chunk(row),
            company_name=row["company_name"],
            job_name=row["job_name"],
            role_name=row["role_name"],
            interview_date=row["interview_date"],
            source_url=row["source_url"],
            lexical_score=lexical_score,
            semantic_score=semantic_score,
        )

    @staticmethod
    def _to_ingestion_job(row: RowMapping) -> InterviewIngestionJobRecord:
        return InterviewIngestionJobRecord(
            id=row["id"], organization_id=row["organization_id"], experience_id=row["experience_id"],
            trigger_type=IngestionTriggerType(row["trigger_type"]), source_url=row["source_url"],
            source_platform=row["source_platform"], status=IngestionJobStatus(row["status"]),
            attempt_count=row["attempt_count"], error_code=row["error_code"],
            error_message=row["error_message"], started_at=row["started_at"],
            completed_at=row["completed_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_collection_job(row: RowMapping) -> InterviewCollectionJobRecord:
        """把采集任务的数据库行收敛为稳定领域对象。"""

        return InterviewCollectionJobRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            created_by_actor_id=row.get("created_by_actor_id"),
            platform_key=row["platform_key"],
            keyword=row["keyword"],
            requested_limit=row["requested_limit"],
            connector_kind=CollectionConnectorKind(row["connector_kind"]),
            status=CollectionJobStatus(row["status"]),
            policy_decision=row["policy_decision"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata_json=InterviewLibraryRepository._read_metadata_json(
                row.get("metadata_json"),
            ),
        )

    @staticmethod
    def _to_collection_candidate(row: RowMapping) -> InterviewCollectionCandidateRecord:
        """把候选资料的数据库行收敛为不包含原始网页的读取对象。"""

        return InterviewCollectionCandidateRecord(
            id=row["id"],
            collection_job_id=row["collection_job_id"],
            source_url=row["source_url"],
            canonical_url=row["canonical_url"],
            source_platform=row["source_platform"],
            title=row["title"],
            snippet=row["snippet"],
            published_at=row["published_at"],
            extracted_markdown=row["extracted_markdown"],
            content_hash=row["content_hash"],
            status=CollectionCandidateStatus(row["status"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata_json=InterviewLibraryRepository._read_metadata_json(
                row.get("metadata_json"),
            ),
        )

    @staticmethod
    def _to_web_document(row: RowMapping) -> InterviewWebDocumentRecord:
        """把永久抓取账本行转换为稳定领域记录。"""

        return InterviewWebDocumentRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            canonical_url=row["canonical_url"],
            source_url=row["source_url"],
            source_platform=row["source_platform"],
            title=row["title"],
            search_snippet=row["search_snippet"],
            status=InterviewWebDocumentStatus(row["status"]),
            normalized_markdown=row["normalized_markdown"],
            content_hash=row["content_hash"],
            imported_experience_id=row["imported_experience_id"],
            attempt_count=row["attempt_count"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            last_fetched_at=row["last_fetched_at"],
            next_retry_at=row["next_retry_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata_json=InterviewLibraryRepository._read_metadata_json(
                row.get("metadata_json"),
            ),
        )

    @staticmethod
    def _to_experience_source(row: RowMapping) -> InterviewExperienceSourceRecord:
        """把主来源或来源别名行转换为稳定领域记录。"""

        keywords_value = row.get("discovery_keywords_json")
        if isinstance(keywords_value, str):
            try:
                keywords_value = json.loads(keywords_value)
            except json.JSONDecodeError:
                keywords_value = []
        keywords = tuple(
            str(item)
            for item in (keywords_value or [])
            if isinstance(item, str) and item.strip()
        )
        return InterviewExperienceSourceRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            experience_id=row["experience_id"],
            canonical_url=row["canonical_url"],
            source_url=row["source_url"],
            source_platform=row["source_platform"],
            is_primary=bool(row["is_primary"]),
            discovery_keywords=keywords,
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
