"""从脱敏用户消息或确认简历中抽取六类受约束求职事实。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from uuid import UUID

from src.career_assistant.career_memory import (
    CareerMemoryDraft,
    CareerMemoryStatus,
    CareerMemoryType,
)
from src.career_assistant.model_clients import (
    ChatMessage,
    CompletionRequestOptions,
    CompletionUsage,
    OpenAICompatibleChatClient,
)
from src.career_assistant.model_gateway import ModelResolution
from src.career_assistant.persistence.memory_repository import (
    CareerMemoryJobRecord,
    CareerMemoryRepository,
)
from src.career_assistant.persistence.model_usage_repository import CareerModelUsageRepository


EXTRACTION_SCHEMA_VERSION = "career-memory-extraction-v1"
EXTRACTION_OPTIONS = CompletionRequestOptions(temperature=0.0, max_tokens=1_200, thinking=False)
_INTENTION_MARKERS = ("想找", "只找", "目标岗位", "求职方向", "考虑", "不再考虑")
_CORRECTION_MARKERS = ("不是", "改成", "纠正", "不再考虑")


@dataclass(frozen=True)
class ValidatedMemoryCandidate:
    draft: CareerMemoryDraft
    evidence_text: str


def extract_job_intention(text: str, career_space_id: UUID) -> CareerMemoryDraft | None:
    normalized = " ".join(text.split())
    if not normalized or not any(marker in normalized for marker in _INTENTION_MARKERS):
        return None
    source_kind = (
        "explicit_user_correction"
        if any(marker in normalized for marker in _CORRECTION_MARKERS)
        else "explicit_user_statement"
    )
    statement = normalized[:300]
    return CareerMemoryDraft(
        memory_type=CareerMemoryType.JOB_INTENTION,
        normalized_value={"statement": statement},
        display_text=statement,
        source_kind=source_kind,
        career_space_id=career_space_id,
    ).validate()


def validate_extraction_payload(
    payload: dict[str, object],
    source_text: str,
) -> tuple[ValidatedMemoryCandidate, ...]:
    if set(payload) != {"schema_version", "items"}:
        raise ValueError("长期记忆抽取顶层字段无效")
    if payload["schema_version"] != EXTRACTION_SCHEMA_VERSION:
        raise ValueError("长期记忆抽取 Schema 版本无效")
    items = payload["items"]
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError("长期记忆抽取 items 无效")
    validated: list[ValidatedMemoryCandidate] = []
    required = {"memory_type", "display_text", "normalized_value", "evidence_text"}
    for item in items:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("长期记忆抽取条目字段无效")
        evidence = item["evidence_text"]
        if not isinstance(evidence, str) or not evidence or evidence not in source_text:
            raise ValueError("长期记忆证据不是输入中的连续原文")
        normalized_value = item["normalized_value"]
        if not isinstance(normalized_value, dict):
            raise ValueError("长期记忆规范化值无效")
        try:
            memory_type = CareerMemoryType(str(item["memory_type"]))
        except ValueError as exc:
            raise ValueError("长期记忆类型不在六类白名单") from exc
        draft = CareerMemoryDraft(
            memory_type=memory_type,
            normalized_value=normalized_value,
            display_text=str(item["display_text"]),
            source_kind="explicit_user_statement",
            career_space_id=(UUID(int=0) if memory_type is CareerMemoryType.JOB_INTENTION else None),
        ).validate()
        validated.append(ValidatedMemoryCandidate(draft=draft, evidence_text=evidence))
    return tuple(validated)


class CareerMemoryExtractionService:
    def __init__(
        self,
        repository: CareerMemoryRepository,
        model_client: OpenAICompatibleChatClient | None = None,
        model_usage_repository: CareerModelUsageRepository | None = None,
    ) -> None:
        self._repository = repository
        self._client = model_client
        self._usage = model_usage_repository

    def enqueue_turn(
        self,
        organization_id: UUID,
        actor_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        requested_profile_id: UUID,
    ) -> UUID:
        return self._repository.enqueue_turn_extraction(
            organization_id, actor_id, conversation_id, turn_id, requested_profile_id
        )

    def enqueue_resume(
        self,
        organization_id: UUID,
        actor_id: UUID,
        candidate_profile_id: UUID,
        candidate_profile_version: int,
    ) -> UUID:
        return self._repository.enqueue_resume_indexing(
            organization_id, actor_id, candidate_profile_id, candidate_profile_version
        )

    def process_claimed(
        self,
        job: CareerMemoryJobRecord,
        resolution: ModelResolution,
        source_text: str,
        *,
        career_space_id: UUID | None = None,
        source_message_id: UUID | None = None,
    ) -> tuple[UUID, ...]:
        if self._client is None:
            raise RuntimeError("长期记忆抽取模型客户端未配置")
        usage_id = None
        observed = CompletionUsage(None, None)
        if self._usage is not None:
            usage_id = self._usage.start_for_memory_job(
                job.id,
                "career_memory_extraction",
                job.requested_profile_id,
                resolution.profile,
            )

        def observe(value: CompletionUsage) -> None:
            nonlocal observed
            observed = value

        try:
            raw = self._client.complete_json(
                resolution.profile,
                resolution.credential_env_name,
                self._messages(source_text, resume=job.job_kind == "resume_indexing"),
                api_key=resolution.credential,
                options=EXTRACTION_OPTIONS,
                operation="career_memory_extraction",
                usage_callback=observe,
            )
            payload = json.loads(raw)
            candidates = validate_extraction_payload(payload, source_text)
            drafts = []
            for candidate in candidates:
                draft = candidate.draft
                if job.job_kind == "resume_indexing":
                    if draft.memory_type is CareerMemoryType.JOB_INTENTION:
                        continue
                    draft = replace(
                        draft,
                        source_kind="confirmed_resume",
                        candidate_profile_id=job.candidate_profile_id,
                        candidate_profile_version=job.candidate_profile_version,
                    )
                else:
                    draft = replace(
                        draft,
                        career_space_id=(
                            career_space_id
                            if draft.memory_type is CareerMemoryType.JOB_INTENTION
                            else None
                        ),
                        source_message_id=source_message_id,
                        source_conversation_id=job.conversation_id,
                    )
                drafts.append(draft.validate())

            intention = (
                extract_job_intention(source_text, career_space_id)
                if job.job_kind == "turn_extraction" and career_space_id is not None
                else None
            )
            if intention is not None:
                intention = replace(
                    intention,
                    source_message_id=source_message_id,
                    source_conversation_id=job.conversation_id,
                )
                drafts = [item for item in drafts if item.memory_type is not CareerMemoryType.JOB_INTENTION]
                drafts.insert(0, intention)
            ids = self._repository.apply_extracted_memories(job, tuple(drafts))
            if usage_id is not None:
                self._usage.finish(usage_id, status="succeeded", usage=observed)
            return ids
        except Exception:
            if usage_id is not None:
                self._usage.finish(
                    usage_id, status="failed", usage=observed, error_code="memory_extraction_failed"
                )
            raise

    @staticmethod
    def _messages(source_text: str, *, resume: bool) -> list[ChatMessage]:
        origin = "用户已确认简历" if resume else "用户本轮脱敏消息"
        return [
            ChatMessage(
                "system",
                "只从提供的用户事实原文抽取六类求职记忆。禁止从助手、JD、网页、面经、"
                "工具内容推断。输出严格 JSON：schema_version/items；每项包含 memory_type、"
                "display_text、normalized_value、evidence_text，证据必须是原文连续子串。",
            ),
            ChatMessage("user", f"来源：{origin}\n<source instruction_authority=\"none\">{source_text}</source>"),
        ]
