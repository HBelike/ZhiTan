"""会话压缩后台任务的 PostgreSQL 租约仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.persistence.database import CareerDatabase


@dataclass(frozen=True)
class CompactionJobRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    conversation_id: UUID
    trigger_turn_id: UUID
    requested_profile_id: UUID | None
    status: str
    expected_summary_version: int
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    error_code: str | None


class CareerCompactionRepository:
    """按 trigger Turn 去重并安全领取会话压缩任务。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def enqueue(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        trigger_turn_id: UUID,
        *,
        requested_profile_id: UUID | None,
        expected_summary_version: int,
    ) -> CompactionJobRecord:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.conversation_compaction_jobs (
                      id, organization_id, actor_id, conversation_id,
                      trigger_turn_id, requested_profile_id, status,
                      expected_summary_version
                    ) VALUES (
                      :id, :organization_id, :actor_id, :conversation_id,
                      :trigger_turn_id, :requested_profile_id, 'queued',
                      :expected_summary_version
                    )
                    ON CONFLICT (trigger_turn_id) DO UPDATE
                    SET trigger_turn_id = EXCLUDED.trigger_turn_id
                    RETURNING *
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "trigger_turn_id": trigger_turn_id,
                    "requested_profile_id": requested_profile_id,
                    "expected_summary_version": expected_summary_version,
                },
            ).mappings().one()
        return self._to_record(row)

    def claim(self, worker_id: str, *, lease_seconds: int = 120) -> CompactionJobRecord | None:
        if not worker_id.strip() or lease_seconds < 10:
            raise ValueError("压缩 Worker 和租约配置无效")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    WITH candidate AS (
                      SELECT id
                      FROM career_assistant.conversation_compaction_jobs
                      WHERE status = 'queued'
                         OR (status = 'running' AND lease_expires_at < NOW())
                      ORDER BY created_at
                      FOR UPDATE SKIP LOCKED
                      LIMIT 1
                    )
                    UPDATE career_assistant.conversation_compaction_jobs AS job
                    SET status = 'running', lease_owner = :worker_id,
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        attempt_count = attempt_count + 1, updated_at = NOW()
                    FROM candidate
                    WHERE job.id = candidate.id
                    RETURNING job.*
                    """,
                ),
                {"worker_id": worker_id.strip(), "lease_seconds": lease_seconds},
            ).mappings().one_or_none()
        return self._to_record(row) if row is not None else None

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 120,
    ) -> CompactionJobRecord | None:
        """领取队列中最早的可运行任务。"""

        return self.claim(worker_id, lease_seconds=lease_seconds)

    def claim_for_turn(
        self,
        trigger_turn_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int = 120,
    ) -> CompactionJobRecord | None:
        """同步请求只领取本 Turn 的 queued/过期任务，绝不重复执行 running。"""

        if not worker_id.strip() or lease_seconds < 10:
            raise ValueError("压缩 Worker 和租约配置无效")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.conversation_compaction_jobs
                    SET status = 'running', lease_owner = :worker_id,
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        attempt_count = attempt_count + 1, updated_at = NOW()
                    WHERE trigger_turn_id = :trigger_turn_id
                      AND (status = 'queued'
                           OR (status = 'running' AND lease_expires_at < NOW()))
                    RETURNING *
                    """,
                ),
                {
                    "trigger_turn_id": trigger_turn_id,
                    "worker_id": worker_id.strip(),
                    "lease_seconds": lease_seconds,
                },
            ).mappings().one_or_none()
        return self._to_record(row) if row is not None else None

    def finish(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        status: str,
        error_code: str | None = None,
    ) -> bool:
        if status not in {"succeeded", "failed", "superseded"}:
            raise ValueError("压缩任务终态无效")
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.conversation_compaction_jobs
                    SET status = :status, error_code = :error_code,
                        lease_owner = NULL, lease_expires_at = NULL,
                        completed_at = NOW(), updated_at = NOW()
                    WHERE id = :job_id AND status = 'running'
                      AND lease_owner = :worker_id
                    """,
                ),
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "status": status,
                    "error_code": error_code,
                },
            )
        return result.rowcount == 1

    @staticmethod
    def _to_record(row: RowMapping) -> CompactionJobRecord:
        return CompactionJobRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            actor_id=row["actor_id"],
            conversation_id=row["conversation_id"],
            trigger_turn_id=row["trigger_turn_id"],
            requested_profile_id=row["requested_profile_id"],
            status=row["status"],
            expected_summary_version=row["expected_summary_version"],
            attempt_count=row["attempt_count"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            error_code=row["error_code"],
        )
