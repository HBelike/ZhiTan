"""单一求职对话成功轮次上限测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.career_assistant.contracts import ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.persistence.turn_job_repository import (
    CareerTurnJobRepository,
    ConversationTurnAdmissionError,
    TurnPayloadRecord,
)


class FakeResult:
    def __init__(self, row=None) -> None:
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row

    def one_or_none(self):
        return self.row


class FakeConnection:
    def __init__(self, rows) -> None:
        self.rows = list(rows)
        self.sql: list[str] = []

    def execute(self, statement, parameters=None):
        del parameters
        self.sql.append(" ".join(str(statement).split()))
        return FakeResult(self.rows.pop(0))


class FakeDatabase:
    def __init__(self, rows) -> None:
        self.connection = FakeConnection(rows)

    @contextmanager
    def transaction(self):
        yield self.connection


def enqueue(repository: CareerTurnJobRepository) -> None:
    turn_id = uuid4()
    repository.enqueue(
        turn_id=turn_id,
        conversation_id=uuid4(),
        actor_id=uuid4(),
        model_selection=ModelSelectionRequest(mode=ModelSelectionMode.FREE_QUOTA_FIRST),
        input_kind_codes=frozenset({"text"}),
        payload=TurnPayloadRecord(
            turn_id=turn_id,
            input_text="继续优化简历",
            effective_text="继续优化简历",
            job_url=None,
            attachment_payloads=(),
            request_metadata={},
        ),
    )


def test_thirty_succeeded_turns_block_next_enqueue() -> None:
    repository = CareerTurnJobRepository(FakeDatabase([
        {"id": uuid4()},
        {"successful_turns": 30, "active_turns": 0},
    ]))

    with pytest.raises(ConversationTurnAdmissionError) as error:
        enqueue(repository)

    assert error.value.code == "conversation_turn_limit_reached"


def test_queued_or_running_turn_blocks_concurrent_enqueue() -> None:
    repository = CareerTurnJobRepository(FakeDatabase([
        {"id": uuid4()},
        {"successful_turns": 29, "active_turns": 1},
    ]))

    with pytest.raises(ConversationTurnAdmissionError) as error:
        enqueue(repository)

    assert error.value.code == "conversation_turn_in_progress"


def test_failed_and_cancelled_turns_are_excluded_by_success_filter() -> None:
    now = datetime.now(UTC)
    turn_id = uuid4()
    conversation_id = uuid4()
    actor_id = uuid4()
    queued_row = {
        "id": turn_id,
        "conversation_id": conversation_id,
        "actor_id": actor_id,
        "requested_selection_mode": "free_quota_first",
        "requested_model_profile_id": None,
        "input_kind_codes": ["text"],
        "status": "queued",
        "error_code": None,
        "error_message": None,
        "started_at": None,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
        "queue_sequence": 30,
        "attempt_count": 0,
        "lease_expires_at": None,
        "cancel_requested_at": None,
    }
    database = FakeDatabase([
        {"id": conversation_id},
        {"successful_turns": 29, "active_turns": 0},
        queued_row,
        None,
        None,
    ])
    repository = CareerTurnJobRepository(database)
    repository.enqueue(
        turn_id=turn_id,
        conversation_id=conversation_id,
        actor_id=actor_id,
        model_selection=ModelSelectionRequest(mode=ModelSelectionMode.FREE_QUOTA_FIRST),
        input_kind_codes=frozenset({"text"}),
        payload=TurnPayloadRecord(
            turn_id=turn_id,
            input_text="第 30 轮",
            effective_text="第 30 轮",
            job_url=None,
            attachment_payloads=(),
            request_metadata={},
        ),
    )

    count_sql = database.connection.sql[1]
    assert "status = 'succeeded'" in count_sql
    assert "status IN ('queued', 'running')" in count_sql
