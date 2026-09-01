from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.career_assistant.interview_library.models import (
    InterviewExperienceStatus,
    InterviewSourceType,
)
from src.career_assistant.live_interview.archive import (
    LiveInterviewArchiveService,
    build_archive_markdown,
    normalize_question_key,
)


def _session_payload(
    session_id: UUID,
    *,
    started_at: datetime,
    ended_at: datetime,
) -> dict[str, object]:
    return {
        "id": str(session_id),
        "started_at": started_at,
        "ended_at": ended_at,
        "created_at": started_at,
    }


class FakeLiveRepository:
    def __init__(self, histories: dict[UUID, dict[str, object]]) -> None:
        self.histories = histories

    def history(self, organization_id, actor_id, session_id):
        return self.histories.get(session_id)


class FakeInterviewRepository:
    def __init__(self, existing=None) -> None:
        self.existing = existing

    def get_experience_by_company_and_job_name(self, organization_id, company_name, job_name):
        return self.existing


class FakeInterviewService:
    def __init__(self) -> None:
        self.ingested = None
        self.updated = None

    def ingest(self, organization_id, draft, *, trigger_type, **ownership):
        self.ingested = (organization_id, draft, trigger_type, ownership)
        return _experience(
            company_name=draft.company_name,
            role_name=draft.role_name,
            interview_date=draft.interview_date,
            markdown_content=draft.markdown_content,
            source_type=draft.source_type,
        )

    def update_markdown(
        self,
        organization_id,
        experience_id,
        *,
        actor_id,
        can_manage_all,
        markdown_content,
        summary_text,
        tags,
    ):
        self.updated = {
            "organization_id": organization_id,
            "experience_id": experience_id,
            "markdown_content": markdown_content,
            "summary_text": summary_text,
            "tags": tags,
            "actor_id": actor_id,
            "can_manage_all": can_manage_all,
        }
        return SimpleNamespace(
            **{
                **vars(self.repository.existing),
                "markdown_content": markdown_content,
                "summary_text": summary_text,
                "tags": tags,
            }
        )


def _experience(
    *,
    company_name: str = "示例公司",
    role_name: str = "后端开发",
    interview_date: date = date(2026, 8, 24),
    markdown_content: str = "",
    source_type: InterviewSourceType = InterviewSourceType.AUTHENTICATED_SESSION,
):
    now = datetime(2026, 8, 24, 8, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid4(),
        company_name=company_name,
        role_name=role_name,
        interview_date=interview_date,
        job_name=f"{role_name} · {interview_date.isoformat()}",
        markdown_content=markdown_content,
        summary_text=None,
        tags=("面试大师",),
        source_type=source_type,
        status=InterviewExperienceStatus.INDEXED,
        created_at=now,
        updated_at=now,
    )


def _service(histories, existing=None):
    interview_repository = FakeInterviewRepository(existing)
    interview_service = FakeInterviewService()
    interview_service.repository = interview_repository
    service = LiveInterviewArchiveService(
        live_repository=FakeLiveRepository(histories),
        interview_repository=interview_repository,
        interview_service=interview_service,
    )
    return service, interview_service


def test_question_key_normalizes_spacing_case_and_terminal_punctuation() -> None:
    assert normalize_question_key(" Agent 和  Workflow 有什么区别？ ") == normalize_question_key(
        "agent 和 workflow 有什么区别?"
    )


def test_build_archive_markdown_contains_only_questions_and_time_metadata() -> None:
    markdown = build_archive_markdown(
        company_name="字节跳动",
        role_name="AI Agent 开发工程师",
        interview_date=date(2026, 8, 24),
        started_at=datetime(2026, 8, 24, 6, 30, tzinfo=UTC),
        ended_at=datetime(2026, 8, 24, 7, 42, tzinfo=UTC),
        questions=("请介绍 Agent 项目。", "如何处理长上下文？"),
    )

    assert "# 字节跳动 · AI Agent 开发工程师面经" in markdown
    assert "- 开始时间：14:30" in markdown
    assert "- 结束时间：15:42" in markdown
    assert "- 面试时长：72 分钟" in markdown
    assert "1. 请介绍 Agent 项目。" in markdown
    assert "回答" not in markdown


