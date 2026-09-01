"""面经库全平台公开读取与创建者写权限契约测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.interview_library.models import (
    InterviewExperienceStatus,
    InterviewSourceType,
)
from src.career_assistant.web import router as career_router
from src.platform_access.contracts import PlatformRole


def _experience(*, owner_id=None):
    now = datetime(2026, 8, 28, 10, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        created_by_actor_id=owner_id,
        company_id=uuid4(),
        company_name="公开公司",
        job_name="AI Agent · 日期待补充",
        role_name="AI Agent",
        interview_date=None,
        source_type=InterviewSourceType.PUBLIC_URL,
        source_platform="公开网页",
        source_url="https://example.com/interview",
        summary_text="公开面经",
        markdown_content="# 面经",
        tags=("Agent",),
        status=InterviewExperienceStatus.INDEXED,
        chunking_version="v1",
        indexed_at=now,
        created_at=now,
        updated_at=now,
    )


class _Repository:
    def __init__(self, experience) -> None:
        self.experience = experience
        self.tree_calls = []

    def get_public_experience(self, experience_id):
        return self.experience if experience_id == self.experience.id else None

    def list_public_experience_sources(self, experience_id):
        assert experience_id == self.experience.id
        return []

    def list_tree(self, *, query=None):
        self.tree_calls.append(query)
        return [{"id": "company:公开公司", "label": "公开公司", "children": []}]


def _client(monkeypatch, *, experience, actor):
    repository = _Repository(experience)
    app = FastAPI()
    app.include_router(career_router.router)
    monkeypatch.setattr(career_router, "get_request_actor", lambda: actor)
    monkeypatch.setattr(
        career_router,
        "get_career_services",
        lambda _request: SimpleNamespace(interview_library_repository=repository),
    )
    return TestClient(app), repository


def test_cross_organization_experience_is_public_but_other_user_cannot_write(monkeypatch) -> None:
    owner_id = uuid4()
    experience = _experience(owner_id=owner_id)
    actor = career_router.CareerRequestActor(
        organization_id=uuid4(),
        actor_id=uuid4(),
        role=PlatformRole.USER,
    )
    client, repository = _client(monkeypatch, experience=experience, actor=actor)

    with client:
        detail = client.get(
            f"/api/career/interview-library/experiences/{experience.id}",
        )
        tree = client.get("/api/career/interview-library/tree?query=Agent")

    assert detail.status_code == 200
    assert detail.json()["can_write"] is False
    assert tree.status_code == 200
    assert repository.tree_calls == ["Agent"]


def test_admin_can_write_legacy_experience_without_creator(monkeypatch) -> None:
    experience = _experience(owner_id=None)
    actor = career_router.CareerRequestActor(
        organization_id=uuid4(),
        actor_id=uuid4(),
        role=PlatformRole.ADMIN,
    )
    client, _repository = _client(monkeypatch, experience=experience, actor=actor)

    with client:
        detail = client.get(
            f"/api/career/interview-library/experiences/{experience.id}",
        )

    assert detail.status_code == 200
    assert detail.json()["can_write"] is True


def test_ownership_migration_keeps_history_nullable_and_records_collection_owner() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260828_28_interview_experience_ownership.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "20260827_27"' in migration
    assert "ALTER TABLE career_assistant.interview_experiences" in migration
    assert "ALTER TABLE career_assistant.interview_collection_jobs" in migration
    assert migration.count("created_by_actor_id UUID") == 2
    assert "ON DELETE SET NULL" in migration
    assert "UPDATE career_assistant.interview_experiences" not in migration
