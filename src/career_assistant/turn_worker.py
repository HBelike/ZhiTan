"""使用 asyncio 驱动 PostgreSQL 权威队列的求职助手 Worker。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Protocol

from src.career_assistant.agent_loop import CareerAgentLoop
from src.career_assistant.intake_graph import CareerIntakeGraph
from src.career_assistant.persistence.records import AgentTurnStatus
from src.career_assistant.persistence.turn_job_repository import (
    CareerTurnJobRepository,
    ClaimedTurnRecord,
)
from src.career_assistant.response_runner import CareerResponseRunner
from src.career_assistant.settings import CareerTurnWorkerSettings
from src.career_assistant.turn_payloads import restore_turn_input


LOGGER = logging.getLogger(__name__)
TurnEventEmitter = Callable[[str, dict[str, object]], None]


@dataclass(frozen=True)
class TurnExecutionOutcome:
    """Agent 主链完成后交给队列仓储原子收口的结果。"""

    status: AgentTurnStatus
    event_payload: dict[str, object]


class TurnProcessor(Protocol):
    """隔离 Worker 调度与具体 Agent/LLM 实现的同步执行接口。"""

    def process(
        self,
        claim: ClaimedTurnRecord,
        emit: TurnEventEmitter,
    ) -> TurnExecutionOutcome: ...


class CareerAgentTurnProcessor:
    """把已领取 payload 交给现有 Intake Graph 和 Response Runner。"""

    _DELTA_FLUSH_CHARACTERS = 512

    def __init__(
        self,
        agent_loop: CareerAgentLoop,
        intake_graph: CareerIntakeGraph,
        response_runner: CareerResponseRunner,
        turn_job_repository: CareerTurnJobRepository,
    ) -> None:
        self._agent_loop = agent_loop
        self._intake_graph = intake_graph
        self._response_runner = response_runner
        self._turn_job_repository = turn_job_repository

    def process(
        self,
        claim: ClaimedTurnRecord,
        emit: TurnEventEmitter,
    ) -> TurnExecutionOutcome:
        prepared = restore_turn_input(claim.turn, claim.payload)
        active_turn = self._agent_loop.activate_turn(
            claim.turn.actor_id,
            claim.turn.id,
        )
        intake_result = None
        for event in self._intake_graph.stream_prepared(
            prepared.inbound_message,
            active_turn,
            prepared.parsed_attachments,
        ):
            if event.step is not None:
                emit(
                    "progress",
                    {"step": event.step.value, "state": "completed"},
                )
            if event.result is not None:
                intake_result = event.result
        if intake_result is None:
            raise RuntimeError("Worker 输入处理图未返回结果")

        buffered_delta: list[str] = []
        buffered_characters = 0
        final_result = None
        for event in self._response_runner.stream(
            prepared.inbound_message,
            intake_result,
        ):
            if event.event_type == "delta" and event.content:
                buffered_delta.append(event.content)
                buffered_characters += len(event.content)
                if buffered_characters >= self._DELTA_FLUSH_CHARACTERS:
                    emit("delta", {"content": "".join(buffered_delta)})
                    buffered_delta.clear()
                    buffered_characters = 0
            elif event.event_type not in {"done", "error"}:
                emit(event.event_type, {"content": event.content or ""})
            if event.result is not None:
                final_result = event.result
        if buffered_delta:
            emit("delta", {"content": "".join(buffered_delta)})
        if final_result is None:
            raise RuntimeError("Worker 模型回复未返回最终结果")

        turn_limit = self._turn_job_repository.get_turn_limit(
            claim.turn.actor_id,
            claim.turn.conversation_id,
        )

        return TurnExecutionOutcome(
            status=final_result.turn.status,
            event_payload={
                "state": final_result.turn.status.value,
                "turn_id": str(final_result.turn.id),
                "turn": {
                    "id": str(final_result.turn.id),
                    "conversation_id": str(final_result.turn.conversation_id),
                    "status": final_result.turn.status.value,
                    "input_kind_codes": list(final_result.turn.input_kind_codes),
                    "requested_selection_mode": (
                        final_result.turn.requested_selection_mode.value
                    ),
                    "created_at": final_result.turn.created_at.isoformat(),
                    "started_at": (
                        final_result.turn.started_at.isoformat()
                        if final_result.turn.started_at
                        else None
                    ),
                    "completed_at": (
                        final_result.turn.completed_at.isoformat()
                        if final_result.turn.completed_at
                        else None
                    ),
                },
                "message": {
                    "id": str(intake_result.persisted_message.id),
                    "turn_id": str(intake_result.persisted_message.turn_id),
                    "role": intake_result.persisted_message.role.value,
                    "content": intake_result.persisted_message.content_text,
                    "is_redacted": intake_result.persisted_message.is_redacted,
                    "created_at": intake_result.persisted_message.created_at.isoformat(),
                },
                "assistant_message": {
                    "id": str(final_result.assistant_message.id),
                    "turn_id": str(final_result.assistant_message.turn_id),
                    "role": final_result.assistant_message.role.value,
                    "content": final_result.assistant_message.content_text,
                    "is_redacted": final_result.assistant_message.is_redacted,
                    "created_at": final_result.assistant_message.created_at.isoformat(),
                    **_response_model_payload(final_result),
                },
                "completed_steps": [step.value for step in intake_result.completed_steps],
                "job_source": {
                    "status": intake_result.job_source_status,
                    "message": intake_result.job_source_message,
                },
                "activated_skills": [
                    {
                        "id": item.skill_id,
                        "name": item.name,
                        "description": item.description,
                        "status": "mounted",
                        "execution_mode": item.execution_mode.value,
                        "tool_names": list(item.tool_names),
                        "invocation_source": item.invocation_source,
                        "primary": item.primary,
                    }
                    for item in intake_result.model_context.activated_skills
                ],
                "skill_executions": [
                    {
                        "skill_name": item.skill_name,
                        "tool_name": item.tool_name,
                        "execution_mode": item.execution_mode,
                        "status": item.status,
                        "result_count": item.result_count,
                        "message": item.message,
                    }
                    for item in final_result.skill_executions
                ],
                "context_usage": (
                    {
                        "used_percent": final_result.context_usage.used_percent,
                        "remaining_percent": final_result.context_usage.remaining_percent,
                        "state": final_result.context_usage.state.value,
                        "approximate": True,
                    }
                    if final_result.context_usage is not None
                    else None
                ),
                "turn_limit": {
                    "successful_turns": turn_limit.successful_turns,
                    "remaining_turns": turn_limit.remaining_turns,
                    "max_turns": turn_limit.max_turns,
                    "reached": turn_limit.reached,
                },
            },
        )


def _response_model_payload(response_result) -> dict[str, object]:
    """把本轮真实模型附到 Worker 完成事件中的助手消息。"""

    if not response_result.model_was_invoked or response_result.model_resolution is None:
        return {}
    resolution = response_result.model_resolution
    reported_model_id = response_result.provider_reported_model_id
    return {
        "model": {
            "provider_key": resolution.profile.provider_key,
            "model_id": reported_model_id or resolution.profile.model_id,
            "requested_model_id": resolution.profile.model_id,
            "provider_reported_model_id": reported_model_id,
            "source": "provider_response" if reported_model_id else "request",
        },
    }


class CareerTurnWorker:
    """在本地并发上限内领取 Turn，并由 PostgreSQL 控制全局并发。"""

    def __init__(
        self,
        repository: CareerTurnJobRepository,
        processor: TurnProcessor,
        settings: CareerTurnWorkerSettings,
    ) -> None:
        settings.validate()
        self._repository = repository
        self._processor = processor
        self._settings = settings
        self._stopping = asyncio.Event()
        self._active: set[asyncio.Task[None]] = set()

    async def run_forever(self) -> None:
        """持续领取任务，直到收到 stop；不会把空队列当作错误。"""

        await asyncio.to_thread(
            self._repository.recover_expired,
            global_limit=self._settings.global_concurrency,
        )
        try:
            while not self._stopping.is_set():
                claimed_any = await self._fill_capacity()
                if self._active:
                    done, _ = await asyncio.wait(
                        self._active,
                        timeout=self._settings.poll_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    self._active.difference_update(done)
                    await asyncio.gather(*done)
                elif not claimed_any:
                    try:
                        await asyncio.wait_for(
                            self._stopping.wait(),
                            timeout=self._settings.poll_seconds,
                        )
                    except TimeoutError:
                        pass
        finally:
            if self._active:
                await asyncio.gather(*self._active, return_exceptions=True)
                self._active.clear()

    async def run_until_idle(self) -> None:
        """测试与一次性运维入口：消费到队列为空且本地任务全部结束。"""

        await asyncio.to_thread(
            self._repository.recover_expired,
            global_limit=self._settings.global_concurrency,
        )
        while True:
            claimed_any = await self._fill_capacity()
            if not self._active:
                if not claimed_any:
                    return
                continue
            done, _ = await asyncio.wait(
                self._active,
                return_when=asyncio.FIRST_COMPLETED,
            )
            self._active.difference_update(done)
            await asyncio.gather(*done)

    def stop(self) -> None:
        """请求 Worker 在当前真实执行结束后退出。"""

        self._stopping.set()

    async def _fill_capacity(self) -> bool:
        claimed_any = False
        while len(self._active) < self._settings.worker_concurrency:
            claim = await asyncio.to_thread(
                self._repository.claim_next,
                self._settings.worker_id,
                lease_seconds=max(1, int(self._settings.lease_seconds)),
                global_limit=self._settings.global_concurrency,
            )
            if claim is None:
                break
            claimed_any = True
            task = asyncio.create_task(self._execute_claim(claim))
            self._active.add(task)
        return claimed_any

    async def _execute_claim(self, claim: ClaimedTurnRecord) -> None:
        execution_finished = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_until_finished(claim, execution_finished),
        )

        def emit(event_type: str, payload: dict[str, object]) -> None:
            self._repository.append_event(claim.turn.id, event_type, payload)

        try:
            outcome = await asyncio.to_thread(
                self._processor.process,
                claim,
                emit,
            )
            event_type = "done" if outcome.status is AgentTurnStatus.SUCCEEDED else "error"
            error_code = None
            error_message = None
        except Exception as exc:
            LOGGER.exception("Agent Worker 执行失败：turn_id=%s", claim.turn.id)
            outcome = TurnExecutionOutcome(
                status=AgentTurnStatus.FAILED,
                event_payload={
                    "state": "failed",
                    "message": "求职助手执行失败，请稍后重试。",
                },
            )
            event_type = "error"
            error_code = "career_worker_execution_failed"
            error_message = "Agent Worker 执行异常，请稍后重试。"
        finally:
            execution_finished.set()
            await heartbeat_task

        await asyncio.to_thread(
            self._repository.finish_claim,
            claim.turn.id,
            claim.worker_id,
            status=outcome.status,
            event_type=event_type,
            event_payload=outcome.event_payload,
            error_code=error_code,
            error_message=error_message,
        )

    async def _heartbeat_until_finished(
        self,
        claim: ClaimedTurnRecord,
        execution_finished: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    execution_finished.wait(),
                    timeout=self._settings.heartbeat_seconds,
                )
                return
            except TimeoutError:
                renewed = await asyncio.to_thread(
                    self._repository.heartbeat,
                    claim.turn.id,
                    claim.worker_id,
                    lease_seconds=max(1, int(self._settings.lease_seconds)),
                )
                if not renewed:
                    LOGGER.error("Agent Worker 租约续期失败：turn_id=%s", claim.turn.id)
                    return


__all__ = [
    "CareerAgentTurnProcessor",
    "CareerTurnWorker",
    "CareerTurnWorkerSettings",
    "TurnExecutionOutcome",
]
