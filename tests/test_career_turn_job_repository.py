"""持久化 Turn 队列的数据结构与仓储测试。"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.career_assistant.contracts import (
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.persistence.records import AgentTurnStatus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT
    / "migrations"
    / "versions"
    / "20260823_17_career_turn_worker_queue.py"
)


class CareerTurnQueueMigrationTests(unittest.TestCase):
    def test_queue_migration_contains_required_tables_columns_and_indexes(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        for token in (
            'revision = "20260823_17"',
            'down_revision = "20260821_16"',
            "queue_sequence",
            "available_at",
            "attempt_count",
            "lease_owner",
            "lease_expires_at",
            "heartbeat_at",
            "cancel_requested_at",
            "agent_turn_payloads",
            "attachment_payloads_json",
            "request_metadata_json",
            "agent_turn_events",
            "agent_execution_slots",
            "idx_career_agent_turns_queue_claim",
            "idx_career_agent_turns_conversation_queue",
        ):
            self.assertIn(token, source)

    def test_queue_migration_has_a_complete_downgrade(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        self.assertIn("def downgrade() -> None:", source)
        self.assertIn("DROP TABLE career_assistant.agent_execution_slots", source)
        self.assertIn("DROP TABLE career_assistant.agent_turn_events", source)
        self.assertIn("DROP TABLE career_assistant.agent_turn_payloads", source)
        self.assertIn("DROP COLUMN queue_sequence", source)


class FakeResult:
    def __init__(self, *, row=None, rows=None, rowcount: int = 1) -> None:
        self.row = row
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one(self):
        if self.row is None:
            raise AssertionError("测试结果没有单行数据")
        return self.row

    def one_or_none(self):
        return self.row

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.row


class FakeConnection:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, dict(parameters or {})))
        if not self.results:
            raise AssertionError(f"没有为 SQL 准备测试结果：{sql}")
        return self.results.pop(0)


class FakeDatabase:
    def __init__(self, results: list[FakeResult]) -> None:
        self.connection = FakeConnection(results)
        self.transaction_count = 0

    @contextmanager
    def transaction(self):
        self.transaction_count += 1
        yield self.connection


def _turn_row(*, status: str = "queued") -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "conversation_id": uuid4(),
        "actor_id": uuid4(),
        "requested_selection_mode": "free_quota_first",
        "requested_model_profile_id": None,
        "input_kind_codes": ["text"],
        "status": status,
        "error_code": None,
        "error_message": None,
        "started_at": now if status == "running" else None,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
        "queue_sequence": 7,
        "attempt_count": 1 if status == "running" else 0,
        "lease_expires_at": now if status == "running" else None,
        "cancel_requested_at": None,
    }


class CareerTurnJobRepositoryTests(unittest.TestCase):
    def test_enqueue_writes_turn_payload_and_queued_event_in_one_transaction(self) -> None:
        from src.career_assistant.persistence.turn_job_repository import (
            CareerTurnJobRepository,
            TurnPayloadRecord,
        )

        row = _turn_row()
        database = FakeDatabase(
            [
                FakeResult(row={"id": row["conversation_id"]}),
                FakeResult(row={"successful_turns": 0, "active_turns": 0}),
                FakeResult(row=row),
                FakeResult(),
                FakeResult(),
            ],
        )
        repository = CareerTurnJobRepository(database)
        payload = TurnPayloadRecord(
            turn_id=row["id"],
            input_text="张三的完整经历",
            effective_text="张三的完整经历",
            job_url=None,
            attachment_payloads=(),
            request_metadata={"source": "web"},
        )

        turn = repository.enqueue(
            turn_id=row["id"],
            conversation_id=row["conversation_id"],
            actor_id=row["actor_id"],
            model_selection=ModelSelectionRequest(
                mode=ModelSelectionMode.FREE_QUOTA_FIRST,
            ),
            input_kind_codes=frozenset({"text"}),
            payload=payload,
        )

        self.assertEqual(turn.status, AgentTurnStatus.QUEUED)
        self.assertEqual(database.transaction_count, 1)
        executed_sql = "\n".join(call[0] for call in database.connection.calls)
        self.assertIn("INSERT INTO career_assistant.agent_turns", executed_sql)
        self.assertIn("INSERT INTO career_assistant.agent_turn_payloads", executed_sql)
        self.assertIn("INSERT INTO career_assistant.agent_turn_events", executed_sql)
        payload_parameters = database.connection.calls[3][1]
        self.assertIn("张三的完整经历", str(payload_parameters["input_text"]))

    def test_claim_next_uses_skip_locked_fifo_and_global_slot(self) -> None:
        from src.career_assistant.persistence.turn_job_repository import (
            CareerTurnJobRepository,
        )

        queued = _turn_row()
        running = dict(queued)
        running.update(_turn_row(status="running"))
        running["id"] = queued["id"]
        running["conversation_id"] = queued["conversation_id"]
        running["actor_id"] = queued["actor_id"]
        candidate = {
            **queued,
            "input_text": "完整输入",
            "effective_text": "完整输入",
            "job_url": None,
            "attachment_payloads_json": [],
            "request_metadata_json": {},
        }
        database = FakeDatabase(
            [
                FakeResult(),
                FakeResult(row={"slot_number": 2}),
                FakeResult(row=candidate),
                FakeResult(row=running),
                FakeResult(),
                FakeResult(),
            ],
        )
        repository = CareerTurnJobRepository(database)

        claim = repository.claim_next(
            "worker-a",
            lease_seconds=90,
            global_limit=8,
        )

        self.assertIsNotNone(claim)
        self.assertEqual(claim.worker_id, "worker-a")
        self.assertEqual(claim.slot_number, 2)
        self.assertEqual(claim.turn.status, AgentTurnStatus.RUNNING)
        executed_sql = "\n".join(call[0] for call in database.connection.calls)
        self.assertIn("agent_execution_slots", executed_sql)
        self.assertIn("FOR UPDATE SKIP LOCKED", executed_sql)
        self.assertIn("older.queue_sequence < turn.queue_sequence", executed_sql)
        self.assertIn("older.status IN ('queued', 'running')", executed_sql)

    def test_list_events_scopes_access_to_turn_owner(self) -> None:
        from src.career_assistant.persistence.turn_job_repository import (
            CareerTurnJobRepository,
        )

        now = datetime.now(UTC)
        database = FakeDatabase(
            [
                FakeResult(
                    rows=[
                        {
                            "id": 11,
                            "turn_id": uuid4(),
                            "event_type": "progress",
                            "payload_json": {"label": "正在分析"},
                            "created_at": now,
                        },
                    ],
                ),
            ],
        )
        repository = CareerTurnJobRepository(database)
        actor_id = uuid4()

        events = repository.list_events(actor_id, uuid4(), after_id=10)

        self.assertEqual(len(events), 1)
        sql, parameters = database.connection.calls[0]
        self.assertIn("turn.actor_id = :actor_id", sql)
        self.assertIn("event.id > :after_id", sql)
        self.assertEqual(parameters["actor_id"], actor_id)

    def test_heartbeat_renews_turn_and_slot_in_one_transaction(self) -> None:
        from src.career_assistant.persistence.turn_job_repository import (
            CareerTurnJobRepository,
        )

        database = FakeDatabase([FakeResult(rowcount=1), FakeResult(rowcount=1)])
        repository = CareerTurnJobRepository(database)

        renewed = repository.heartbeat(
            uuid4(),
            "worker-a",
            lease_seconds=90,
        )

        self.assertTrue(renewed)
        self.assertEqual(database.transaction_count, 1)
        executed_sql = "\n".join(call[0] for call in database.connection.calls)
        self.assertIn("UPDATE career_assistant.agent_turns", executed_sql)
        self.assertIn("UPDATE career_assistant.agent_execution_slots", executed_sql)
        self.assertIn("lease_owner = :worker_id", executed_sql)

    def test_recover_expired_does_not_replay_non_idempotent_tools(self) -> None:
        from src.career_assistant.persistence.turn_job_repository import (
            CareerTurnJobRepository,
        )

        database = FakeDatabase([FakeResult(rowcount=3), FakeResult(rowcount=3)])
        repository = CareerTurnJobRepository(database)

        recovered = repository.recover_expired(global_limit=8)

        self.assertEqual(recovered, 3)
        turn_sql = database.connection.calls[0][0]
        slot_sql = database.connection.calls[1][0]
        self.assertIn("event.event_type = 'tool_started'", turn_sql)
        self.assertIn("worker_lease_expired_after_side_effect", turn_sql)
        self.assertIn("THEN 'failed'", turn_sql)
        self.assertIn("ELSE 'queued'", turn_sql)
        self.assertEqual(
            turn_sql.count("FALSE ) ) THEN"),
            4,
            "每个 WHEN EXISTS 子查询都必须在 THEN 前闭合 EXISTS 括号",
        )
        self.assertIn("lease_expires_at < NOW()", slot_sql)

    def test_release_claim_clears_turn_and_slot_ownership(self) -> None:
        from src.career_assistant.persistence.turn_job_repository import (
            CareerTurnJobRepository,
        )

        database = FakeDatabase([FakeResult(rowcount=1), FakeResult(rowcount=1)])
        repository = CareerTurnJobRepository(database)
        turn_id = uuid4()

        released = repository.release_claim(turn_id, "worker-a")

        self.assertTrue(released)
        for sql, parameters in database.connection.calls:
            self.assertIn("lease_owner", sql)
            self.assertEqual(parameters["turn_id"], turn_id)
            self.assertEqual(parameters["worker_id"], "worker-a")

    def test_finish_claim_writes_terminal_event_and_releases_slot_atomically(self) -> None:
        from src.career_assistant.persistence.turn_job_repository import (
            CareerTurnJobRepository,
        )

        database = FakeDatabase(
            [
                FakeResult(row={"status": "succeeded"}),
                FakeResult(),
                FakeResult(rowcount=1),
            ],
        )
        repository = CareerTurnJobRepository(database)
        turn_id = uuid4()

        finished = repository.finish_claim(
            turn_id,
            "worker-a",
            status=AgentTurnStatus.SUCCEEDED,
            event_type="done",
            event_payload={"state": "succeeded"},
        )

        self.assertTrue(finished)
        self.assertEqual(database.transaction_count, 1)
        executed_sql = "\n".join(call[0] for call in database.connection.calls)
        self.assertIn("UPDATE career_assistant.agent_turns", executed_sql)
        self.assertIn("INSERT INTO career_assistant.agent_turn_events", executed_sql)
        self.assertIn("UPDATE career_assistant.agent_execution_slots", executed_sql)


if __name__ == "__main__":
    unittest.main()
