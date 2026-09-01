from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.online_assessment.contracts import (
    AssessmentProblem,
    AssessmentSolution,
    InterfaceKind,
    ProgrammingLanguage,
)
from src.career_assistant.online_assessment.solution_service import SolveResult
from src.career_assistant.online_assessment.web import (
    get_online_assessment_archive_service,
    get_online_assessment_service,
    router,
)


def problem() -> AssessmentProblem:
    return AssessmentProblem(
        title="求和",
        statement="读取两个整数并输出它们的和，输入输出格式完整。",
        language=ProgrammingLanguage.PYTHON,
        interface_kind=InterfaceKind.STDIN_STDOUT,
        confidence=0.9,
    )


def solution() -> AssessmentSolution:
    return AssessmentSolution(
        approach_markdown="相加。",
        code="a,b=map(int,input().split());print(a+b)",
        language=ProgrammingLanguage.PYTHON,
        time_complexity="O(1)",
        space_complexity="O(1)",
    )


class FakeAssessmentService:
    def analyze(self, capture):
        del capture
        return problem()

    def solve(self, value):
        del value
        return SolveResult(status="ready", solution=solution())


class FakeArchiveService:
    def preview(self, **kwargs):
        del kwargs
        return SimpleNamespace(markdown="# 预览", question_count=1, passed_question_count=0)

    def archive(self, **kwargs):
        del kwargs
        return SimpleNamespace(id=uuid4(), job_name="后端开发 · 日期待补充")


def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_online_assessment_service] = lambda: FakeAssessmentService()
    app.dependency_overrides[get_online_assessment_archive_service] = lambda: FakeArchiveService()
    return TestClient(app)


def test_analyze_echoes_run_version() -> None:
    response = client().post(
        "/api/career/online-assessments/analyze",
        json={"run_version": 4, "capture": {"visible_text": "题面"}},
    )

    assert response.status_code == 200
    assert response.json()["run_version"] == 4
    assert response.json()["problem"]["title"] == "求和"


def test_solve_stream_returns_versioned_ndjson() -> None:
    response = client().post(
        "/api/career/online-assessments/solve/stream",
        json={"run_version": 2, "problem": problem().model_dump(mode="json")},
    )

    lines = [line for line in response.text.splitlines() if line]
    assert response.status_code == 200
    assert '"type":"solution"' in lines[1]
    assert '"run_version":2' in lines[1]


def test_archive_endpoint_returns_created_experience() -> None:
    response = client().post(
        "/api/career/online-assessments/archive",
        json={
            "company_name": "示例公司",
            "role_name": "后端开发",
            "items": [{"problem": problem().model_dump(mode="json"), "solution": solution().model_dump(mode="json")}],
        },
    )

    assert response.status_code == 201
    assert response.json()["experience_id"]
