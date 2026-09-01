"""全网公开面经搜索、永久去重、分析与入库编排。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from src.career_assistant.attachments import AttachmentParser, TemporaryAttachmentStore
from src.career_assistant.contracts import AttachmentKind
from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    IngestionTriggerType,
    InterviewSourceType,
    InterviewWebDocumentStatus,
)
from src.career_assistant.interview_library.public_web import (
    DownloadedPublicImage,
    FirecrawlClient,
    FirecrawlRequestError,
    PublicWebImageDownloader,
    PublicWebUrlError,
    canonicalize_public_url,
    hash_public_markdown,
    infer_public_platform,
    normalize_public_markdown,
    validate_public_https_target,
)
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)


LOGGER = logging.getLogger(__name__)
MIN_PUBLIC_WEB_MARKDOWN_CHARACTERS = 40
MAX_PUBLIC_WEB_PAGES_PER_JOB = 10
_INTERVIEW_TITLE_PATTERN = re.compile(
    r"(?:面经|面试(?:题|经历|复盘|总结)|[一二三四五六七八九十终]+面)",
    re.IGNORECASE,
)
_QUESTION_LINE_PATTERN = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\d{1,2}[.、．)）]|[一二三四五六七八九十]+[、.．])\s*(.+)$",
)


class PublicWebCollectionCoordinator:
    """执行 search → URL 账本 → scrape → 正文哈希 → Agent → 入库。"""

    POLICY_DECISION = (
        "只搜索和解析无需登录的公开 HTTPS 页面；不发送账号、Cookie、组织资料或"
        "面经库正文，不绕过登录、验证码和访问限制。"
    )

    def __init__(
        self,
        repository: Any,
        library_service: InterviewLibraryService | Any,
        firecrawl_client: FirecrawlClient | Any,
        evidence_analyzer: Any,
        *,
        temporary_attachment_store: TemporaryAttachmentStore | None = None,
        attachment_parser: AttachmentParser | None = None,
        image_downloader: PublicWebImageDownloader | None = None,
        url_validator: Callable[[str], None] = validate_public_https_target,
    ) -> None:
        self._repository = repository
        self._library_service = library_service
        self._firecrawl = firecrawl_client
        self._evidence_analyzer = evidence_analyzer
        self._temporary_attachment_store = temporary_attachment_store
        self._attachment_parser = attachment_parser
        self._image_downloader = image_downloader
        self._url_validator = url_validator

    @staticmethod
    def empty_summary() -> dict[str, int]:
        return {
            "discovered_count": 0,
            "known_url_count": 0,
            "scraped_count": 0,
            "duplicate_count": 0,
            "valid_count": 0,
            "imported_count": 0,
            "filtered_count": 0,
            "failed_count": 0,
        }

    def create_job(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        keyword: str,
        requested_limit: int,
    ):
        normalized_keyword = " ".join(str(keyword or "").split())
        if not normalized_keyword:
            raise ValueError("请输入搜索关键词。")
        if not 5 <= requested_limit <= MAX_PUBLIC_WEB_PAGES_PER_JOB:
            raise ValueError("全网公开信息收集数量必须在 5 到 10 之间")
        return self._repository.create_collection_job(
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            platform_key="public_web",
            keyword=normalized_keyword,
            requested_limit=requested_limit,
            connector_kind=CollectionConnectorKind.PUBLIC_API,
            policy_decision=self.POLICY_DECISION,
            metadata_json={
                "source_kind": "firecrawl_public_web_search",
                "phase": "search",
                "progress_percent": 0,
                "progress_message": "正在搜索公开网页。",
                "summary": self.empty_summary(),
            },
        )

    def run(self, organization_id: UUID, job_id: UUID) -> None:
        job = self._repository.claim_collection_job(organization_id, job_id)
        if job is None:
            return
        summary = self.empty_summary()
        try:
            results = self._firecrawl.search(
                job.keyword,
                # Free 方案每分钟最多执行 10 次 /scrape；候选数与单批抓取预算保持一致。
                limit=min(MAX_PUBLIC_WEB_PAGES_PER_JOB, job.requested_limit),
            )
        except FirecrawlRequestError as exc:
            self._fail_job(organization_id, job.id, exc.code, exc.message, summary)
            return
        except Exception as exc:  # pragma: no cover - 第三方适配器最后隔离
            LOGGER.exception("全网公开面经搜索发生未预期异常")
            self._fail_job(
                organization_id,
                job.id,
                "public_web_search_failed",
                self._safe_message(exc, "公开网页搜索暂时失败，请稍后重试。"),
                summary,
            )
            return

        self._update_job(
            organization_id,
            job.id,
            phase="ledger",
            percent=15,
            message="搜索完成，正在排除已处理地址。",
            summary=summary,
        )
        total = max(1, len(results))
        for index, result in enumerate(results, start=1):
            if summary["valid_count"] >= job.requested_limit:
                break
            try:
                canonical_url = canonicalize_public_url(result.url)
                self._url_validator(canonical_url)
            except (PublicWebUrlError, ValueError):
                summary["filtered_count"] += 1
                continue

            document = self._repository.register_web_document(
                organization_id,
                canonical_url=canonical_url,
                source_url=result.url,
                source_platform=infer_public_platform(canonical_url),
                title=result.title,
                search_snippet=result.snippet,
                keyword=job.keyword,
                search_rank=index,
                metadata_json={"search_metadata": dict(result.metadata)},
            )
            summary["discovered_count"] += 1
            candidate = self._repository.create_collection_candidate(
                organization_id,
                collection_job_id=job.id,
                source_url=result.url,
                canonical_url=canonical_url,
                source_platform=document.source_platform,
                title=result.title,
                snippet=result.snippet,
                metadata_json={
                    "source_kind": "firecrawl_public_web_search",
                    "web_document_id": str(document.id),
                    "ledger_status": document.status.value,
                    "scrape_performed": False,
                },
            )
            try:
                self._process_document(
                    organization_id,
                    job,
                    document,
                    candidate,
                    summary,
                )
            except Exception as exc:  # 单页异常不阻断同批其他来源
                LOGGER.exception("公开网页候选处理失败：document=%s", document.id)
                summary["failed_count"] += 1
                self._repository.update_collection_candidate(
                    organization_id,
                    candidate.id,
                    status=CollectionCandidateStatus.FAILED,
                    error_code="public_web_processing_failed",
                    error_message=self._safe_message(exc, "该公开网页处理失败。"),
                    metadata_json={"ledger_status": document.status.value},
                )
            percent = 15 + int(index / total * 80)
            self._update_job(
                organization_id,
                job.id,
                phase="scrape",
                percent=min(95, percent),
                message=f"正在处理公开网页 {index}/{len(results)}。",
                summary=summary,
            )

        self._repository.update_collection_job_status(
            organization_id,
            job.id,
            status=CollectionJobStatus.SUCCEEDED,
            metadata_json={
                "phase": "completed",
                "progress_percent": 100,
                "progress_message": (
                    f"收集完成：发现 {summary['discovered_count']} 条，"
                    f"解析 {summary['scraped_count']} 条，"
                    f"新入库 {summary['imported_count']} 条。"
                ),
                "summary": summary,
            },
        )

    def attach_candidate_sources(
        self,
        organization_id: UUID,
        candidate: Any,
        experience_id: UUID,
    ) -> None:
        """人工保存公开网页候选后补齐同正文来源。"""

        document_id_value = candidate.metadata_json.get("web_document_id")
        if not isinstance(document_id_value, str):
            return
        try:
            document_id = UUID(document_id_value)
        except ValueError:
            return
        document = self._repository.get_web_document(organization_id, document_id)
        if document is None or not document.content_hash:
            return
        self._attach_content_sources(
            organization_id,
            experience_id,
            document.content_hash,
            document.id,
        )

    def _process_document(
        self,
        organization_id: UUID,
        job: Any,
        document: Any,
        candidate: Any,
        summary: dict[str, int],
    ) -> None:
        if document.status in {
            InterviewWebDocumentStatus.IMPORTED,
            InterviewWebDocumentStatus.DUPLICATE,
            InterviewWebDocumentStatus.PERMANENT_FAILED,
        }:
            summary["known_url_count"] += 1
            if document.imported_experience_id:
                self._ensure_document_source(
                    organization_id,
                    job,
                    document,
                    document.imported_experience_id,
                )
            candidate_status = (
                CollectionCandidateStatus.IMPORTED
                if document.imported_experience_id
                else CollectionCandidateStatus.FETCHED
                if document.normalized_markdown
                else CollectionCandidateStatus.FAILED
            )
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=candidate_status,
                extracted_markdown=document.normalized_markdown,
                content_hash=document.content_hash,
                metadata_json={
                    "ledger_status": document.status.value,
                    "scrape_performed": False,
                    "imported_experience_id": (
                        str(document.imported_experience_id)
                        if document.imported_experience_id else None
                    ),
                },
            )
            return

        if document.status is InterviewWebDocumentStatus.FILTERED:
            stored_analysis = document.metadata_json.get("analysis")
            if (
                isinstance(stored_analysis, Mapping)
                and stored_analysis.get("status") == "pending_model"
                and document.normalized_markdown
                and document.content_hash
            ):
                summary["known_url_count"] += 1
                self._analyze_and_import(
                    organization_id,
                    job,
                    document,
                    candidate,
                    document.normalized_markdown,
                    document.content_hash,
                    summary,
                    scrape_performed=False,
                )
                return
            summary["known_url_count"] += 1
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.FETCHED,
                extracted_markdown=document.normalized_markdown,
                content_hash=document.content_hash,
                metadata_json={
                    "ledger_status": document.status.value,
                    "scrape_performed": False,
                },
            )
            return

        if document.status is InterviewWebDocumentStatus.FETCHED and document.normalized_markdown:
            summary["known_url_count"] += 1
            self._analyze_and_import(
                organization_id,
                job,
                document,
                candidate,
                document.normalized_markdown,
                document.content_hash or hash_public_markdown(document.normalized_markdown),
                summary,
                scrape_performed=False,
            )
            return

        claimed = self._repository.claim_web_document(
            organization_id,
            document.id,
            now=datetime.now(UTC),
        )
        if claimed is None:
            summary["known_url_count"] += 1
            return
        try:
            scraped = self._firecrawl.scrape(claimed.canonical_url)
            final_url = canonicalize_public_url(scraped.final_url)
            self._url_validator(final_url)
            markdown = normalize_public_markdown(scraped.markdown)
            ocr_metadata: dict[str, object] = {}
            if len(markdown) < MIN_PUBLIC_WEB_MARKDOWN_CHARACTERS and scraped.image_urls:
                markdown, ocr_metadata = self._append_ocr(markdown, scraped.image_urls)
            markdown = normalize_public_markdown(markdown)
            if len(markdown) < MIN_PUBLIC_WEB_MARKDOWN_CHARACTERS:
                raise FirecrawlRequestError(
                    "page_content_unavailable",
                    "公开网页没有可用正文，或需要登录后才能查看。",
                    retryable=False,
                )
            content_hash = hash_public_markdown(markdown)
            document = self._repository.mark_web_document_fetched(
                organization_id,
                claimed.id,
                normalized_markdown=markdown,
                content_hash=content_hash,
                metadata_json={
                    "resolved_canonical_url": final_url,
                    "cacheState": scraped.metadata.get("cacheState"),
                    "cachedAt": scraped.metadata.get("cachedAt"),
                    "ocr": ocr_metadata,
                },
            )
            if final_url != claimed.canonical_url:
                alias = self._repository.register_web_document(
                    organization_id,
                    canonical_url=final_url,
                    source_url=scraped.final_url,
                    source_platform=infer_public_platform(final_url),
                    title=scraped.title or claimed.title,
                    search_snippet=claimed.search_snippet,
                    keyword=job.keyword,
                    search_rank=int(claimed.metadata_json.get("search_rank") or 1),
                    metadata_json={"redirected_from": claimed.canonical_url},
                )
                self._repository.mark_web_document_fetched(
                    organization_id,
                    alias.id,
                    normalized_markdown=markdown,
                    content_hash=content_hash,
                    metadata_json={"redirect_alias": True},
                )
            summary["scraped_count"] += 1
        except FirecrawlRequestError as exc:
            self._repository.mark_web_document_failed(
                organization_id,
                claimed.id,
                error_code=exc.code,
                error_message=exc.message,
                retryable=exc.retryable,
                now=datetime.now(UTC),
            )
            summary["failed_count"] += 1
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
                metadata_json={"ledger_status": (
                    InterviewWebDocumentStatus.RETRYABLE_FAILED.value
                    if exc.retryable else InterviewWebDocumentStatus.PERMANENT_FAILED.value
                )},
            )
            return

        self._analyze_and_import(
            organization_id,
            job,
            document,
            candidate,
            markdown,
            content_hash,
            summary,
            scrape_performed=True,
        )

    def _analyze_and_import(
        self,
        organization_id: UUID,
        job: Any,
        document: Any,
        candidate: Any,
        markdown: str,
        content_hash: str,
        summary: dict[str, int],
        *,
        scrape_performed: bool,
    ) -> None:
        existing_document = self._repository.find_imported_document_by_content_hash(
            organization_id,
            content_hash,
        )
        existing_experience = None
        if existing_document is not None and existing_document.imported_experience_id:
            existing_experience = SimpleExperience(existing_document.imported_experience_id)
        if existing_experience is None:
            existing_experience = self._repository.get_experience_by_source_content_hash(
                organization_id,
                content_hash,
            )
        if existing_experience is not None:
            source_url = str(document.metadata_json.get("resolved_canonical_url") or document.canonical_url)
            existing_sources = self._repository.list_experience_sources(
                organization_id,
                existing_experience.id,
            )
            self._repository.add_experience_source(
                organization_id,
                existing_experience.id,
                canonical_url=source_url,
                source_url=document.source_url,
                source_platform=document.source_platform,
                keyword=job.keyword,
                # 旧任务可能已写入面经、但在来源写入阶段中断。首次重遇时补齐主来源，
                # 后续相同正文地址仍只作为别名，确保每份面经恰好一个主来源。
                is_primary=not any(item.is_primary for item in existing_sources),
            )
            self._repository.link_web_document_experience(
                organization_id,
                document.id,
                existing_experience.id,
                status=InterviewWebDocumentStatus.DUPLICATE,
            )
            summary["duplicate_count"] += 1
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.IMPORTED,
                extracted_markdown=markdown,
                content_hash=content_hash,
                metadata_json={
                    "ledger_status": "duplicate",
                    "scrape_performed": scrape_performed,
                    "imported_experience_id": str(existing_experience.id),
                },
            )
            return

        same_documents = self._repository.list_web_documents_by_content_hash(
            organization_id,
            content_hash,
        )
        if same_documents and same_documents[0].id != document.id:
            self._repository.mark_web_document_duplicate_pending(
                organization_id,
                document.id,
                metadata_json={
                    "duplicate_of_canonical_url": same_documents[0].canonical_url,
                },
            )
            summary["duplicate_count"] += 1
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.FETCHED,
                extracted_markdown=markdown,
                content_hash=content_hash,
                metadata_json={
                    "ledger_status": "duplicate",
                    "scrape_performed": scrape_performed,
                    "duplicate_pending": True,
                },
            )
            return

        try:
            analysis = self._evidence_analyzer.analyze(
                organization_id,
                source_url=document.canonical_url,
                title=document.title,
                markdown_content=markdown,
            )
        except Exception as exc:
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.FAILED,
                extracted_markdown=markdown,
                content_hash=content_hash,
                error_code="public_web_analysis_failed",
                error_message=self._safe_message(exc, "正文已保存，但 Agent 分析暂时失败。"),
                metadata_json={
                    "ledger_status": "fetched",
                    "scrape_performed": scrape_performed,
                },
            )
            summary["failed_count"] += 1
            return

        analysis = self._apply_deterministic_fallback(
            analysis,
            title=document.title,
            markdown=markdown,
        )

        candidate_markdown = normalize_public_markdown(
            getattr(analysis, "normalized_markdown", None) or markdown,
        )
        analysis_metadata = analysis.as_metadata()
        can_import = self._can_auto_import(analysis)
        if getattr(analysis, "is_valid_interview", False) is True:
            summary["valid_count"] += 1
        if not can_import:
            if getattr(analysis, "is_valid_interview", None) is None:
                self._repository.update_collection_candidate(
                    organization_id,
                    candidate.id,
                    status=CollectionCandidateStatus.FETCHED,
                    extracted_markdown=candidate_markdown,
                    content_hash=content_hash,
                    metadata_json={
                        "analysis": analysis_metadata,
                        "ledger_status": "fetched",
                        "scrape_performed": scrape_performed,
                    },
                )
                return
            self._repository.mark_web_document_filtered(
                organization_id,
                document.id,
                metadata_json={"analysis": analysis_metadata},
            )
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.FETCHED,
                extracted_markdown=candidate_markdown,
                content_hash=content_hash,
                metadata_json={
                    "analysis": analysis_metadata,
                    "ledger_status": "filtered",
                    "scrape_performed": scrape_performed,
                },
            )
            summary["filtered_count"] += 1
            return

        try:
            experience = self._library_service.ingest(
                organization_id,
                InterviewExperienceDraft(
                    company_name=analysis.company_name,
                    role_name=analysis.role_name,
                    interview_date=analysis.interview_date,
                    markdown_content=candidate_markdown,
                    source_type=InterviewSourceType.PUBLIC_URL,
                    source_platform=document.source_platform,
                    source_url=document.canonical_url,
                    summary_text=analysis.summary_text or document.title,
                    tags=tuple(analysis.tags),
                    job_name=(
                        f"{' '.join(analysis.role_name.split())[:180]} · 全网 · {content_hash[:8]}"
                    ),
                ),
                trigger_type=IngestionTriggerType.API_SYNC,
                created_by_actor_id=job.created_by_actor_id,
            )
        except Exception as exc:
            self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.FETCHED,
                extracted_markdown=candidate_markdown,
                content_hash=content_hash,
                error_code="public_web_import_failed",
                error_message=self._safe_message(exc, "正文已保存，但自动入库暂时失败。"),
                metadata_json={
                    "analysis": analysis_metadata,
                    "ledger_status": "fetched",
                    "scrape_performed": scrape_performed,
                },
            )
            summary["failed_count"] += 1
            return

        self._attach_content_sources(
            organization_id,
            experience.id,
            content_hash,
            document.id,
        )
        summary["imported_count"] += 1
        self._repository.update_collection_candidate(
            organization_id,
            candidate.id,
            status=CollectionCandidateStatus.IMPORTED,
            extracted_markdown=candidate_markdown,
            content_hash=content_hash,
            metadata_json={
                "analysis": analysis_metadata,
                "ledger_status": "imported",
                "scrape_performed": scrape_performed,
                "imported_experience_id": str(experience.id),
            },
        )

    @staticmethod
    def _apply_deterministic_fallback(
        analysis: Any,
        *,
        title: str | None,
        markdown: str,
    ) -> Any:
        """模型不可用时，只对强证据面经启用保守的本地自动入库判定。"""

        if getattr(analysis, "is_valid_interview", None) is not None:
            return analysis
        questions = PublicWebCollectionCoordinator._extract_explicit_questions(markdown)
        confidence = getattr(analysis, "confidence", None)
        has_title_signal = bool(_INTERVIEW_TITLE_PATTERN.search(str(title or "")))
        if not (
            getattr(analysis, "analyzer_status", None) == "pending_model"
            and has_title_signal
            and bool(getattr(analysis, "company_name", None))
            and bool(getattr(analysis, "role_name", None))
            and isinstance(confidence, (int, float))
            and float(confidence) >= 0.7
            and len(questions) >= 2
        ):
            return analysis
        try:
            return replace(
                analysis,
                is_valid_interview=True,
                questions=questions,
                normalized_markdown=markdown,
                rejection_reason=None,
                analyzer_status="deterministic_fallback",
                analyzer_message="模型暂不可用，已按严格本地面经证据完成甄别。",
                model_label="本地确定性规则",
            )
        except TypeError:
            return analysis

    @staticmethod
    def _extract_explicit_questions(markdown: str) -> tuple[str, ...]:
        """提取带编号的具体问题或考察点，普通段落和营销文案不计入。"""

        questions: list[str] = []
        for raw_line in str(markdown or "").replace("\r\n", "\n").split("\n"):
            match = _QUESTION_LINE_PATTERN.match(raw_line)
            if match is None:
                continue
            value = " ".join(match.group(1).split()).strip(" -*#")
            if not 6 <= len(value) <= 600:
                continue
            if value not in questions:
                questions.append(value)
            if len(questions) >= 30:
                break
        return tuple(questions)

    def _ensure_document_source(
        self,
        organization_id: UUID,
        job: Any,
        document: Any,
        experience_id: UUID,
    ) -> None:
        """为已入库账本补齐来源，兼容正文入库后来源写入中断的历史任务。"""

        existing_sources = self._repository.list_experience_sources(
            organization_id,
            experience_id,
        )
        canonical_url = str(
            document.metadata_json.get("resolved_canonical_url") or document.canonical_url,
        )
        self._repository.add_experience_source(
            organization_id,
            experience_id,
            canonical_url=canonical_url,
            source_url=document.source_url,
            source_platform=document.source_platform,
            keyword=job.keyword,
            is_primary=not any(item.is_primary for item in existing_sources),
        )

    def _attach_content_sources(
        self,
        organization_id: UUID,
        experience_id: UUID,
        content_hash: str,
        primary_document_id: UUID,
    ) -> None:
        documents = self._repository.list_web_documents_by_content_hash(
            organization_id,
            content_hash,
        )
        for item in documents:
            canonical_url = str(
                item.metadata_json.get("resolved_canonical_url") or item.canonical_url,
            )
            keyword = str(item.metadata_json.get("latest_keyword") or "公开网页收集")
            is_primary = item.id == primary_document_id
            self._repository.add_experience_source(
                organization_id,
                experience_id,
                canonical_url=canonical_url,
                source_url=item.source_url,
                source_platform=item.source_platform,
                keyword=keyword,
                is_primary=is_primary,
            )
            self._repository.link_web_document_experience(
                organization_id,
                item.id,
                experience_id,
                status=(
                    InterviewWebDocumentStatus.IMPORTED
                    if is_primary else InterviewWebDocumentStatus.DUPLICATE
                ),
            )

    def _append_ocr(
        self,
        markdown: str,
        image_urls: tuple[str, ...],
    ) -> tuple[str, dict[str, object]]:
        metadata: dict[str, object] = {
            "declared_count": len(image_urls),
            "downloaded_count": 0,
            "ocr_success_count": 0,
            "failed_count": 0,
        }
        if (
            self._image_downloader is None
            or self._temporary_attachment_store is None
            or self._attachment_parser is None
        ):
            return markdown, metadata
        images = self._image_downloader.download(image_urls, limit=10)
        metadata["downloaded_count"] = len(images)
        attachments = []
        sections: list[str] = []
        try:
            for image in images:
                try:
                    attachment = self._temporary_attachment_store.save_bytes(
                        image.data,
                        self._image_filename(image),
                        image.media_type,
                        AttachmentKind.INTERVIEW_EVIDENCE_IMAGE,
                    )
                    attachments.append(attachment)
                    parsed = self._attachment_parser.parse(attachment)
                    text = parsed.extracted_text.strip()
                    if text:
                        sections.append(f"### 配图 {image.index} 的文字识别\n\n{text}")
                        metadata["ocr_success_count"] = int(metadata["ocr_success_count"]) + 1
                    else:
                        metadata["failed_count"] = int(metadata["failed_count"]) + 1
                except (OSError, ValueError):
                    metadata["failed_count"] = int(metadata["failed_count"]) + 1
        finally:
            self._temporary_attachment_store.cleanup(tuple(attachments))
        if sections:
            markdown = "\n\n".join(
                part for part in (markdown, "## 配图文字识别\n\n" + "\n\n".join(sections)) if part
            )
        return markdown, metadata

    @staticmethod
    def _can_auto_import(analysis: Any) -> bool:
        return (
            getattr(analysis, "is_valid_interview", None) is True
            and bool(getattr(analysis, "company_name", None))
            and bool(getattr(analysis, "role_name", None))
            and bool(getattr(analysis, "questions", None))
            and isinstance(getattr(analysis, "confidence", None), (int, float))
            and float(analysis.confidence) >= 0.6
        )

    @staticmethod
    def _image_filename(image: DownloadedPublicImage) -> str:
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(image.media_type, ".img")
        return f"public-web-image-{image.index}{extension}"

    def _update_job(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        phase: str,
        percent: int,
        message: str,
        summary: Mapping[str, int],
    ) -> None:
        self._repository.update_collection_job_status(
            organization_id,
            job_id,
            status=CollectionJobStatus.RUNNING,
            metadata_json={
                "phase": phase,
                "progress_percent": max(0, min(100, percent)),
                "progress_message": message,
                "summary": dict(summary),
            },
        )

    def _fail_job(
        self,
        organization_id: UUID,
        job_id: UUID,
        code: str,
        message: str,
        summary: Mapping[str, int],
    ) -> None:
        self._repository.update_collection_job_status(
            organization_id,
            job_id,
            status=CollectionJobStatus.FAILED,
            error_code=code,
            error_message=message,
            metadata_json={
                "phase": "failed",
                "progress_percent": 100,
                "progress_message": message,
                "summary": dict(summary),
            },
        )

    @staticmethod
    def _safe_message(exc: Exception, fallback: str) -> str:
        value = " ".join(str(exc).split()).strip()
        return value[:500] if value else fallback


class SimpleExperience:
    """只在正文去重分支携带既有面经 ID。"""

    def __init__(self, experience_id: UUID) -> None:
        self.id = experience_id
