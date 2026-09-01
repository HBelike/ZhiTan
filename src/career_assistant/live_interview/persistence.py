"""实时面试会话、final 话语和答案的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.live_interview.contracts import (
    AnswerStatus,
    LiveInterviewStatus,
    QuestionIntent,
    TranscriptEvent,
)
from src.career_assistant.persistence.database import CareerDatabase


@dataclass(frozen=True)
class LiveInterviewSessionRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    candidate_profile_id: UUID | None
    target_role_profile_id: UUID | None
    interview_experience_ids: tuple[UUID, ...]
    asr_provider: str
    asr_model_profile_id: UUID | None
    answer_model_profile_id: UUID | None
    client_kind: str
    candidate_audio_enabled: bool
    status: LiveInterviewStatus
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LiveInterviewRepository:
    """所有会话读取均同时限制 organization_id 和 actor_id。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def create_session(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        candidate_profile_id: UUID | None,
        target_role_profile_id: UUID | None,
        interview_experience_ids: tuple[UUID, ...] = (),
        asr_provider: str = "openai",
        asr_model_profile_id: UUID | None = None,
        answer_model_profile_id: UUID | None = None,
        client_kind: str = "desktop",
        candidate_audio_enabled: bool = True,
    ) -> LiveInterviewSessionRecord:
        session_id = uuid4()
        params = {
            "id": session_id,
            "organization_id": organization_id,
            "actor_id": actor_id,
            "candidate_profile_id": candidate_profile_id,
            "target_role_profile_id": target_role_profile_id,
            "experience_ids": json.dumps([str(item) for item in interview_experience_ids]),
            "asr_provider": asr_provider.strip() or "openai",
            "asr_model_profile_id": asr_model_profile_id,
            "answer_model_profile_id": answer_model_profile_id,
            "client_kind": client_kind,
            "candidate_audio_enabled": candidate_audio_enabled,
        }
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.live_interview_sessions
                        (id, organization_id, actor_id, candidate_profile_id,
                         target_role_profile_id, interview_experience_ids,
                         asr_provider, asr_model_profile_id, answer_model_profile_id,
                         client_kind, candidate_audio_enabled)
                    SELECT
                        :id, :organization_id, :actor_id, :candidate_profile_id,
                        :target_role_profile_id, CAST(:experience_ids AS JSONB),
                        :asr_provider, :asr_model_profile_id, :answer_model_profile_id,
                        :client_kind, :candidate_audio_enabled
                    WHERE
                        (CAST(:candidate_profile_id AS UUID) IS NULL OR EXISTS(
                            SELECT 1 FROM career_assistant.candidate_profiles
                            WHERE id = CAST(:candidate_profile_id AS UUID)
                              AND organization_id = :organization_id
                              AND actor_id = :actor_id
                        ))
                        AND
                        (CAST(:target_role_profile_id AS UUID) IS NULL OR EXISTS(
                            SELECT 1 FROM career_assistant.target_role_profiles
                            WHERE id = CAST(:target_role_profile_id AS UUID)
                              AND organization_id = :organization_id
                              AND actor_id = :actor_id
                        ))
                    RETURNING *
                    """
                ),
                params,
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("简历或目标岗位不存在，或无权访问")
        return _session(row)

    def get_session(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
    ) -> LiveInterviewSessionRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.live_interview_sessions
                    WHERE id = :session_id
                      AND organization_id = :organization_id
                      AND actor_id = :actor_id
                    """
                ),
                {
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                },
            ).mappings().one_or_none()
        return _session(row) if row is not None else None

    def activate(self, organization_id: UUID, actor_id: UUID, session_id: UUID) -> bool:
        return self._set_session_status(
            organization_id,
            actor_id,
            session_id,
            LiveInterviewStatus.ACTIVE,
        )

    def end(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        *,
        failed: bool = False,
    ) -> bool:
        return self._set_session_status(
            organization_id,
            actor_id,
            session_id,
            LiveInterviewStatus.FAILED if failed else LiveInterviewStatus.COMPLETED,
        )

    def _set_session_status(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        target: LiveInterviewStatus,
    ) -> bool:
        timestamp_field = "started_at" if target is LiveInterviewStatus.ACTIVE else "ended_at"
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    f"""
                    UPDATE career_assistant.live_interview_sessions
                    SET status = :status, {timestamp_field} = COALESCE({timestamp_field}, NOW()),
                        updated_at = NOW()
                    WHERE id = :session_id
                      AND organization_id = :organization_id
                      AND actor_id = :actor_id
                      AND status NOT IN ('completed', 'failed')
                    """
                ),
                {
                    "status": target.value,
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                },
            )
        return bool(result.rowcount)

    def append_final_utterance(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        event: TranscriptEvent,
        *,
        corrected_text: str | None = None,
    ) -> UUID:
        if not event.is_final:
            raise ValueError("只允许持久化 final 转写")
        utterance_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.live_interview_utterances
                        (id, session_id, role, channel_sequence, raw_text,
                         corrected_text, provider, confidence)
                    SELECT :id, session.id, :role, :sequence, :raw_text,
                           :corrected_text, :provider, :confidence
                    FROM career_assistant.live_interview_sessions AS session
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ON CONFLICT (session_id, role, channel_sequence) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": utterance_id,
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "role": event.role.value,
                    "sequence": event.sequence,
                    "raw_text": event.text,
                    "corrected_text": corrected_text if corrected_text != event.text else None,
                    "provider": event.provider,
                    "confidence": event.confidence,
                },
            ).scalar_one_or_none()
        if row is None:
            raise LookupError("会话不存在、无权访问或该 final 已保存")
        return UUID(str(row))

    def upsert_answer(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        *,
        question_version: int,
        attempt: int,
        question: str,
        intent: QuestionIntent,
        status: AnswerStatus,
        answer_text: str = "",
        evidence: tuple[dict[str, Any], ...] = (),
        error_code: str | None = None,
    ) -> UUID:
        answer_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.live_interview_answers
                        (id, session_id, question_version, attempt, original_question,
                         normalized_question, intent, status, answer_text,
                         evidence_json, error_code)
                    SELECT :id, session.id, :question_version, :attempt, :question,
                           :question, :intent, :status, :answer_text,
                           CAST(:evidence AS JSONB), :error_code
                    FROM career_assistant.live_interview_sessions AS session
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ON CONFLICT (session_id, question_version, attempt)
                    DO UPDATE SET status = EXCLUDED.status,
                                  answer_text = EXCLUDED.answer_text,
                                  evidence_json = EXCLUDED.evidence_json,
                                  error_code = EXCLUDED.error_code,
                                  updated_at = NOW()
                    RETURNING id
                    """
                ),
                {
                    "id": answer_id,
                    "session_id": session_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "question_version": question_version,
                    "attempt": attempt,
                    "question": question.strip(),
                    "intent": intent.value,
                    "status": status.value,
                    "answer_text": answer_text,
                    "evidence": json.dumps(evidence, ensure_ascii=False),
                    "error_code": error_code,
                },
            ).scalar_one_or_none()
        if row is None:
            raise LookupError("会话不存在或无权访问")
        return UUID(str(row))

    def history(
        self,
        organization_id: UUID,
        actor_id: UUID,
        session_id: UUID,
    ) -> dict[str, object] | None:
        session = self.get_session(organization_id, actor_id, session_id)
        if session is None:
            return None
        with self._database.transaction() as connection:
            utterances = connection.execute(
                text(
                    """
                    SELECT utterance.*
                    FROM career_assistant.live_interview_utterances AS utterance
                    JOIN career_assistant.live_interview_sessions AS session
                      ON session.id = utterance.session_id
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ORDER BY utterance.created_at, utterance.channel_sequence
                    """
                ),
                {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().all()
            answers = connection.execute(
                text(
                    """
                    SELECT answer.*
                    FROM career_assistant.live_interview_answers AS answer
                    JOIN career_assistant.live_interview_sessions AS session
                      ON session.id = answer.session_id
                    WHERE session.id = :session_id
                      AND session.organization_id = :organization_id
                      AND session.actor_id = :actor_id
                    ORDER BY answer.question_version, answer.attempt
                    """
                ),
                {"session_id": session_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().all()
        return {
            "session": _session_payload(session),
            "utterances": [dict(row) for row in utterances],
            "answers": [dict(row) for row in answers],
        }


def _session(row: RowMapping) -> LiveInterviewSessionRecord:
    return LiveInterviewSessionRecord(
        id=UUID(str(row["id"])),
        organization_id=UUID(str(row["organization_id"])),
        actor_id=UUID(str(row["actor_id"])),
        candidate_profile_id=UUID(str(row["candidate_profile_id"])) if row["candidate_profile_id"] else None,
        target_role_profile_id=UUID(str(row["target_role_profile_id"])) if row["target_role_profile_id"] else None,
        interview_experience_ids=tuple(UUID(str(item)) for item in row["interview_experience_ids"]),
        asr_provider=str(row["asr_provider"]),
        asr_model_profile_id=UUID(str(row["asr_model_profile_id"])) if row["asr_model_profile_id"] else None,
        answer_model_profile_id=UUID(str(row["answer_model_profile_id"])) if row["answer_model_profile_id"] else None,
        client_kind=str(row["client_kind"]),
        candidate_audio_enabled=bool(row["candidate_audio_enabled"]),
        status=LiveInterviewStatus(str(row["status"])),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _session_payload(record: LiveInterviewSessionRecord) -> dict[str, object]:
    return {
        "id": str(record.id),
        "status": record.status.value,
        "candidate_profile_id": str(record.candidate_profile_id) if record.candidate_profile_id else None,
        "target_role_profile_id": str(record.target_role_profile_id) if record.target_role_profile_id else None,
        "interview_experience_ids": [str(item) for item in record.interview_experience_ids],
        "asr_provider": record.asr_provider,
        "asr_model_profile_id": str(record.asr_model_profile_id) if record.asr_model_profile_id else None,
        "answer_model_profile_id": str(record.answer_model_profile_id) if record.answer_model_profile_id else None,
        "client_kind": record.client_kind,
        "candidate_audio_enabled": record.candidate_audio_enabled,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "created_at": record.created_at,
    }


session_payload = _session_payload
