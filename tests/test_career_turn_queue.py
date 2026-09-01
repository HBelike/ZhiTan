"""求职助手进程内异步 Turn 队列测试。"""

from __future__ import annotations

import asyncio
import threading
import unittest
from uuid import uuid4

from src.career_assistant.turn_queue import AsyncTurnCoordinator
from src.career_assistant.web.router import _stream_events_with_heartbeats


class AsyncTurnCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_conversation_waits_in_submission_order(self) -> None:
        coordinator = AsyncTurnCoordinator(max_concurrent_turns=2)
        conversation_id = uuid4()

        first = await coordinator.reserve(conversation_id)
        second = await coordinator.reserve(conversation_id)

        self.assertFalse(first.is_queued)
        self.assertTrue(second.is_queued)
        self.assertEqual(second.queue_scope, "conversation")
        self.assertEqual(second.queue_position, 1)

        first_lease = await first.acquire()
        second_task = asyncio.create_task(second.acquire())
        await asyncio.sleep(0)
        self.assertFalse(second_task.done())

        first_lease.release()
        second_lease = await asyncio.wait_for(second_task, timeout=1)
        second_lease.release()

    async def test_reservation_order_wins_even_if_later_turn_awaits_first(self) -> None:
        coordinator = AsyncTurnCoordinator(max_concurrent_turns=2)
        conversation_id = uuid4()
        first = await coordinator.reserve(conversation_id)
        second = await coordinator.reserve(conversation_id)
        third = await coordinator.reserve(conversation_id)
        first_lease = await first.acquire()

        third_task = asyncio.create_task(third.acquire())
        await asyncio.sleep(0)
        first_lease.release()
        await asyncio.sleep(0)
        self.assertFalse(third_task.done())

        second_lease = await asyncio.wait_for(second.acquire(), timeout=1)
        second_lease.release()
        third_lease = await asyncio.wait_for(third_task, timeout=1)
        third_lease.release()

    async def test_different_conversations_can_run_concurrently(self) -> None:
        coordinator = AsyncTurnCoordinator(max_concurrent_turns=2)

        first = await (await coordinator.reserve(uuid4())).acquire()
        second = await asyncio.wait_for(
            (await coordinator.reserve(uuid4())).acquire(),
            timeout=1,
        )

        first.release()
        second.release()

    async def test_global_limit_queues_instead_of_rejecting(self) -> None:
        coordinator = AsyncTurnCoordinator(max_concurrent_turns=1)

        first = await (await coordinator.reserve(uuid4())).acquire()
        second_reservation = await coordinator.reserve(uuid4())
        self.assertTrue(second_reservation.is_queued)
        self.assertEqual(second_reservation.queue_scope, "global")

        second_task = asyncio.create_task(second_reservation.acquire())
        await asyncio.sleep(0)
        self.assertFalse(second_task.done())

        first.release()
        second = await asyncio.wait_for(second_task, timeout=1)
        second.release()

    async def test_cancelled_waiter_does_not_block_later_turn(self) -> None:
        coordinator = AsyncTurnCoordinator(max_concurrent_turns=1)
        conversation_id = uuid4()
        first = await (await coordinator.reserve(conversation_id)).acquire()
        cancelled_reservation = await coordinator.reserve(conversation_id)
        cancelled_task = asyncio.create_task(cancelled_reservation.acquire())
        await asyncio.sleep(0)

        cancelled_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled_task

        first.release()
        later = await asyncio.wait_for(
            (await coordinator.reserve(conversation_id)).acquire(),
            timeout=1,
        )
        later.release()

    async def test_run_sync_releases_slot_after_operation_failure(self) -> None:
        coordinator = AsyncTurnCoordinator(max_concurrent_turns=1)
        conversation_id = uuid4()

        def fail() -> None:
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await coordinator.run_sync(conversation_id, fail)

        later = await asyncio.wait_for(
            (await coordinator.reserve(conversation_id)).acquire(),
            timeout=1,
        )
        later.release()

    async def test_cancelling_waiter_does_not_release_running_thread_early(self) -> None:
        coordinator = AsyncTurnCoordinator(max_concurrent_turns=1)
        conversation_id = uuid4()
        started = threading.Event()
        finish = threading.Event()

        def blocking_operation() -> str:
            started.set()
            finish.wait(timeout=2)
            return "done"

        caller = asyncio.create_task(
            coordinator.run_sync(conversation_id, blocking_operation),
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait, 1), timeout=2)

        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller

        next_reservation = await coordinator.reserve(conversation_id)
        next_turn = asyncio.create_task(next_reservation.acquire())
        await asyncio.sleep(0)
        self.assertFalse(next_turn.done())

        finish.set()
        next_lease = await asyncio.wait_for(next_turn, timeout=2)
        next_lease.release()


class AsyncCareerStreamBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_events_are_forwarded_and_completion_callback_runs(self) -> None:
        completed = threading.Event()

        def events():
            yield "event: done\ndata: {}\n\n"

        received = [
            event
            async for event in _stream_events_with_heartbeats(
                events(),
                heartbeat_seconds=0.1,
                expected_turn_seconds=1,
                on_processing_complete=completed.set,
            )
        ]
        await asyncio.wait_for(asyncio.to_thread(completed.wait, 1), timeout=2)

        self.assertEqual(received, ["event: done\ndata: {}\n\n"])

    async def test_stream_disconnect_does_not_finish_running_work_early(self) -> None:
        allow_finish = threading.Event()
        completed = threading.Event()

        def events():
            yield "event: progress\ndata: {}\n\n"
            allow_finish.wait(timeout=2)
            yield "event: done\ndata: {}\n\n"

        stream = _stream_events_with_heartbeats(
            events(),
            heartbeat_seconds=0.1,
            expected_turn_seconds=1,
            on_processing_complete=completed.set,
        )
        first = await anext(stream)
        self.assertIn("event: progress", first)

        await stream.aclose()
        self.assertFalse(completed.is_set())

        allow_finish.set()
        await asyncio.wait_for(asyncio.to_thread(completed.wait, 1), timeout=2)


if __name__ == "__main__":
    unittest.main()
