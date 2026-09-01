"""面经库 RAG 的索引与混合召回服务。

链路为：结构感知切片 -> 可选 Embedding -> PostgreSQL trigram/pgvector 双路召回
-> Reciprocal Rank Fusion -> 带来源的引用片段。RRF 是确定性融合重排，不把整篇
资料或模型响应作为黑盒评分依据，便于未来接入专用 reranker 时进行离线对比。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging
from uuid import UUID

from src.career_assistant.interview_library.embedding import (
    InterviewEmbeddingError,
    OpenAICompatibleEmbeddingClient,
)
from src.career_assistant.interview_library.models import InterviewChunkCandidate
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.settings import InterviewRetrievalSettings
from src.observability.langsmith_runtime import trace_operation


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterviewRetrievalResult:
    """给求职助手或面经库页面消费的可解释检索结果。"""

    query: str
    retrieval_mode: str
    candidates: tuple[InterviewChunkCandidate, ...]
    degraded_reason: str | None = None


class InterviewRetrievalService:
    """协调可选向量索引、关键词兜底与 RRF 融合排序。

    本类不负责生成最终回答；调用者必须把 ``candidates`` 作为带来源的上下文交给
    对话 Agent，从而让未来的 ``@面经`` 回答能够引用公司、岗位和原始资料链接。
    """

    def __init__(
        self,
        repository: InterviewLibraryRepository,
        settings: InterviewRetrievalSettings,
        *,
        embedding_client: OpenAICompatibleEmbeddingClient | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._embedding_client = embedding_client

    @property
    def vector_indexing_enabled(self) -> bool:
        return bool(self._embedding_client and self._embedding_client.is_configured)

    @property
    def configuration_snapshot(self) -> dict[str, object]:
        """返回不含凭证的检索身份，供评测实验校验实际运行配置。"""

        client = self._embedding_client
        return {
            "lexical_candidate_limit": self._settings.lexical_candidate_limit,
            "semantic_candidate_limit": self._settings.semantic_candidate_limit,
            "final_limit": self._settings.final_limit,
            "reciprocal_rank_fusion_k": self._settings.reciprocal_rank_fusion_k,
            "embedding_enabled": bool(client and client.is_configured),
            "embedding_model_id": client.model_id if client and client.is_configured else None,
        }

    def close(self) -> None:
        """由应用生命周期钩子关闭可选向量客户端。"""

        if self._embedding_client is not None:
            self._embedding_client.close()

    def index_experience(self, organization_id: UUID, experience_id: UUID) -> bool:
        """为一份已入库资料补齐向量；失败时保留关键词可检索能力。

        返回 ``False`` 不影响 Markdown、切片和入库任务成功状态。故障原因只写入
        服务端日志，不将上游响应或凭证相关细节泄露给前端。
        """

        client = self._embedding_client
        if client is None or not client.is_configured or not client.model_id:
            return False
        chunks = self._repository.list_chunks(organization_id, experience_id)
        if not chunks:
            return False
        try:
            embeddings_by_chunk_id: dict[UUID, list[float]] = {}
            batch_size = self._settings.embedding.max_batch_size
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                result = client.embed_texts([chunk.contextual_content for chunk in batch])
                for chunk, vector in zip(batch, result.vectors, strict=True):
                    embeddings_by_chunk_id[chunk.id] = list(vector)
            updated = self._repository.replace_chunk_embeddings(
                organization_id,
                embedding_model=client.model_id,
                embeddings_by_chunk_id=embeddings_by_chunk_id,
                expected_dimensions=client.expected_dimensions,
            )
            return updated == len(chunks)
        except InterviewEmbeddingError as exc:
            LOGGER.warning("面经向量索引暂未完成：%s", exc)
            return False

    def retrieve(
        self,
        organization_id: UUID,
        query: str,
        *,
        limit: int | None = None,
        experience_ids: tuple[UUID, ...] = (),
    ) -> InterviewRetrievalResult:
        """按关键词与语义双路召回，失败时透明降级为关键词检索。"""

        return trace_operation(
            run_name="interview.rag.retrieve",
            run_type="retriever",
            execute=lambda: self._retrieve(
                organization_id,
                query,
                limit=limit,
                experience_ids=experience_ids,
            ),
            summarize=lambda result: {
                "retrieval_mode": result.retrieval_mode,
                "result_count": len(result.candidates),
                "degraded": result.degraded_reason is not None,
            },
            metadata={
                "vector_indexing_enabled": self.vector_indexing_enabled,
                "lexical_candidate_limit": self._settings.lexical_candidate_limit,
                "semantic_candidate_limit": self._settings.semantic_candidate_limit,
                "query_characters": len(query),
                "requested_limit": limit or self._settings.final_limit,
                "selected_experience_count": len(experience_ids),
                "privacy_mode": "metadata_only",
            },
            tags=("career", "interview", "rag", "retrieval", "privacy:metadata-only"),
        )

    def _retrieve(
        self,
        organization_id: UUID,
        query: str,
        *,
        limit: int | None = None,
        experience_ids: tuple[UUID, ...] = (),
    ) -> InterviewRetrievalResult:
        """执行关键词/语义双路召回，原始查询与片段均不进入追踪数据。"""

        requested_limit = limit or self._settings.final_limit
        if not 1 <= requested_limit <= self._settings.final_limit:
            raise ValueError(f"检索结果数量必须在 1 到 {self._settings.final_limit} 之间")

        lexical_candidates = self._repository.search_lexical_chunks(
            organization_id,
            query,
            limit=self._settings.lexical_candidate_limit,
            experience_ids=experience_ids,
        )
        semantic_candidates: list[InterviewChunkCandidate] = []
        degraded_reason: str | None = None
        client = self._embedding_client
        if client is not None and client.is_configured:
            try:
                query_embedding = list(client.embed_texts([query]).vectors[0])
                semantic_candidates = self._repository.search_semantic_chunks(
                    organization_id,
                    query_embedding,
                    expected_dimensions=client.expected_dimensions,
                    limit=self._settings.semantic_candidate_limit,
                    experience_ids=experience_ids,
                )
            except InterviewEmbeddingError as exc:
                LOGGER.warning("面经语义召回降级为关键词召回：%s", exc)
                degraded_reason = "向量服务暂不可用，已使用关键词召回"

        if semantic_candidates:
            fused = self._reciprocal_rank_fusion(lexical_candidates, semantic_candidates)
            return InterviewRetrievalResult(
                query=query.strip(),
                retrieval_mode="hybrid_rrf",
                candidates=tuple(fused[:requested_limit]),
                degraded_reason=degraded_reason,
            )
        if lexical_candidates:
            return InterviewRetrievalResult(
                query=query.strip(),
                retrieval_mode="lexical",
                candidates=tuple(lexical_candidates[:requested_limit]),
                degraded_reason=degraded_reason,
            )

        fallback_candidates = self._repository.list_chunks_for_experiences(
            organization_id,
            experience_ids,
            limit=requested_limit,
        )
        return InterviewRetrievalResult(
            query=query.strip(),
            retrieval_mode="selected_experience_fallback" if fallback_candidates else "lexical",
            candidates=tuple(fallback_candidates),
            degraded_reason=degraded_reason,
        )

    def _reciprocal_rank_fusion(
        self,
        lexical_candidates: list[InterviewChunkCandidate],
        semantic_candidates: list[InterviewChunkCandidate],
    ) -> list[InterviewChunkCandidate]:
        """以 RRF 融合两条独立排序，避免不同 Provider 分数尺度不可比。"""

        scores: dict[UUID, float] = defaultdict(float)
        candidates: dict[UUID, InterviewChunkCandidate] = {}
        for candidate_list in (lexical_candidates, semantic_candidates):
            for rank, candidate in enumerate(candidate_list, start=1):
                chunk_id = candidate.chunk.id
                candidates[chunk_id] = self._merge_candidate_scores(
                    candidates.get(chunk_id),
                    candidate,
                )
                scores[chunk_id] += 1 / (self._settings.reciprocal_rank_fusion_k + rank)
        return sorted(
            candidates.values(),
            key=lambda item: (
                scores[item.chunk.id],
                item.semantic_score or 0.0,
                item.lexical_score or 0.0,
            ),
            reverse=True,
        )

    @staticmethod
    def _merge_candidate_scores(
        existing: InterviewChunkCandidate | None,
        incoming: InterviewChunkCandidate,
    ) -> InterviewChunkCandidate:
        if existing is None:
            return incoming
        return InterviewChunkCandidate(
            chunk=existing.chunk,
            company_name=existing.company_name,
            job_name=existing.job_name,
            role_name=existing.role_name,
            interview_date=existing.interview_date,
            source_url=existing.source_url,
            lexical_score=existing.lexical_score if existing.lexical_score is not None else incoming.lexical_score,
            semantic_score=existing.semantic_score if existing.semantic_score is not None else incoming.semantic_score,
        )
