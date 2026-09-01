"""六类长期求职记忆的白名单与作用域仓储测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.career_assistant.career_memory import (
    CareerMemoryDraft,
    CareerMemoryStatus,
    CareerMemoryType,
)
from src.career_assistant.persistence.memory_repository import CareerMemoryRepository


class FakeResult:
    def __init__(self, *, row=None, rows=None, rowcount=1) -> None:
        self.row = row
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one(self):
        return self.row

    def one_or_none(self):
        return self.row

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((" ".join(str(statement).split()), dict(parameters or {})))
        return self.results.pop(0)


class FakeDatabase:
    def __init__(self, results) -> None:
        self.connection = FakeConnection(results)

    @contextmanager
    def transaction(self):
        yield self.connection


def test_job_intention_requires_career_space() -> None:
    repository = CareerMemoryRepository(FakeDatabase([]))
    draft = CareerMemoryDraft(
        memory_type=CareerMemoryType.JOB_INTENTION,
        normalized_value={"statement": "只找上海 Java 后端"},
        display_text="只找上海 Java 后端",
        source_kind="explicit_user_statement",
    )

    with pytest.raises(ValueError, match="职业空间"):
        repository.create_memory(uuid4(), uuid4(), draft)


def test_memory_rejects_types_outside_six_type_schema() -> None:
    with pytest.raises(ValueError):
        CareerMemoryType("hobby")


def test_normalized_value_rejects_unknown_fields() -> None:
    draft = CareerMemoryDraft(
        memory_type=CareerMemoryType.AWARD,
        normalized_value={"name": "一等奖", "private_note": "不应保存"},
        display_text="获得一等奖",
        source_kind="confirmed_resume",
    )

    with pytest.raises(ValueError, match="未知字段"):
        draft.validate()


def test_list_memories_requires_organization_actor_and_space() -> None:
    database = FakeDatabase([FakeResult(rows=[])])
    repository = CareerMemoryRepository(database)
    organization_id = uuid4()
    actor_id = uuid4()
    space_id = uuid4()

    repository.list_active_for_prompt(
        organization_id,
        actor_id,
        space_id,
        memory_types=(CareerMemoryType.JOB_INTENTION,),
    )

    sql, params = database.connection.calls[0]
    assert "organization_id = :organization_id" in sql
    assert "actor_id = :actor_id" in sql
    assert "career_space_id = :career_space_id" in sql
    assert params["organization_id"] == organization_id
    assert params["actor_id"] == actor_id
    assert params["career_space_id"] == space_id


def test_list_memories_omits_unset_optional_filters() -> None:
    database = FakeDatabase([FakeResult(rows=[])])
    repository = CareerMemoryRepository(database)
    organization_id = uuid4()
    actor_id = uuid4()

    repository.list_memories(organization_id, actor_id)

    sql, params = database.connection.calls[0]
    assert "organization_id = :organization_id" in sql
    assert "actor_id = :actor_id" in sql
    assert "career_space_id" not in sql
    assert "status = :status" not in sql
    assert params == {
        "organization_id": organization_id,
        "actor_id": actor_id,
    }


def test_list_memories_adds_only_requested_optional_filters() -> None:
    database = FakeDatabase([FakeResult(rows=[])])
    repository = CareerMemoryRepository(database)
    organization_id = uuid4()
    actor_id = uuid4()
    space_id = uuid4()

    repository.list_memories(
        organization_id,
        actor_id,
        career_space_id=space_id,
        status=CareerMemoryStatus.ACTIVE,
    )

    sql, params = database.connection.calls[0]
    assert "career_space_id = :career_space_id" in sql
    assert "status = :status" in sql
    assert params["career_space_id"] == space_id
    assert params["status"] == "active"


def test_supersede_active_updates_old_and_links_replacement_in_one_transaction() -> None:
    now = datetime.now(UTC)
    old_id = uuid4()
    organization_id = uuid4()
    actor_id = uuid4()
    replacement_id = uuid4()
    replacement_row = {
        "id": replacement_id,
        "organization_id": organization_id,
        "actor_id": actor_id,
        "career_space_id": uuid4(),
        "memory_type": "job_intention",
        "normalized_value_json": {"statement": "目标改为 AI 应用工程"},
        "display_text": "目标改为 AI 应用工程",
        "source_kind": "explicit_user_correction",
        "source_message_id": None,
        "source_conversation_id": None,
        "candidate_profile_id": None,
        "candidate_profile_version": None,
        "status": "active",
        "supersedes_memory_id": old_id,
        "valid_from": now,
        "valid_to": None,
        "created_at": now,
        "updated_at": now,
    }
    database = FakeDatabase([
        FakeResult(row={"id": old_id}),
        FakeResult(),
        FakeResult(row=replacement_row),
    ])
    repository = CareerMemoryRepository(database)
    replacement = CareerMemoryDraft(
        memory_type=CareerMemoryType.JOB_INTENTION,
        normalized_value={"statement": "目标改为 AI 应用工程"},
        display_text="目标改为 AI 应用工程",
        source_kind="explicit_user_correction",
        career_space_id=replacement_row["career_space_id"],
    )

    created = repository.supersede_active(
        organization_id,
        actor_id,
        old_id,
        replacement,
    )

    assert created.supersedes_memory_id == old_id
    assert "status = 'superseded'" in database.connection.calls[1][0]
    assert database.connection.calls[2][1]["supersedes_memory_id"] == old_id


def test_delete_conversation_can_forget_only_message_derived_memories() -> None:
    database = FakeDatabase([
        FakeResult(row={"id": uuid4()}),
        FakeResult(rowcount=3),
        FakeResult(),
        FakeResult(rowcount=1),
    ])
    repository = CareerMemoryRepository(database)
    organization_id = uuid4()
    actor_id = uuid4()
    conversation_id = uuid4()

    deleted, forgotten = repository.delete_conversation_with_memory_choice(
        organization_id,
        actor_id,
        conversation_id,
        forget_derived_memories=True,
    )

    assert deleted is True
    assert forgotten == 3
    memory_sql = database.connection.calls[1][0]
    assert "source_conversation_id = :conversation_id" in memory_sql
    assert "source_kind <> 'confirmed_resume'" in memory_sql


def test_delete_conversation_defaults_to_preserving_long_term_memories() -> None:
    database = FakeDatabase([
        FakeResult(row={"id": uuid4()}),
        FakeResult(),
        FakeResult(rowcount=1),
    ])
    deleted, forgotten = CareerMemoryRepository(database).delete_conversation_with_memory_choice(
        uuid4(), uuid4(), uuid4(), forget_derived_memories=False
    )
    assert deleted is True
    assert forgotten == 0
    assert all("career_memory_items" not in sql for sql, _ in database.connection.calls[1:])
