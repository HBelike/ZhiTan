"""全网公开面经收集编排的离线回归验证。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.career_assistant.interview_library.models import (  # noqa: E402
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    InterviewWebDocumentStatus,
)
from src.career_assistant.interview_library.public_web import (  # noqa: E402
    FirecrawlDocument,
    FirecrawlSearchResult,
)
from src.career_assistant.interview_library.public_web_collection import (  # noqa: E402
    PublicWebCollectionCoordinator,
)


ORG_ID = UUID("00000000-0000-0000-0000-000000000181")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000182")
BODY = "# Agent 开发一面\n\n1. 如何设计 Agent memory？\n2. Tool calling 如何重试？"


class FakeRepository:
    def __init__(self) -> None:
        self.jobs = {}
        self.documents_by_url = {}
        self.documents_by_id = {}
        self.candidates = {}
        self.sources = []

    def create_collection_job(self, **kwargs):  # type: ignore[no-untyped-def]
        now = datetime.now(UTC)
        metadata_json = dict(kwargs.pop("metadata_json", {}) or {})
        job = SimpleNamespace(
            id=uuid4(), status=CollectionJobStatus.QUEUED,
            started_at=None, completed_at=None, error_code=None, error_message=None,
            created_at=now, updated_at=now, metadata_json=metadata_json,
            **kwargs,
        )
        self.jobs[job.id] = job
        return job

    def claim_collection_job(self, organization_id, job_id):  # type: ignore[no-untyped-def]
        job = self.jobs[job_id]
        if job.organization_id != organization_id or job.status is not CollectionJobStatus.QUEUED:
            return None
        job.status = CollectionJobStatus.RUNNING
        return job

    def update_collection_job_status(self, organization_id, job_id, **kwargs):  # type: ignore[no-untyped-def]
        job = self.jobs[job_id]
        assert job.organization_id == organization_id
        job.status = kwargs["status"]
        job.error_code = kwargs.get("error_code")
        job.error_message = kwargs.get("error_message")
        job.metadata_json.update(kwargs.get("metadata_json") or {})
        return job

    def register_web_document(self, organization_id, **kwargs):  # type: ignore[no-untyped-def]
        url = kwargs["canonical_url"]
        existing = self.documents_by_url.get(url)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        metadata_json = dict(kwargs.pop("metadata_json", {}) or {})
        metadata_json["latest_keyword"] = kwargs["keyword"]
        document = SimpleNamespace(
            id=uuid4(), organization_id=organization_id,
            status=InterviewWebDocumentStatus.DISCOVERED,
            normalized_markdown=None, content_hash=None, imported_experience_id=None,
            attempt_count=0, next_retry_at=None, metadata_json=metadata_json,
            first_seen_at=now, **kwargs,
        )
        self.documents_by_url[url] = document
        self.documents_by_id[document.id] = document
        return document

    def create_collection_candidate(self, organization_id, **kwargs):  # type: ignore[no-untyped-def]
        candidate = SimpleNamespace(
            id=uuid4(), status=kwargs.pop("status", CollectionCandidateStatus.DISCOVERED),
            extracted_markdown=kwargs.pop("extracted_markdown", None),
            content_hash=kwargs.pop("content_hash", None),
            error_code=None, error_message=None,
            metadata_json=dict(kwargs.pop("metadata_json", {}) or {}), **kwargs,
        )
        self.candidates[candidate.id] = candidate
        return candidate

    def update_collection_candidate(self, organization_id, candidate_id, **kwargs):  # type: ignore[no-untyped-def]
        candidate = self.candidates[candidate_id]
        for key in ("status", "extracted_markdown", "content_hash", "error_code", "error_message"):
            if key in kwargs and kwargs[key] is not None:
                setattr(candidate, key, kwargs[key])
        candidate.metadata_json.update(kwargs.get("metadata_json") or {})
        return candidate

    update_collection_candidate_status = update_collection_candidate

    def claim_web_document(self, organization_id, document_id, *, now):  # type: ignore[no-untyped-def]
        document = self.documents_by_id[document_id]
        if document.status not in {InterviewWebDocumentStatus.DISCOVERED, InterviewWebDocumentStatus.RETRYABLE_FAILED}:
            return None
        document.status = InterviewWebDocumentStatus.FETCHING
        document.attempt_count += 1
        return document

    def mark_web_document_fetched(self, organization_id, document_id, **kwargs):  # type: ignore[no-untyped-def]
        document = self.documents_by_id[document_id]
        document.status = InterviewWebDocumentStatus.FETCHED
        document.normalized_markdown = kwargs["normalized_markdown"]
        document.content_hash = kwargs["content_hash"]
        document.metadata_json.update(kwargs.get("metadata_json") or {})
        return document

    def mark_web_document_failed(self, organization_id, document_id, **kwargs):  # type: ignore[no-untyped-def]
        document = self.documents_by_id[document_id]
        document.status = (InterviewWebDocumentStatus.RETRYABLE_FAILED
                           if kwargs["retryable"] else InterviewWebDocumentStatus.PERMANENT_FAILED)
        return document

    def mark_web_document_filtered(self, organization_id, document_id, **kwargs):  # type: ignore[no-untyped-def]
        document = self.documents_by_id[document_id]
        document.status = InterviewWebDocumentStatus.FILTERED
        document.metadata_json.update(kwargs.get("metadata_json") or {})
        return document

    def find_imported_document_by_content_hash(self, organization_id, content_hash):  # type: ignore[no-untyped-def]
        return next((item for item in self.documents_by_id.values()
                     if item.content_hash == content_hash and item.imported_experience_id), None)

    def get_experience_by_source_content_hash(self, organization_id, content_hash):  # type: ignore[no-untyped-def]
        return None

    def list_web_documents_by_content_hash(self, organization_id, content_hash):  # type: ignore[no-untyped-def]
        return sorted(
            (item for item in self.documents_by_id.values() if item.content_hash == content_hash),
            key=lambda item: item.first_seen_at,
        )

    def add_experience_source(self, organization_id, experience_id, **kwargs):  # type: ignore[no-untyped-def]
        source = SimpleNamespace(experience_id=experience_id, **kwargs)
        self.sources.append(source)
        return source

    def list_experience_sources(self, organization_id, experience_id):  # type: ignore[no-untyped-def]
        return [item for item in self.sources if item.experience_id == experience_id]

    def link_web_document_experience(self, organization_id, document_id, experience_id, *, status):  # type: ignore[no-untyped-def]
        document = self.documents_by_id[document_id]
        document.imported_experience_id = experience_id
        document.status = status
        return document

    def mark_web_document_duplicate_pending(self, organization_id, document_id, **kwargs):  # type: ignore[no-untyped-def]
        document = self.documents_by_id[document_id]
        document.status = InterviewWebDocumentStatus.DUPLICATE
        document.metadata_json.update(kwargs.get("metadata_json") or {})
        return document


class FakeFirecrawlClient:
    def __init__(self, results, documents):  # type: ignore[no-untyped-def]
        self.results = tuple(results)
        self.documents = dict(documents)
        self.scrape_calls = []

    def search(self, keyword, *, limit):  # type: ignore[no-untyped-def]
        return self.results[:limit]

    def scrape(self, source_url):  # type: ignore[no-untyped-def]
        self.scrape_calls.append(source_url)
        return self.documents[source_url]


class ValidAnalyzer:
    def analyze(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            is_valid_interview=True, company_name="示例科技", role_name="Agent 开发工程师",
            questions=("如何设计 Agent memory？",), confidence=0.9,
            interview_date=None, summary_text="Agent 开发面经", tags=("Agent",),
            normalized_markdown=BODY,
            as_metadata=lambda: {"is_valid_interview": True, "confidence": 0.9},
        )


class FakeLibraryService:
    def __init__(self) -> None:
        self.ingested = []

    def ingest(self, organization_id, draft, *, trigger_type, **kwargs):  # type: ignore[no-untyped-def]
        experience = SimpleNamespace(id=uuid4(), draft=draft, ownership=kwargs)
        self.ingested.append(experience)
        return experience


def result(url: str) -> FirecrawlSearchResult:
    return FirecrawlSearchResult(title="Agent 面经", url=url, snippet="一面题目")


def document(url: str) -> FirecrawlDocument:
    return FirecrawlDocument(
        source_url=url, final_url=url, title="Agent 面经", markdown=BODY,
        image_urls=(), metadata={"cacheState": "hit"},
    )


def main() -> None:
    repository = FakeRepository()
    known = repository.register_web_document(
        ORG_ID, canonical_url="https://known.example/interview",
        source_url="https://known.example/interview", source_platform="known.example",
        title="已知面经", search_snippet="已入库", keyword="旧关键词", search_rank=1,
    )
    known.status = InterviewWebDocumentStatus.IMPORTED
    known.imported_experience_id = uuid4()

    firecrawl = FakeFirecrawlClient(
        [result("https://known.example/interview"), result("https://a.example/agent"),
         result("https://mirror.example/agent")],
        {"https://a.example/agent": document("https://a.example/agent"),
         "https://mirror.example/agent": document("https://mirror.example/agent")},
    )
    library = FakeLibraryService()
    coordinator = PublicWebCollectionCoordinator(
        repository, library, firecrawl, ValidAnalyzer(),
        url_validator=lambda _url: None,
    )
    job = coordinator.create_job(
        ORG_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="agent开发面经",
        requested_limit=5,
    )
    coordinator.run(ORG_ID, job.id)

    assert firecrawl.scrape_calls == ["https://a.example/agent", "https://mirror.example/agent"]
    assert len(library.ingested) == 1
    assert [source.is_primary for source in repository.sources] == [True, True, False]
    summary = repository.jobs[job.id].metadata_json["summary"]
    assert summary == {
        "discovered_count": 3, "known_url_count": 1, "scraped_count": 2,
        "duplicate_count": 1, "valid_count": 1, "imported_count": 1,
        "filtered_count": 0, "failed_count": 0,
    }, summary

    reusable_repository = FakeRepository()
    fetched = reusable_repository.register_web_document(
        ORG_ID, canonical_url="https://reuse.example/agent",
        source_url="https://reuse.example/agent", source_platform="reuse.example",
        title="复用正文", search_snippet="一面", keyword="旧关键词", search_rank=1,
    )
    fetched.status = InterviewWebDocumentStatus.FETCHED
    fetched.normalized_markdown = BODY
    from src.career_assistant.interview_library.public_web import hash_public_markdown
    fetched.content_hash = hash_public_markdown(BODY)
    reusable_firecrawl = FakeFirecrawlClient([result(fetched.canonical_url)], {})
    reusable_library = FakeLibraryService()
    reusable = PublicWebCollectionCoordinator(
        reusable_repository, reusable_library, reusable_firecrawl, ValidAnalyzer(),
        url_validator=lambda _url: None,
    )
    retry_job = reusable.create_job(
        ORG_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="agent开发面经",
        requested_limit=5,
    )
    reusable.run(ORG_ID, retry_job.id)
    assert reusable_firecrawl.scrape_calls == []
    assert len(reusable_library.ingested) == 1
    print("public_web_collection_orchestration_ok")


if __name__ == "__main__":
    main()
