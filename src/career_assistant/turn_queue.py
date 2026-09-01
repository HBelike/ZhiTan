"""求职助手 Turn 的进程内异步排队协调器。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeVar
from uuid import UUID


T = TypeVar("T")
QueueScope = Literal["conversation", "global"]


@dataclass
class _QueueTicket:
    """按提交顺序进入会话队列的一张等待票。"""

    ready: asyncio.Future[None]


@dataclass
class _ConversationQueueState:
    """单个会话当前的 FIFO 等待票与执行状态。"""

    pending: deque[_QueueTicket] = field(default_factory=deque)
    active: bool = False


class TurnLease:
    """一次 Turn 已取得的会话执行权和全局并发名额。"""

    def __init__(
        self,
        coordinator: AsyncTurnCoordinator,
        conversation_id: UUID,
        state: _ConversationQueueState,
    ) -> None:
        self._coordinator = coordinator
        self._conversation_id = conversation_id
        self._state = state
        self._released = False

    @property
    def released(self) -> bool:
        """返回该名额是否已经归还。"""

        return self._released

    def release(self) -> None:
        """幂等归还全局名额并唤醒同会话的下一个 Turn。"""

        if self._released:
            return
        self._released = True
        self._coordinator._release_lease(
            self._conversation_id,
            self._state,
        )


class TurnReservation:
    """Turn 进入队列后的等待凭据。"""

    def __init__(
        self,
        coordinator: AsyncTurnCoordinator,
        conversation_id: UUID,
        state: _ConversationQueueState,
        ticket: _QueueTicket,
        *,
        queue_scope: QueueScope | None,
        queue_position: int,
    ) -> None:
        self._coordinator = coordinator
        self._conversation_id = conversation_id
        self._state = state
        self._ticket = ticket
        self._status = "waiting"
        self.queue_scope = queue_scope
        self.queue_position = queue_position

    @property
    def is_queued(self) -> bool:
        """返回提交时是否已有会话任务或全局任务占满名额。"""

        return self.queue_scope is not None

    async def acquire(self) -> TurnLease:
        """按 FIFO 顺序等待并取得执行权。"""

        if self._status != "waiting":
            raise RuntimeError("Turn reservation 已经被取得或取消")
        try:
            lease = await self._coordinator._acquire_reservation(
                self._conversation_id,
                self._state,
                self._ticket,
            )
        except BaseException:
            self._status = "cancelled"
            raise
        self._status = "acquired"
        return lease

    async def cancel(self) -> None:
        """取消尚未开始等待的 reservation，并清理空闲会话状态。"""

        if self._status != "waiting":
            return
        self._status = "cancelled"
        await self._coordinator._cancel_reservation(
            self._conversation_id,
            self._state,
            self._ticket,
        )


class AsyncTurnCoordinator:
    """同会话 FIFO 串行、跨会话受控并发的异步调度器。

    该对象只协调当前 Python 进程。多进程部署必须在此边界之外增加 PostgreSQL
    权威队列或租约，不能把这里的 asyncio 原语当作跨进程锁。
    """

    def __init__(self, max_concurrent_turns: int) -> None:
        if isinstance(max_concurrent_turns, bool) or max_concurrent_turns <= 0:
            raise ValueError("max_concurrent_turns 必须是正整数")
        self._global_slots = asyncio.Semaphore(max_concurrent_turns)
        self._states: dict[UUID, _ConversationQueueState] = {}
        self._state_guard = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

    async def reserve(self, conversation_id: UUID) -> TurnReservation:
        """把 Turn 加入会话队列，并返回其提交时的排队信息。"""

        async with self._state_guard:
            state = self._states.get(conversation_id)
            if state is None:
                state = _ConversationQueueState()
                self._states[conversation_id] = state

            conversation_ahead = len(state.pending) + int(state.active)
            global_full = self._global_slots.locked()
            ticket = _QueueTicket(asyncio.get_running_loop().create_future())
            state.pending.append(ticket)
            if conversation_ahead == 0:
                ticket.ready.set_result(None)

        if conversation_ahead > 0:
            queue_scope: QueueScope | None = "conversation"
            queue_position = conversation_ahead
        elif global_full:
            queue_scope = "global"
            queue_position = 1
        else:
            queue_scope = None
            queue_position = 0

        return TurnReservation(
            self,
            conversation_id,
            state,
            ticket,
            queue_scope=queue_scope,
            queue_position=queue_position,
        )

    async def run_sync(
        self,
        conversation_id: UUID,
        operation: Callable[[], T],
    ) -> T:
        """排队后在线程中执行同步操作，真实结束时才释放名额。

        `asyncio.shield` 只隔离调用方取消；底层同步操作仍受其自身 HTTP 和解析超时
        控制。后台 Task 被集合持有，避免请求断开后被垃圾回收。
        """

        reservation = await self.reserve(conversation_id)
        lease = await reservation.acquire()
        task = asyncio.create_task(asyncio.to_thread(operation))
        self._background_tasks.add(task)

        def finish(finished: asyncio.Task[T]) -> None:
            lease.release()
            self._background_tasks.discard(finished)
            if finished.cancelled():
                return
            # 即使 HTTP 调用方已经取消，也要取出异常，避免事件循环输出未检索警告。
            finished.exception()

        task.add_done_callback(finish)
        return await asyncio.shield(task)

    async def _acquire_reservation(
        self,
        conversation_id: UUID,
        state: _ConversationQueueState,
        ticket: _QueueTicket,
    ) -> TurnLease:
        try:
            await ticket.ready
            await self._global_slots.acquire()
        except BaseException:
            await self._remove_ticket(conversation_id, state, ticket)
            raise

        async with self._state_guard:
            if not state.pending or state.pending[0] is not ticket:
                self._global_slots.release()
                raise RuntimeError("会话 Turn 队列顺序异常")
            state.pending.popleft()
            state.active = True
        return TurnLease(self, conversation_id, state)

    async def _cancel_reservation(
        self,
        conversation_id: UUID,
        state: _ConversationQueueState,
        ticket: _QueueTicket,
    ) -> None:
        await self._remove_ticket(conversation_id, state, ticket)

    async def _remove_ticket(
        self,
        conversation_id: UUID,
        state: _ConversationQueueState,
        ticket: _QueueTicket,
    ) -> None:
        async with self._state_guard:
            was_head = bool(state.pending) and state.pending[0] is ticket
            try:
                state.pending.remove(ticket)
            except ValueError:
                return
            if not ticket.ready.done():
                ticket.ready.cancel()
            if was_head and not state.active and state.pending:
                next_ticket = state.pending[0]
                if not next_ticket.ready.done():
                    next_ticket.ready.set_result(None)
            self._remove_idle_state(conversation_id, state)

    def _release_lease(
        self,
        conversation_id: UUID,
        state: _ConversationQueueState,
    ) -> None:
        self._global_slots.release()
        state.active = False
        if state.pending:
            next_ticket = state.pending[0]
            if not next_ticket.ready.done():
                next_ticket.ready.set_result(None)
        self._remove_idle_state(conversation_id, state)

    def _remove_idle_state(
        self,
        conversation_id: UUID,
        state: _ConversationQueueState,
    ) -> None:
        if state.pending or state.active:
            return
        if self._states.get(conversation_id) is state:
            self._states.pop(conversation_id, None)
