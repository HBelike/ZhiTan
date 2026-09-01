"""职业空间与长期求职记忆的 PostgreSQL 作用域仓储。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.career_memory import (
    CareerMemoryDraft,
    CareerMemoryStatus,
    CareerMemoryType,
)
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.records import CareerMemoryItemRecord, CareerSpaceRecord


@dataclass(frozen=True)
class CareerMemoryJobRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    job_kind: str
    status: str
    attempt_count: int
    created_at: datetime
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    candidate_profile_id: UUID | None = None
    candidate_profile_version: int | None = None
    requested_profile_id: UUID | None = None


class CareerMemoryRepository:
    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def ensure_default_space(
        self,
        organization_id: UUID,
        actor_id: UUID,
    ) -> CareerSpaceRecord:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.career_spaces
                      (id, organization_id, actor_id, name, normalized_name, is_default)
                    VALUES (:id, :organization_id, :actor_id, '默认求职方向', '默认求职方向', TRUE)
                    ON CONFLICT (organization_id, actor_id, normalized_name) DO UPDATE
                    SET updated_at = career_assistant.career_spaces.updated_at
                    RETURNING id, organization_id, actor_id, name, normalized_name,
                              is_default, created_at, updated_at
                    """,
                ),
                {"id": uuid4(), "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one()
        return self._space_record(row)

    def create_space(
        self,
        organization_id: UUID,
        actor_id: UUID,
        name: str,
    ) -> CareerSpaceRecord:
        normalized_name = " ".join(name.split())
        if not normalized_name or len(normalized_name) > 120:
            raise ValueError("职业空间名称长度必须在 1 到 120 字符之间")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.career_spaces
                      (id, organization_id, actor_id, name, normalized_name, is_default)
                    VALUES (:id, :organization_id, :actor_id, :name, :normalized_name, FALSE)
                    RETURNING id, organization_id, actor_id, name, normalized_name,
                              is_default, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "name": normalized_name,
                    "normalized_name": normalized_name.casefold(),
                },
            ).mappings().one()
        return self._space_record(row)

    def list_spaces(self, organization_id: UUID, actor_id: UUID) -> tuple[CareerSpaceRecord, ...]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, organization_id, actor_id, name, normalized_name,
                           is_default, created_at, updated_at
                    FROM career_assistant.career_spaces
                    WHERE organization_id = :organization_id AND actor_id = :actor_id
                    ORDER BY is_default DESC, updated_at DESC
                    """,
                ),
                {"organization_id": organization_id, "actor_id": actor_id},
            ).mappings().all()
        return tuple(self._space_record(row) for row in rows)

    def list_memories(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        career_space_id: UUID | None = None,
        status: CareerMemoryStatus | None = None,
    ) -> tuple[CareerMemoryItemRecord, ...]:
        conditions = [
            "organization_id = :organization_id",
            "actor_id = :actor_id",
        ]
        parameters: dict[str, object] = {
            "organization_id": organization_id,
            "actor_id": actor_id,
        }
        if career_space_id is not None:
            conditions.append("career_space_id = :career_space_id")
            parameters["career_space_id"] = career_space_id
        if status is not None:
            conditions.append("status = :status")
            parameters["status"] = status.value

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM career_assistant.career_memory_items "
                    f"WHERE {' AND '.join(conditions)} "
                    "ORDER BY updated_at DESC, id DESC",
                ),
                parameters,
            ).mappings().all()
        return tuple(self._memory_record(row) for row in rows)

    def confirm_candidate(
        self, organization_id: UUID, actor_id: UUID, memory_id: UUID
    ) -> CareerMemoryItemRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.career_memory_items
                    SET status = 'active', source_kind = 'user_confirmed_candidate', updated_at = NOW()
                    WHERE id = :memory_id AND organization_id = :organization_id
                      AND actor_id = :actor_id AND status = 'candidate'
                    RETURNING *
                    """,
                ),
                {"memory_id": memory_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        return self._memory_record(row) if row is not None else None

    def disable_memory(
        self, organization_id: UUID, actor_id: UUID, memory_id: UUID
    ) -> CareerMemoryItemRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.career_memory_items
                    SET status = 'disabled', valid_to = NOW(), updated_at = NOW()
                    WHERE id = :memory_id AND organization_id = :organization_id
                      AND actor_id = :actor_id AND status IN ('active','candidate')
                    RETURNING *
                    """,
                ),
                {"memory_id": memory_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        return self._memory_record(row) if row is not None else None

    def delete_memory(self, organization_id: UUID, actor_id: UUID, memory_id: UUID) -> bool:
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM career_assistant.career_memory_items
                    WHERE id = :memory_id AND organization_id = :organization_id AND actor_id = :actor_id
                    """,
                ),
                {"memory_id": memory_id, "organization_id": organization_id, "actor_id": actor_id},
            )
        return result.rowcount == 1

    def get_memory(
        self, organization_id: UUID, actor_id: UUID, memory_id: UUID
    ) -> CareerMemoryItemRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.career_memory_items
                    WHERE id = :memory_id AND organization_id = :organization_id AND actor_id = :actor_id
                    """,
                ),
                {"memory_id": memory_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        return self._memory_record(row) if row is not None else None

    def list_turn_usages(
        self,
        organization_id: UUID,
        actor_id: UUID,
        turn_id: UUID,
    ) -> tuple[dict[str, object], ...]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT usage.memory_id, usage.memory_type, usage.source_kind, usage.created_at,
                           memory.display_text, memory.candidate_profile_version,
                           profile.display_name AS candidate_profile_name,
                           conversation.created_at AS source_conversation_created_at
                    FROM career_assistant.turn_memory_usages AS usage
                    INNER JOIN career_assistant.agent_turns AS turn ON turn.id = usage.turn_id
                    LEFT JOIN career_assistant.career_memory_items AS memory ON memory.id = usage.memory_id
                    LEFT JOIN career_assistant.candidate_profiles AS profile
                      ON profile.id = memory.candidate_profile_id
                    LEFT JOIN career_assistant.conversations AS conversation
                      ON conversation.id = memory.source_conversation_id
                    WHERE usage.turn_id = :turn_id AND usage.organization_id = :organization_id
                      AND usage.actor_id = :actor_id AND turn.actor_id = :actor_id
                    ORDER BY usage.created_at
                    """,
                ),
                {"turn_id": turn_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().all()
        return tuple(dict(row) for row in rows)

    def delete_conversation_with_memory_choice(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        *,
        forget_derived_memories: bool,
    ) -> tuple[bool, int]:
        with self._database.transaction() as connection:
            owned = connection.execute(
                text(
                    """
                    SELECT id FROM career_assistant.conversations
                    WHERE id = :conversation_id AND organization_id = :organization_id
                      AND actor_id = :actor_id FOR UPDATE
                    """,
                ),
                {"conversation_id": conversation_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one_or_none()
            if owned is None:
                return False, 0
            forgotten = 0
            if forget_derived_memories:
                deleted_memories = connection.execute(
                    text(
                        """
                        DELETE FROM career_assistant.career_memory_items
                        WHERE organization_id = :organization_id AND actor_id = :actor_id
                          AND source_conversation_id = :conversation_id
                          AND source_kind <> 'confirmed_resume'
                        """,
                    ),
                    {"conversation_id": conversation_id, "organization_id": organization_id, "actor_id": actor_id},
                )
                forgotten = deleted_memories.rowcount
            connection.execute(
                text(
                    """
                    DELETE FROM career_assistant.interview_retrieval_feedback
                    WHERE conversation_id = :conversation_id
                    """,
                ),
                {"conversation_id": conversation_id},
            )
            result = connection.execute(
                text("DELETE FROM career_assistant.conversations WHERE id = :conversation_id AND actor_id = :actor_id"),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            )
        return result.rowcount == 1, forgotten

    def bind_conversation_space(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        career_space_id: UUID,
    ) -> bool:
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.conversations AS conversation
                    SET career_space_id = space.id, updated_at = NOW()
                    FROM career_assistant.career_spaces AS space
                    WHERE conversation.id = :conversation_id
                      AND conversation.organization_id = :organization_id
                      AND conversation.actor_id = :actor_id
                      AND space.id = :career_space_id
                      AND space.organization_id = :organization_id
                      AND space.actor_id = :actor_id
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "career_space_id": career_space_id,
                },
            )
        return result.rowcount == 1

    def enqueue_turn_extraction(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        requested_profile_id: UUID,
    ) -> UUID:
        return self._enqueue_job(
            organization_id,
            actor_id,
            "turn_extraction",
            conversation_id=conversation_id,
            turn_id=turn_id,
            requested_profile_id=requested_profile_id,
        )

    def enqueue_resume_indexing(
        self,
        organization_id: UUID,
        actor_id: UUID,
        candidate_profile_id: UUID,
        candidate_profile_version: int,
    ) -> UUID:
        if candidate_profile_version < 1:
            raise ValueError("简历版本必须大于 0")
        return self._enqueue_job(
            organization_id,
            actor_id,
            "resume_indexing",
            candidate_profile_id=candidate_profile_id,
            candidate_profile_version=candidate_profile_version,
        )

    def claim_next_memory_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
    ) -> CareerMemoryJobRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    WITH candidate AS (
                      SELECT id FROM career_assistant.career_memory_jobs
                      WHERE status = 'queued'
                      ORDER BY created_at, id
                      FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    UPDATE career_assistant.career_memory_jobs AS job
                    SET status = 'running', lease_owner = :worker_id,
                        lease_expires_at = NOW() + (:lease_seconds * INTERVAL '1 second'),
                        attempt_count = attempt_count + 1, updated_at = NOW()
                    FROM candidate
                    WHERE job.id = candidate.id
                    RETURNING job.*
                    """,
                ),
                {"worker_id": worker_id, "lease_seconds": lease_seconds},
            ).mappings().one_or_none()
        return self._job_record(row) if row is not None else None

    def finish_memory_job(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        status: str,
        error_code: str | None = None,
    ) -> bool:
        if status not in {"succeeded", "failed", "superseded"}:
            raise ValueError("长期记忆任务终态无效")
        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE career_assistant.career_memory_jobs
                    SET status = :status, error_code = :error_code, completed_at = NOW(),
                        lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = :job_id AND status = 'running' AND lease_owner = :worker_id
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

    def load_memory_job_source(
        self,
        job: CareerMemoryJobRecord,
    ) -> tuple[str, UUID | None, UUID | None]:
        with self._database.transaction() as connection:
            if job.job_kind == "turn_extraction":
                row = connection.execute(
                    text(
                        """
                        SELECT message.content_text, message.id AS source_message_id,
                               conversation.career_space_id
                        FROM career_assistant.messages AS message
                        INNER JOIN career_assistant.conversations AS conversation
                          ON conversation.id = message.conversation_id
                        INNER JOIN career_assistant.agent_turns AS turn ON turn.id = message.turn_id
                        WHERE message.turn_id = :turn_id AND message.role = 'user'
                          AND turn.status = 'succeeded'
                          AND conversation.organization_id = :organization_id
                          AND conversation.actor_id = :actor_id
                        ORDER BY message.created_at LIMIT 1
                        """,
                    ),
                    {
                        "turn_id": job.turn_id,
                        "organization_id": job.organization_id,
                        "actor_id": job.actor_id,
                    },
                ).mappings().one_or_none()
                if row is None:
                    raise LookupError("成功 Turn 的用户消息不存在")
                return row["content_text"], row["source_message_id"], row["career_space_id"]
            row = connection.execute(
                text(
                    """
                    SELECT resume_outline FROM career_assistant.candidate_profiles
                    WHERE id = :candidate_profile_id AND version = :candidate_profile_version
                      AND organization_id = :organization_id AND actor_id = :actor_id
                    """,
                ),
                {
                    "candidate_profile_id": job.candidate_profile_id,
                    "candidate_profile_version": job.candidate_profile_version,
                    "organization_id": job.organization_id,
                    "actor_id": job.actor_id,
                },
            ).mappings().one_or_none()
            if row is None:
                raise LookupError("已确认简历版本不存在")
            return row["resume_outline"], None, None

    def apply_extracted_memories(
        self,
        job: CareerMemoryJobRecord,
        drafts: tuple[CareerMemoryDraft, ...],
    ) -> tuple[UUID, ...]:
        normalized = tuple(item.validate() for item in drafts)
        with self._database.transaction() as connection:
            if job.job_kind == "resume_indexing":
                connection.execute(
                    text(
                        """
                        UPDATE career_assistant.career_memory_items AS memory
                        SET status = 'superseded', valid_to = NOW(), updated_at = NOW()
                        FROM career_assistant.candidate_profiles AS current_profile,
                             career_assistant.candidate_profiles AS old_profile
                        WHERE current_profile.id = :candidate_profile_id
                          AND current_profile.actor_id = :actor_id
                          AND old_profile.display_name = current_profile.display_name
                          AND old_profile.actor_id = current_profile.actor_id
                          AND memory.candidate_profile_id = old_profile.id
                          AND memory.organization_id = :organization_id
                          AND memory.actor_id = :actor_id
                          AND memory.source_kind = 'confirmed_resume'
                          AND memory.status = 'active'
                        """,
                    ),
                    {
                        "candidate_profile_id": job.candidate_profile_id,
                        "organization_id": job.organization_id,
                        "actor_id": job.actor_id,
                    },
                )
            created_ids: list[UUID] = []
            for draft in normalized:
                if (
                    job.job_kind == "turn_extraction"
                    and draft.memory_type is CareerMemoryType.JOB_INTENTION
                    and draft.source_kind == "explicit_user_correction"
                ):
                    connection.execute(
                        text(
                            """
                            UPDATE career_assistant.career_memory_items
                            SET status = 'superseded', valid_to = NOW(), updated_at = NOW()
                            WHERE organization_id = :organization_id AND actor_id = :actor_id
                              AND career_space_id = :career_space_id
                              AND memory_type = 'job_intention' AND status = 'active'
                            """,
                        ),
                        {
                            "organization_id": job.organization_id,
                            "actor_id": job.actor_id,
                            "career_space_id": draft.career_space_id,
                        },
                    )
                status = (
                    CareerMemoryStatus.ACTIVE
                    if job.job_kind == "resume_indexing"
                    or draft.memory_type is CareerMemoryType.JOB_INTENTION
                    else CareerMemoryStatus.CANDIDATE
                )
                row = self._insert_memory(
                    connection, job.organization_id, job.actor_id, draft, status
                )
                created_ids.append(row["id"])
        return tuple(created_ids)

    def _enqueue_job(
        self,
        organization_id: UUID,
        actor_id: UUID,
        job_kind: str,
        *,
        conversation_id: UUID | None = None,
        turn_id: UUID | None = None,
        candidate_profile_id: UUID | None = None,
        candidate_profile_version: int | None = None,
        requested_profile_id: UUID | None = None,
    ) -> UUID:
        job_id = uuid4()
        conflict = (
            "(turn_id) WHERE turn_id IS NOT NULL"
            if turn_id is not None
            else "(candidate_profile_id, candidate_profile_version) WHERE candidate_profile_id IS NOT NULL"
        )
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    f"""
                    INSERT INTO career_assistant.career_memory_jobs (
                      id, organization_id, actor_id, conversation_id, turn_id,
                      candidate_profile_id, candidate_profile_version, job_kind,
                      requested_profile_id, status
                    ) VALUES (
                      :id, :organization_id, :actor_id, :conversation_id, :turn_id,
                      :candidate_profile_id, :candidate_profile_version, :job_kind,
                      :requested_profile_id, 'queued'
                    ) ON CONFLICT {conflict} DO UPDATE
                    SET updated_at = career_assistant.career_memory_jobs.updated_at
                    RETURNING id
                    """,
                ),
                {
                    "id": job_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "candidate_profile_id": candidate_profile_id,
                    "candidate_profile_version": candidate_profile_version,
                    "job_kind": job_kind,
                    "requested_profile_id": requested_profile_id,
                },
            ).mappings().one()
        return row["id"]

    def create_memory(
        self,
        organization_id: UUID,
        actor_id: UUID,
        draft: CareerMemoryDraft,
        status: CareerMemoryStatus = CareerMemoryStatus.CANDIDATE,
    ) -> CareerMemoryItemRecord:
        normalized = draft.validate()
        with self._database.transaction() as connection:
            row = self._insert_memory(connection, organization_id, actor_id, normalized, status)
        return self._memory_record(row)

    def supersede_active(
        self,
        organization_id: UUID,
        actor_id: UUID,
        old_id: UUID,
        replacement: CareerMemoryDraft,
    ) -> CareerMemoryItemRecord:
        normalized = replacement.validate()
        with self._database.transaction() as connection:
            old = connection.execute(
                text(
                    """
                    SELECT id FROM career_assistant.career_memory_items
                    WHERE id = :old_id AND organization_id = :organization_id
                      AND actor_id = :actor_id AND status = 'active'
                    FOR UPDATE
                    """,
                ),
                {"old_id": old_id, "organization_id": organization_id, "actor_id": actor_id},
            ).mappings().one_or_none()
            if old is None:
                raise LookupError("求职记忆不存在或已经失效")
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.career_memory_items
                    SET status = 'superseded', valid_to = NOW(), updated_at = NOW()
                    WHERE id = :old_id AND organization_id = :organization_id AND actor_id = :actor_id
                    """,
                ),
                {"old_id": old_id, "organization_id": organization_id, "actor_id": actor_id},
            )
            row = self._insert_memory(
                connection,
                organization_id,
                actor_id,
                normalized,
                CareerMemoryStatus.ACTIVE,
                supersedes_memory_id=old_id,
            )
        return self._memory_record(row)

    def list_active_for_prompt(
        self,
        organization_id: UUID,
        actor_id: UUID,
        career_space_id: UUID,
        *,
        memory_types: Sequence[CareerMemoryType],
        candidate_profile_id: UUID | None = None,
        candidate_profile_version: int | None = None,
        query: str = "",
        limit: int = 5,
    ) -> tuple[CareerMemoryItemRecord, ...]:
        if limit < 1 or limit > 50:
            raise ValueError("求职记忆读取数量无效")
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.career_memory_items
                    WHERE organization_id = :organization_id
                      AND actor_id = :actor_id
                      AND status = 'active'
                      AND memory_type = ANY(:memory_types)
                      AND (career_space_id = :career_space_id OR career_space_id IS NULL)
                      AND (candidate_profile_id IS NULL
                           OR (:candidate_profile_id IS NOT NULL
                               AND candidate_profile_id = :candidate_profile_id
                               AND candidate_profile_version = :candidate_profile_version))
                      AND (:query = '' OR
                           to_tsvector('simple', display_text) @@ plainto_tsquery('simple', :query))
                    ORDER BY
                      CASE source_kind
                        WHEN 'explicit_user_correction' THEN 0
                        WHEN 'confirmed_resume' THEN 1
                        ELSE 2
                      END,
                      CASE WHEN :query = '' THEN 0 ELSE
                        ts_rank(to_tsvector('simple', display_text), plainto_tsquery('simple', :query))
                      END DESC,
                      updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "career_space_id": career_space_id,
                    "memory_types": [item.value for item in memory_types],
                    "candidate_profile_id": candidate_profile_id,
                    "candidate_profile_version": candidate_profile_version,
                    "query": query.strip(),
                    "limit": limit,
                },
            ).mappings().all()
        return tuple(self._memory_record(row) for row in rows)

    def get_conversation_memory_scope(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> tuple[UUID, UUID | None, int | None]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT conversation.career_space_id,
                           candidate.id AS candidate_profile_id,
                           candidate.version AS candidate_profile_version
                    FROM career_assistant.conversations AS conversation
                    LEFT JOIN LATERAL (
                      SELECT binding.candidate_profile_id
                      FROM career_assistant.conversation_context_bindings AS binding
                      WHERE binding.conversation_id = conversation.id
                      ORDER BY binding.binding_version DESC LIMIT 1
                    ) AS latest_binding ON TRUE
                    LEFT JOIN career_assistant.candidate_profiles AS candidate
                      ON candidate.id = latest_binding.candidate_profile_id
                    WHERE conversation.id = :conversation_id
                      AND conversation.organization_id = :organization_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("会话不存在或无访问权限")
        return (
            row["career_space_id"],
            row["candidate_profile_id"],
            row["candidate_profile_version"],
        )

    def record_turn_usages(
        self,
        organization_id: UUID,
        actor_id: UUID,
        turn_id: UUID,
        memory_ids: Sequence[UUID],
    ) -> int:
        with self._database.transaction() as connection:
            count = 0
            for memory_id in memory_ids:
                result = connection.execute(
                    text(
                        """
                        INSERT INTO career_assistant.turn_memory_usages
                          (id, organization_id, actor_id, turn_id, memory_id, memory_type, source_kind)
                        SELECT :id, :organization_id, :actor_id, turn.id,
                               memory.id, memory.memory_type, memory.source_kind
                        FROM career_assistant.agent_turns AS turn
                        INNER JOIN career_assistant.career_memory_items AS memory
                          ON memory.id = :memory_id
                        WHERE turn.id = :turn_id AND turn.actor_id = :actor_id
                          AND memory.organization_id = :organization_id
                          AND memory.actor_id = :actor_id
                        ON CONFLICT (turn_id, memory_id) DO NOTHING
                        """,
                    ),
                    {
                        "id": uuid4(),
                        "organization_id": organization_id,
                        "actor_id": actor_id,
                        "turn_id": turn_id,
                        "memory_id": memory_id,
                    },
                )
                count += result.rowcount
        return count

    def count_turn_usages(
        self,
        organization_id: UUID,
        actor_id: UUID,
        turn_ids: Sequence[UUID],
    ) -> dict[UUID, int]:
        normalized = tuple(dict.fromkeys(turn_ids))
        if not normalized:
            return {}
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT usage.turn_id, COUNT(*) AS usage_count
                    FROM career_assistant.turn_memory_usages AS usage
                    INNER JOIN career_assistant.agent_turns AS turn ON turn.id = usage.turn_id
                    WHERE usage.organization_id = :organization_id
                      AND usage.actor_id = :actor_id
                      AND turn.actor_id = :actor_id
                      AND usage.turn_id = ANY(:turn_ids)
                    GROUP BY usage.turn_id
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "turn_ids": list(normalized),
                },
            ).mappings().all()
        return {row["turn_id"]: int(row["usage_count"]) for row in rows}

    @staticmethod
    def _insert_memory(
        connection,
        organization_id: UUID,
        actor_id: UUID,
        draft: CareerMemoryDraft,
        status: CareerMemoryStatus,
        *,
        supersedes_memory_id: UUID | None = None,
    ) -> RowMapping:
        return connection.execute(
            text(
                """
                INSERT INTO career_assistant.career_memory_items (
                  id, organization_id, actor_id, career_space_id, memory_type,
                  normalized_value_json, display_text, source_kind, source_message_id,
                  source_conversation_id, candidate_profile_id, candidate_profile_version,
                  status, supersedes_memory_id
                ) VALUES (
                  :id, :organization_id, :actor_id, :career_space_id, :memory_type,
                  CAST(:normalized_value_json AS JSONB), :display_text, :source_kind, :source_message_id,
                  :source_conversation_id, :candidate_profile_id, :candidate_profile_version,
                  :status, :supersedes_memory_id
                ) RETURNING *
                """,
            ),
            {
                "id": uuid4(),
                "organization_id": organization_id,
                "actor_id": actor_id,
                "career_space_id": draft.career_space_id,
                "memory_type": draft.memory_type.value,
                "normalized_value_json": json.dumps(draft.normalized_value, ensure_ascii=False),
                "display_text": draft.display_text,
                "source_kind": draft.source_kind,
                "source_message_id": draft.source_message_id,
                "source_conversation_id": draft.source_conversation_id,
                "candidate_profile_id": draft.candidate_profile_id,
                "candidate_profile_version": draft.candidate_profile_version,
                "status": status.value,
                "supersedes_memory_id": supersedes_memory_id,
            },
        ).mappings().one()

    @staticmethod
    def _space_record(row: RowMapping) -> CareerSpaceRecord:
        return CareerSpaceRecord(**{key: row[key] for key in CareerSpaceRecord.__dataclass_fields__})

    @staticmethod
    def _memory_record(row: RowMapping) -> CareerMemoryItemRecord:
        normalized = row["normalized_value_json"]
        if isinstance(normalized, str):
            normalized = json.loads(normalized)
        return CareerMemoryItemRecord(
            id=row["id"], organization_id=row["organization_id"], actor_id=row["actor_id"],
            career_space_id=row["career_space_id"], memory_type=row["memory_type"],
            normalized_value=dict(normalized), display_text=row["display_text"],
            source_kind=row["source_kind"], source_message_id=row["source_message_id"],
            source_conversation_id=row["source_conversation_id"],
            candidate_profile_id=row["candidate_profile_id"],
            candidate_profile_version=row["candidate_profile_version"], status=row["status"],
            supersedes_memory_id=row["supersedes_memory_id"], valid_from=row["valid_from"],
            valid_to=row["valid_to"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _job_record(row: RowMapping) -> CareerMemoryJobRecord:
        return CareerMemoryJobRecord(
            id=row["id"], organization_id=row["organization_id"], actor_id=row["actor_id"],
            conversation_id=row["conversation_id"], turn_id=row["turn_id"],
            candidate_profile_id=row["candidate_profile_id"],
            candidate_profile_version=row["candidate_profile_version"],
            job_kind=row["job_kind"], requested_profile_id=row["requested_profile_id"],
            status=row["status"], attempt_count=row["attempt_count"], created_at=row["created_at"],
        )
