"""长期求职记忆的六类白名单、校验和用户纠正规则。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from src.career_assistant.persistence.memory_repository import CareerMemoryRepository
    from src.career_assistant.persistence.records import CareerMemoryItemRecord


class CareerMemoryType(StrEnum):
    JOB_INTENTION = "job_intention"
    WORK_EXPERIENCE = "work_experience"
    EDUCATION = "education"
    AWARD = "award"
    PUBLICATION = "publication"
    PERSONAL_ADVANTAGE = "personal_advantage"


class CareerMemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISABLED = "disabled"
    SUPERSEDED = "superseded"


MEMORY_SOURCE_KINDS = frozenset(
    {
        "explicit_user_statement",
        "explicit_user_correction",
        "confirmed_resume",
        "user_confirmed_candidate",
    },
)

_ALLOWED_VALUE_KEYS = {
    CareerMemoryType.JOB_INTENTION: frozenset({"statement", "role", "location", "industry"}),
    CareerMemoryType.WORK_EXPERIENCE: frozenset({"summary", "company", "role", "period"}),
    CareerMemoryType.EDUCATION: frozenset({"summary", "school", "degree", "major", "period"}),
    CareerMemoryType.AWARD: frozenset({"summary", "name", "issuer", "date"}),
    CareerMemoryType.PUBLICATION: frozenset({"summary", "title", "venue", "date"}),
    CareerMemoryType.PERSONAL_ADVANTAGE: frozenset({"summary", "skill", "evidence"}),
}


@dataclass(frozen=True)
class CareerMemoryDraft:
    memory_type: CareerMemoryType
    normalized_value: dict[str, object]
    display_text: str
    source_kind: str
    career_space_id: UUID | None = None
    source_message_id: UUID | None = None
    source_conversation_id: UUID | None = None
    candidate_profile_id: UUID | None = None
    candidate_profile_version: int | None = None

    def validate(self) -> "CareerMemoryDraft":
        display_text = " ".join(self.display_text.split())
        if not display_text or len(display_text) > 500:
            raise ValueError("求职记忆正文长度必须在 1 到 500 字符之间")
        if self.source_kind not in MEMORY_SOURCE_KINDS:
            raise ValueError("求职记忆来源不可信")
        if self.memory_type is CareerMemoryType.JOB_INTENTION and self.career_space_id is None:
            raise ValueError("岗位意向必须归属职业空间")
        unknown = set(self.normalized_value) - _ALLOWED_VALUE_KEYS[self.memory_type]
        if unknown:
            raise ValueError(f"规范化值包含未知字段：{', '.join(sorted(unknown))}")
        encoded = json.dumps(self.normalized_value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 2_000:
            raise ValueError("求职记忆规范化值过长")
        if self.candidate_profile_version is not None and self.candidate_profile_version < 1:
            raise ValueError("简历版本必须大于 0")
        return replace(self, display_text=display_text)


RESUME_MEMORY_TYPES = (
    CareerMemoryType.WORK_EXPERIENCE,
    CareerMemoryType.EDUCATION,
    CareerMemoryType.AWARD,
    CareerMemoryType.PUBLICATION,
    CareerMemoryType.PERSONAL_ADVANTAGE,
)


@dataclass(frozen=True)
class RetrievedCareerMemory:
    items: tuple[CareerMemoryItemRecord, ...]
    rendered_data: str
    estimated_tokens: int
    job_intention_tokens: int


def render_memory_data_envelope(items: Sequence[CareerMemoryItemRecord]) -> str:
    payload = json.dumps(
        [
            {"id": str(item.id), "type": item.memory_type, "fact": item.display_text}
            for item in items
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    escaped = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return f'<career_memory_data instruction_authority="none">{escaped}</career_memory_data>'


class CareerMemoryService:
    _RESUME_QUERY_MARKERS = (
        "经历", "学历", "奖项", "论文", "优势", "简历", "面试", "自我介绍", "项目", "技能"
    )

    def __init__(self, repository: "CareerMemoryRepository") -> None:
        self._repository = repository

    def scope_for_conversation(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> tuple[UUID, UUID | None, int | None]:
        return self._repository.get_conversation_memory_scope(
            organization_id, actor_id, conversation_id
        )

    def record_turn_usages(
        self,
        organization_id: UUID,
        actor_id: UUID,
        turn_id: UUID,
        memory_ids: Sequence[UUID],
    ) -> int:
        return self._repository.record_turn_usages(
            organization_id, actor_id, turn_id, memory_ids
        )

    def correct(
        self,
        organization_id: UUID,
        actor_id: UUID,
        memory_id: UUID,
        *,
        display_text: str,
        normalized_value: dict[str, object],
    ) -> "CareerMemoryItemRecord":
        old = self._repository.get_memory(organization_id, actor_id, memory_id)
        if old is None or old.status not in {
            CareerMemoryStatus.ACTIVE.value,
            CareerMemoryStatus.CANDIDATE.value,
        }:
            raise LookupError("求职记忆不存在或已经失效")
        replacement = CareerMemoryDraft(
            memory_type=CareerMemoryType(old.memory_type),
            normalized_value=normalized_value,
            display_text=display_text,
            source_kind="explicit_user_correction",
            career_space_id=old.career_space_id,
            source_message_id=old.source_message_id,
            source_conversation_id=old.source_conversation_id,
            candidate_profile_id=old.candidate_profile_id,
            candidate_profile_version=old.candidate_profile_version,
        )
        if old.status == CareerMemoryStatus.CANDIDATE.value:
            confirmed = self._repository.confirm_candidate(
                organization_id, actor_id, memory_id
            )
            if confirmed is None:
                raise LookupError("候选求职记忆不存在")
        return self._repository.supersede_active(
            organization_id, actor_id, memory_id, replacement
        )

    def retrieve_for_prompt(
        self,
        organization_id: UUID,
        actor_id: UUID,
        career_space_id: UUID,
        question: str,
        *,
        candidate_profile_id: UUID | None,
        candidate_profile_version: int | None,
        maximum_items: int = 5,
        maximum_tokens: int = 800,
    ) -> RetrievedCareerMemory:
        intentions = self._repository.list_active_for_prompt(
            organization_id,
            actor_id,
            career_space_id,
            memory_types=(CareerMemoryType.JOB_INTENTION,),
            candidate_profile_id=candidate_profile_id,
            candidate_profile_version=candidate_profile_version,
            query="",
            limit=2,
        )
        related: tuple[CareerMemoryItemRecord, ...] = ()
        if any(marker in question for marker in self._RESUME_QUERY_MARKERS):
            related = self._repository.list_active_for_prompt(
                organization_id,
                actor_id,
                career_space_id,
                memory_types=RESUME_MEMORY_TYPES,
                candidate_profile_id=candidate_profile_id,
                candidate_profile_version=candidate_profile_version,
                query=question,
                limit=maximum_items,
            )
        selected: list[CareerMemoryItemRecord] = []
        total_tokens = 0
        intention_tokens = 0
        for item in (*intentions, *related):
            if len(selected) >= maximum_items:
                break
            tokens = _estimate_text_tokens(item.display_text)
            if item.memory_type == CareerMemoryType.JOB_INTENTION.value:
                if intention_tokens + tokens > 300:
                    continue
                intention_tokens += tokens
            if total_tokens + tokens > maximum_tokens:
                continue
            selected.append(item)
            total_tokens += tokens
        items = tuple(selected)
        return RetrievedCareerMemory(
            items=items,
            rendered_data=render_memory_data_envelope(items),
            estimated_tokens=total_tokens,
            job_intention_tokens=intention_tokens,
        )


def _estimate_text_tokens(text: str) -> int:
    chinese = sum(1 for character in text if "\u4e00" <= character <= "\u9fff")
    other = len(text) - chinese
    return chinese + (other + 3) // 4
