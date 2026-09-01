"""面经永久删除接口契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.web import router as career_router


class FakeInterviewLibraryService:
    def __init__(self, experience_id: UUID) -> None:
        self.experience_id = experience_id
        self.calls: list[tuple[UUID, UUID, UUID, bool]] = []

    def delete_experience(
        self,
        organization_id: UUID,
        experience_id: UUID,
        *,
        actor_id: UUID,
        can_manage_all: bool,
    ) -> None:
        self.calls.append((organization_id, experience_id, actor_id, can_manage_all))
        if experience_id != self.experience_id:
            raise LookupError("面经不存在或无访问权限")


def test_delete_interview_experience_returns_real_result_and_404(monkeypatch) -> None:
    experience_id = uuid4()
    service = FakeInterviewLibraryService(experience_id)
    app = FastAPI()
    app.include_router(career_router.router)
    monkeypatch.setattr(
        career_router,
        "get_career_services",
        lambda _request: SimpleNamespace(interview_library_service=service),
    )

    with TestClient(app) as client:
        deleted = client.delete(
            f"/api/career/interview-library/experiences/{experience_id}",
        )
        missing = client.delete(
            f"/api/career/interview-library/experiences/{uuid4()}",
        )

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert service.calls[0] == (
        career_router.DEFAULT_ORGANIZATION_ID,
        experience_id,
        career_router.DEFAULT_ACTOR_ID,
        False,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "面经不存在或无访问权限"
