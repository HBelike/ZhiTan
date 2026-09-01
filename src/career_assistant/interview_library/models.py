"""面经库的稳定领域读取模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Mapping
from uuid import UUID


class InterviewSourceType(StrEnum):
    """面经正文的受控来源类型。"""

    MANUAL_UPLOAD = "manual_upload"
    MANUAL_TEXT = "manual_text"
    PUBLIC_URL = "public_url"
    AUTHENTICATED_SESSION = "authenticated_session"
    OFFICIAL_API = "official_api"
    ONLINE_ASSESSMENT = "online_assessment"


class InterviewExperienceStatus(StrEnum):
    """面经从解析到索引的技术状态；不包含人工审核状态。"""

    QUEUED = "queued"
    PARSING = "parsing"
    PARSED = "parsed"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class IngestionTriggerType(StrEnum):
    """触发一次入库任务的入口。"""

    MANUAL_UPLOAD = "manual_upload"
    MANUAL_URL = "manual_url"
    SCHEDULED_SCAN = "scheduled_scan"
    API_SYNC = "api_sync"


class IngestionJobStatus(StrEnum):
    """可观测的采集/解析任务生命周期。"""

    QUEUED = "queued"
    FETCHING = "fetching"
    PARSING = "parsing"
    NORMALIZING = "normalizing"
    INDEXING = "indexing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CollectionConnectorKind(StrEnum):
    """面经资料发现阶段允许使用的合规连接器类型。

    该枚举刻意不包含“账号密码直连”或“Cookie 持久化”。第三方站点的登录态必须
    由用户在其原生页面完成授权，并仅以外部受控会话引用的方式接入。
    """

    PUBLIC_API = "public_api"
    USER_AUTHORIZED_BROWSER = "user_authorized_browser"
    URL_IMPORT = "url_import"
    MANUAL_TEXT = "manual_text"


class CollectionJobStatus(StrEnum):
    """关键词发现任务的生命周期，独立于最终的 Markdown 入库任务。"""

    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_USER_INTERACTION = "needs_user_interaction"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CollectionCandidateStatus(StrEnum):
    """候选网页从被发现到被选中入库前的状态。"""

    DISCOVERED = "discovered"
    FETCHED = "fetched"
    EMPTY = "empty"
    BLOCKED = "blocked"
    SELECTED = "selected"
    IMPORTED = "imported"
    FAILED = "failed"


class InterviewWebDocumentStatus(StrEnum):
    """公开网页从发现到去重、入库或失败的永久状态。"""

    DISCOVERED = "discovered"
    FETCHING = "fetching"
    FETCHED = "fetched"
    DUPLICATE = "duplicate"
    IMPORTED = "imported"
    FILTERED = "filtered"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENT_FAILED = "permanent_failed"


@dataclass(frozen=True)
class InterviewCompanyRecord:
    """面经树的公司根节点。"""

    id: UUID
    organization_id: UUID
    display_name: str
    normalized_name: str
    aliases: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class InterviewExperienceRecord:
    """可编辑、可追溯的一份面经 Markdown。"""

    id: UUID
    organization_id: UUID
    company_id: UUID
    company_name: str
    job_name: str
    role_name: str
    normalized_role_name: str
    interview_date: date | None
    source_type: InterviewSourceType
    source_platform: str | None
    source_url: str | None
    summary_text: str | None
    markdown_content: str
    normalized_markdown: str
    tags: tuple[str, ...]
    status: InterviewExperienceStatus
    chunking_version: str | None
    indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by_actor_id: UUID | None = None


@dataclass(frozen=True)
class InterviewChunkRecord:
    """用于检索的父标题增强切片；向量值不读取回 Web 层。"""

    id: UUID
    experience_id: UUID
    parent_heading: str | None
    heading_path: str
    chunk_index: int
    content_text: str
    contextual_content: str
    token_estimate: int
    chunk_hash: str
    chunking_version: str
    embedding_model: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class InterviewChunkCandidate:
    """一次检索中可直接引用的面经切片及其来源信息。

    该对象只携带归一化后的文本和可展示的来源元数据，不携带原始附件、Cookie
    或模型凭证。它是检索层与未来求职助手 ``@面经`` 上下文拼装之间的稳定契约。
    """

    chunk: InterviewChunkRecord
    company_name: str
    job_name: str
    role_name: str
    interview_date: date | None
    source_url: str | None
    lexical_score: float | None = None
    semantic_score: float | None = None


@dataclass(frozen=True)
class InterviewIngestionJobRecord:
    """一条采集或导入任务的无原件可观测记录。"""

    id: UUID
    organization_id: UUID
    experience_id: UUID | None
    trigger_type: IngestionTriggerType
    source_url: str | None
    source_platform: str | None
    status: IngestionJobStatus
    attempt_count: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class InterviewCollectionJobRecord:
    """一次平台关键词检索或 URL 导入的可追踪任务。

    ``policy_decision`` 仅保存连接器是否被条款、授权和反爬边界允许执行的判断，
    不保存账号、密码、Cookie 或网页原始 HTML。
    """

    id: UUID
    organization_id: UUID
    platform_key: str
    keyword: str
    requested_limit: int
    connector_kind: CollectionConnectorKind
    status: CollectionJobStatus
    policy_decision: str
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # 采集批次的轻量可观测信息，例如入口 URL、页码和发现数量。
    # 仅保存 JSON 可序列化的派生信息，绝不保存 Cookie、原始 HTML 或附件。
    metadata_json: Mapping[str, object] = field(default_factory=dict)
    created_by_actor_id: UUID | None = None


@dataclass(frozen=True)
class InterviewCollectionCandidateRecord:
    """待人工选择的公开资料候选项；只保留解析后的 Markdown，不保留原始页面。"""

    id: UUID
    collection_job_id: UUID
    source_url: str
    canonical_url: str
    source_platform: str
    title: str | None
    snippet: str | None
    published_at: datetime | None
    extracted_markdown: str | None
    content_hash: str | None
    status: CollectionCandidateStatus
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    # 单条候选资料的派生信息，例如图文页数量、OCR 结果摘要和提取置信度。
    # 原始图片文件由临时存储负责清理，不能写入此字段。
    metadata_json: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class InterviewWebDocumentRecord:
    """跨批次复用的公开网页正文和抓取状态，不包含原始 HTML 或图片。"""

    id: UUID
    organization_id: UUID
    canonical_url: str
    source_url: str
    source_platform: str
    title: str | None
    search_snippet: str | None
    status: InterviewWebDocumentStatus
    normalized_markdown: str | None
    content_hash: str | None
    imported_experience_id: UUID | None
    attempt_count: int
    error_code: str | None
    error_message: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_fetched_at: datetime | None
    next_retry_at: datetime | None
    created_at: datetime
    updated_at: datetime
    metadata_json: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class InterviewExperienceSourceRecord:
    """一份面经的主来源或同正文来源别名。"""

    id: UUID
    organization_id: UUID
    experience_id: UUID
    canonical_url: str
    source_url: str
    source_platform: str
    is_primary: bool
    discovery_keywords: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
