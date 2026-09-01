"""按操作记录模型真实用量。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import bindparam, text

from src.career_assistant.model_clients import CompletionUsage
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.model_profile_repository import ModelProfileRecord


MODEL_OPERATION_KINDS = frozenset(
    {"career_response", "conversation_memory_compaction", "career_memory_extraction"},
)


@dataclass(frozen=True)
class ModelInvocationMetadata:
    """供消息历史展示的模型调用元数据，不包含凭证与请求正文。"""

    provider_key: str
    requested_model_id: str
    provider_reported_model_id: str | None

    @property
    def model_id(self) -> str:
        return self.provider_reported_model_id or self.requested_model_id

    @property
    def source(self) -> str:
        return "provider_response" if self.provider_reported_model_id else "request"


class CareerModelUsageRepository:
    """利用 `(turn_id, operation_kind)` 保证每种操作只记一行。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def start(
        self,
        turn_id: UUID,
        operation_kind: str,
        requested_profile_id: UUID | None,
        resolved_profile: ModelProfileRecord,
    ) -> UUID:
        if operation_kind not in MODEL_OPERATION_KINDS:
            raise ValueError("模型操作类型无效")
        usage_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.model_usage (
                      id, turn_id, operation_kind, requested_profile_id,
                      resolved_profile_id, resolved_provider_key,
                      resolved_model_id, status
                    ) VALUES (
                      :id, :turn_id, :operation_kind, :requested_profile_id,
                      :resolved_profile_id, :resolved_provider_key,
                      :resolved_model_id, 'started'
                    )
                    ON CONFLICT (turn_id, operation_kind) WHERE turn_id IS NOT NULL DO UPDATE
                    SET turn_id = EXCLUDED.turn_id
                    RETURNING id
                    """,
                ),
                {
                    "id": usage_id,
                    "turn_id": turn_id,
                    "operation_kind": operation_kind,
                    "requested_profile_id": requested_profile_id,
                    "resolved_profile_id": resolved_profile.id,
                    "resolved_provider_key": resolved_profile.provider_key,
                    "resolved_model_id": resolved_profile.model_id,
                },
            ).mappings().one()
        return row["id"]

    def finish(
        self,
        usage_id: UUID,
        *,
        status: str,
        usage: CompletionUsage,
        error_code: str | None = None,
    ) -> None:
        if status not in {"succeeded", "rate_limited", "failed"}:
            raise ValueError("模型用量终态无效")
        with self._database.transaction() as connection:
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.model_usage
                    SET status = :status, input_tokens = :input_tokens,
                        output_tokens = :output_tokens, error_code = :error_code,
                        provider_reported_model_id = :provider_reported_model_id,
                        completed_at = NOW()
                    WHERE id = :usage_id
                    """,
                ),
                {
                    "usage_id": usage_id,
                    "status": status,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "provider_reported_model_id": usage.provider_reported_model_id,
                    "error_code": error_code,
                },
            )

    def list_turn_metadata(
        self,
        turn_ids: list[UUID] | tuple[UUID, ...],
    ) -> dict[UUID, ModelInvocationMetadata]:
        """批量读取回复对应的真实路由与 Provider 回报模型，避免历史页逐条查询。"""

        normalized_ids = tuple(dict.fromkeys(turn_ids))
        if not normalized_ids:
            return {}
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT turn_id, resolved_provider_key, resolved_model_id,
                           provider_reported_model_id
                    FROM career_assistant.model_usage
                    WHERE turn_id IN :turn_ids
                      AND operation_kind = 'career_response'
                      AND status = 'succeeded'
                      AND resolved_provider_key IS NOT NULL
                      AND resolved_model_id IS NOT NULL
                    """,
                ).bindparams(bindparam("turn_ids", expanding=True)),
                {"turn_ids": normalized_ids},
            ).mappings().all()
        return {
            row["turn_id"]: ModelInvocationMetadata(
                provider_key=row["resolved_provider_key"],
                requested_model_id=row["resolved_model_id"],
                provider_reported_model_id=row["provider_reported_model_id"],
            )
            for row in rows
        }

    def start_for_memory_job(
        self,
        memory_job_id: UUID,
        operation_kind: str,
        requested_profile_id: UUID | None,
        resolved_profile: ModelProfileRecord,
    ) -> UUID:
        if operation_kind != "career_memory_extraction":
            raise ValueError("长期记忆任务只允许使用抽取操作类型")
        usage_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.model_usage (
                      id, memory_job_id, operation_kind, requested_profile_id,
                      resolved_profile_id, resolved_provider_key,
                      resolved_model_id, status
                    ) VALUES (
                      :id, :memory_job_id, :operation_kind, :requested_profile_id,
                      :resolved_profile_id, :resolved_provider_key,
                      :resolved_model_id, 'started'
                    )
                    ON CONFLICT (memory_job_id, operation_kind)
                      WHERE memory_job_id IS NOT NULL DO UPDATE
                    SET memory_job_id = EXCLUDED.memory_job_id
                    RETURNING id
                    """,
                ),
                {
                    "id": usage_id,
                    "memory_job_id": memory_job_id,
                    "operation_kind": operation_kind,
                    "requested_profile_id": requested_profile_id,
                    "resolved_profile_id": resolved_profile.id,
                    "resolved_provider_key": resolved_profile.provider_key,
                    "resolved_model_id": resolved_profile.model_id,
                },
            ).mappings().one()
        return row["id"]
