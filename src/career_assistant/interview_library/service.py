"""面经库的应用服务。

这里负责编排“解析结果 → 规范 Markdown → 面经树 → 切片 → 可观测任务”这一条
幂等入库链路。模型 Embedding 与重排是可替换的下一层，不让它们成为正文入库的单点。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import logging
import re
from urllib.parse import urlparse
from uuid import UUID

from src.career_assistant.interview_library.chunking import HierarchicalMarkdownChunker
from src.career_assistant.interview_library.models import (
    IngestionTriggerType,
    InterviewExperienceRecord,
    InterviewSourceType,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.retrieval import InterviewRetrievalService
from src.observability.langsmith_runtime import trace_operation


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterviewExperienceDraft:
    """已完成文件或网页解析后，进入长期面经库的受控输入。"""

    company_name: str
    role_name: str
    interview_date: date | None
    markdown_content: str
    source_type: InterviewSourceType
    source_platform: str | None = None
    source_url: str | None = None
    summary_text: str | None = None
    tags: tuple[str, ...] = ()
    job_name: str | None = None


class InterviewLibraryService:
    """提供面经入库、编辑重索引和 @面经 名称检索。

    本类线程安全：仅保存无状态 chunker 与数据库仓储；每次调用自行开启仓储事务。
    数据库事务覆盖正文和切片替换，不覆盖未来长耗时网页抓取/OCR，以避免长事务。
    """

    def __init__(
        self,
        repository: InterviewLibraryRepository,
        *,
        chunker: HierarchicalMarkdownChunker | None = None,
        retrieval_service: InterviewRetrievalService | None = None,
    ) -> None:
        self._repository = repository
        self._chunker = chunker or HierarchicalMarkdownChunker()
        self._retrieval_service = retrieval_service

    def ingest(
        self,
        organization_id: UUID,
        draft: InterviewExperienceDraft,
        *,
        trigger_type: IngestionTriggerType,
        created_by_actor_id: UUID | None = None,
        can_manage_all: bool = False,
    ) -> InterviewExperienceRecord:
        """建立面经解析、切片与索引父链，只记录规模和来源类别。"""

        return trace_operation(
            run_name="career.interview_library.ingest",
            run_type="chain",
            inputs={
                "source_type": draft.source_type.value,
                "trigger_type": trigger_type.value,
                "content_characters": len(draft.markdown_content),
                "tag_count": len(draft.tags),
            },
            metadata={
                "component": "interview_library",
                "privacy_mode": "metadata_only",
            },
            tags=("career", "interview-library", "rag"),
            execute=lambda: self._ingest_untraced(
                organization_id,
                draft,
                trigger_type=trigger_type,
                created_by_actor_id=created_by_actor_id,
                can_manage_all=can_manage_all,
            ),
            summarize=lambda _: {"completed": True},
        )

    def _ingest_untraced(
        self,
        organization_id: UUID,
        draft: InterviewExperienceDraft,
        *,
        trigger_type: IngestionTriggerType,
        created_by_actor_id: UUID | None,
        can_manage_all: bool,
    ) -> InterviewExperienceRecord:
        """将已解析文本持久化并同步建立无向量切片索引。"""

        markdown_content = self._normalize_markdown(draft.markdown_content)
        source_platform = draft.source_platform or self._infer_platform(draft.source_url)
        job = self._repository.create_ingestion_job(
            organization_id=organization_id,
            trigger_type=trigger_type,
            source_url=draft.source_url,
            source_platform=source_platform,
        )
        company = self._repository.upsert_company(organization_id, draft.company_name)
        job_name = draft.job_name or self.build_job_name(draft.role_name, draft.interview_date)
        content_hash = sha256(markdown_content.encode("utf-8")).hexdigest()
        experience = self._repository.create_experience(
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            company_id=company.id,
            job_name=job_name,
            role_name=draft.role_name,
            interview_date=draft.interview_date,
            source_type=draft.source_type,
            source_platform=source_platform,
            source_url=draft.source_url,
            markdown_content=markdown_content,
            normalized_markdown=self._normalize_for_retrieval(markdown_content),
            source_content_hash=content_hash,
            summary_text=draft.summary_text,
            tags=draft.tags,
            can_manage_all=can_manage_all,
        )
        chunks = self._chunker.split(
            experience.normalized_markdown,
            company_name=company.display_name,
            role_name=experience.role_name,
            job_name=experience.job_name,
        )
        self._repository.replace_chunks(organization_id, experience.id, chunks)
        self._index_without_blocking_ingestion(organization_id, experience.id)
        self._repository.complete_ingestion_job(organization_id, job.id, experience.id)
        indexed_experience = self._repository.get_experience(organization_id, experience.id)
        if indexed_experience is None:
            raise RuntimeError("面经入库后无法重新读取")
        return indexed_experience

    def update_markdown(
        self,
        organization_id: UUID,
        experience_id: UUID,
        *,
        actor_id: UUID,
        can_manage_all: bool = False,
        company_name: str | None = None,
        role_name: str | None = None,
        markdown_content: str,
        summary_text: str | None,
        tags: tuple[str, ...],
    ) -> InterviewExperienceRecord:
        """保存编辑内容并同步重建切片，保证 Markdown 与索引版本一致。"""

        current = self._repository.get_public_experience(experience_id)
        if current is None:
            raise LookupError("面经不存在")
        if not self.can_write(current, actor_id=actor_id, can_manage_all=can_manage_all):
            raise PermissionError("仅创建者或管理员可以修改该面经")
        target_organization_id = current.organization_id
        company = self._repository.upsert_company(
            target_organization_id,
            company_name if company_name is not None else current.company_name,
        )
        resolved_role_name = role_name if role_name is not None else current.role_name
        normalized_markdown = self._normalize_for_retrieval(markdown_content)
        content_hash = sha256(self._normalize_markdown(markdown_content).encode("utf-8")).hexdigest()
        updated = self._repository.update_experience_markdown(
            target_organization_id,
            experience_id,
            actor_id=actor_id,
            can_manage_all=can_manage_all,
            company_id=company.id,
            role_name=resolved_role_name,
            markdown_content=markdown_content,
            normalized_markdown=normalized_markdown,
            source_content_hash=content_hash,
            summary_text=summary_text,
            tags=tags,
        )
        chunks = self._chunker.split(
            updated.normalized_markdown,
            company_name=updated.company_name,
            role_name=updated.role_name,
            job_name=updated.job_name,
        )
        self._repository.replace_chunks(target_organization_id, updated.id, chunks)
        self._index_without_blocking_ingestion(target_organization_id, updated.id)
        reloaded = self._repository.get_experience(target_organization_id, updated.id)
        if reloaded is None:
            raise RuntimeError("面经重建索引后无法重新读取")
        return reloaded

    def delete_experience(
        self,
        organization_id: UUID,
        experience_id: UUID,
        *,
        actor_id: UUID,
        can_manage_all: bool = False,
    ) -> None:
        """永久删除一份面经及其数据库内 RAG 切片和来源记录。"""

        del organization_id
        current = self._repository.get_public_experience(experience_id)
        if current is None:
            raise LookupError("面经不存在")
        if not self.can_write(current, actor_id=actor_id, can_manage_all=can_manage_all):
            raise PermissionError("仅创建者或管理员可以删除该面经")
        if not self._repository.delete_experience(
            experience_id,
            actor_id=actor_id,
            can_manage_all=can_manage_all,
        ):
            raise PermissionError("面经归属已变化，请刷新后重试")

    def search_for_mention(
        self,
        organization_id: UUID,
        query: str,
        *,
        limit: int = 8,
    ) -> list[InterviewExperienceRecord]:
        """用于求职助手 @面经 的候选列表；只返回用户可见元数据和 Markdown。"""

        return self._repository.search_experiences(organization_id, query, limit=limit)

    @staticmethod
    def can_write(
        experience: InterviewExperienceRecord,
        *,
        actor_id: UUID,
        can_manage_all: bool,
    ) -> bool:
        """管理员可维护全部面经；普通用户仅可维护自己创建的记录。"""

        return can_manage_all or experience.created_by_actor_id == actor_id

    def _index_without_blocking_ingestion(
        self,
        organization_id: UUID,
        experience_id: UUID,
    ) -> None:
        """向量索引是增强能力，失败时不影响资料入库与关键词检索。"""

        if self._retrieval_service is None:
            return
        try:
            self._retrieval_service.index_experience(organization_id, experience_id)
        except Exception:  # pragma: no cover - 最后一层隔离，避免外部模型阻塞资料持久化。
            LOGGER.exception("面经向量索引发生未预期异常，已保留关键词检索")

    @staticmethod
    def build_job_name(role_name: str, interview_date: date | None) -> str:
        """按“岗位 + 面试日期”生成树叶名称，未知日期也保持可搜索。"""

        role = " ".join(role_name.split()).strip()
        if not role:
            raise ValueError("岗位名称不能为空")
        return f"{role} · {interview_date.isoformat() if interview_date else '日期待补充'}"

    @staticmethod
    def _normalize_markdown(markdown: str) -> str:
        normalized = "\n".join(
            line.rstrip()
            for line in markdown.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").split("\n")
        ).strip()
        if not normalized:
            raise ValueError("面经解析后没有可保存的文本")
        if len(normalized) > 300_000:
            raise ValueError("面经 Markdown 不能超过 300000 个字符")
        return normalized

    @classmethod
    def _normalize_for_retrieval(cls, markdown: str) -> str:
        """保留 Markdown 结构，同时清除常见网页噪声行供混合检索使用。"""

        ignored_prefixes = ("免责声明", "打开小红书", "下载 APP", "广告", "举报")
        lines = []
        for line in cls._normalize_markdown(markdown).splitlines():
            compact = " ".join(line.split())
            if compact and not compact.startswith(ignored_prefixes):
                lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _infer_platform(source_url: str | None) -> str | None:
        if not source_url:
            return None
        hostname = urlparse(source_url).hostname or ""
        hostname = hostname.lower()
        mapping = {
            "xiaohongshu.com": "小红书",
            "nowcoder.com": "牛客",
            "maimai.cn": "脉脉",
            "zhihu.com": "知乎",
        }
        for domain, name in mapping.items():
            if hostname == domain or hostname.endswith(f".{domain}"):
                return name
        return hostname or None
