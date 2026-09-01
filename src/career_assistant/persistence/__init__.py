"""求职助手专用的 PostgreSQL 持久化边界。

这里不复用现有的 SQLite DatabaseManager，避免把求职对话和公众号工作流耦合在一起。
"""

from src.career_assistant.persistence.conversation_repository import (
    CareerConversationRepository,
)
from src.career_assistant.persistence.context_repository import CareerContextRepository
from src.career_assistant.persistence.compaction_repository import (
    CareerCompactionRepository,
    CompactionJobRecord,
)
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.model_profile_repository import (
    CareerModelProfileRepository,
    ModelContextPolicy,
    ModelCostTier,
    ModelProfileDraft,
    ModelProfileRecord,
)
from src.career_assistant.persistence.model_usage_repository import (
    CareerModelUsageRepository,
)
from src.career_assistant.persistence.memory_repository import (
    CareerMemoryJobRecord,
    CareerMemoryRepository,
)
from src.career_assistant.persistence.job_assessment_repository import (
    CareerJobAssessmentRepository,
    JobAssessmentRecord,
    JobAssessmentStatus,
)
from src.career_assistant.persistence.records import (
    AgentTurnRecord,
    AgentTurnStatus,
    ConversationRecord,
    CareerMemoryItemRecord,
    CareerSpaceRecord,
    MessageRecord,
    MessageRole,
    SessionSummaryRecord,
)
from src.career_assistant.persistence.turn_job_repository import (
    CareerTurnJobRepository,
    ClaimedTurnRecord,
    ConversationTurnAdmissionError,
    ConversationTurnLimit,
    TurnEventRecord,
    TurnPayloadRecord,
    TurnQueueStatusRecord,
)

__all__ = [
    "CareerConversationRepository",
    "CareerContextRepository",
    "CareerCompactionRepository",
    "CompactionJobRecord",
    "CareerDatabase",
    "CareerModelProfileRepository",
    "ModelContextPolicy",
    "ModelCostTier",
    "ModelProfileDraft",
    "ModelProfileRecord",
    "CareerModelUsageRepository",
    "AgentTurnRecord",
    "AgentTurnStatus",
    "ConversationRecord",
    "CareerMemoryItemRecord",
    "CareerSpaceRecord",
    "CareerMemoryRepository",
    "CareerMemoryJobRecord",
    "MessageRecord",
    "MessageRole",
    "SessionSummaryRecord",
    "CareerTurnJobRepository",
    "ClaimedTurnRecord",
    "ConversationTurnAdmissionError",
    "ConversationTurnLimit",
    "TurnEventRecord",
    "TurnPayloadRecord",
    "TurnQueueStatusRecord",
]
