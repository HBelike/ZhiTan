"""我的求职记忆 API 的所有权和用户控制测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.career_memory import CareerMemoryStatus, CareerMemoryType
from src.career_assistant.persistence.records import CareerMemoryItemRecord
from src.career_assistant.web import memory_router, router as career_router


def item(*, status="candidate"):
    now = datetime.now(UTC)
    return CareerMemoryItemRecord(
        id=uuid4(), organization_id=uuid4(), actor_id=uuid4(), career_space_id=uuid4(),
        memory_type="award", normalized_value={"summary": "一等奖"},
        display_text="获得一等奖", source_kind="explicit_user_statement", status=status,
        valid_from=now, created_at=now, updated_at=now,
    )


def client_with(repository, service=None, **extra_services):
    app = FastAPI()
    app.include_router(memory_router.router)
    services = SimpleNamespace(
        memory_repository=repository,
        memory_service=service or SimpleNamespace(),
        **extra_services,
    )
    actor = SimpleNamespace(organization_id=uuid4(), actor_id=uuid4())
    return app, services, actor


def test_owner_can_create_manual_active_memory() -> None:
    space_id = uuid4()
    created = item(status="active")

    class Repository:
        def list_spaces(self, organization_id, actor_id):
            self.scope = (organization_id, actor_id)
            return (SimpleNamespace(id=space_id),)

        def create_memory(self, organization_id, actor_id, draft, status):
            self.created = (organization_id, actor_id, draft, status)
            return created

    repository = Repository()
    app, services, actor = client_with(repository)
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).post(
            "/api/career/memories",
            json={
                "memory_type": "award",
                "career_space_id": str(space_id),
                "display_text": "全国大学生创新竞赛一等奖",
            },
        )

    assert response.status_code == 201
    assert repository.created[2].memory_type is CareerMemoryType.AWARD
    assert repository.created[2].normalized_value == {
        "summary": "全国大学生创新竞赛一等奖"
    }
    assert repository.created[3] is CareerMemoryStatus.ACTIVE
    assert repository.scope == (actor.organization_id, actor.actor_id)


def test_manual_memory_rejects_other_actors_career_space() -> None:
    class Repository:
        def list_spaces(self, *args):
            return ()

    app, services, actor = client_with(Repository())
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).post(
            "/api/career/memories",
            json={
                "memory_type": "personal_advantage",
                "career_space_id": str(uuid4()),
                "display_text": "擅长把复杂需求拆成可交付任务",
            },
        )

    assert response.status_code == 404


def test_latest_existing_resumes_can_be_queued_for_memory_import() -> None:
    older = SimpleNamespace(id=uuid4(), display_name="后端简历", version=1)
    latest = SimpleNamespace(id=uuid4(), display_name="后端简历", version=2)
    another = SimpleNamespace(id=uuid4(), display_name="产品简历", version=1)

    class ContextRepository:
        def list_candidate_profiles(self, actor_id, *, limit):
            self.call = (actor_id, limit)
            return [older, another, latest]

    class ExtractionService:
        def __init__(self):
            self.calls = []

        def enqueue_resume(self, organization_id, actor_id, profile_id, version):
            self.calls.append((organization_id, actor_id, profile_id, version))
            return uuid4()

    context_repository = ContextRepository()
    extraction_service = ExtractionService()
    app, services, actor = client_with(
        SimpleNamespace(),
        context_repository=context_repository,
        memory_extraction_service=extraction_service,
    )
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).post("/api/career/memory-imports/resumes")

    assert response.status_code == 202
    assert response.json()["queued_profile_count"] == 2
    assert {(call[2], call[3]) for call in extraction_service.calls} == {
        (latest.id, 2),
        (another.id, 1),
    }
    assert context_repository.call == (actor.actor_id, 100)


def test_confirmed_resume_save_does_not_enqueue_career_memory() -> None:
    profile = SimpleNamespace(
        id=uuid4(),
        version=3,
        display_name="后端简历",
        source_filename="resume-v3.pdf",
        resume_outline="负责支付系统",
        created_at=datetime.now(UTC),
    )
    actor = SimpleNamespace(organization_id=uuid4(), actor_id=uuid4())
    context_repository = SimpleNamespace(create_candidate_profile=lambda *args, **kwargs: profile)
    services = SimpleNamespace(
        context_repository=context_repository,
        redactor=SimpleNamespace(redact=lambda value: value),
    )
    payload = career_router.CreateCandidateProfileRequest(
        display_name="后端简历",
        source_filename="resume-v3.pdf",
        resume_outline="负责支付系统",
    )

    with (
        patch.object(career_router, "get_career_services", return_value=services),
        patch.object(career_router, "get_request_actor", return_value=actor),
    ):
        result = career_router.create_candidate_profile(payload, SimpleNamespace())

    assert result["id"] == str(profile.id)


def test_candidate_can_be_confirmed_by_owner() -> None:
    candidate = item(status="active")

    class Repository:
        def confirm_candidate(self, organization_id, actor_id, memory_id):
            self.call = (organization_id, actor_id, memory_id)
            return candidate

    repository = Repository()
    app, services, actor = client_with(repository)
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).post(f"/api/career/memories/{candidate.id}/confirm")
    assert response.status_code == 200
    assert response.json()["item"]["status"] == "active"
    assert repository.call[0:2] == (actor.organization_id, actor.actor_id)


def test_other_actor_cannot_patch_memory() -> None:
    memory_id = uuid4()

    class Service:
        def correct(self, *args, **kwargs):
            raise LookupError("求职记忆不存在或已经失效")

    app, services, actor = client_with(SimpleNamespace(), Service())
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).patch(
            f"/api/career/memories/{memory_id}",
            json={"display_text": "越权修改", "normalized_value": {"summary": "越权修改"}},
        )
    assert response.status_code == 404


def test_deleted_memory_usage_does_not_restore_fact_text() -> None:
    class Repository:
        def list_turn_usages(self, *args):
            return ({
                "memory_id": None,
                "memory_type": "award",
                "source_kind": "explicit_user_statement",
                "created_at": datetime.now(UTC),
                "display_text": None,
                "candidate_profile_version": None,
                "candidate_profile_name": None,
                "source_conversation_created_at": None,
            },)

    app, services, actor = client_with(Repository())
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).get(f"/api/career/turns/{uuid4()}/memory-usages")
    assert response.status_code == 200
    assert response.json()["items"][0]["display_text"] == "该记忆已由用户删除"
    assert "一等奖" not in response.text
