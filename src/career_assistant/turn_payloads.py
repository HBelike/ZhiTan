"""在 API 附件解析边界与 Agent Worker 之间转换完整 Turn 输入。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.career_assistant.attachments import ParsedAttachment
from src.career_assistant.contracts import (
    ActivatedSkill,
    AttachmentKind,
    CareerInboundMessage,
    InterviewEvidence,
    ModelCapability,
    ModelSelectionRequest,
    SkillExecutionMode,
)
from src.career_assistant.persistence.records import AgentTurnRecord
from src.career_assistant.persistence.turn_job_repository import TurnPayloadRecord


@dataclass(frozen=True)
class PreparedTurnInput:
    """Worker 可直接交给 Intake Graph 的完整输入与已解析附件。"""

    inbound_message: CareerInboundMessage
    parsed_attachments: tuple[ParsedAttachment, ...]


def prepare_turn_payload(
    inbound_message: CareerInboundMessage,
    parsed_attachments: tuple[ParsedAttachment, ...],
    *,
    source: str = "web",
) -> TurnPayloadRecord:
    """完整序列化文本、附件解析结果和本轮上下文，不执行脱敏。"""

    attachment_payloads = tuple(
        {
            "kind": item.kind.value,
            "media_type": item.media_type,
            "page_count": item.page_count,
            "image_width": item.image_width,
            "image_height": item.image_height,
            "extracted_text": item.extracted_text,
            "requires_vision_model": item.requires_vision_model,
            "document_understanding_error": item.document_understanding_error,
        }
        for item in parsed_attachments
    )
    request_metadata: dict[str, object] = {
        "source": source,
        "required_capabilities": sorted(
            capability.value
            for capability in inbound_message.model_selection.required_capabilities
        ),
        "interview_evidence": [
            {
                "experience_id": str(item.experience_id),
                "citation": item.citation,
                "content": item.content,
                "source_url": item.source_url,
            }
            for item in inbound_message.interview_evidence
        ],
        "activated_skills": [
            {
                "skill_id": item.skill_id,
                "name": item.name,
                "description": item.description,
                "instructions": item.instructions,
                "execution_mode": item.execution_mode.value,
                "tool_names": list(item.tool_names),
                "invocation_source": item.invocation_source,
                "arguments": item.arguments,
                "primary": item.primary,
            }
            for item in inbound_message.activated_skills
        ],
        "candidate_profile_context": inbound_message.candidate_profile_context,
        "target_role_context": inbound_message.target_role_context,
        "context_binding_version": inbound_message.context_binding_version,
    }
    return TurnPayloadRecord(
        turn_id=inbound_message.turn_id,
        input_text=inbound_message.text,
        effective_text=(
            inbound_message.effective_text
            if inbound_message.effective_text is not None
            else inbound_message.text
        ),
        job_url=inbound_message.job_url,
        attachment_payloads=attachment_payloads,
        request_metadata=request_metadata,
    )


def restore_turn_input(
    turn: AgentTurnRecord,
    payload: TurnPayloadRecord,
) -> PreparedTurnInput:
    """从 PostgreSQL payload 恢复 Worker 输入，不依赖原始附件文件。"""

    if turn.id != payload.turn_id:
        raise ValueError("Turn 与 payload ID 不一致")
    parsed_attachments = tuple(
        _restore_attachment(item)
        for item in payload.attachment_payloads
    )
    metadata = payload.request_metadata
    required_capabilities = frozenset(
        ModelCapability(value)
        for value in _list_value(metadata.get("required_capabilities", ["text"]))
    )
    interview_evidence = tuple(
        InterviewEvidence(
            experience_id=UUID(str(item["experience_id"])),
            citation=str(item["citation"]),
            content=str(item["content"]),
            source_url=(
                str(item["source_url"])
                if item.get("source_url") is not None
                else None
            ),
        )
        for item in _dict_items(metadata.get("interview_evidence", []))
    )
    activated_skills = tuple(
        ActivatedSkill(
            skill_id=str(item["skill_id"]),
            name=str(item["name"]),
            description=str(item.get("description", "")),
            instructions=str(item["instructions"]),
            execution_mode=SkillExecutionMode(str(item.get("execution_mode", "prompt"))),
            tool_names=tuple(str(value) for value in _list_value(item.get("tool_names", []))),
            invocation_source=str(item.get("invocation_source", "mention")),
            arguments=str(item.get("arguments", "")),
            primary=bool(item.get("primary", False)),
        )
        for item in _dict_items(metadata.get("activated_skills", []))
    )
    inbound_message = CareerInboundMessage(
        turn_id=turn.id,
        conversation_id=turn.conversation_id,
        actor_id=turn.actor_id,
        text=payload.input_text,
        effective_text=payload.effective_text,
        job_url=payload.job_url,
        model_selection=ModelSelectionRequest(
            mode=turn.requested_selection_mode,
            profile_id=turn.requested_model_profile_id,
            required_capabilities=required_capabilities,
        ),
        prepared_attachment_kinds=tuple(item.kind for item in parsed_attachments),
        interview_evidence=interview_evidence,
        activated_skills=activated_skills,
        candidate_profile_context=str(metadata.get("candidate_profile_context", "")),
        target_role_context=str(metadata.get("target_role_context", "")),
        context_binding_version=_optional_positive_int(
            metadata.get("context_binding_version"),
        ),
    )
    inbound_message.validate()
    return PreparedTurnInput(
        inbound_message=inbound_message,
        parsed_attachments=parsed_attachments,
    )


def collect_input_kind_codes(
    inbound_message: CareerInboundMessage,
    parsed_attachments: tuple[ParsedAttachment, ...],
) -> frozenset[str]:
    """生成不包含正文的稳定输入类型标签。"""

    kinds: set[str] = set()
    if inbound_message.text.strip():
        kinds.add("text")
    if (inbound_message.job_url or "").strip():
        kinds.add("job_url")
    kinds.update(item.kind.value for item in parsed_attachments)
    return frozenset(kinds)


def _restore_attachment(payload: dict[str, object]) -> ParsedAttachment:
    return ParsedAttachment(
        kind=AttachmentKind(str(payload["kind"])),
        media_type=str(payload["media_type"]),
        page_count=_optional_int(payload.get("page_count")),
        image_width=_optional_int(payload.get("image_width")),
        image_height=_optional_int(payload.get("image_height")),
        extracted_text=str(payload.get("extracted_text", "")),
        requires_vision_model=bool(payload.get("requires_vision_model", False)),
        image_bytes=None,
        document_understanding_error=(
            str(payload["document_understanding_error"])
            if payload.get("document_understanding_error") is not None
            else None
        ),
    )


def _dict_items(value: object) -> tuple[dict[str, object], ...]:
    return tuple(item for item in _list_value(value) if isinstance(item, dict))


def _list_value(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Turn request metadata 列表类型无效")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("附件整数元数据无效")
    return value


def _optional_positive_int(value: object) -> int | None:
    result = _optional_int(value)
    if result is not None and result <= 0:
        raise ValueError("上下文绑定版本必须为正整数")
    return result
