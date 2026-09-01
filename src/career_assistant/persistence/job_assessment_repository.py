"""LLM Judge 岗位分析状态与已校验结果仓储。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.persistence.database import CareerDatabase


class JobAssessmentStatus(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    READY = "ready"
    FALLBACK_READY = "fallback_ready"
    FAILED = "failed"


@dataclass(frozen=True)
class JobAssessmentRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    candidate_profile_id: UUID
    target_role_profile_id: UUID
    judge_model_profile_id: UUID | None
    judge_provider_key: str
    judge_model_id: str
    prompt_version: str
    schema_version: str
    status: JobAssessmentStatus
    attempt_count: int
    result: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class CareerJobAssessmentRepository:
    """保证同一资料与模型版本最多只有一个运行中的评估。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def create_or_get_queued(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID,
        candidate_profile_id: UUID,
        target_role_profile_id: UUID,
        judge_model_profile_id: UUID | None,
        judge_provider_key: str,
        judge_model_id: str,
        prompt_version: str,
    ) -> tuple[JobAssessmentRecord, bool]:
        assessment_id = uuid4()
        parameters = {
            "id": assessment_id,
            "organization_id": organization_id,
            "actor_id": actor_id,
            "candidate_profile_id": candidate_profile_id,
            "target_role_profile_id": target_role_profile_id,
            "judge_model_profile_id": judge_model_profile_id,
            "judge_provider_key": judge_provider_key,
            "judge_model_id": judge_model_id,
            "prompt_version": prompt_version,
        }
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.job_match_assessments
                        (id, organization_id, actor_id, candidate_profile_id,
                         target_role_profile_id, judge_model_profile_id,
                         judge_provider_key, judge_model_id, prompt_version)
                    VALUES
                        (:id, :organization_id, :actor_id, :candidate_profile_id,
                         :target_role_profile_id, :judge_model_profile_id,
                         :judge_provider_key, :judge_model_id, :prompt_version)
                    ON CONFLICT
                        (candidate_profile_id, target_role_profile_id,
                         judge_provider_key, judge_model_id, prompt_version)
                    DO NOTHING
                    RETURNING *
                    """
                ),
                parameters,
            ).mappings().one_or_none()
            created = row is not None
            if row is None:
                row = connection.execute(
                    text(
                        """
                        SELECT * FROM career_assistant.job_match_assessments
                        WHERE candidate_profile_id = :candidate_profile_id
                          AND target_role_profile_id = :target_role_profile_id
                          AND judge_provider_key = :judge_provider_key
                          AND judge_model_id = :judge_model_id
                          AND prompt_version = :prompt_version
                        """
                    ),
                    parameters,
                ).mappings().one()
        return _record(row), created

    def get(self, assessment_id: UUID) -> JobAssessmentRecord | None:
        return self._read_one(
            "SELECT * FROM career_assistant.job_match_assessments WHERE id = :id",
            {"id": assessment_id},
        )

    def get_current(
        self,
        actor_id: UUID,
        candidate_profile_id: UUID,
        target_role_profile_id: UUID,
    ) -> JobAssessmentRecord | None:
        return self._read_one(
            """
            SELECT * FROM career_assistant.job_match_assessments
            WHERE actor_id = :actor_id
              AND candidate_profile_id = :candidate_profile_id
              AND target_role_profile_id = :target_role_profile_id
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            {
                "actor_id": actor_id,
                "candidate_profile_id": candidate_profile_id,
                "target_role_profile_id": target_role_profile_id,
            },
        )

    def claim(self, assessment_id: UUID) -> JobAssessmentRecord | None:
        return self._write_one(
            """
            UPDATE career_assistant.job_match_assessments
            SET status = 'analyzing', updated_at = NOW()
            WHERE id = :id AND status = 'queued'
            RETURNING *
            """,
            {"id": assessment_id},
        )

    def save_ready(
        self,
        assessment_id: UUID,
        *,
        result: dict[str, object],
        attempt_count: int,
        fallback: bool = False,
    ) -> JobAssessmentRecord:
        record = self._write_one(
            """
            UPDATE career_assistant.job_match_assessments
            SET status = :status, attempt_count = :attempt_count,
                result = CAST(:result AS JSONB), error_code = NULL, updated_at = NOW()
            WHERE id = :id
            RETURNING *
            """,
            {
                "id": assessment_id,
                "status": "fallback_ready" if fallback else "ready",
                "attempt_count": attempt_count,
                "result": json.dumps(result, ensure_ascii=False),
            },
        )
        if record is None:
            raise LookupError("岗位分析记录不存在")
        return record

    def save_failed(
        self,
        assessment_id: UUID,
        *,
        error_code: str,
        attempt_count: int,
    ) -> JobAssessmentRecord:
        record = self._write_one(
            """
            UPDATE career_assistant.job_match_assessments
            SET status = 'failed', attempt_count = :attempt_count,
                result = NULL, error_code = :error_code, updated_at = NOW()
            WHERE id = :id
            RETURNING *
            """,
            {"id": assessment_id, "attempt_count": attempt_count, "error_code": error_code[:80]},
        )
        if record is None:
            raise LookupError("岗位分析记录不存在")
        return record

    def reset_for_retry(self, assessment_id: UUID) -> JobAssessmentRecord:
        record = self._write_one(
            """
            UPDATE career_assistant.job_match_assessments
            SET status = 'queued', attempt_count = 0, result = NULL,
                error_code = NULL, updated_at = NOW()
            WHERE id = :id AND status IN ('failed', 'fallback_ready', 'ready')
            RETURNING *
            """,
            {"id": assessment_id},
        )
        if record is None:
            existing = self.get(assessment_id)
            if existing is None:
                raise LookupError("岗位分析记录不存在")
            return existing
        return record

    def _read_one(self, sql: str, parameters: dict[str, object]) -> JobAssessmentRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(text(sql), parameters).mappings().one_or_none()
        return _record(row) if row is not None else None

    def _write_one(self, sql: str, parameters: dict[str, object]) -> JobAssessmentRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(text(sql), parameters).mappings().one_or_none()
        return _record(row) if row is not None else None


def _record(row: RowMapping) -> JobAssessmentRecord:
    return JobAssessmentRecord(
        id=row["id"],
        organization_id=row["organization_id"],
        actor_id=row["actor_id"],
        candidate_profile_id=row["candidate_profile_id"],
        target_role_profile_id=row["target_role_profile_id"],
        judge_model_profile_id=row["judge_model_profile_id"],
        judge_provider_key=row["judge_provider_key"],
        judge_model_id=row["judge_model_id"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        status=JobAssessmentStatus(row["status"]),
        attempt_count=int(row["attempt_count"]),
        result=dict(row["result"]) if row["result"] is not None else None,
        error_code=row["error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
