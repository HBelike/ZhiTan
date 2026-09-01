"""长期求职记忆写入来源与候选状态策略测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.career_assistant.career_memory import CareerMemoryDraft, CareerMemoryType
from src.career_assistant.persistence.memory_repository import (
    CareerMemoryJobRecord,
    CareerMemoryRepository,
)


@pytest.mark.parametrize("source_kind", ["assistant", "job_description", "web", "interview", "tool"])
def test_untrusted_sources_cannot_create_active_memory(source_kind: str) -> None:
    draft = CareerMemoryDraft(
        memory_type=CareerMemoryType.PERSONAL_ADVANTAGE,
        normalized_value={"summary": "擅长架构"},
        display_text="擅长架构",
        source_kind=source_kind,
    )
    with pytest.raises(ValueError, match="来源"):
        draft.validate()


class Result:
    def __init__(self, row=None) -> None:
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row


class Connection:
    def __init__(self, rows) -> None:
        self.rows = list(rows)
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((" ".join(str(statement).split()), dict(parameters or {})))
        return Result(self.rows.pop(0) if self.rows else None)


class Database:
    def __init__(self, rows) -> None:
        self.connection = Connection(rows)

    @contextmanager
    def transaction(self):
        yield self.connection


def memory_row(job, draft, status):
    now = datetime.now(UTC)
    return {
        "id": uuid4(), "organization_id": job.organization_id, "actor_id": job.actor_id,
        "career_space_id": draft.career_space_id, "memory_type": draft.memory_type.value,
        "normalized_value_json": draft.normalized_value, "display_text": draft.display_text,
        "source_kind": draft.source_kind, "source_message_id": draft.source_message_id,
        "source_conversation_id": draft.source_conversation_id,
        "candidate_profile_id": draft.candidate_profile_id,
        "candidate_profile_version": draft.candidate_profile_version,
        "status": status, "supersedes_memory_id": None, "valid_from": now,
        "valid_to": None, "created_at": now, "updated_at": now,
    }


def test_chat_experience_is_candidate_until_confirmed() -> None:
    job = CareerMemoryJobRecord(
        id=uuid4(), organization_id=uuid4(), actor_id=uuid4(),
        conversation_id=uuid4(), turn_id=uuid4(), job_kind="turn_extraction",
        status="running", attempt_count=1, created_at=datetime.now(UTC),
    )
    draft = CareerMemoryDraft(
        memory_type=CareerMemoryType.WORK_EXPERIENCE,
        normalized_value={"summary": "负责支付系统"}, display_text="负责支付系统",
        source_kind="explicit_user_statement", source_conversation_id=job.conversation_id,
    )
    database = Database([memory_row(job, draft, "candidate")])
    CareerMemoryRepository(database).apply_extracted_memories(job, (draft,))
    assert database.connection.calls[-1][1]["status"] == "candidate"


def test_confirmed_resume_fact_is_written_active_without_conversation_source() -> None:
    profile_id = uuid4()
    job = CareerMemoryJobRecord(
        id=uuid4(), organization_id=uuid4(), actor_id=uuid4(),
        candidate_profile_id=profile_id, candidate_profile_version=2,
        job_kind="resume_indexing", status="running", attempt_count=1,
        created_at=datetime.now(UTC),
    )
    draft = CareerMemoryDraft(
        memory_type=CareerMemoryType.EDUCATION,
        normalized_value={"summary": "某大学硕士"}, display_text="某大学硕士",
        source_kind="confirmed_resume", candidate_profile_id=profile_id,
        candidate_profile_version=2,
    )
    database = Database([None, memory_row(job, draft, "active")])
    CareerMemoryRepository(database).apply_extracted_memories(job, (draft,))
    params = database.connection.calls[-1][1]
    assert params["status"] == "active"
    assert params["source_conversation_id"] is None


def test_new_resume_version_supersedes_active_memories_from_same_named_resume() -> None:
    profile_id = uuid4()
    job = CareerMemoryJobRecord(
        id=uuid4(), organization_id=uuid4(), actor_id=uuid4(),
        candidate_profile_id=profile_id, candidate_profile_version=3,
        job_kind="resume_indexing", status="running", attempt_count=1,
        created_at=datetime.now(UTC),
    )
    draft = CareerMemoryDraft(
        memory_type=CareerMemoryType.WORK_EXPERIENCE,
        normalized_value={"summary": "负责新版支付系统"},
        display_text="负责新版支付系统",
        source_kind="confirmed_resume",
        candidate_profile_id=profile_id,
        candidate_profile_version=3,
    )
    database = Database([None, memory_row(job, draft, "active")])

    CareerMemoryRepository(database).apply_extracted_memories(job, (draft,))

    supersede_sql, supersede_params = database.connection.calls[0]
    assert "old_profile.display_name = current_profile.display_name" in supersede_sql
    assert "memory.status = 'active'" in supersede_sql
    assert supersede_params["candidate_profile_id"] == profile_id
