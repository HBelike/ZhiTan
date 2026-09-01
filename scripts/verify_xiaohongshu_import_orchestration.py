"""小红书公开面经导入编排的离线回归验证。

本脚本不访问真实小红书、不调用真实模型，也不写入 PostgreSQL。它通过可注入的公开
页面适配器、OCR 解析器、模型分析器和仓储替身，验证后台任务的关键行为：

* 同一任务可处理多篇笔记与多张图片；
* 单篇页面失败不会阻断同批其他笔记；
* 新建任务默认不自动入库，候选必须等待人工确认；
* 无论 Agent 判定为有效、无效或待核验，只要保留了正文，用户都可编辑后手动入库；
* 手动入库会使用用户确认后的 Markdown，并把入库 ID 回写到候选元数据；
* 入口读取失败会把任务标为 FAILED，不会永远停在 RUNNING；
* 图片字节只在临时对象中流转，不会落到候选元数据。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.career_assistant.interview_library.collection import (
    CollectionOperationError,
    InterviewCollectionService,
    InterviewEvidenceAnalysis,
)
from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    InterviewCollectionCandidateRecord,
    InterviewCollectionJobRecord,
)
from src.career_assistant.interview_library.xiaohongshu_collection import (
    XiaohongshuCollectionResult,
    XiaohongshuDiscoveredNote,
    XiaohongshuDownloadedImage,
    XiaohongshuImageDownloadResult,
    XiaohongshuNote,
    XiaohongshuNoteFailure,
    XiaohongshuSourceKind,
)


ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000111")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000112")


class FakeRepository:
    """最小仓储替身，保留真实任务/候选状态机的字段契约。"""

    def __init__(self) -> None:
        self.jobs: dict[UUID, InterviewCollectionJobRecord] = {}
        self.candidates: dict[UUID, InterviewCollectionCandidateRecord] = {}

    def create_collection_job(self, **kwargs) -> InterviewCollectionJobRecord:  # type: ignore[no-untyped-def]
        now = datetime.now(UTC)
        record = InterviewCollectionJobRecord(
            id=uuid4(),
            organization_id=kwargs["organization_id"],
            created_by_actor_id=kwargs.get("created_by_actor_id"),
            platform_key=kwargs["platform_key"],
            keyword=kwargs["keyword"],
            requested_limit=kwargs["requested_limit"],
            connector_kind=kwargs["connector_kind"],
            status=CollectionJobStatus.QUEUED,
            policy_decision=kwargs["policy_decision"],
            error_code=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
            metadata_json=dict(kwargs.get("metadata_json") or {}),
        )
        self.jobs[record.id] = record
        return record

    def get_collection_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord | None:
        record = self.jobs.get(job_id)
        return record if record and record.organization_id == organization_id else None

    def get_collection_candidate(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> InterviewCollectionCandidateRecord | None:
        record = self.candidates.get(candidate_id)
        return record if record and organization_id == ORGANIZATION_ID else None

    def claim_collection_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord | None:
        current = self.get_collection_job(organization_id, job_id)
        if current is None or current.status is not CollectionJobStatus.QUEUED:
            return None
        return self.update_collection_job_status(
            organization_id,
            job_id,
            status=CollectionJobStatus.RUNNING,
        )

    def update_collection_job_status(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        status: CollectionJobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata_json: dict[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionJobRecord:
        current = self.get_collection_job(organization_id, job_id)
        assert current is not None
        metadata = dict(current.metadata_json) if merge_metadata else {}
        if metadata_json is not None:
            metadata.update(metadata_json)
        now = datetime.now(UTC)
        record = replace(
            current,
            status=status,
            error_code=error_code,
            error_message=error_message,
            metadata_json=metadata,
            started_at=current.started_at or (now if status is CollectionJobStatus.RUNNING else None),
            completed_at=(
                now
                if status in {CollectionJobStatus.SUCCEEDED, CollectionJobStatus.FAILED}
                else None
            ),
            updated_at=now,
        )
        self.jobs[job_id] = record
        return record

    def create_collection_candidate(self, organization_id: UUID, **kwargs) -> InterviewCollectionCandidateRecord:  # type: ignore[no-untyped-def]
        assert organization_id == ORGANIZATION_ID
        now = datetime.now(UTC)
        record = InterviewCollectionCandidateRecord(
            id=uuid4(),
            collection_job_id=kwargs["collection_job_id"],
            source_url=kwargs["source_url"],
            canonical_url=kwargs["canonical_url"],
            source_platform=kwargs["source_platform"],
            title=kwargs.get("title"),
            snippet=kwargs.get("snippet"),
            published_at=kwargs.get("published_at"),
            extracted_markdown=kwargs.get("extracted_markdown"),
            content_hash=kwargs.get("content_hash"),
            status=kwargs.get("status", CollectionCandidateStatus.DISCOVERED),
            error_code=kwargs.get("error_code"),
            error_message=kwargs.get("error_message"),
            created_at=now,
            updated_at=now,
            metadata_json=dict(kwargs.get("metadata_json") or {}),
        )
        self.candidates[record.id] = record
        return record

    def update_collection_candidate_status(
        self,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        status: CollectionCandidateStatus,
        metadata_json: dict[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionCandidateRecord:
        return self.update_collection_candidate(
            organization_id,
            candidate_id,
            status=status,
            metadata_json=metadata_json,
            merge_metadata=merge_metadata,
        )

    def set_collection_candidate_status(
        self,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        status: CollectionCandidateStatus,
        metadata_json: dict[str, object] | None = None,
        merge_metadata: bool = True,
    ) -> InterviewCollectionCandidateRecord:
        """兼容候选人工入库服务使用的旧状态更新别名。"""

        return self.update_collection_candidate_status(
            organization_id,
            candidate_id,
            status=status,
            metadata_json=metadata_json,
            merge_metadata=merge_metadata,
        )

    def update_collection_candidate(
        self,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        status: CollectionCandidateStatus | None = None,
        metadata_json: dict[str, object] | None = None,
        merge_metadata: bool = True,
        extracted_markdown: str | None = None,
        content_hash: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        clear_errors: bool = False,
        **_ignored: object,
    ) -> InterviewCollectionCandidateRecord:
        assert organization_id == ORGANIZATION_ID
        current = self.candidates[candidate_id]
        metadata = dict(current.metadata_json) if merge_metadata else {}
        if metadata_json is not None:
            metadata.update(metadata_json)
        record = replace(
            current,
            status=status or current.status,
            extracted_markdown=(
                extracted_markdown
                if extracted_markdown is not None
                else current.extracted_markdown
            ),
            content_hash=content_hash if content_hash is not None else current.content_hash,
            error_code=(
                None
                if clear_errors
                else error_code if error_code is not None else current.error_code
            ),
            error_message=(
                None
                if clear_errors
                else error_message
                if error_message is not None
                else current.error_message
            ),
            metadata_json=metadata,
            updated_at=datetime.now(UTC),
        )
        self.candidates[candidate_id] = record
        return record


class FakeAttachmentStore:
    """记录临时图片是否被清理，而不是把图片写入任何持久对象。"""

    def __init__(self) -> None:
        self.saved: list[SimpleNamespace] = []
        self.cleaned = 0

    def save_bytes(self, data: bytes, filename: str, media_type: str, _kind) -> SimpleNamespace:  # type: ignore[no-untyped-def]
        attachment = SimpleNamespace(data=data, filename=filename, media_type=media_type)
        self.saved.append(attachment)
        return attachment

    def cleanup(self, attachments: tuple[SimpleNamespace, ...]) -> None:
        self.cleaned += len(attachments)


class FakeAttachmentParser:
    """第一张图可读、第二张图空识别，覆盖多图部分失败路径。"""

    def parse(self, attachment: SimpleNamespace) -> SimpleNamespace:
        if "image-2" in attachment.filename:
            return SimpleNamespace(
                extracted_text="",
                document_understanding_error="示例图片没有可识别的文字。",
            )
        return SimpleNamespace(
            extracted_text="1. Explain the RAG retrieval and reranking trade-off?",
            document_understanding_error=None,
        )


class FakeEvidenceAnalyzer:
    """基于标题返回有效或无效分析，完全不调用真实模型。"""

    def analyze(self, _organization_id: UUID, *, title: str | None, **_kwargs) -> InterviewEvidenceAnalysis:
        if title == "无效生活记录":
            return InterviewEvidenceAnalysis(
                is_valid_interview=False,
                confidence=0.95,
                company_name=None,
                role_name=None,
                interview_date=None,
                interview_stage=None,
                tags=(),
                summary_text="与面试无关的生活记录。",
                questions=(),
                # 即使 Agent 暂不建议自动归档，也要保留它已清洗出的可核验正文，
                # 交给用户决定是否手动保存，而不是把候选变成空记录。
                normalized_markdown="1. 解释 RAG 的检索与重排序取舍。",
                rejection_reason="证据不足以自动归档，需人工核验。",
                analyzer_status="completed",
                analyzer_message=None,
                model_label="fake",
            )
        return InterviewEvidenceAnalysis(
            is_valid_interview=True,
            confidence=0.92,
            company_name="东方财富",
            role_name="AI Agent 开发工程师",
            interview_date=None,
            interview_stage="一面",
            tags=("RAG", "系统设计"),
            summary_text="围绕 RAG 检索、重排序和 Agent 工程实践的一面复盘。",
            questions=("请解释 RAG 检索与重排序的取舍。",),
            normalized_markdown="# 东方财富 AI Agent 一面\n\n## 面试问题\n\n1. 请解释 RAG 检索与重排序的取舍。",
            rejection_reason=None,
            analyzer_status="completed",
            analyzer_message=None,
            model_label="fake",
        )


class FailIfCalledEvidenceAnalyzer:
    """采集证据不足时，编排层必须在调用模型前停止。"""

    def analyze(self, *_args, **_kwargs) -> InterviewEvidenceAnalysis:  # type: ignore[no-untyped-def]
        raise AssertionError("证据不完整的笔记不应调用 LLM")


class FakeLibraryService:
    """验证自动入库确实走既有 ingest 入口。"""

    def __init__(self) -> None:
        self.drafts = []

    def ingest(self, _organization_id: UUID, draft, *, trigger_type, **ownership):  # type: ignore[no-untyped-def]
        self.drafts.append((draft, trigger_type, ownership))
        return SimpleNamespace(id=uuid4())


class FakeXiaohongshuAdapter:
    """提供两篇成功笔记、一篇发现后读取失败和两张图片。"""

    def __init__(self, *, raise_at_collect: bool = False) -> None:
        self.raise_at_collect = raise_at_collect
        self.closed = False
        self.valid_note = XiaohongshuNote(
            source_url="https://www.xiaohongshu.com/explore/valid001",
            canonical_url="https://www.xiaohongshu.com/explore/valid001",
            note_id="valid001",
            title="东方财富 AI Agent 一面面经",
            body_text="记录一面问题与 RAG 工程取舍。",
            image_urls=("https://ci.xiaohongshu.com/first.png", "https://ci.xiaohongshu.com/second.jpg"),
            published_at="2026-08-12",
            author_name="测试作者",
        )
        self.invalid_note = XiaohongshuNote(
            source_url="https://www.xiaohongshu.com/explore/invalid002",
            canonical_url="https://www.xiaohongshu.com/explore/invalid002",
            note_id="invalid002",
            title="无效生活记录",
            body_text="今天的日常分享。",
            image_urls=(),
        )

    def collect(self, _source_url: str, *, requested_limit: int) -> XiaohongshuCollectionResult:
        assert requested_limit == 3
        if self.raise_at_collect:
            raise CollectionOperationError("source_content_unavailable", "公开页面未暴露可导入笔记。")
        return XiaohongshuCollectionResult(
            source_url="https://www.xiaohongshu.com/search_result?keyword=面经",
            final_url="https://www.xiaohongshu.com/search_result?keyword=面经",
            source_kind=XiaohongshuSourceKind.SEARCH,
            discovered_notes=(
                XiaohongshuDiscoveredNote(self.valid_note.source_url, self.valid_note.canonical_url, self.valid_note.note_id),
                XiaohongshuDiscoveredNote(self.invalid_note.source_url, self.invalid_note.canonical_url, self.invalid_note.note_id),
                XiaohongshuDiscoveredNote("https://www.xiaohongshu.com/explore/fail003", "https://www.xiaohongshu.com/explore/fail003", "fail003"),
            ),
            notes=(self.valid_note, self.invalid_note),
            note_failures=(
                XiaohongshuNoteFailure(
                    source_url="https://www.xiaohongshu.com/explore/fail003",
                    canonical_url="https://www.xiaohongshu.com/explore/fail003",
                    error_code="note_content_empty",
                    error_message="该公开笔记没有可用正文。",
                ),
            ),
        )

    def download_images(self, note: XiaohongshuNote) -> XiaohongshuImageDownloadResult:
        assert note.note_id == "valid001"
        return XiaohongshuImageDownloadResult(
            note_url=note.canonical_url,
            images=(
                XiaohongshuDownloadedImage(1, note.image_urls[0], note.image_urls[0], "image/png", b"first-image"),
                XiaohongshuDownloadedImage(2, note.image_urls[1], note.image_urls[1], "image/jpeg", b"second-image"),
            ),
            failures=(),
            skipped_image_count=0,
            total_bytes=len(b"first-image") + len(b"second-image"),
        )

    def close(self) -> None:
        self.closed = True


def _service(adapter: FakeXiaohongshuAdapter) -> tuple[InterviewCollectionService, FakeRepository, FakeLibraryService, FakeAttachmentStore]:
    repository = FakeRepository()
    library_service = FakeLibraryService()
    attachment_store = FakeAttachmentStore()
    service = InterviewCollectionService(
        repository,
        library_service,
        evidence_analyzer=FakeEvidenceAnalyzer(),
        xiaohongshu_source_adapter=adapter,
        temporary_attachment_store=attachment_store,
        attachment_parser=FakeAttachmentParser(),
    )
    return service, repository, library_service, attachment_store


def verify_mixed_result_job() -> None:
    adapter = FakeXiaohongshuAdapter()
    service, repository, library_service, attachment_store = _service(adapter)
    job = service.create_xiaohongshu_import_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        source_url="https://www.xiaohongshu.com/search_result?keyword=%E9%9D%A2%E7%BB%8F",
        requested_limit=3,
        include_images=True,
        auto_import=False,
    )
    trace_summaries: list[dict[str, object]] = []

    def trace_operation_stub(**kwargs):  # type: ignore[no-untyped-def]
        result = kwargs["execute"]()
        trace_summaries.append(dict(kwargs["summarize"](result)))
        return result

    with patch(
        "src.career_assistant.interview_library.collection.trace_operation",
        side_effect=trace_operation_stub,
    ):
        public_result = service.run_xiaohongshu_import(ORGANIZATION_ID, job.id)

    assert public_result is None
    assert trace_summaries == [
        {
            "status": "succeeded",
            "completed": True,
            "count": 2,
            "error_class": None,
        }
    ]

    completed = repository.jobs[job.id]
    assert completed.status is CollectionJobStatus.SUCCEEDED
    summary = completed.metadata_json["summary"]
    assert summary == {
        "discovered_count": 3,
        "processed_count": 2,
        "valid_count": 1,
        "imported_count": 0,
        "rejected_count": 1,
        "incomplete_count": 0,
        "failed_count": 1,
    }
    assert completed.metadata_json["auto_import"] is False
    assert not library_service.drafts
    assert attachment_store.cleaned == 2
    candidates = list(repository.candidates.values())
    assert {candidate.status for candidate in candidates} == {
        CollectionCandidateStatus.FETCHED,
        CollectionCandidateStatus.FAILED,
    }
    valid_candidate = next(
        candidate
        for candidate in candidates
        if candidate.title == "东方财富 AI Agent 一面面经"
    )
    rejected_candidate = next(
        candidate for candidate in candidates if candidate.title == "无效生活记录"
    )
    # Agent 的“非面经”结论是审核提示，不得把已提取正文变成无法人工处理的空候选。
    assert rejected_candidate.status is CollectionCandidateStatus.FETCHED
    assert rejected_candidate.metadata_json["analysis"]["is_valid_interview"] is False
    assert rejected_candidate.extracted_markdown == "1. 解释 RAG 的检索与重排序取舍。"
    assert valid_candidate.metadata_json["images"]["ocr_success_count"] == 1
    assert valid_candidate.metadata_json["images"]["failed_count"] == 1
    assert b"first-image" not in repr(valid_candidate.metadata_json).encode("utf-8")
    assert valid_candidate.extracted_markdown
    assert "公开配图" not in valid_candidate.extracted_markdown
    assert "配图文字识别" not in valid_candidate.extracted_markdown
    assert "xiaohongshu.com" not in valid_candidate.extracted_markdown

    # 人工确认后的正文必须优先于采集候选正文，且入库 ID 要回写到候选元数据。
    confirmed_markdown = (
        "## 二面问题\n\n"
        "1. 请说明 RAG 检索与重排序在召回率、时延上的取舍。\n\n"
        "2. 说明 Agent 工具调用失败后的重试与观测方案。"
    )
    experience = service.ingest_selected_candidate(
        ORGANIZATION_ID,
        actor_id=ACTOR_ID,
        candidate_id=valid_candidate.id,
        company_name="东方财富",
        role_name="AI Agent 开发工程师",
        interview_date=None,
        summary_text="用户确认后的二面技术问题。",
        tags=("RAG", "Agent"),
        markdown_content=confirmed_markdown,
    )
    assert len(library_service.drafts) == 1
    saved_draft, _trigger_type, ownership = library_service.drafts[0]
    assert ownership["created_by_actor_id"] == ACTOR_ID
    assert saved_draft.markdown_content == confirmed_markdown
    persisted_candidate = repository.candidates[valid_candidate.id]
    assert persisted_candidate.status is CollectionCandidateStatus.IMPORTED
    assert persisted_candidate.extracted_markdown == confirmed_markdown
    assert persisted_candidate.metadata_json["imported_experience_id"] == str(experience.id)
    service.run_xiaohongshu_import(ORGANIZATION_ID, job.id)
    assert len(library_service.drafts) == 1, "已完成任务不能重复触发自动入库"
    service.close()
    assert adapter.closed


def verify_entry_failure_is_terminal() -> None:
    adapter = FakeXiaohongshuAdapter(raise_at_collect=True)
    service, repository, _library_service, _attachment_store = _service(adapter)
    job = service.create_xiaohongshu_import_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        source_url="https://www.xiaohongshu.com/search_result?keyword=%E9%9D%A2%E7%BB%8F",
        requested_limit=3,
        include_images=False,
        auto_import=False,
    )
    trace_summaries: list[dict[str, object]] = []

    def trace_operation_stub(**kwargs):  # type: ignore[no-untyped-def]
        result = kwargs["execute"]()
        trace_summaries.append(dict(kwargs["summarize"](result)))
        return result

    with patch(
        "src.career_assistant.interview_library.collection.trace_operation",
        side_effect=trace_operation_stub,
    ):
        public_result = service.run_xiaohongshu_import(ORGANIZATION_ID, job.id)

    assert public_result is None
    assert trace_summaries == [
        {
            "status": "failed",
            "completed": False,
            "count": 0,
            "error_class": "CollectionOperationError",
        }
    ]
    captured_trace = repr(trace_summaries)
    assert "xiaohongshu.com" not in captured_trace
    assert "测试用公开页面" not in captured_trace

    failed = repository.jobs[job.id]
    assert failed.status is CollectionJobStatus.FAILED
    assert failed.error_code == "source_content_unavailable"
    assert failed.metadata_json["phase"] == "failed"


def verify_keyword_search_reuses_public_import_pipeline() -> None:
    """关键词入口必须生成公开搜索 URL 并复用既有导入编排，而不是停在占位状态。"""

    adapter = FakeXiaohongshuAdapter()
    service, repository, library_service, _attachment_store = _service(adapter)
    job = service.create_keyword_collection_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        platform_key="xiaohongshu",
        keyword="  eastern fortune   ai interview  ",
        requested_limit=3,
    )

    assert job.status is CollectionJobStatus.QUEUED
    assert job.connector_kind is CollectionConnectorKind.URL_IMPORT
    assert job.metadata_json["source_kind"] == "xiaohongshu_keyword_search"
    source_url = str(job.metadata_json["source_url"])
    parsed = urlparse(source_url)
    assert parsed.path == "/search_result"
    assert parse_qs(parsed.query)["keyword"] == ["eastern fortune ai interview"]

    service.run_xiaohongshu_import(ORGANIZATION_ID, job.id)
    completed = repository.jobs[job.id]
    assert completed.status is CollectionJobStatus.SUCCEEDED
    assert completed.metadata_json["auto_import"] is False
    assert not library_service.drafts
    service.close()


def verify_incomplete_source_never_becomes_a_false_rejection() -> None:
    """只有站点摘要、没有正文/OCR 时应保留待复核候选，不能伪造过滤结论。"""

    adapter = FakeXiaohongshuAdapter()
    incomplete_note = XiaohongshuNote(
        source_url="https://www.xiaohongshu.com/explore/incomplete004",
        canonical_url="https://www.xiaohongshu.com/explore/incomplete004",
        note_id="incomplete004",
        title="疑似面经，但页面正文未暴露",
        body_text="3 亿人的生活经验，都在小红书",
        image_urls=(),
        body_source="meta",
    )
    adapter.collect = lambda _url, *, requested_limit: XiaohongshuCollectionResult(  # type: ignore[method-assign]
        source_url="https://www.xiaohongshu.com/explore/incomplete004",
        final_url="https://www.xiaohongshu.com/explore/incomplete004",
        source_kind=XiaohongshuSourceKind.NOTE,
        discovered_notes=(
            XiaohongshuDiscoveredNote(
                incomplete_note.source_url,
                incomplete_note.canonical_url,
                incomplete_note.note_id,
            ),
        ),
        notes=(incomplete_note,),
        note_failures=(),
    )
    repository = FakeRepository()
    library_service = FakeLibraryService()
    service = InterviewCollectionService(
        repository,
        library_service,
        evidence_analyzer=FailIfCalledEvidenceAnalyzer(),
        xiaohongshu_source_adapter=adapter,
        temporary_attachment_store=FakeAttachmentStore(),
        attachment_parser=FakeAttachmentParser(),
    )
    job = service.create_xiaohongshu_import_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        source_url=incomplete_note.source_url,
        requested_limit=1,
        include_images=True,
        auto_import=False,
    )
    service.run_xiaohongshu_import(ORGANIZATION_ID, job.id)

    completed = repository.jobs[job.id]
    summary = completed.metadata_json["summary"]
    assert completed.status is CollectionJobStatus.SUCCEEDED
    assert summary["incomplete_count"] == 1
    assert summary["rejected_count"] == 0
    assert not library_service.drafts
    candidate = next(iter(repository.candidates.values()))
    # 页面仅暴露平台摘要时没有可审核正文，保留“采集不完整”说明即可，不能伪造可保存面经。
    assert candidate.status is CollectionCandidateStatus.EMPTY, candidate.status
    analysis = candidate.metadata_json["analysis"]
    assert analysis["status"] == "insufficient_source"
    assert analysis["is_valid_interview"] is None
    evidence = candidate.metadata_json["images"]["evidence"]
    assert evidence["is_complete"] is False
    service.close()


if __name__ == "__main__":
    verify_mixed_result_job()
    verify_entry_failure_is_terminal()
    verify_keyword_search_reuses_public_import_pipeline()
    verify_incomplete_source_never_becomes_a_false_rejection()
    print("小红书面经导入编排离线自检通过")