def test_archive_aggregates_all_sessions_and_keeps_failed_questions() -> None:
    first_id, second_id = uuid4(), uuid4()
    start = datetime(2026, 8, 24, 6, 30, tzinfo=UTC)
    histories = {
        first_id: {
            "session": _session_payload(first_id, started_at=start, ended_at=start + timedelta(minutes=30)),
            "answers": [
                {"question_version": 1, "original_question": "介绍一下你的 Agent 项目？", "status": "completed"},
                {"question_version": 2, "original_question": "如何处理长上下文？", "status": "failed"},
            ],
        },
        second_id: {
            "session": _session_payload(
                second_id,
                started_at=start + timedelta(minutes=31),
                ended_at=start + timedelta(minutes=60),
            ),
            "answers": [
                {"question_version": 1, "original_question": "介绍一下你的 Agent 项目?", "status": "cancelled"},
                {"question_version": 2, "original_question": "Agent 和 Workflow 有什么区别？", "status": "completed"},
            ],
        },
    }
    service, interview_service = _service(histories)

    result = service.archive(
        organization_id=uuid4(),
        actor_id=uuid4(),
        session_ids=(first_id, second_id),
        company_name="字节跳动",
        role_name="AI Agent 开发工程师",
        interview_date=date(2026, 8, 24),
    )

    assert result.questions == (
        "介绍一下你的 Agent 项目？",
        "如何处理长上下文？",
        "Agent 和 Workflow 有什么区别？",
    )
    assert interview_service.ingested is not None
    assert interview_service.ingested[1].source_type is InterviewSourceType.AUTHENTICATED_SESSION
    assert "回答" not in interview_service.ingested[1].markdown_content


def test_archive_merges_existing_questions_without_repeating_them() -> None:
    session_id = uuid4()
    start = datetime(2026, 8, 24, 6, 30, tzinfo=UTC)
    existing_markdown = build_archive_markdown(
        company_name="示例公司",
        role_name="后端开发",
        interview_date=date(2026, 8, 24),
        started_at=start,
        ended_at=start + timedelta(minutes=20),
        questions=("如何保证接口幂等？",),
    )
    existing = _experience(markdown_content=existing_markdown)
    histories = {
        session_id: {
            "session": _session_payload(
                session_id,
                started_at=start + timedelta(minutes=30),
                ended_at=start + timedelta(minutes=60),
            ),
            "answers": [
                {"question_version": 1, "original_question": "如何保证接口幂等?", "status": "completed"},
                {"question_version": 2, "original_question": "如何设计消息重试？", "status": "completed"},
            ],
        }
    }
    service, interview_service = _service(histories, existing)

    result = service.archive(
        organization_id=uuid4(),
        actor_id=uuid4(),
        session_ids=(session_id,),
        company_name="示例公司",
        role_name="后端开发",
        interview_date=date(2026, 8, 24),
    )

    saved = interview_service.updated["markdown_content"]
    assert saved.count("如何保证接口幂等") == 1
    assert "2. 如何设计消息重试？" in saved
    assert result.question_count == 2


def test_archive_rejects_unknown_session_and_empty_questions() -> None:
    service, _ = _service({})
    with pytest.raises(LookupError, match="会话不存在"):
        service.archive(
            organization_id=uuid4(),
            actor_id=uuid4(),
            session_ids=(uuid4(),),
            company_name="示例公司",
            role_name="后端开发",
            interview_date=date(2026, 8, 24),
        )

    session_id = uuid4()
    now = datetime(2026, 8, 24, 6, 30, tzinfo=UTC)
    empty_service, _ = _service(
        {
            session_id: {
                "session": _session_payload(session_id, started_at=now, ended_at=now),
                "answers": [],
            }
        }
    )
    with pytest.raises(ValueError, match="未识别到可归档的问题"):
        empty_service.archive(
            organization_id=uuid4(),
            actor_id=uuid4(),
            session_ids=(session_id,),
            company_name="示例公司",
            role_name="后端开发",
            interview_date=date(2026, 8, 24),
        )
