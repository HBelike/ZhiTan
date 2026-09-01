"""全网公开信息收集 API 的离线契约验证。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import src.career_assistant.web.router as career_router
from src.career_assistant.interview_library.models import (
    CollectionConnectorKind,
    CollectionJobStatus,
    InterviewCollectionJobRecord,
)


class FakePublicWebService:
    """只实现公开网页路由必需的服务形状。"""

    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.run_calls: list[tuple[UUID, UUID]] = []

    def public_web_ready(self) -> bool:
        return self.ready

    def create_public_web_import_job(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        keyword: str,
        requested_limit: int,
    ) -> InterviewCollectionJobRecord:
        now = datetime.now(UTC)
        return InterviewCollectionJobRecord(
            id=uuid4(),
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            platform_key="public_web",
            keyword=keyword,
            requested_limit=requested_limit,
            connector_kind=CollectionConnectorKind.PUBLIC_API,
            status=CollectionJobStatus.QUEUED,
            policy_decision="仅处理全网公开网页。",
            error_code=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
            metadata_json={
                "phase": "search",
                "progress_percent": 0,
                "summary": {
                    "found": 0,
                    "new": 0,
                    "parsed": 0,
                    "imported": 0,
                    "deduplicated": 0,
                    "skipped": 0,
                    "retryable_failed": 0,
                    "permanent_failed": 0,
                },
            },
        )

    def run_public_web_import(self, organization_id: UUID, job_id: UUID) -> None:
        self.run_calls.append((organization_id, job_id))


class FakeInterviewRepository:
    """验证面经详情会同时返回主来源与正文重复来源。"""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.experience_id = uuid4()
        self.experience = SimpleNamespace(
            id=self.experience_id,
            organization_id=career_router.DEFAULT_ORGANIZATION_ID,
            created_by_actor_id=career_router.DEFAULT_ACTOR_ID,
            company_id=uuid4(),
            company_name="公开资料",
            job_name=None,
            role_name="Agent 开发",
            interview_date=None,
            source_type=SimpleNamespace(value="public_url"),
            source_platform="知乎",
            source_url="https://example.com/a",
            summary_text="Agent 开发面经",
            markdown_content="1. Agent 如何管理工具调用？",
            tags=("Agent",),
            status=SimpleNamespace(value="ready"),
            chunking_version="v1",
            indexed_at=now,
            created_at=now,
            updated_at=now,
        )
        self.sources = [
            SimpleNamespace(
                id=uuid4(),
                canonical_url="https://example.com/a",
                source_url="https://example.com/a?from=search",
                source_platform="知乎",
                is_primary=True,
                discovery_keywords=("agent开发面经",),
                first_seen_at=now,
                last_seen_at=now,
            ),
            SimpleNamespace(
                id=uuid4(),
                canonical_url="https://mirror.example.com/a",
                source_url="https://mirror.example.com/a",
                source_platform="公开网页",
                is_primary=False,
                discovery_keywords=("agent开发面经", "AI Agent面试"),
                first_seen_at=now,
                last_seen_at=now,
            ),
        ]

    def get_experience(self, _organization_id: UUID, experience_id: UUID):  # type: ignore[no-untyped-def]
        return self.experience if experience_id == self.experience_id else None

    def get_public_experience(self, experience_id: UUID):
        return self.experience if experience_id == self.experience_id else None

    def list_experience_sources(
        self,
        _organization_id: UUID,
        experience_id: UUID,
    ):  # type: ignore[no-untyped-def]
        return self.sources if experience_id == self.experience_id else []

    def list_public_experience_sources(self, experience_id: UUID):
        return self.sources if experience_id == self.experience_id else []


def main() -> None:
    """验证未配置时 503、已配置时 202 且后台任务真正被调度。"""

    original_get_services = career_router.get_career_services
    app = FastAPI()
    app.include_router(career_router.router)
    current = FakePublicWebService(ready=False)
    repository = FakeInterviewRepository()

    def get_fake_services(_request):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            interview_collection_service=current,
            interview_library_repository=repository,
        )

    career_router.get_career_services = get_fake_services
    try:
        with TestClient(app) as client:
            unavailable = client.post(
                "/api/career/interview-library/public-web-imports",
                json={"keyword": "agent开发面经", "requested_limit": 10},
            )
            assert unavailable.status_code == 503, unavailable.text
            assert "FIRECRAWL_API_KEY" in unavailable.json()["detail"]

            current = FakePublicWebService(ready=True)
            accepted = client.post(
                "/api/career/interview-library/public-web-imports",
                json={"keyword": "agent开发面经", "requested_limit": 10},
            )
            assert accepted.status_code == 202, accepted.text
            payload = accepted.json()["job"]
            assert payload["platform_key"] == "public_web"
            assert payload["keyword"] == "agent开发面经"
            assert payload["requested_limit"] == 10
            assert payload["connector_kind"] == "public_api"
            assert payload["metadata"]["summary"]["imported"] == 0
            assert current.run_calls == [
                (career_router.DEFAULT_ORGANIZATION_ID, UUID(payload["id"]))
            ]

            invalid = client.post(
                "/api/career/interview-library/public-web-imports",
                json={"keyword": "agent开发面经", "requested_limit": 11},
            )
            assert invalid.status_code == 422, invalid.text

            detail = client.get(
                f"/api/career/interview-library/experiences/{repository.experience_id}"
            )
            assert detail.status_code == 200, detail.text
            sources = detail.json()["sources"]
            assert len(sources) == 2
            assert sources[0]["is_primary"] is True
            assert sources[1]["discovery_keywords"] == [
                "agent开发面经",
                "AI Agent面试",
            ]
    finally:
        career_router.get_career_services = original_get_services

    print("public_web_api_contract_ok")


if __name__ == "__main__":
    main()
