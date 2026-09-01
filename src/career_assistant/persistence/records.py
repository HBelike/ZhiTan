"""求职助手持久化层的不可变读取模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.career_assistant.contracts import ModelSelectionMode


class MessageRole(StrEnum):
    """允许写入对话历史的消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentTurnStatus(StrEnum):
    """Agent Turn 的可持久化生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ConversationRecord:
    """单个求职助手会话的持久化视图。"""

    id: UUID
    organization_id: UUID
    actor_id: UUID
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    career_space_id: UUID | None = None


@dataclass(frozen=True)
class CareerSpaceRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    name: str
    normalized_name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CareerMemoryItemRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    memory_type: str
    normalized_value: dict[str, object]
    display_text: str
    source_kind: str
    status: str
    valid_from: datetime
    created_at: datetime
    updated_at: datetime
    career_space_id: UUID | None = None
    source_message_id: UUID | None = None
    source_conversation_id: UUID | None = None
    candidate_profile_id: UUID | None = None
    candidate_profile_version: int | None = None
    supersedes_memory_id: UUID | None = None
    valid_to: datetime | None = None


@dataclass(frozen=True)
class MessageRecord:
    """已脱敏后才允许写入的对话消息。"""

    id: UUID
    conversation_id: UUID
    turn_id: UUID | None
    role: MessageRole
    content_text: str
    is_redacted: bool
    created_at: datetime


@dataclass(frozen=True)
class SessionSummaryRecord:
    """供后续 AgentLoop 压缩上下文使用的脱敏会话摘要。"""

    id: UUID
    conversation_id: UUID
    summary_text: str
    summary_version: int
    contains_sensitive_data: bool
    created_at: datetime
    updated_at: datetime
    covered_through_message_id: UUID | None = None
    summary_schema_version: str = "career-conversation-summary-v2"
    compacted_with_profile_id: UUID | None = None
    compacted_input_tokens: int | None = None
    compacted_output_tokens: int | None = None


@dataclass(frozen=True)
class AgentTurnRecord:
    """单次 Agent 执行的状态记录，不保存原始附件或模型密钥。"""

    id: UUID
    conversation_id: UUID
    actor_id: UUID
    requested_selection_mode: ModelSelectionMode
    requested_model_profile_id: UUID | None
    input_kind_codes: tuple[str, ...]
    status: AgentTurnStatus
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
