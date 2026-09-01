"""独立 Agent Worker 的 asyncio 调度、心跳与异常收口测试。"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.career_assistant.contracts import ModelSelectionMode
from src.career_assistant.persistence.records import AgentTurnRecord, AgentTurnStatus
from src.career_assistant.persistence.turn_job_repository import (
    ClaimedTurnRecord,
    TurnPayloadRecord,
)


def _claim(*, worker_id: str, slot_number: int) -> ClaimedTurnRecord:
    now = datetime.now(UTC)
    turn_id = uuid4()
    turn = AgentTurnRecord(
        id=turn_id,
        conversation_id=uuid4(),
        actor_id=uuid4(),
        requested_selection_mode=ModelSelectionMode.FREE_QUOTA_FIRST,
        requested_model_profile_id=None,
        input_kind_codes=("text",),
        status=AgentTurnStatus.RUNNING,
        error_code=None,
        error_message=None,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    return ClaimedTurnRecord(
        turn=turn,
        payload=TurnPayloadRecord(
            turn_id=turn_id,
            input_text="分析我的项目",
            effective_text="分析我的项目",
            job_url=None,
            attachment_payloads=(),
            request_metadata={},
        ),
        queue_sequence=slot_number,
        attempt_count=1,
        slot_number=slot_number,
        worker_id=worker_id,
        lease_expires_at=now + timedelta(seconds=30),
        cancel_requested_at=None,
    )


class FakeTurnJobRepository:
    def __init__(self, claims) -> None:
        self._claims = list(claims)
        self._lock = threading.Lock()
        self.heartbeat_calls = 0
        self.finished: list[tuple[object, AgentTurnStatus, str]] = []
        self.events: list[tuple[str, dict[str, object]]] = []
        self.recover_calls = 0

    def claim_next(self, worker_id, *, lease_seconds, global_limit):
        with self._lock:
            if not self._claims:
                return None
            return self._claims.pop(0)

    def heartbeat(self, turn_id, worker_id, *, lease_seconds):
        with self._lock:
            self.heartbeat_calls += 1
        return True

    def finish_claim(
        self,
        turn_id,
        worker_id,
        *,
        status,
        event_type,
        event_payload,
        error_code=None,
        error_message=None,
    ):
        with self._lock:
            self.finished.append((turn_id, status, event_type))
        return True

    def append_event(self, turn_id, event_type, payload):
        with self._lock:
            self.events.append((event_type, payload))
        return None

    def recover_expired(self, *, global_limit):
        self.recover_calls += 1
        return 0


class TrackingProcessor:
    def __init__(self, *, delay: float = 0.04, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.running = 0
        self.max_running = 0
        self.lock = threading.Lock()

    def process(self, claim, emit):
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            emit("progress", {"step": "model"})
            time.sleep(self.delay)
            if self.fail:
                raise RuntimeError("模拟 Worker 执行失败")
            from src.career_assistant.turn_worker import TurnExecutionOutcome

            return TurnExecutionOutcome(
                status=AgentTurnStatus.SUCCEEDED,
                event_payload={"state": "succeeded"},
            )
        finally:
            with self.lock:
                self.running -= 1


class CareerTurnWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_until_idle_executes_different_conversations_concurrently(self) -> None:
        from src.career_assistant.turn_worker import (
            CareerTurnWorker,
            CareerTurnWorkerSettings,
        )

        worker_id = "worker-a"
        repository = FakeTurnJobRepository(
            [_claim(worker_id=worker_id, slot_number=1), _claim(worker_id=worker_id, slot_number=2)],
        )
        processor = TrackingProcessor()
        worker = CareerTurnWorker(
            repository,
            processor,
            CareerTurnWorkerSettings(
                worker_id=worker_id,
                global_concurrency=4,
                worker_concurrency=2,
                lease_seconds=1.0,
                heartbeat_seconds=0.01,
                poll_seconds=0.01,
            ),
        )

        await worker.run_until_idle()

        self.assertEqual(processor.max_running, 2)
        self.assertEqual(len(repository.finished), 2)
        self.assertTrue(all(item[1] is AgentTurnStatus.SUCCEEDED for item in repository.finished))
        self.assertEqual(repository.recover_calls, 1)

    async def test_long_running_claim_renews_lease_before_finishing(self) -> None:
        from src.career_assistant.turn_worker import (
            CareerTurnWorker,
            CareerTurnWorkerSettings,
        )

        worker_id = "worker-a"
        repository = FakeTurnJobRepository([_claim(worker_id=worker_id, slot_number=1)])
        worker = CareerTurnWorker(
            repository,
            TrackingProcessor(delay=0.06),
            CareerTurnWorkerSettings(
                worker_id=worker_id,
                global_concurrency=1,
                worker_concurrency=1,
                lease_seconds=1.0,
                heartbeat_seconds=0.01,
                poll_seconds=0.01,
            ),
        )

        await worker.run_until_idle()

        self.assertGreaterEqual(repository.heartbeat_calls, 2)
        self.assertEqual(repository.finished[0][2], "done")

    async def test_processor_exception_finishes_claim_as_failed(self) -> None:
        from src.career_assistant.turn_worker import (
            CareerTurnWorker,
            CareerTurnWorkerSettings,
        )

        worker_id = "worker-a"
        repository = FakeTurnJobRepository([_claim(worker_id=worker_id, slot_number=1)])
        worker = CareerTurnWorker(
            repository,
            TrackingProcessor(fail=True),
            CareerTurnWorkerSettings(
                worker_id=worker_id,
                global_concurrency=1,
                worker_concurrency=1,
                lease_seconds=1.0,
                heartbeat_seconds=0.01,
                poll_seconds=0.01,
            ),
        )

        await worker.run_until_idle()

        self.assertEqual(repository.finished[0][1], AgentTurnStatus.FAILED)
        self.assertEqual(repository.finished[0][2], "error")


if __name__ == "__main__":
    unittest.main()
