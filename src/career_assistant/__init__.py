"""求职助手独立模块。

该包不依赖技能库、公众号任务或现有 SQLite 数据库。
后续会在这里逐步加入 Agent Runtime、PostgreSQL 持久化、模型网关和领域工具。
"""

from src.career_assistant.contracts import (
    AgentStepName,
    AttachmentDescriptor,
    AttachmentKind,
    CareerInboundMessage,
    InterviewEvidence,
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.agent_loop import ActiveAgentTurn, CareerAgentLoop
from src.career_assistant.intake_graph import CareerIntakeGraph, IntakeGraphResult
from src.career_assistant.model_gateway import (
    ModelGateway,
    ModelProfileAvailability,
    ModelReadiness,
    ModelResolution,
    ModelResolutionReason,
)
from src.career_assistant.persistence import (
    AgentTurnRecord,
    AgentTurnStatus,
    CareerConversationRepository,
    CareerDatabase,
    ConversationRecord,
    MessageRecord,
    MessageRole,
    SessionSummaryRecord,
)

__all__ = [
    "AgentStepName",
    "AttachmentDescriptor",
    "AttachmentKind",
    "CareerInboundMessage",
    "InterviewEvidence",
    "ModelCapability",
    "ModelSelectionMode",
    "ModelSelectionRequest",
    "ActiveAgentTurn",
    "CareerAgentLoop",
    "CareerIntakeGraph",
    "IntakeGraphResult",
    "ModelGateway",
    "ModelProfileAvailability",
    "ModelReadiness",
    "ModelResolution",
    "ModelResolutionReason",
    "CareerConversationRepository",
    "CareerDatabase",
    "AgentTurnRecord",
    "AgentTurnStatus",
    "ConversationRecord",
    "MessageRecord",
    "MessageRole",
    "SessionSummaryRecord",
]
