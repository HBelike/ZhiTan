"""评测模块跨层使用的稳定数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


class EvaluationTargetType(str, Enum):
    """当前首版支持的业务评测目标。"""

    INTERVIEW_RAG = "interview_rag"
    CAREER_AGENT = "career_agent"
    RESUME_OPTIMIZER = "resume_optimizer"


class EvaluationSourceKind(str, Enum):
    """评测案例的数据来源，用于把真实成绩与补充数据分开聚合。"""

    ANONYMIZED_REAL = "anonymized_real"
    FAILURE_REPLAY = "failure_replay"
    CURATED_EDGE = "curated_edge"
    SYNTHETIC = "synthetic"


class DatasetVersionStatus(str, Enum):
    DRAFT = "draft"
    FROZEN = "frozen"


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationRunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class MetricDefinition:
    """一个可复算指标的版本化定义。"""

    key: str
    version: str
    display_name: str
    target_type: str
    evaluator_kind: str
    direction: str
    unit: str
    aggregation: str
    formula: str
    threshold: float
    critical: bool
    description: str


@dataclass(frozen=True)
class MetricScore:
    """一次运行在单个指标上的原始得分和证据。"""

    metric_key: str
    metric_version: str
    raw_value: float
    passed: bool
    evaluator_kind: str = "deterministic"
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationCaseRecord:
    id: UUID
    dataset_version_id: UUID
    case_key: str
    target_type: EvaluationTargetType
    source_kind: EvaluationSourceKind
    source_ref_id: str | None
    input_data: Mapping[str, Any]
    expectation: Mapping[str, Any]
    metadata: Mapping[str, Any]
    content_hash: str
    created_at: datetime


@dataclass(frozen=True)
class EvaluationObservation:
    """一次业务执行暴露给评测器的事实；不要求业务模块理解具体指标。"""

    output_text: str = ""
    retrieved_items: tuple[Mapping[str, Any], ...] = ()
    agent_steps: tuple[Mapping[str, Any], ...] = ()
    structure_valid: bool | None = None
    artifacts: Mapping[str, Any] = field(default_factory=dict)
