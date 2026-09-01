"""多人同时使用求职助手时的 Worker 调度模拟。"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.career_assistant.contracts import ModelSelectionMode
from src.career_assistant.persistence.records import AgentTurnRecord, AgentTurnStatus
from src.career_assistant.persistence.turn_job_repository import (
    ClaimedTurnRecord,
    TurnPayloadRecord,
)
from src.career_assistant.settings import CareerTurnWorkerSettings
from src.career_assistant.turn_worker import CareerTurnWorker, TurnExecutionOutcome


class InMemoryDurableQueue:
    """模拟 PostgreSQL 的全局槽位与同会话 FIFO claim 语义。"""

    def __init__(self, conversations: list[UUID], turns_per_conversation: int) -> None:
        self._lock = threading.Lock()
        self._pending = [
            (sequence, conversation_id, turn_index, uuid4())
            for sequence, (conversation_id, turn_index) in enumerate(
                (
                    (conversation_id, turn_index)
                    for conversation_id in conversations
                    for turn_index in range(turns_per_conversation)
                ),
                start=1,
            )
        ]
        self._active_conversations: set[UUID] = set()
        self._active_turns: dict[UUID, tuple[UUID, int]] = {}
        self.finished: list[UUID] = []

    def claim_next(self, worker_id, *, lease_seconds, global_limit):
        with self._lock:
            if len(self._active_turns) >= global_limit:
                return None
            candidate_index = next(
                (
                    index
                    for index, (_, conversation_id, _, _) in enumerate(self._pending)
                    if conversation_id not in self._active_conversations
                ),
                None,
            )
            if candidate_index is None:
                return None
            sequence, conversation_id, turn_index, turn_id = self._pending.pop(
                candidate_index
            )
            self._active_conversations.add(conversation_id)
            self._active_turns[turn_id] = (conversation_id, sequence)
            slot_number = len(self._active_turns)

        now = datetime.now(UTC)
        turn = AgentTurnRecord(
            id=turn_id,
            conversation_id=conversation_id,
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
                input_text=f"第 {turn_index + 1} 轮",
                effective_text=f"第 {turn_index + 1} 轮",
                job_url=None,
                attachment_payloads=(),
                request_metadata={"turn_index": turn_index},
            ),
            queue_sequence=sequence,
            attempt_count=1,
            slot_number=slot_number,
            worker_id=worker_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            cancel_requested_at=None,
        )

    def append_event(self, turn_id, event_type, payload):
        return None

    def heartbeat(self, turn_id, worker_id, *, lease_seconds):
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
            conversation_id, _ = self._active_turns.pop(turn_id)
            self._active_conversations.remove(conversation_id)
            self.finished.append(turn_id)
        return True

    def recover_expired(self, *, global_limit):
        return 0


class ConcurrencyTrackingProcessor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = 0
        self._running_by_conversation: dict[UUID, int] = defaultdict(int)
        self.max_running = 0
        self.max_running_by_conversation: dict[UUID, int] = defaultdict(int)
        self.started_turn_indexes: dict[UUID, list[int]] = defaultdict(list)

    def process(self, claim, emit):
        conversation_id = claim.turn.conversation_id
        turn_index = int(claim.payload.request_metadata["turn_index"])
        with self._lock:
            self._running += 1
            self._running_by_conversation[conversation_id] += 1
            self.max_running = max(self.max_running, self._running)
            self.max_running_by_conversation[conversation_id] = max(
                self.max_running_by_conversation[conversation_id],
                self._running_by_conversation[conversation_id],
            )
            self.started_turn_indexes[conversation_id].append(turn_index)
        try:
            time.sleep(0.02)
            return TurnExecutionOutcome(
                status=AgentTurnStatus.SUCCEEDED,
                event_payload={"state": "succeeded"},
            )
        finally:
            with self._lock:
                self._running -= 1
                self._running_by_conversation[conversation_id] -= 1


class CareerTurnWorkerConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_hundred_users_share_global_slots_without_conversation_overlap(
        self,
    ) -> None:
        conversations = [uuid4() for _ in range(100)]
        repository = InMemoryDurableQueue(conversations, turns_per_conversation=2)
        processor = ConcurrencyTrackingProcessor()

        def worker(worker_id: str) -> CareerTurnWorker:
            return CareerTurnWorker(
                repository,
                processor,
                CareerTurnWorkerSettings(
                    worker_id=worker_id,
                    global_concurrency=8,
                    worker_concurrency=4,
                    lease_seconds=90,
                    heartbeat_seconds=20,
                    poll_seconds=0.001,
                ),
            )

        await asyncio.gather(
            worker("worker-a").run_until_idle(),
            worker("worker-b").run_until_idle(),
        )

        self.assertEqual(len(repository.finished), 200)
        self.assertEqual(processor.max_running, 8)
        self.assertTrue(
            all(value == 1 for value in processor.max_running_by_conversation.values())
        )
        self.assertTrue(
            all(
                processor.started_turn_indexes[conversation_id] == [0, 1]
                for conversation_id in conversations
            )
        )


if __name__ == "__main__":
    unittest.main()
