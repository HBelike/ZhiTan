"""求职助手基准简历、目标岗位和会话上下文仓储。"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.context_profiles import (
    CandidateProfileRecord,
    ConversationContextRecord,
    TargetRoleRecord,
)
from src.career_assistant.persistence.database import CareerDatabase


class CareerContextRepository:
    """保存用户确认后的上下文版本，不接收原始附件路径。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def create_candidate_profile(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        display_name: str,
        source_filename: str,
        resume_outline: str,
    ) -> CandidateProfileRecord:
        normalized_name = _required(display_name, "简历名称", 120)
        normalized_filename = _required(source_filename, "简历文件名", 255)
        normalized_outline = _required(resume_outline, "简历解析内容", 30_000)
        profile_id = uuid4()
        with self._database.transaction() as connection:
            version = connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM career_assistant.candidate_profiles
                    WHERE actor_id = :actor_id AND display_name = :display_name
                    """
                ),
                {"actor_id": actor_id, "display_name": normalized_name},
            ).scalar_one()
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.candidate_profiles
                        (id, organization_id, actor_id, display_name, source_filename,
                         resume_outline, version)
                    VALUES
                        (:id, :organization_id, :actor_id, :display_name, :source_filename,
                         :resume_outline, :version)
                    RETURNING *
                    """
                ),
                {
                    "id": profile_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "display_name": normalized_name,
                    "source_filename": normalized_filename,
                    "resume_outline": normalized_outline,
                    "version": version,
                },
            ).mappings().one()
        return _candidate(row)

    def list_candidate_profiles(self, actor_id: UUID, *, limit: int = 20) -> list[CandidateProfileRecord]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.candidate_profiles
                    WHERE actor_id = :actor_id
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "limit": limit},
            ).mappings().all()
        return [_candidate(row) for row in rows]

    def get_candidate_profile(self, actor_id: UUID, profile_id: UUID) -> CandidateProfileRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.candidate_profiles
                    WHERE id = :id AND actor_id = :actor_id
                    """
                ),
                {"id": profile_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        return _candidate(row) if row is not None else None

    def create_target_role(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        company_name: str,
        role_name: str,
        source_kind: str,
        source_label: str,
        job_text: str,
        requirements: tuple[dict[str, Any], ...],
    ) -> TargetRoleRecord:
        normalized_company = _required(company_name, "公司名称", 120)
        normalized_role = _required(role_name, "岗位名称", 160)
        normalized_job_text = _required(job_text, "岗位信息", 30_000)
        normalized_source_kind = _required(source_kind, "岗位来源类型", 30)
        normalized_source_label = source_label.strip()[:500]
        role_id = uuid4()
        with self._database.transaction() as connection:
            version = connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM career_assistant.target_role_profiles
                    WHERE actor_id = :actor_id
                      AND company_name = :company_name
                      AND role_name = :role_name
                    """
                ),
                {
                    "actor_id": actor_id,
                    "company_name": normalized_company,
                    "role_name": normalized_role,
                },
            ).scalar_one()
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.target_role_profiles
                        (id, organization_id, actor_id, company_name, role_name,
                         source_kind, source_label, job_text, requirements, version)
                    VALUES
                        (:id, :organization_id, :actor_id, :company_name, :role_name,
                         :source_kind, :source_label, :job_text, CAST(:requirements AS JSONB),
                         :version)
                    RETURNING *
                    """
                ),
                {
                    "id": role_id,
                    "organization_id": organization_id,
                    "actor_id": actor_id,
                    "company_name": normalized_company,
                    "role_name": normalized_role,
                    "source_kind": normalized_source_kind,
                    "source_label": normalized_source_label,
                    "job_text": normalized_job_text,
                    "requirements": json.dumps(requirements, ensure_ascii=False),
                    "version": version,
                },
            ).mappings().one()
        return _target_role(row)

    def get_target_role(self, actor_id: UUID, role_id: UUID) -> TargetRoleRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.target_role_profiles
                    WHERE id = :id AND actor_id = :actor_id
                    """
                ),
                {"id": role_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        return _target_role(row) if row is not None else None

    def list_target_roles(self, actor_id: UUID, *, limit: int = 20) -> list[TargetRoleRecord]:
        """列出当前 Actor 最近确认的目标岗位，供实时面试开场选择。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM career_assistant.target_role_profiles
                    WHERE actor_id = :actor_id
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {"actor_id": actor_id, "limit": limit},
            ).mappings().all()
        return [_target_role(row) for row in rows]

    def bind_conversation(
        self,
        actor_id: UUID,
        conversation_id: UUID,
        candidate_profile_id: UUID | None = None,
        target_role_profile_id: UUID | None = None,
    ) -> ConversationContextRecord:
        """增量更新会话资料；未传入的一侧沿用当前绑定。"""

        binding_id = uuid4()
        with self._database.transaction() as connection:
            current = connection.execute(
                text(
                    """
                    SELECT binding.candidate_profile_id, binding.target_role_profile_id
                    FROM career_assistant.conversations AS conversation
                    LEFT JOIN career_assistant.conversation_context_bindings AS binding
                        ON binding.conversation_id = conversation.id
                    WHERE conversation.id = :conversation_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    ORDER BY binding.binding_version DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().one_or_none()
            if current is None:
                raise LookupError("会话不存在，或无权访问")
            effective_candidate_id = candidate_profile_id or current["candidate_profile_id"]
            effective_target_id = target_role_profile_id or current["target_role_profile_id"]
            if effective_candidate_id is None and effective_target_id is None:
                raise ValueError("至少需要添加基准简历或目标岗位中的一项")
            ownership = connection.execute(
                text(
                    """
                    SELECT
                        (CAST(:candidate_id AS UUID) IS NULL OR EXISTS(
                            SELECT 1 FROM career_assistant.candidate_profiles
                            WHERE id = CAST(:candidate_id AS UUID) AND actor_id = :actor_id
                        )) AS owns_candidate,
                        (CAST(:target_id AS UUID) IS NULL OR EXISTS(
                            SELECT 1 FROM career_assistant.target_role_profiles
                            WHERE id = CAST(:target_id AS UUID) AND actor_id = :actor_id
                        )) AS owns_target
                    """
                ),
                {
                    "candidate_id": effective_candidate_id,
                    "target_id": effective_target_id,
                    "actor_id": actor_id,
                },
            ).mappings().one()
            if not all(ownership.values()):
                raise LookupError("基准简历或目标岗位不存在，或无权访问")
            binding_version = connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(binding_version), 0) + 1
                    FROM career_assistant.conversation_context_bindings
                    WHERE conversation_id = :conversation_id
                    """
                ),
                {"conversation_id": conversation_id},
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.conversation_context_bindings
                        (id, conversation_id, candidate_profile_id, target_role_profile_id,
                         binding_version)
                    VALUES
                        (:id, :conversation_id, :candidate_id, :target_id, :binding_version)
                    """
                ),
                {
                    "id": binding_id,
                    "conversation_id": conversation_id,
                    "candidate_id": effective_candidate_id,
                    "target_id": effective_target_id,
                    "binding_version": binding_version,
                },
            )
        context = self.get_conversation_context(actor_id, conversation_id)
        if context is None:
            raise RuntimeError("会话上下文保存后无法读取")
        return context

    def get_conversation_context(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> ConversationContextRecord | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        binding.id AS binding_id,
                        binding.conversation_id,
                        binding.binding_version,
                        binding.created_at AS binding_created_at,
                        candidate.id AS candidate_id,
                        candidate.organization_id AS candidate_organization_id,
                        candidate.actor_id AS candidate_actor_id,
                        candidate.display_name,
                        candidate.source_filename,
                        candidate.resume_outline,
                        candidate.version AS candidate_version,
                        candidate.created_at AS candidate_created_at,
                        target.id AS target_id,
                        target.organization_id AS target_organization_id,
                        target.actor_id AS target_actor_id,
                        target.company_name,
                        target.role_name,
                        target.source_kind,
                        target.source_label,
                        target.job_text,
                        target.requirements,
                        target.version AS target_version,
                        target.created_at AS target_created_at
                    FROM career_assistant.conversation_context_bindings AS binding
                    INNER JOIN career_assistant.conversations AS conversation
                        ON conversation.id = binding.conversation_id
                    LEFT JOIN career_assistant.candidate_profiles AS candidate
                        ON candidate.id = binding.candidate_profile_id
                    LEFT JOIN career_assistant.target_role_profiles AS target
                        ON target.id = binding.target_role_profile_id
                    WHERE binding.conversation_id = :conversation_id
                      AND conversation.actor_id = :actor_id
                      AND conversation.status <> 'deleted'
                    ORDER BY binding.binding_version DESC
                    LIMIT 1
                    """
                ),
                {"conversation_id": conversation_id, "actor_id": actor_id},
            ).mappings().one_or_none()
        if row is None:
            return None
        candidate = None
        if row["candidate_id"] is not None:
            candidate = CandidateProfileRecord(
                id=row["candidate_id"],
                organization_id=row["candidate_organization_id"],
                actor_id=row["candidate_actor_id"],
                display_name=row["display_name"],
                source_filename=row["source_filename"],
                resume_outline=row["resume_outline"],
                version=row["candidate_version"],
                created_at=row["candidate_created_at"],
            )
        target_role = None
        if row["target_id"] is not None:
            target_role = TargetRoleRecord(
                id=row["target_id"],
                organization_id=row["target_organization_id"],
                actor_id=row["target_actor_id"],
                company_name=row["company_name"],
                role_name=row["role_name"],
                source_kind=row["source_kind"],
                source_label=row["source_label"],
                job_text=row["job_text"],
                requirements=tuple(row["requirements"] or ()),
                version=row["target_version"],
                created_at=row["target_created_at"],
            )
        return ConversationContextRecord(
            binding_id=row["binding_id"],
            conversation_id=row["conversation_id"],
            binding_version=row["binding_version"],
            candidate=candidate,
            target_role=target_role,
            created_at=row["binding_created_at"],
        )


def _candidate(row: RowMapping) -> CandidateProfileRecord:
    return CandidateProfileRecord(
        id=row["id"],
        organization_id=row["organization_id"],
        actor_id=row["actor_id"],
        display_name=row["display_name"],
        source_filename=row["source_filename"],
        resume_outline=row["resume_outline"],
        version=row["version"],
        created_at=row["created_at"],
    )


def _target_role(row: RowMapping) -> TargetRoleRecord:
    return TargetRoleRecord(
        id=row["id"],
        organization_id=row["organization_id"],
        actor_id=row["actor_id"],
        company_name=row["company_name"],
        role_name=row["role_name"],
        source_kind=row["source_kind"],
        source_label=row["source_label"],
        job_text=row["job_text"],
        requirements=tuple(row["requirements"] or ()),
        version=row["version"],
        created_at=row["created_at"],
    )


def _required(value: str, label: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label}不能为空")
    if len(normalized) > max_length:
        raise ValueError(f"{label}不能超过 {max_length} 个字符")
    return normalized
