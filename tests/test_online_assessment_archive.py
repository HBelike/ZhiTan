from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from src.career_assistant.interview_library.models import InterviewSourceType
from src.career_assistant.online_assessment.archive import (
    AssessmentArchiveItem,
    AssessmentArchiveService,
    build_assessment_markdown,
    merge_assessment_markdown,
)
from src.career_assistant.online_assessment.contracts import (
    AssessmentProblem,
    AssessmentSolution,
    InterfaceKind,
    ProgrammingLanguage,
)


def item(title: str = "两数之和") -> AssessmentArchiveItem:
    return AssessmentArchiveItem(
        problem=AssessmentProblem(
            title=title,
            statement="读取两个整数并输出它们的和。输入输出格式完整。",
            language=ProgrammingLanguage.PYTHON,
            interface_kind=InterfaceKind.STDIN_STDOUT,
            confidence=0.9,
        ),
        solution=AssessmentSolution(
            approach_markdown="直接相加。",
            code="a,b=map(int,input().split())\nprint(a+b)",
            language=ProgrammingLanguage.PYTHON,
            time_complexity="O(1)",
            space_complexity="O(1)",
        ),
    )


class FakeRepository:
    def __init__(self, existing=None) -> None:
        self.existing = existing

    def get_experience_by_company_and_job_name(self, organization_id, company_name, job_name):
        del organization_id, company_name, job_name
        return self.existing


class FakeLibraryService:
    def __init__(self, existing=None) -> None:
        self.existing = existing
        self.ingested = None
        self.updated = None

    def ingest(self, organization_id, draft, *, trigger_type, **ownership):
        self.ingested = (organization_id, draft, trigger_type, ownership)
        return SimpleNamespace(id=uuid4(), job_name=draft.job_name)

    def update_markdown(self, organization_id, experience_id, **kwargs):
        self.updated = (organization_id, experience_id, kwargs)
        return SimpleNamespace(id=experience_id, job_name=self.existing.job_name)


def test_build_archive_excludes_raw_capture_and_keeps_confirmed_solution() -> None:
    markdown = build_assessment_markdown(
        company_name="示例公司",
        role_name="后端开发",
        assessment_date=date(2026, 9, 1),
        items=(item(),),
    )

    assert "两数之和" in markdown
    assert "print(a+b)" in markdown
    assert "截图" not in markdown
    assert "完整运行日志" not in markdown
    assert "assessment-question:" in markdown


def test_merge_archive_deduplicates_same_question_hash() -> None:
    first = build_assessment_markdown(
        company_name="示例公司",
        role_name="后端开发",
        assessment_date=date(2026, 9, 1),
        items=(item(),),
    )
    duplicate = build_assessment_markdown(
        company_name="示例公司",
        role_name="后端开发",
        assessment_date=date(2026, 9, 1),
        items=(item(),),
    )

    assert merge_assessment_markdown(first, duplicate) == first


def test_archive_uses_online_assessment_source() -> None:
    repository = FakeRepository()
    library = FakeLibraryService()
    service = AssessmentArchiveService(repository, library)

    result = service.archive(
        organization_id=uuid4(),
        actor_id=uuid4(),
        can_manage_all=False,
        company_name="示例公司",
        role_name="后端开发",
        assessment_date=date(2026, 9, 1),
        items=(item(),),
    )

    assert result.id
    assert library.ingested[1].source_type is InterviewSourceType.ONLINE_ASSESSMENT
    assert library.ingested[2].value == "api_sync"
    assert library.ingested[1].job_name == "后端开发 · 2026-09-01 · 线上笔试"


def test_archive_appends_new_question_to_existing_assessment() -> None:
    original = build_assessment_markdown(
        company_name="示例公司",
        role_name="后端开发",
        assessment_date=date(2026, 9, 1),
        items=(item(),),
    )
    existing = SimpleNamespace(
        id=uuid4(),
        job_name="后端开发 · 2026-09-01 · 线上笔试",
        markdown_content=original,
        tags=("线上笔试",),
    )
    repository = FakeRepository(existing)
    library = FakeLibraryService(existing)
    service = AssessmentArchiveService(repository, library)

    service.archive(
        organization_id=uuid4(),
        actor_id=uuid4(),
        can_manage_all=False,
        company_name="示例公司",
        role_name="后端开发",
        assessment_date=date(2026, 9, 1),
        items=(item("最长子串"),),
    )

    assert "最长子串" in library.updated[2]["markdown_content"]
    assert library.updated[2]["markdown_content"].count("assessment-question:") == 4
