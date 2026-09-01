"""离线验证小红书登录浏览器卡片发现、OCR 复用、自动入库与暂停状态。"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.interview_library.collection import (
    InterviewCollectionService,
    XiaohongshuBrowserDiscovery,
    XiaohongshuBrowserNote,
)
from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
)
from src.career_assistant.interview_library.xiaohongshu_collection import (
    XiaohongshuDownloadedImage,
    XiaohongshuImageDownloadResult,
)
from scripts.verify_xiaohongshu_import_orchestration import (
    FakeAttachmentParser,
    FakeAttachmentStore,
    FakeEvidenceAnalyzer,
    FakeLibraryService,
    FakeRepository,
    ACTOR_ID,
    ORGANIZATION_ID,
)


NOTE_ID = "6a4a58ff000000002101659d"
RESTRICTED_NOTE_ID = "7a4a58ff000000002101659e"


class BrowserRepository(FakeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.experiences_by_url: dict[str, SimpleNamespace] = {}

    def list_collection_candidates(self, organization_id: UUID, collection_job_id: UUID):  # type: ignore[no-untyped-def]
        assert organization_id == ORGANIZATION_ID
        return [
            item for item in self.candidates.values()
            if item.collection_job_id == collection_job_id
        ]

    def get_collection_candidate_by_canonical_url(
        self,
        organization_id: UUID,
        collection_job_id: UUID,
        canonical_url: str,
    ):
        assert organization_id == ORGANIZATION_ID
        return next((
            item for item in self.candidates.values()
            if item.collection_job_id == collection_job_id
            and item.canonical_url == canonical_url
        ), None)

    def get_experience_by_source_url(self, organization_id: UUID, source_url: str):  # type: ignore[no-untyped-def]
        assert organization_id == ORGANIZATION_ID
        return self.experiences_by_url.get(source_url)


class BrowserImageAdapter:
    def download_images(self, note: XiaohongshuBrowserNote) -> XiaohongshuImageDownloadResult:
        return XiaohongshuImageDownloadResult(
            note_url=note.canonical_url,
            images=(
                XiaohongshuDownloadedImage(
                    1,
                    note.image_urls[0],
                    note.image_urls[0],
                    "image/png",
                    b"browser-image",
                ),
            ),
            failures=(),
            skipped_image_count=0,
            total_bytes=len(b"browser-image"),
        )

    def close(self) -> None:
        return None


def build_service():  # type: ignore[no-untyped-def]
    repository = BrowserRepository()
    library = FakeLibraryService()
    attachments = FakeAttachmentStore()
    service = InterviewCollectionService(
        repository,
        library,
        evidence_analyzer=FakeEvidenceAnalyzer(),
        xiaohongshu_source_adapter=BrowserImageAdapter(),
        temporary_attachment_store=attachments,
        attachment_parser=FakeAttachmentParser(),
    )
    return service, repository, library, attachments


def verify_browser_collection() -> None:
    service, repository, library, attachments = build_service()
    job = service.create_xiaohongshu_browser_collection_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="全栈开发面经",
        requested_limit=20,
    )
    assert job.connector_kind is CollectionConnectorKind.USER_AUTHORIZED_BROWSER
    assert "source_url" not in job.metadata_json

    candidates = service.register_xiaohongshu_browser_discoveries(
        ORGANIZATION_ID,
        job.id,
        (
            XiaohongshuBrowserDiscovery(
                note_id=NOTE_ID,
                title="东方财富 AI Agent 一面面经",
                author_name="测试作者",
                liked_count="56",
            ),
        ),
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status is CollectionCandidateStatus.DISCOVERED
    assert "xsec_token" not in str(candidate.metadata_json)
    assert "image" not in str(candidate.metadata_json).lower()

    note = XiaohongshuBrowserNote(
        note_id=NOTE_ID,
        title="东方财富 AI Agent 一面面经",
        body_text="1. 请解释 RAG 检索与重排序的取舍？",
        image_urls=("https://sns-webpic-qc.xhscdn.com/browser-note.png",),
        author_name="测试作者",
        tags=("RAG", "面经"),
    )
    queued = service.queue_xiaohongshu_browser_note(ORGANIZATION_ID, job.id, note)
    assert queued.metadata_json["processing_state"] == "queued"
    assert note.image_urls[0] not in str(queued.metadata_json)

    service.process_xiaohongshu_browser_note(ORGANIZATION_ID, job.id, note)
    processed = repository.get_collection_candidate(ORGANIZATION_ID, candidate.id)
    assert processed is not None
    assert processed.status is CollectionCandidateStatus.IMPORTED
    assert processed.metadata_json["images"]["ocr_success_count"] == 1
    assert processed.metadata_json["tags"] == ["RAG", "面经"]
    assert library.drafts
    assert attachments.cleaned == 1

    completed = service.complete_xiaohongshu_browser_job(ORGANIZATION_ID, job.id)
    assert completed.status is CollectionJobStatus.SUCCEEDED
    assert completed.metadata_json["summary"]["imported_count"] == 1


def verify_duplicate_and_pause() -> None:
    service, repository, _library, _attachments = build_service()
    canonical_url = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
    repository.experiences_by_url[canonical_url] = SimpleNamespace(id=UUID("00000000-0000-0000-0000-000000000999"))
    job = service.create_xiaohongshu_browser_collection_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="AI 面经",
        requested_limit=20,
    )
    candidate = service.register_xiaohongshu_browser_discoveries(
        ORGANIZATION_ID,
        job.id,
        (XiaohongshuBrowserDiscovery(note_id=NOTE_ID, title="重复笔记"),),
    )[0]
    assert candidate.status is CollectionCandidateStatus.IMPORTED
    assert candidate.metadata_json["duplicate"] is True

    paused_job = service.create_xiaohongshu_browser_collection_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="后端面经",
        requested_limit=20,
    )
    paused = service.pause_xiaohongshu_browser_job(
        ORGANIZATION_ID,
        paused_job.id,
        error_code="verification_required",
        error_message="请完成安全验证。",
    )
    assert paused.status is CollectionJobStatus.NEEDS_USER_INTERACTION


def verify_restricted_note_is_recorded_and_skipped() -> None:
    service, repository, library, attachments = build_service()
    job = service.create_xiaohongshu_browser_collection_job(
        ORGANIZATION_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="agent开发面经",
        requested_limit=50,
    )
    candidate = service.register_xiaohongshu_browser_discoveries(
        ORGANIZATION_ID,
        job.id,
        (XiaohongshuBrowserDiscovery(note_id=RESTRICTED_NOTE_ID, title="App 限制笔记"),),
    )[0]
    note = XiaohongshuBrowserNote(
        note_id=RESTRICTED_NOTE_ID,
        title="App 限制笔记",
        body_text="",
        image_urls=(),
        source_error_code="content_restricted",
        source_error_message="当前仅允许在小红书 App 查看。",
    )
    service.queue_xiaohongshu_browser_note(ORGANIZATION_ID, job.id, note)
    service.process_xiaohongshu_browser_note(ORGANIZATION_ID, job.id, note)
    processed = repository.get_collection_candidate(ORGANIZATION_ID, candidate.id)
    assert processed is not None
    assert processed.status is CollectionCandidateStatus.BLOCKED
    assert processed.error_code == "content_restricted"
    assert processed.metadata_json["source_restricted"] is True
    assert not library.drafts
    assert attachments.cleaned == 0

    completed = service.complete_xiaohongshu_browser_job(ORGANIZATION_ID, job.id)
    assert completed.status is CollectionJobStatus.SUCCEEDED
    assert completed.metadata_json["summary"]["failed_count"] == 1


if __name__ == "__main__":
    verify_browser_collection()
    verify_duplicate_and_pause()
    verify_restricted_note_is_recorded_and_skipped()
    print("xiaohongshu_browser_collection_contract_ok")
