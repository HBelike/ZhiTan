"""求职助手 PostgreSQL Turn 权威队列仓储。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping, text

from src.career_assistant.contracts import ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.records import AgentTurnRecord, AgentTurnStatus


@dataclass(frozen=True)
class TurnPayloadRecord:
    """Worker 执行 Turn 所需的完整输入，不包含原始附件二进制。"""

    turn_id: UUID
    input_text: str
    effective_text: str
    job_url: str | None
    attachment_payloads: tuple[dict[str, object], ...]
    request_metadata: dict[str, object]


@dataclass(frozen=True)
class TurnEventRecord:
    """可供 SSE 断线续传的一条持久化 Turn 事件。"""

    id: int
    turn_id: UUID
    event_type: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class ClaimedTurnRecord:
    """Worker 已经同时取得 Turn 租约和全局执行槽位的快照。"""

    turn: AgentTurnRecord
    payload: TurnPayloadRecord
    queue_sequence: int
    attempt_count: int
    slot_number: int
    worker_id: str
    lease_expires_at: datetime
    cancel_requested_at: datetime | None


@dataclass(frozen=True)
class TurnQueueStatusRecord:
    """API 展示 Turn 状态和会话内准确等待位置所需的读取模型。"""

    turn: AgentTurnRecord
    queue_sequence: int
    attempt_count: int
    cancel_requested_at: datetime | None
    conversation_position: int


MAX_SUCCESSFUL_TURNS = 30


@dataclass(frozen=True)
class ConversationTurnLimit:
    successful_turns: int
    remaining_turns: int
    max_turns: int = MAX_SUCCESSFUL_TURNS
    reached: bool = False


class ConversationTurnAdmissionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CareerTurnJobRepository:
    """原子管理 Turn 入队、领取、租约、事件和取消请求。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def enqueue(
        self,
        *,
        turn_id: UUID,
        conversation_id: UUID,
        actor_id: UUID,
        model_selection: ModelSelectionRequest,
        input_kind_codes: frozenset[str],
        payload: TurnPayloadRecord,
    ) -> AgentTurnRecord:
        """在一个事务中写入 queued Turn、完整 payload 和 queued 事件。"""

        model_selection.validate()
        if payload.turn_id != turn_id:
            raise ValueError("Turn payload 与 Turn ID 不一致")
        normalized_kinds = sorted({item.strip() for item in input_kind_codes if item.strip()})
        if not normalized_kinds:
            raise ValueError("Agent Turn 至少需要一种输入类型")

        with self._database.transaction() as connection:
            conversation = connection.execute(
                text(
                    """
                    SELECT conversation.id
                    FROM career_assistant.conversations AS conversation
                    WHERE conversation.id = :conversation_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status = 'active'
                    FOR UPDATE
                    """,
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().one_or_none()
            if conversation is None:
                raise LookupError("会话不存在、无访问权限、已归档或已删除")
            counts = connection.execute(
                text(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE status = 'succeeded') AS successful_turns,
                      COUNT(*) FILTER (WHERE status IN ('queued', 'running')) AS active_turns
                    FROM career_assistant.agent_turns
                    WHERE conversation_id = :conversation_id
                      AND actor_id = :actor_id
                    """,
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().one()
            if int(counts["active_turns"]) > 0:
                raise ConversationTurnAdmissionError(
                    "conversation_turn_in_progress",
                    "当前回复完成后可继续发送",
                )
            if int(counts["successful_turns"]) >= MAX_SUCCESSFUL_TURNS:
                raise ConversationTurnAdmissionError(
                    "conversation_turn_limit_reached",
                    "本对话已完成 30 轮，请开启新对话继续。",
                )
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.agent_turns (
                        id, conversation_id, actor_id,
                        requested_selection_mode, requested_model_profile_id,
                        input_kind_codes
                    )
                    SELECT :turn_id, conversation.id, :actor_id,
                           :selection_mode, :model_profile_id,
                           CAST(:input_kind_codes AS jsonb)
                    FROM career_assistant.conversations AS conversation
                    WHERE conversation.id = :conversation_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status = 'active'
                    RETURNING id, conversation_id, actor_id,
                              requested_selection_mode, requested_model_profile_id,
                              input_kind_codes, status, error_code, error_message,
                              started_at, completed_at, created_at, updated_at,
                              queue_sequence, attempt_count, lease_expires_at,
                              cancel_requested_at
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "actor_id": actor_id,
                    "selection_mode": model_selection.mode.value,
                    "model_profile_id": model_selection.profile_id,
                    "input_kind_codes": json.dumps(normalized_kinds, ensure_ascii=False),
                },
            ).mappings().one_or_none()
            if row is None:
                raise LookupError("会话不存在、无访问权限、已归档或已删除")

            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.agent_turn_payloads (
                        turn_id, input_text, effective_text, job_url,
                        attachment_payloads_json, request_metadata_json
                    ) VALUES (
                        :turn_id, :input_text, :effective_text, :job_url,
                        CAST(:attachment_payloads AS jsonb),
                        CAST(:request_metadata AS jsonb)
                    )
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "input_text": payload.input_text,
                    "effective_text": payload.effective_text,
                    "job_url": payload.job_url,
                    "attachment_payloads": json.dumps(
                        payload.attachment_payloads,
                        ensure_ascii=False,
                    ),
                    "request_metadata": json.dumps(
                        payload.request_metadata,
                        ensure_ascii=False,
                    ),
                },
            )
            self._insert_event(
                connection,
                turn_id,
                "queued",
                {"state": "queued", "queue_sequence": row["queue_sequence"]},
            )

        return self._to_turn_record(row)

    def get_turn_limit(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> ConversationTurnLimit:
        with self._database.transaction() as connection:
            successful = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM career_assistant.agent_turns AS turn
                    INNER JOIN career_assistant.conversations AS conversation
                      ON conversation.id = turn.conversation_id
                    WHERE turn.conversation_id = :conversation_id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                      AND turn.status = 'succeeded'
                    """,
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).scalar_one()
        successful_turns = int(successful)
        return ConversationTurnLimit(
            successful_turns=successful_turns,
            remaining_turns=max(0, MAX_SUCCESSFUL_TURNS - successful_turns),
            reached=successful_turns >= MAX_SUCCESSFUL_TURNS,
        )

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        global_limit: int,
    ) -> ClaimedTurnRecord | None:
        """按同会话 FIFO 领取一轮，同时占用一个跨 Worker 全局槽位。"""

        normalized_worker_id = self._validate_worker_inputs(
            worker_id,
            lease_seconds,
            global_limit,
        )
        with self._database.transaction() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.agent_execution_slots (slot_number)
                    SELECT slot_number
                    FROM generate_series(1, :global_limit) AS slot_number
                    ON CONFLICT (slot_number) DO NOTHING
                    """,
                ),
                {"global_limit": global_limit},
            )
            slot = connection.execute(
                text(
                    """
                    SELECT slot.slot_number
                    FROM career_assistant.agent_execution_slots AS slot
                    WHERE slot.slot_number <= :global_limit
                      AND (slot.turn_id IS NULL OR slot.lease_expires_at < NOW())
                    ORDER BY slot.slot_number
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                ),
                {"global_limit": global_limit},
            ).mappings().one_or_none()
            if slot is None:
                return None

            candidate = connection.execute(
                text(
                    """
                    SELECT turn.id, turn.conversation_id, turn.actor_id,
                           turn.requested_selection_mode,
                           turn.requested_model_profile_id,
                           turn.input_kind_codes, turn.status, turn.error_code,
                           turn.error_message, turn.started_at, turn.completed_at,
                           turn.created_at, turn.updated_at, turn.queue_sequence,
                           turn.attempt_count, turn.lease_expires_at,
                           turn.cancel_requested_at,
                           payload.input_text, payload.effective_text,
                           payload.job_url, payload.attachment_payloads_json,
                           payload.request_metadata_json
                    FROM career_assistant.agent_turns AS turn
                    INNER JOIN career_assistant.agent_turn_payloads AS payload
                        ON payload.turn_id = turn.id
                    WHERE turn.status = 'queued'
                      AND turn.available_at <= NOW()
                      AND turn.cancel_requested_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM career_assistant.agent_turns AS older
                          WHERE older.conversation_id = turn.conversation_id
                            AND older.queue_sequence < turn.queue_sequence
                            AND older.status IN ('queued', 'running')
                      )
                    ORDER BY turn.queue_sequence
                    FOR UPDATE OF turn SKIP LOCKED
                    LIMIT 1
                    """,
                ),
            ).mappings().one_or_none()
            if candidate is None:
                return None

            running = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns
                    SET status = 'running',
                        attempt_count = attempt_count + 1,
                        lease_owner = :worker_id,
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        heartbeat_at = NOW(),
                        started_at = COALESCE(started_at, NOW()),
                        updated_at = NOW()
                    WHERE id = :turn_id
                      AND status = 'queued'
                    RETURNING id, conversation_id, actor_id,
                              requested_selection_mode, requested_model_profile_id,
                              input_kind_codes, status, error_code, error_message,
                              started_at, completed_at, created_at, updated_at,
                              queue_sequence, attempt_count, lease_expires_at,
                              cancel_requested_at
                    """,
                ),
                {
                    "turn_id": candidate["id"],
                    "worker_id": normalized_worker_id,
                    "lease_seconds": lease_seconds,
                },
            ).mappings().one()
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_execution_slots
                    SET turn_id = :turn_id,
                        lease_owner = :worker_id,
                        lease_expires_at = :lease_expires_at,
                        updated_at = NOW()
                    WHERE slot_number = :slot_number
                    """,
                ),
                {
                    "turn_id": candidate["id"],
                    "worker_id": normalized_worker_id,
                    "lease_expires_at": running["lease_expires_at"],
                    "slot_number": slot["slot_number"],
                },
            )
            self._insert_event(
                connection,
                candidate["id"],
                "started",
                {
                    "state": "running",
                    "attempt": running["attempt_count"],
                },
            )

        payload = TurnPayloadRecord(
            turn_id=candidate["id"],
            input_text=candidate["input_text"],
            effective_text=candidate["effective_text"],
            job_url=candidate["job_url"],
            attachment_payloads=tuple(
                self._json_value(candidate["attachment_payloads_json"], list)
            ),
            request_metadata=dict(
                self._json_value(candidate["request_metadata_json"], dict)
            ),
        )
        return ClaimedTurnRecord(
            turn=self._to_turn_record(running),
            payload=payload,
            queue_sequence=int(running["queue_sequence"]),
            attempt_count=int(running["attempt_count"]),
            slot_number=int(slot["slot_number"]),
            worker_id=normalized_worker_id,
            lease_expires_at=running["lease_expires_at"],
            cancel_requested_at=running["cancel_requested_at"],
        )

    def heartbeat(
        self,
        turn_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int,
    ) -> bool:
        """仅允许当前租约持有者同时续期 Turn 和它的槽位。"""

        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id or lease_seconds <= 0:
            raise ValueError("Worker ID 和租约秒数必须有效")
        with self._database.transaction() as connection:
            turn_result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns
                    SET heartbeat_at = NOW(),
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        updated_at = NOW()
                    WHERE id = :turn_id
                      AND status = 'running'
                      AND lease_owner = :worker_id
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "worker_id": normalized_worker_id,
                    "lease_seconds": lease_seconds,
                },
            )
            if turn_result.rowcount != 1:
                return False
            slot_result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_execution_slots
                    SET lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        updated_at = NOW()
                    WHERE turn_id = :turn_id
                      AND lease_owner = :worker_id
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "worker_id": normalized_worker_id,
                    "lease_seconds": lease_seconds,
                },
            )
        return slot_result.rowcount == 1

    def release_claim(self, turn_id: UUID, worker_id: str) -> bool:
        """执行真实结束后清除 Turn 租约并归还全局槽位。"""

        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise ValueError("Worker ID 不能为空")
        with self._database.transaction() as connection:
            turn_result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns
                    SET lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        updated_at = NOW()
                    WHERE id = :turn_id
                      AND lease_owner = :worker_id
                    """,
                ),
                {"turn_id": turn_id, "worker_id": normalized_worker_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_execution_slots
                    SET turn_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = NOW()
                    WHERE turn_id = :turn_id
                      AND lease_owner = :worker_id
                    """,
                ),
                {"turn_id": turn_id, "worker_id": normalized_worker_id},
            )
        return turn_result.rowcount == 1

    def finish_claim(
        self,
        turn_id: UUID,
        worker_id: str,
        *,
        status: AgentTurnStatus,
        event_type: str,
        event_payload: dict[str, object],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """原子收口 Worker 租约、写最终事件并归还执行槽位。"""

        if status not in {
            AgentTurnStatus.SUCCEEDED,
            AgentTurnStatus.FAILED,
            AgentTurnStatus.CANCELLED,
        }:
            raise ValueError("Worker 只能把 Turn 收口为终态")
        normalized_worker_id = worker_id.strip()
        normalized_event_type = event_type.strip()
        if not normalized_worker_id or not normalized_event_type:
            raise ValueError("Worker ID 和最终事件类型不能为空")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns
                    SET status = CASE
                            WHEN status = 'running' THEN :status
                            ELSE status
                        END,
                        error_code = CASE
                            WHEN status = 'running' THEN :error_code
                            ELSE error_code
                        END,
                        error_message = CASE
                            WHEN status = 'running' THEN :error_message
                            ELSE error_message
                        END,
                        completed_at = COALESCE(completed_at, NOW()),
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        updated_at = NOW()
                    WHERE id = :turn_id
                      AND lease_owner = :worker_id
                    RETURNING status
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "worker_id": normalized_worker_id,
                    "status": status.value,
                    "error_code": error_code if status is AgentTurnStatus.FAILED else None,
                    "error_message": (
                        error_message if status is AgentTurnStatus.FAILED else None
                    ),
                },
            ).mappings().one_or_none()
            if row is None:
                return False
            self._insert_event(
                connection,
                turn_id,
                normalized_event_type,
                event_payload,
            )
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_execution_slots
                    SET turn_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = NOW()
                    WHERE turn_id = :turn_id
                      AND lease_owner = :worker_id
                    """,
                ),
                {"turn_id": turn_id, "worker_id": normalized_worker_id},
            )
        return True

    def recover_expired(self, *, global_limit: int) -> int:
        """回收过期租约；已进入非幂等 Tool 的 Turn 失败，其余重新排队。"""

        if global_limit <= 0:
            raise ValueError("全局并发上限必须大于零")
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns AS turn
                    SET status = CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM career_assistant.agent_turn_events AS event
                                WHERE event.turn_id = turn.id
                                  AND event.event_type = 'tool_started'
                                  AND COALESCE(
                                      (event.payload_json ->> 'non_idempotent')::boolean,
                                      FALSE
                                  )
                            )
                            THEN 'failed'
                            ELSE 'queued'
                        END,
                        error_code = CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM career_assistant.agent_turn_events AS event
                                WHERE event.turn_id = turn.id
                                  AND event.event_type = 'tool_started'
                                  AND COALESCE(
                                      (event.payload_json ->> 'non_idempotent')::boolean,
                                      FALSE
                                  )
                            )
                            THEN 'worker_lease_expired_after_side_effect'
                            ELSE NULL
                        END,
                        error_message = CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM career_assistant.agent_turn_events AS event
                                WHERE event.turn_id = turn.id
                                  AND event.event_type = 'tool_started'
                                  AND COALESCE(
                                      (event.payload_json ->> 'non_idempotent')::boolean,
                                      FALSE
                                  )
                            )
                            THEN 'Worker 异常退出；为避免重复执行外部操作，本轮未自动重试。'
                            ELSE NULL
                        END,
                        available_at = NOW(),
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        completed_at = CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM career_assistant.agent_turn_events AS event
                                WHERE event.turn_id = turn.id
                                  AND event.event_type = 'tool_started'
                                  AND COALESCE(
                                      (event.payload_json ->> 'non_idempotent')::boolean,
                                      FALSE
                                  )
                            )
                            THEN NOW()
                            ELSE NULL
                        END,
                        updated_at = NOW()
                    WHERE turn.status = 'running'
                      AND turn.lease_expires_at < NOW()
                    """,
                ),
            )
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_execution_slots
                    SET turn_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = NOW()
                    WHERE slot_number <= :global_limit
                      AND lease_expires_at < NOW()
                    """,
                ),
                {"global_limit": global_limit},
            )
        return result.rowcount

    def append_event(
        self,
        turn_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> TurnEventRecord:
        """追加一条持久事件并返回数据库生成的递增事件 ID。"""

        normalized_type = event_type.strip()
        if not normalized_type:
            raise ValueError("Turn 事件类型不能为空")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.agent_turn_events (
                        turn_id, event_type, payload_json
                    ) VALUES (
                        :turn_id, :event_type, CAST(:payload AS jsonb)
                    )
                    RETURNING id, turn_id, event_type, payload_json, created_at
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "event_type": normalized_type,
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            ).mappings().one()
        return self._to_event_record(row)

    def list_events(
        self,
        actor_id: UUID,
        turn_id: UUID,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> tuple[TurnEventRecord, ...]:
        """按 Turn 所有者范围读取事件，支持 SSE Last-Event-ID。"""

        if after_id < 0:
            raise ValueError("事件游标不能为负数")
        if not 1 <= limit <= 500:
            raise ValueError("单次事件读取数量必须在 1 到 500 之间")
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT event.id, event.turn_id, event.event_type,
                           event.payload_json, event.created_at
                    FROM career_assistant.agent_turn_events AS event
                    INNER JOIN career_assistant.agent_turns AS turn
                        ON turn.id = event.turn_id
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = turn.conversation_id
                    WHERE event.turn_id = :turn_id
                      AND event.id > :after_id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    ORDER BY event.id
                    LIMIT :limit
                    """,
                ),
                {
                    "turn_id": turn_id,
                    "after_id": after_id,
                    "actor_id": actor_id,
                    "limit": limit,
                },
            ).mappings().all()
        return tuple(self._to_event_record(row) for row in rows)

    def request_cancel(self, actor_id: UUID, turn_id: UUID) -> AgentTurnStatus | None:
        """取消 queued Turn，或为 running Turn 写入协作式取消标记。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.agent_turns AS turn
                    SET status = CASE
                            WHEN turn.status = 'queued' THEN 'cancelled'
                            ELSE turn.status
                        END,
                        cancel_requested_at = NOW(),
                        completed_at = CASE
                            WHEN turn.status = 'queued' THEN NOW()
                            ELSE turn.completed_at
                        END,
                        updated_at = NOW()
                    FROM career_assistant.conversations AS conversation
                    WHERE turn.id = :turn_id
                      AND turn.conversation_id = conversation.id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND turn.status IN ('queued', 'running')
                    RETURNING turn.status
                    """,
                ),
                {"turn_id": turn_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        return AgentTurnStatus(row["status"]) if row is not None else None

    def get_turn_status(
        self,
        actor_id: UUID,
        turn_id: UUID,
    ) -> TurnQueueStatusRecord | None:
        """按所有者读取单个 Turn 及它在当前会话内的等待位置。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT turn.id, turn.conversation_id, turn.actor_id,
                           turn.requested_selection_mode,
                           turn.requested_model_profile_id,
                           turn.input_kind_codes, turn.status, turn.error_code,
                           turn.error_message, turn.started_at, turn.completed_at,
                           turn.created_at, turn.updated_at, turn.queue_sequence,
                           turn.attempt_count, turn.cancel_requested_at,
                           (
                               SELECT COUNT(*)
                               FROM career_assistant.agent_turns AS older
                               WHERE older.conversation_id = turn.conversation_id
                                 AND older.queue_sequence < turn.queue_sequence
                                 AND older.status IN ('queued', 'running')
                           ) AS conversation_position
                    FROM career_assistant.agent_turns AS turn
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = turn.conversation_id
                    WHERE turn.id = :turn_id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    """,
                ),
                {"turn_id": turn_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        return self._to_queue_status(row) if row is not None else None

    def list_active_turns(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> tuple[TurnQueueStatusRecord, ...]:
        """恢复页面刷新前已经提交但尚未进入终态的 Turn 列表。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT turn.id, turn.conversation_id, turn.actor_id,
                           turn.requested_selection_mode,
                           turn.requested_model_profile_id,
                           turn.input_kind_codes, turn.status, turn.error_code,
                           turn.error_message, turn.started_at, turn.completed_at,
                           turn.created_at, turn.updated_at, turn.queue_sequence,
                           turn.attempt_count, turn.cancel_requested_at,
                           ROW_NUMBER() OVER (ORDER BY turn.queue_sequence) - 1
                               AS conversation_position
                    FROM career_assistant.agent_turns AS turn
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = turn.conversation_id
                    WHERE turn.conversation_id = :conversation_id
                      AND turn.actor_id = :actor_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                      AND turn.status IN ('queued', 'running')
                    ORDER BY turn.queue_sequence
                    """,
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().all()
        return tuple(self._to_queue_status(row) for row in rows)

    @staticmethod
    def _insert_event(connection, turn_id: UUID, event_type: str, payload: dict[str, object]) -> None:
        connection.execute(
            text(
                """
                INSERT INTO career_assistant.agent_turn_events (
                    turn_id, event_type, payload_json
                ) VALUES (
                    :turn_id, :event_type, CAST(:payload AS jsonb)
                )
                """,
            ),
            {
                "turn_id": turn_id,
                "event_type": event_type,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )

    @staticmethod
    def _validate_worker_inputs(worker_id: str, lease_seconds: int, global_limit: int) -> str:
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise ValueError("Worker ID 不能为空")
        if lease_seconds <= 0:
            raise ValueError("租约秒数必须大于零")
        if global_limit <= 0:
            raise ValueError("全局并发上限必须大于零")
        return normalized_worker_id

    @staticmethod
    def _json_value(value: object, expected_type: type[list] | type[dict]):
        parsed = json.loads(value) if isinstance(value, str) else value
        if not isinstance(parsed, expected_type):
            raise ValueError("Turn payload JSON 类型无效")
        return parsed

    @staticmethod
    def _to_event_record(row: RowMapping) -> TurnEventRecord:
        return TurnEventRecord(
            id=int(row["id"]),
            turn_id=row["turn_id"],
            event_type=row["event_type"],
            payload=dict(CareerTurnJobRepository._json_value(row["payload_json"], dict)),
            created_at=row["created_at"],
        )

    @staticmethod
    def _to_turn_record(row: RowMapping) -> AgentTurnRecord:
        return AgentTurnRecord(
            id=row["id"],
            conversation_id=row["conversation_id"],
            actor_id=row["actor_id"],
            requested_selection_mode=ModelSelectionMode(row["requested_selection_mode"]),
            requested_model_profile_id=row["requested_model_profile_id"],
            input_kind_codes=tuple(row["input_kind_codes"]),
            status=AgentTurnStatus(row["status"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_queue_status(row: RowMapping) -> TurnQueueStatusRecord:
        return TurnQueueStatusRecord(
            turn=CareerTurnJobRepository._to_turn_record(row),
            queue_sequence=int(row["queue_sequence"]),
            attempt_count=int(row["attempt_count"]),
            cancel_requested_at=row["cancel_requested_at"],
            conversation_position=int(row["conversation_position"]),
        )
