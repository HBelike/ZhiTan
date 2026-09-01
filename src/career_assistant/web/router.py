"""求职助手的独立 FastAPI Router。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextvars import ContextVar, Token
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from src.career_assistant.agent_loop import CareerAgentLoop
from src.career_assistant.attachments import (
    AttachmentParser,
    AttachmentSettings,
    TemporaryAttachmentStore,
)
from src.career_assistant.contracts import (
    AgentStepName,
    AttachmentKind,
    CareerInboundMessage,
    InterviewEvidence,
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.context_profiles import (
    extract_job_requirements,
)
from src.career_assistant.context_budget import ContextBudgetService
from src.career_assistant.conversation_memory import ConversationMemoryService
from src.career_assistant.document_parsing import DoclingServiceDocumentParser
from src.career_assistant.cloud_vision import CloudVisionRouter
from src.career_assistant.free_model_catalog import build_free_model_catalog_payload
from src.career_assistant.greetings import (
    CareerGreetingService,
    GreetingCandidateNotFoundError,
    GreetingGenerationError,
    GreetingGenerationResult,
    GreetingJobInput,
    GreetingJobValidationError,
    GreetingModelUnavailableError,
)
from src.career_assistant.intake_graph import CareerIntakeGraph
from src.career_assistant.interview_library.models import (
    IngestionTriggerType,
    InterviewSourceType,
)
from src.career_assistant.interview_library.collection import (
    CollectionOperationError,
    InterviewCollectionService,
    XiaohongshuBrowserDiscovery,
    XiaohongshuBrowserNote,
)
from src.career_assistant.interview_library.embedding import (
    OpenAICompatibleEmbeddingClient,
)
from src.career_assistant.interview_library.metadata import (
    InterviewMaterialMetadataExtractor,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.public_web import (
    FirecrawlClient,
    load_firecrawl_settings,
)
from src.career_assistant.interview_library.retrieval import (
    InterviewRetrievalService,
)
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)
from src.career_assistant.job_sources import JobPostingExtractor
from src.career_assistant.job_assessment import CareerJobAssessmentService
from src.career_assistant.legacy_office import GotenbergOfficeConverter
from src.career_assistant.model_gateway import (
    ModelGateway,
    ModelProfileAvailability,
    ModelResolution,
)
from src.career_assistant.model_clients import (
    ModelConnectionTarget,
    ModelInvocationError,
    OpenAICompatibleChatClient,
)
from src.career_assistant.privacy import SensitiveDataRedactor
from src.career_assistant.response_runner import CareerResponseRunner
from src.career_assistant.prompt_context import PromptContextService
from src.career_assistant.resume_normalizer import ResumeNormalizer
from src.career_assistant.resume_assistant import (
    ResumeOptimizationRepository,
    ResumeOptimizationService,
)
from src.career_assistant.resume_assistant.models import ResumeSuggestion
from src.career_assistant.skill_runtime import CareerSkillRuntime
from src.career_assistant.skill_tools import SkillToolRegistry
from src.career_assistant.turn_queue import AsyncTurnCoordinator
from src.config.config_manager import ConfigManager
from src.observability.langsmith_runtime import trace_operation, trace_stream
from src.platform_access.contracts import PlatformRole, PlatformUser
from src.platform_access.web import get_current_platform_user
from src.services.skill_library_service import SkillLibraryService
from src.career_assistant.persistence import (
    CareerConversationRepository,
    CareerCompactionRepository,
    CareerContextRepository,
    CareerDatabase,
    CareerJobAssessmentRepository,
    CareerModelProfileRepository,
    CareerModelUsageRepository,
    CareerTurnJobRepository,
    MessageRole,
    ModelCostTier,
    ModelProfileDraft,
    ModelProfileRecord,
)
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
)
from src.career_assistant.persistence.credential_cipher import (
    CredentialCipherError,
    ensure_credential_master_key,
)
from src.career_assistant.settings import (
    load_attachment_processing_settings,
    load_career_runtime_settings,
    load_cloud_vision_settings,
    load_document_understanding_settings,
    load_legacy_office_conversion_settings,
    load_model_gateway_settings,
    load_interview_retrieval_settings,
    load_job_assessment_settings,
    load_response_generation_settings,
    load_career_memory_worker_settings,
)


router = APIRouter(prefix="/api/career", tags=["career-assistant"])
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CareerRequestActor:
    """当前请求在 Career 数据模型中的组织与操作者边界。"""

    organization_id: UUID
    actor_id: UUID
    role: PlatformRole = PlatformRole.USER


# API 的认证中间件会在每个已登录请求开始时写入真实平台用户。ContextVar 可以让既有
# 同步路由和流式处理链继续调用 get_request_actor()，无需把身份参数层层穿透到 Task。
# 开发环境未开启 PLATFORM_AUTH_REQUIRED 时保留默认 Actor，避免破坏本地历史验证脚本。
_request_actor_context: ContextVar[CareerRequestActor | None] = ContextVar(
    "career_request_actor",
    default=None,
)


@dataclass(frozen=True)
class CareerAssistantReadServices:
    """首屏只读接口共享的轻量服务，不创建任何外部 HTTP Client。"""

    database: CareerDatabase
    conversation_repository: CareerConversationRepository
    context_repository: CareerContextRepository
    job_assessment_repository: CareerJobAssessmentRepository
    model_profile_repository: CareerModelProfileRepository
    model_gateway: ModelGateway
    model_usage_repository: CareerModelUsageRepository


@dataclass
class CareerAssistantServices:
    """一个 FastAPI 进程内共享的求职助手服务容器。"""

    database: CareerDatabase
    conversation_repository: CareerConversationRepository
    turn_job_repository: CareerTurnJobRepository
    context_repository: CareerContextRepository
    job_assessment_repository: CareerJobAssessmentRepository
    job_assessment_service: CareerJobAssessmentService
    greeting_service: CareerGreetingService
    model_profile_repository: CareerModelProfileRepository
    interview_library_repository: InterviewLibraryRepository
    interview_library_service: InterviewLibraryService
    interview_retrieval_service: InterviewRetrievalService
    interview_collection_service: InterviewCollectionService
    skill_runtime: CareerSkillRuntime
    agent_loop: CareerAgentLoop
    intake_graph: CareerIntakeGraph
    model_gateway: ModelGateway
    response_runner: CareerResponseRunner
    compaction_repository: CareerCompactionRepository
    model_usage_repository: CareerModelUsageRepository
    conversation_memory_service: ConversationMemoryService
    prompt_context_service: PromptContextService
    temporary_attachment_store: TemporaryAttachmentStore
    attachment_parser: AttachmentParser
    redactor: SensitiveDataRedactor
    resume_optimization_repository: ResumeOptimizationRepository
    resume_optimization_service: ResumeOptimizationService
    model_connection_client: OpenAICompatibleChatClient
    document_understanding_client: DoclingServiceDocumentParser | None
    legacy_office_converter: GotenbergOfficeConverter | None
    cloud_vision_client: CloudVisionRouter | None
    turn_coordinator: AsyncTurnCoordinator
    stream_heartbeat_seconds: float
    turn_expected_seconds: float

    def close(self) -> None:
        """在应用关闭时释放 HTTP 与 PostgreSQL 连接池。"""

        self.interview_collection_service.close()
        self.model_connection_client.close()
        self.interview_retrieval_service.close()
        if self.document_understanding_client is not None:
            self.document_understanding_client.close()
        if self.legacy_office_converter is not None:
            self.legacy_office_converter.close()
        if self.cloud_vision_client is not None:
            self.cloud_vision_client.close()
        self.database.close()


class CreateConversationRequest(BaseModel):
    """新建求职会话请求。"""

    title: str = Field(min_length=1, max_length=160)
    candidate_profile_id: UUID | None = None
    target_role_profile_id: UUID | None = None


class CreateCandidateProfileRequest(BaseModel):
    """确认一份已解析的基准简历。"""

    display_name: str = Field(min_length=1, max_length=120)
    source_filename: str = Field(min_length=1, max_length=255)
    resume_outline: str = Field(min_length=1, max_length=30_000)


class CreateTargetRoleRequest(BaseModel):
    """确认一份目标岗位信息。"""

    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    source_kind: str = Field(default="text", min_length=1, max_length=30)
    source_label: str = Field(default="", max_length=500)
    job_text: str = Field(min_length=1, max_length=30_000)


class UpdateConversationContextRequest(BaseModel):
    """为既有会话增量添加简历或岗位，并创建新的绑定版本。"""

    candidate_profile_id: UUID | None = None
    target_role_profile_id: UUID | None = None


class RenameConversationRequest(BaseModel):
    """重命名求职会话请求。"""

    title: str = Field(min_length=1, max_length=160)


class SubmitIntakeRequest(BaseModel):
    """文本和职位链接输入；文件上传将在附件解析步骤单独实现。"""

    text: str = Field(default="", max_length=30_000)
    job_url: str | None = Field(default=None, max_length=2_000)
    selection_mode: ModelSelectionMode = ModelSelectionMode.FREE_QUOTA_FIRST
    model_profile_id: UUID | None = None
    interview_experience_ids: list[UUID] = Field(default_factory=list, max_length=5)
    selected_skill_ids: list[str] = Field(default_factory=list, max_length=3)


class UpsertModelProfileRequest(BaseModel):
    """模型设置页保存的无密钥模型档案。"""

    display_name: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(min_length=1, max_length=50)
    model_id: str = Field(min_length=1, max_length=200)
    capabilities: set[ModelCapability] = Field(min_length=1)
    cost_tier: ModelCostTier
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    api_base_url: str | None = Field(default=None, max_length=500)
    provider_website_url: str | None = Field(default=None, max_length=500)


class ModelConnectionRequest(UpsertModelProfileRequest):
    """页面配置的完整模型连接；Key 仅用于本次测试与加密入库。"""

    api_key: str = Field(min_length=1, max_length=2_000)


class ResolveModelRequest(BaseModel):
    """聊天页请求预览或确认本轮模型路由。"""

    selection_mode: ModelSelectionMode = ModelSelectionMode.FREE_QUOTA_FIRST
    model_profile_id: UUID | None = None
    required_capabilities: set[ModelCapability] = Field(
        default_factory=lambda: {ModelCapability.TEXT},
        min_length=1,
    )


class GreetingJobRequest(BaseModel):
    """来自浏览器职位库的一份完整岗位快照。"""

    id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=240)
    company: str = Field(default="", max_length=240)
    recruiter: str = Field(default="", max_length=160)
    description: str = Field(min_length=1, max_length=50_000)
    skills: list[str] = Field(default_factory=list, max_length=50)
    source_url: str = Field(default="", max_length=2_000)

    def to_domain(self) -> GreetingJobInput:
        """转换成与 Web 层无关的不可变岗位输入。"""

        return GreetingJobInput(
            id=self.id,
            title=self.title,
            company=self.company,
            recruiter=self.recruiter,
            description=self.description,
            skills=tuple(self.skills),
            source_url=self.source_url,
        )


class GenerateGreetingRequest(BaseModel):
    """为单个岗位生成或重新生成招呼语。"""

    candidate_profile_id: UUID
    job: GreetingJobRequest
    previous_message: str = Field(default="", max_length=2_000)


class CreateInterviewExperienceRequest(BaseModel):
    """手工整理完成的面经 Markdown 入库请求。

    附件和网页的原始内容不会从该接口写入数据库；它们会先经解析器转换成
    Markdown，再由此接口持久化文本、来源与索引切片。
    """

    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    interview_date: date | None = None
    markdown_content: str = Field(min_length=1, max_length=300_000)
    source_type: InterviewSourceType = InterviewSourceType.MANUAL_TEXT
    source_platform: str | None = Field(default=None, max_length=80)
    source_url: str | None = Field(default=None, max_length=2_000)
    summary_text: str | None = Field(default=None, max_length=12_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    job_name: str | None = Field(default=None, max_length=220)


class UpdateInterviewExperienceRequest(BaseModel):
    """编辑面经归属与正文后触发切片重建的请求。"""

    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    markdown_content: str = Field(min_length=1, max_length=300_000)
    summary_text: str | None = Field(default=None, max_length=12_000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class CreateInterviewCollectionJobRequest(BaseModel):
    """创建一个平台关键词资料发现任务。"""

    platform_key: str = Field(min_length=1, max_length=50)
    keyword: str = Field(min_length=1, max_length=180)
    requested_limit: int = Field(default=10, ge=1, le=50)


class CollectInterviewUrlRequest(BaseModel):
    """读取用户明确提交的单个公开页面。"""

    source_url: str = Field(min_length=8, max_length=2_000)


class CreateXiaohongshuImportRequest(BaseModel):
    """创建一个由用户主动提交链接触发的小红书公开面经导入任务。"""

    source_url: str = Field(min_length=8, max_length=2_000)
    requested_limit: int = Field(default=20, ge=1, le=50)
    include_images: bool = True
    # 小红书候选必须先经过内容分析和人工确认，默认不自动写入面经库。
    auto_import: bool = False


class CreateXiaohongshuBrowserImportRequest(BaseModel):
    """创建一个由当前登录 Chrome 执行的关键词信息收集任务。"""

    model_config = ConfigDict(extra="forbid")
    keyword: str = Field(min_length=1, max_length=180)
    requested_limit: int = Field(default=20, ge=5, le=50)


class CreatePublicWebImportRequest(BaseModel):
    """创建一个只检索公开网页的关键词信息收集任务。"""

    model_config = ConfigDict(extra="forbid")
    keyword: str = Field(min_length=1, max_length=120)
    requested_limit: int = Field(default=10, ge=5, le=10)


class XiaohongshuBrowserDiscoveryRequest(BaseModel):
    """一张已经移除临时访问参数的搜索卡片。"""

    model_config = ConfigDict(extra="forbid")
    note_id: str = Field(min_length=24, max_length=24)
    title: str | None = Field(default=None, max_length=300)
    author_name: str | None = Field(default=None, max_length=120)
    liked_count: str | None = Field(default=None, max_length=40)


class RegisterXiaohongshuBrowserDiscoveriesRequest(BaseModel):
    """批量登记浏览器已读取的无会话卡片。"""

    model_config = ConfigDict(extra="forbid")
    items: list[XiaohongshuBrowserDiscoveryRequest] = Field(min_length=1, max_length=50)


class ProcessXiaohongshuBrowserNoteRequest(BaseModel):
    """提交一篇浏览器已展开的笔记；不接受签名详情 URL。"""

    model_config = ConfigDict(extra="forbid")
    note_id: str = Field(min_length=24, max_length=24)
    title: str | None = Field(default=None, max_length=300)
    body_text: str = Field(default="", max_length=100_000)
    author_name: str | None = Field(default=None, max_length=120)
    published_at: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=30)
    image_urls: list[str] = Field(default_factory=list, max_length=20)
    source_error_code: str | None = Field(default=None, max_length=80)
    source_error_message: str | None = Field(default=None, max_length=1000)

    def to_domain(self) -> XiaohongshuBrowserNote:
        return XiaohongshuBrowserNote(
            note_id=self.note_id,
            title=self.title,
            body_text=self.body_text,
            image_urls=tuple(self.image_urls),
            published_at=self.published_at,
            author_name=self.author_name,
            tags=tuple(self.tags),
            source_error_code=self.source_error_code,
            source_error_message=self.source_error_message,
        )


class PauseXiaohongshuBrowserJobRequest(BaseModel):
    """暂停需要浏览器人工处理的批次，或响应用户主动停止。"""

    model_config = ConfigDict(extra="forbid")
    error_code: str = Field(default="browser_interaction_required", min_length=1, max_length=80)
    error_message: str = Field(default="请检查小红书页面后继续。", min_length=1, max_length=1000)
    cancelled: bool = False


class ImportInterviewCollectionCandidateRequest(BaseModel):
    """将已选择的候选正文写入面经库，并触发既有 RAG 建索引。"""

    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    interview_date: date | None = None
    summary_text: str | None = Field(default=None, max_length=12_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    markdown_content: str | None = Field(default=None, min_length=1, max_length=300_000)


class ResumeSuggestionRequest(BaseModel):
    """前端勾选后回传的一条简历修改意见。"""

    id: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(default="", max_length=4_000)
    original_evidence: str = Field(default="", max_length=4_000)
    job_evidence: str = Field(default="", max_length=4_000)
    proposed_change: str = Field(min_length=1, max_length=8_000)
    priority: str = Field(default="medium", max_length=20)

    def to_domain(self) -> ResumeSuggestion:
        """转换为服务层不可变契约。"""

        return ResumeSuggestion(**self.model_dump())


class GenerateOptimizedResumeRequest(BaseModel):
    """根据用户确认的意见生成优化简历。"""

    original_markdown: str = Field(min_length=1, max_length=300_000)
    job_description_text: str = Field(min_length=1, max_length=100_000)
    suggestions: list[ResumeSuggestionRequest] = Field(min_length=1, max_length=80)
    extra_prompt: str = Field(default="", max_length=20_000)
    model_profile_id: UUID


def install_career_assistant_api(
    app: FastAPI,
    project_root: Path,
    *,
    skill_library_service: SkillLibraryService | None = None,
) -> None:
    """将独立 Router 安装到既有 FastAPI 应用，不初始化旧模块以外的资源。

    PostgreSQL 连接采用懒加载：预览服务即使尚未配置 ``CAREER_DATABASE_URL`` 也能正常
    启动；只有访问求职助手接口时才会返回明确的配置错误。
    """

    if skill_library_service is None:
        skill_library_service = SkillLibraryService(
            ConfigManager(project_root=project_root).load(),
        )

    app.include_router(router)
    from src.career_assistant.web.turn_router import router as turn_router

    app.include_router(turn_router)
    from src.career_assistant.web.admin_context_router import router as admin_context_router

    app.include_router(admin_context_router)
    from src.career_assistant.live_interview.web import router as live_interview_router

    app.include_router(live_interview_router)
    from src.career_assistant.online_assessment.web import router as online_assessment_router

    app.include_router(online_assessment_router)
    app.state.career_assistant_project_root = project_root
    app.state.career_skill_runtime = CareerSkillRuntime(skill_library_service)
    app.state.career_skill_tool_registry = SkillToolRegistry(skill_library_service)
    # 会话列表只依赖 PostgreSQL 仓储，不应为了读取标题先初始化模型、Embedding、
    # 文档解析和云视觉客户端。轻量仓储与完整服务共享同一个 Engine，但使用独立锁，
    # 避免首屏并发加载模型配置时把历史列表阻塞在重服务初始化之后。
    app.state.career_assistant_read_services = None
    app.state.career_assistant_repository_lock = Lock()
    app.state.career_assistant_services = None
    app.state.career_assistant_services_lock = Lock()
    app.state.live_interview_connections = set()

    @app.on_event("shutdown")
    def close_career_assistant_services() -> None:
        """关闭求职助手连接池，不影响既有 SQLite 资源。"""

        services: CareerAssistantServices | None = app.state.career_assistant_services
        if services is not None:
            services.close()
            app.state.career_assistant_services = None
        else:
            read_services: CareerAssistantReadServices | None = (
                app.state.career_assistant_read_services
            )
            if read_services is not None:
                read_services.database.close()
        app.state.career_assistant_read_services = None


def set_request_actor(actor: CareerRequestActor) -> Token[CareerRequestActor | None]:
    """把认证后的平台用户映射为当前请求的 Career Actor。"""

    return _request_actor_context.set(actor)


def reset_request_actor(token: Token[CareerRequestActor | None]) -> None:
    """在请求结束时清除 ContextVar，避免连接复用时串用上一个用户。"""

    _request_actor_context.reset(token)


def get_request_actor() -> CareerRequestActor:
    """返回当前请求 Actor；仅未启用认证的本地开发保留默认 Actor。"""

    actor = _request_actor_context.get()
    if actor is not None:
        return actor

    return CareerRequestActor(
        organization_id=DEFAULT_ORGANIZATION_ID,
        actor_id=DEFAULT_ACTOR_ID,
        role=PlatformRole.USER,
    )


def get_career_services(request: Request) -> CareerAssistantServices:
    """懒加载求职助手服务；连接配置缺失时返回 503，不干扰旧接口。"""

    existing_services: CareerAssistantServices | None = request.app.state.career_assistant_services
    if existing_services is not None:
        return existing_services

    # 先取得可复用的轻量只读边界。该步骤不创建任何 HTTP Client，因此首屏历史、
    # 简历档案和模型可用性可先返回，再按实际操作初始化完整 Agent 服务。
    read_services = get_career_read_services(request)
    database = read_services.database
    conversation_repository = read_services.conversation_repository

    services_lock: Lock = request.app.state.career_assistant_services_lock
    with services_lock:
        existing_services = request.app.state.career_assistant_services
        if existing_services is not None:
            return existing_services

        try:
            context_repository = read_services.context_repository
            model_profile_repository = read_services.model_profile_repository
            interview_library_repository = InterviewLibraryRepository(database)
            agent_loop = CareerAgentLoop(conversation_repository)
            project_root: Path = request.app.state.career_assistant_project_root
            career_config_path = _resolve_career_config_path(project_root)
            attachment_settings = load_attachment_processing_settings(
                career_config_path,
            )
            document_understanding_settings = load_document_understanding_settings(
                career_config_path,
            )
            cloud_vision_settings = load_cloud_vision_settings(
                career_config_path,
            )
            legacy_office_conversion_settings = load_legacy_office_conversion_settings(
                career_config_path,
            )
            response_generation_settings = load_response_generation_settings(
                career_config_path,
            )
            job_assessment_settings = load_job_assessment_settings(
                career_config_path,
            )
            interview_retrieval_settings = load_interview_retrieval_settings(
                career_config_path,
            )
            runtime_settings = load_career_runtime_settings(
                career_config_path,
            )
            attachment_storage_settings = AttachmentSettings(
                temporary_root=_resolve_temporary_attachment_root(project_root),
                max_size_bytes=attachment_settings.max_size_bytes,
                ttl_seconds=attachment_settings.ttl_seconds,
                max_pdf_pages=attachment_settings.max_pdf_pages,
            )
            temporary_attachment_store = TemporaryAttachmentStore(
                attachment_storage_settings,
            )
            redactor = SensitiveDataRedactor(
                enabled=attachment_settings.redaction_enabled,
            )
            model_gateway = read_services.model_gateway
            model_connection_client = OpenAICompatibleChatClient(
                completion_max_tokens=response_generation_settings.max_completion_tokens,
                request_timeout_seconds=response_generation_settings.request_timeout_seconds,
            )
            memory_worker_settings = load_career_memory_worker_settings()
            compaction_repository = CareerCompactionRepository(database)
            model_usage_repository = read_services.model_usage_repository
            conversation_memory_service = ConversationMemoryService(
                conversation_repository,
                compaction_repository,
                model_connection_client,
                model_usage_repository,
                worker_id=memory_worker_settings.worker_id,
                lease_seconds=max(10, int(memory_worker_settings.lease_seconds)),
            )
            prompt_context_service = PromptContextService(
                conversation_repository,
                conversation_memory_service,
                ContextBudgetService(),
                request.app.state.career_skill_tool_registry,
                system_max_output_tokens=(
                    response_generation_settings.max_completion_tokens
                ),
            )
            job_assessment_repository = read_services.job_assessment_repository
            job_assessment_service = CareerJobAssessmentService(
                repository=job_assessment_repository,
                context_repository=context_repository,
                model_repository=model_profile_repository,
                model_gateway=model_gateway,
                model_client=model_connection_client,
                settings=job_assessment_settings,
            )
            greeting_service = CareerGreetingService(
                context_repository=context_repository,
                model_gateway=model_gateway,
                model_client=model_connection_client,
            )
            interview_retrieval_service = InterviewRetrievalService(
                interview_library_repository,
                interview_retrieval_settings,
                embedding_client=OpenAICompatibleEmbeddingClient(
                    interview_retrieval_settings.embedding,
                ),
            )
            interview_library_service = InterviewLibraryService(
                interview_library_repository,
                retrieval_service=interview_retrieval_service,
            )
            document_understanding_client = (
                DoclingServiceDocumentParser(document_understanding_settings)
                if document_understanding_settings.enabled
                else None
            )
            legacy_office_converter = (
                GotenbergOfficeConverter(legacy_office_conversion_settings)
                if legacy_office_conversion_settings.enabled
                else None
            )
            cloud_vision_client = (
                CloudVisionRouter(cloud_vision_settings)
                if cloud_vision_settings.enabled
                else None
            )
            attachment_parser = AttachmentParser(
                attachment_storage_settings,
                document_understanding_parser=document_understanding_client,
                legacy_office_converter=legacy_office_converter,
                cloud_vision_parser=cloud_vision_client,
            )
            resume_optimization_repository = ResumeOptimizationRepository(database)
            resume_optimization_service = ResumeOptimizationService(
                repository=resume_optimization_repository,
                model_repository=model_profile_repository,
                model_gateway=model_gateway,
                chat_client=model_connection_client,
                attachment_parser=attachment_parser,
                legacy_office_converter=legacy_office_converter,
                storage_root=project_root / "data" / "resume-assistant",
            )
            interview_collection_service = InterviewCollectionService(
                interview_library_repository,
                interview_library_service,
                model_gateway=model_gateway,
                model_connection_client=model_connection_client,
                temporary_attachment_store=temporary_attachment_store,
                attachment_parser=attachment_parser,
                firecrawl_client=(
                    FirecrawlClient(firecrawl_settings)
                    if (firecrawl_settings := load_firecrawl_settings()) is not None
                    else None
                ),
            )
            services = CareerAssistantServices(
                database=database,
                conversation_repository=conversation_repository,
                turn_job_repository=CareerTurnJobRepository(database),
                context_repository=context_repository,
                job_assessment_repository=job_assessment_repository,
                job_assessment_service=job_assessment_service,
                greeting_service=greeting_service,
                model_profile_repository=model_profile_repository,
                interview_library_repository=interview_library_repository,
                interview_library_service=interview_library_service,
                interview_retrieval_service=interview_retrieval_service,
                interview_collection_service=interview_collection_service,
                skill_runtime=request.app.state.career_skill_runtime,
                agent_loop=agent_loop,
                intake_graph=CareerIntakeGraph(
                    agent_loop,
                    redactor=redactor,
                    attachment_parser=attachment_parser,
                    temporary_attachment_store=temporary_attachment_store,
                    job_posting_extractor=JobPostingExtractor(),
                ),
                model_gateway=model_gateway,
                response_runner=CareerResponseRunner(
                    agent_loop,
                    model_gateway,
                    chat_client=model_connection_client,
                    redactor=redactor,
                    skill_tool_registry=request.app.state.career_skill_tool_registry,
                    prompt_context=prompt_context_service,
                    model_usage_repository=model_usage_repository,
                    max_persisted_response_characters=(
                        response_generation_settings.max_persisted_response_characters
                    ),
                    max_attempts=response_generation_settings.max_attempts,
                    retry_backoff_seconds=response_generation_settings.retry_backoff_seconds,
                ),
                compaction_repository=compaction_repository,
                model_usage_repository=model_usage_repository,
                conversation_memory_service=conversation_memory_service,
                prompt_context_service=prompt_context_service,
                temporary_attachment_store=temporary_attachment_store,
                attachment_parser=attachment_parser,
                redactor=redactor,
                resume_optimization_repository=resume_optimization_repository,
                resume_optimization_service=resume_optimization_service,
                model_connection_client=model_connection_client,
                document_understanding_client=document_understanding_client,
                legacy_office_converter=legacy_office_converter,
                cloud_vision_client=cloud_vision_client,
                turn_coordinator=AsyncTurnCoordinator(
                    max_concurrent_turns=runtime_settings.max_concurrent_turns,
                ),
                stream_heartbeat_seconds=runtime_settings.stream_heartbeat_seconds,
                turn_expected_seconds=runtime_settings.turn_timeout_seconds,
            )
        except (OSError, SQLAlchemyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"求职助手初始化失败：{exc}",
            ) from exc

        request.app.state.career_assistant_services = services
        return services


def get_career_read_services(
    request: Request,
) -> CareerAssistantReadServices:
    """返回首屏只读数据所需的轻量服务，不触发外部客户端初始化。"""

    try:
        _load_career_environment(request.app.state.career_assistant_project_root)
    except (CredentialCipherError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"求职助手运行配置加载失败：{exc}",
        ) from exc

    services: CareerAssistantServices | None = request.app.state.career_assistant_services
    if services is not None:
        existing_read_services: CareerAssistantReadServices | None = (
            request.app.state.career_assistant_read_services
        )
        if existing_read_services is not None:
            return existing_read_services

    existing_read_services: CareerAssistantReadServices | None = (
        request.app.state.career_assistant_read_services
    )
    if existing_read_services is not None:
        return existing_read_services

    repository_lock: Lock = request.app.state.career_assistant_repository_lock
    with repository_lock:
        existing_read_services = request.app.state.career_assistant_read_services
        if existing_read_services is not None:
            return existing_read_services

        database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
        if not database_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="求职助手数据库尚未配置 CAREER_DATABASE_URL",
            )

        try:
            database = CareerDatabase(database_url)
            conversation_repository = CareerConversationRepository(database)
            context_repository = CareerContextRepository(database)
            job_assessment_repository = CareerJobAssessmentRepository(database)
            model_profile_repository = CareerModelProfileRepository(database)
            model_usage_repository = CareerModelUsageRepository(database)
            model_gateway = ModelGateway(
                model_profile_repository,
                load_model_gateway_settings(
                    _resolve_career_config_path(
                        request.app.state.career_assistant_project_root,
                    ),
                ),
            )
        except (OSError, SQLAlchemyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"求职助手数据库初始化失败：{exc}",
            ) from exc

        read_services = CareerAssistantReadServices(
            database=database,
            conversation_repository=conversation_repository,
            context_repository=context_repository,
            job_assessment_repository=job_assessment_repository,
            model_profile_repository=model_profile_repository,
            model_gateway=model_gateway,
            model_usage_repository=model_usage_repository,
        )
        request.app.state.career_assistant_read_services = read_services
        return read_services


def get_career_conversation_repository(
    request: Request,
) -> CareerConversationRepository:
    """兼容会话仓储调用点，并保持首屏轻量初始化语义。"""

    return get_career_read_services(request).conversation_repository


def get_career_turn_job_repository(
    request: Request,
) -> CareerTurnJobRepository:
    """返回活动 Turn 查询仓储，不为历史浏览初始化完整 Agent 服务。"""

    existing_services: CareerAssistantServices | None = getattr(
        request.app.state,
        "career_assistant_services",
        None,
    )
    if existing_services is not None:
        return existing_services.turn_job_repository
    return CareerTurnJobRepository(get_career_read_services(request).database)


def _load_career_environment(project_root: Path) -> None:
    """加载求职模块配置，并保证模型凭据的持久化主密钥已就绪。"""

    environment_path = project_root / ".env.career-assistant"
    if environment_path.is_file():
        try:
            from dotenv import load_dotenv
        except ImportError as exc:
            raise RuntimeError("检测到求职助手环境文件，但缺少 python-dotenv") from exc

        load_dotenv(dotenv_path=environment_path, override=False)

    # 本地自动写入项目 data 目录，生产 Docker 则自动落到 application_data 命名卷。
    # 已通过部署 Secret 显式配置时会优先使用该值，不会覆盖现有部署策略。
    ensure_credential_master_key(project_root)


def _resolve_career_config_path(project_root: Path) -> Path:
    """解析求职模块配置路径，允许容器部署通过环境变量替换配置挂载点。

    未设置 ``CAREER_ASSISTANT_CONFIG_PATH`` 时保留当前本地默认路径。相对路径
    始终相对于项目根目录解析，避免工作目录变化后加载到意外的 YAML；路径只由
    服务端环境控制，永不接收浏览器输入。
    """

    configured_path = os.getenv("CAREER_ASSISTANT_CONFIG_PATH", "").strip()
    if not configured_path:
        return project_root / "config" / "career_assistant.yaml"

    candidate = Path(configured_path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved_path = candidate.resolve()
    if not resolved_path.is_file():
        raise ValueError(
            "未找到 CAREER_ASSISTANT_CONFIG_PATH 指定的配置文件："
            f"{resolved_path}",
        )
    return resolved_path


def _resolve_temporary_attachment_root(project_root: Path) -> Path:
    """确定附件临时目录；生产容器可把它挂载到 tmpfs，避免原件落盘持久化。"""

    configured_root = os.getenv("CAREER_TEMPORARY_ATTACHMENT_ROOT", "").strip()
    if not configured_root:
        return project_root / "data" / "career-temporary-attachments"

    candidate = Path(configured_root).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _resolve_conversation_skill_activation(
    services: CareerAssistantServices,
    actor_id: UUID,
    conversation_id: UUID,
    selected_skill_ids: list[str],
    user_text: str,
):
    """让一次显式 Skill 调用在当前会话的后续轮次持续生效。"""

    messages = services.conversation_repository.list_messages(
        actor_id,
        conversation_id,
        limit=200,
    )
    previous_user_texts = [
        message.content_text
        for message in messages
        if message.role is MessageRole.USER
    ]
    return services.skill_runtime.resolve_for_conversation(
        selected_skill_ids,
        user_text,
        previous_user_texts,
    )


@router.get("/candidate-profiles")
def list_candidate_profiles(request: Request) -> dict[str, object]:
    """列出当前用户可复用的基准简历版本，最近使用的版本排在前面。"""

    actor = get_request_actor()
    read_services = get_career_read_services(request)
    profiles = read_services.context_repository.list_candidate_profiles(actor.actor_id)
    return {"items": [_candidate_profile_payload(item) for item in profiles]}


@router.post("/greetings/generate")
def generate_greeting(
    request_body: GenerateGreetingRequest,
    request: Request,
) -> dict[str, object]:
    """使用当前用户简历和单个完整 JD 生成一条可审核招呼语。"""

    actor = get_request_actor()
    try:
        result = get_career_services(request).greeting_service.generate(
            actor.organization_id,
            actor.actor_id,
            request_body.candidate_profile_id,
            request_body.job.to_domain(),
            request_body.previous_message,
        )
    except GreetingCandidateNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "candidate_profile_not_found", "message": str(exc)},
        ) from exc
    except GreetingModelUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "greeting_model_unavailable", "message": str(exc)},
        ) from exc
    except GreetingJobValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "incomplete_job_detail", "message": str(exc)},
        ) from exc
    except GreetingGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "greeting_generation_failed", "message": str(exc)},
        ) from exc
    return _greeting_payload(result)


@router.post("/candidate-profiles/parse")
async def parse_candidate_profile(
    request: Request,
    resume_file: UploadFile = File(...),
) -> dict[str, object]:
    """真实解析基准简历并返回待确认提纲；原文件在响应前清理。"""

    services = get_career_services(request)
    attachments = []
    try:
        attachment = await services.temporary_attachment_store.save_upload(
            resume_file,
            _upload_kind(resume_file, is_resume=True),
        )
        attachments.append(attachment)
        parsed = await asyncio.to_thread(services.attachment_parser.parse, attachment)
        extracted_text = parsed.extracted_text.strip()
        if not extracted_text:
            raise ValueError("简历没有解析出可确认文本，请上传可复制文本的文件或清晰图片")
        profile = ResumeNormalizer().normalize(extracted_text)
        outline = profile.to_model_outline()
        if not outline.strip():
            outline = extracted_text[:30_000]
        outline = services.redactor.redact(outline)[:30_000]
        return {
            "source_filename": attachment.original_filename,
            "resume_outline": outline,
            "source_character_count": profile.source_character_count,
            "sections": [
                {
                    "key": section.section.value,
                    "content": services.redactor.redact(section.content)[:4_000],
                    "confidence": section.recognition_confidence,
                }
                for section in profile.sections
            ],
            "notices": [
                item for item in (parsed.document_understanding_error,) if item
            ],
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))


@router.post("/candidate-profiles", status_code=status.HTTP_201_CREATED)
def create_candidate_profile(
    request_body: CreateCandidateProfileRequest,
    request: Request,
) -> dict[str, object]:
    """保存用户已确认的基准简历版本。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        profile = services.context_repository.create_candidate_profile(
            actor.organization_id,
            actor.actor_id,
            display_name=request_body.display_name,
            source_filename=Path(request_body.source_filename).name,
            resume_outline=services.redactor.redact(request_body.resume_outline),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _candidate_profile_payload(profile)


@router.post("/target-role-profiles", status_code=status.HTTP_201_CREATED)
def create_target_role_profile(
    request_body: CreateTargetRoleRequest,
    request: Request,
) -> dict[str, object]:
    """保存用户确认后的目标岗位，并确定性拆解可复核要求项。"""

    actor = get_request_actor()
    services = get_career_services(request)
    job_text = request_body.job_text.strip()
    requirements = extract_job_requirements(job_text)
    try:
        role = services.context_repository.create_target_role(
            actor.organization_id,
            actor.actor_id,
            company_name=request_body.company_name,
            role_name=request_body.role_name,
            source_kind=request_body.source_kind,
            source_label=request_body.source_label,
            job_text=job_text,
            requirements=requirements,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _target_role_payload(role)


@router.post("/target-role-profiles/parse-file")
async def parse_target_role_file(
    request: Request,
    job_file: UploadFile = File(...),
) -> dict[str, object]:
    """解析岗位文件为可编辑正文；原文件在响应前清理。"""

    services = get_career_services(request)
    attachments = []
    try:
        attachment = await services.temporary_attachment_store.save_upload(
            job_file,
            _upload_kind(job_file, is_resume=False),
        )
        attachments.append(attachment)
        parsed = await asyncio.to_thread(services.attachment_parser.parse, attachment)
        job_text = parsed.extracted_text.strip()
        if not job_text:
            raise ValueError("岗位文件没有解析出可编辑文本，请改为粘贴职位要求")
        return {
            "source_filename": attachment.original_filename,
            "job_text": job_text[:30_000],
            "requirements": list(extract_job_requirements(job_text)),
            "notices": [
                item for item in (parsed.document_understanding_error,) if item
            ],
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))


@router.get("/conversations")
def list_conversations(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
) -> dict[str, object]:
    """读取当前用户的求职会话历史摘要。"""

    actor = get_request_actor()
    repository = get_career_conversation_repository(request)
    conversations, total = repository.list_conversation_page(
        actor.actor_id,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": [_conversation_payload(item) for item in conversations],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    request_body: CreateConversationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """创建新会话；由 AgentLoop 而不是 Web 层直接调用仓储。"""

    actor = get_request_actor()
    has_initial_context = bool(
        request_body.candidate_profile_id or request_body.target_role_profile_id
    )
    if has_initial_context:
        services = get_career_services(request)
        agent_loop = services.agent_loop
    else:
        read_services = get_career_read_services(request)
        services = None
        agent_loop = CareerAgentLoop(read_services.conversation_repository)

    conversation = agent_loop.open_conversation(
        actor.organization_id,
        actor.actor_id,
        request_body.title,
    )
    context = None
    if services is not None:
        try:
            context = services.context_repository.bind_conversation(
                actor.actor_id,
                conversation.id,
                request_body.candidate_profile_id,
                request_body.target_role_profile_id,
            )
            _enqueue_job_assessment(services, background_tasks, context)
        except (LookupError, ValueError) as exc:
            services.conversation_repository.delete_conversation_permanently(
                actor.actor_id,
                conversation.id,
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = _conversation_payload(conversation)
    payload["context"] = (
        _conversation_context_payload(
            context,
            _current_job_assessment(services, context),
        )
        if context
        else None
    )
    return payload


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """读取会话及其已脱敏消息历史。"""

    actor = get_request_actor()
    read_services = get_career_read_services(request)
    conversation = read_services.conversation_repository.get_conversation(
        actor.actor_id,
        conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在、已删除或无访问权限")
    messages = read_services.conversation_repository.list_messages(
        actor.actor_id,
        conversation_id,
    )
    assistant_turn_ids = [
        message.turn_id
        for message in messages
        if message.role is MessageRole.ASSISTANT and message.turn_id is not None
    ]
    model_usage_repository = getattr(read_services, "model_usage_repository", None)
    message_models = (
        model_usage_repository.list_turn_metadata(assistant_turn_ids)
        if assistant_turn_ids and model_usage_repository is not None
        else {}
    )
    last_model_selection = read_services.conversation_repository.get_last_model_selection(
        actor.actor_id,
        conversation_id,
    )
    latest_turn = read_services.conversation_repository.get_latest_agent_turn(
        actor.actor_id,
        conversation_id,
    )
    count_successful_turns = getattr(
        read_services.conversation_repository,
        "count_successful_turns",
        None,
    )
    successful_turns = (
        count_successful_turns(actor.actor_id, conversation_id)
        if callable(count_successful_turns)
        else 0
    )
    context = read_services.context_repository.get_conversation_context(
        actor.actor_id,
        conversation_id,
    )
    assessment = (
        _current_job_assessment(read_services, context)
        if context is not None
        else None
    )
    has_complete_assessment_context = (
        context is not None
        and context.candidate is not None
        and context.target_role is not None
    )
    if has_complete_assessment_context and assessment is None:
        services = get_career_services(request)
        _enqueue_job_assessment(services, background_tasks, context)
        assessment = _current_job_assessment(services, context)
    elif has_complete_assessment_context:
        background_tasks.add_task(
            _refresh_job_assessment_after_response,
            request,
            context,
        )
    return {
        "conversation": _conversation_payload(conversation),
        "messages": [
            _message_payload(item, message_models.get(item.turn_id))
            for item in messages
        ],
        "last_model_selection": _model_selection_payload(last_model_selection),
        "latest_turn": _turn_payload(latest_turn) if latest_turn is not None else None,
        "turn_limit": {
            "successful_turns": successful_turns,
            "remaining_turns": max(0, 30 - successful_turns),
            "max_turns": 30,
            "reached": successful_turns >= 30,
        },
        "context": (
            _conversation_context_payload(
                context,
                assessment,
            )
            if context
            else None
        ),
    }


@router.post("/conversations/{conversation_id}/context")
def update_conversation_context(
    conversation_id: UUID,
    request_body: UpdateConversationContextRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """显式创建会话上下文新版本；历史消息不会被重写。"""

    actor = get_request_actor()
    services = get_career_services(request)
    if not request_body.candidate_profile_id and not request_body.target_role_profile_id:
        raise HTTPException(status_code=422, detail="至少需要添加基准简历或目标岗位中的一项")
    try:
        context = services.context_repository.bind_conversation(
            actor.actor_id,
            conversation_id,
            request_body.candidate_profile_id,
            request_body.target_role_profile_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _enqueue_job_assessment(services, background_tasks, context)
    return _conversation_context_payload(
        context,
        _current_job_assessment(services, context),
    )


@router.post("/conversations/{conversation_id}/assessment/retry")
def retry_conversation_assessment(
    conversation_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """重新排队当前简历与岗位版本的分析，不并发重复调用 Judge。"""

    actor = get_request_actor()
    services = get_career_services(request)
    context = services.context_repository.get_conversation_context(
        actor.actor_id,
        conversation_id,
    )
    if context is None:
        raise HTTPException(status_code=404, detail="会话不存在或无访问权限")
    if context.candidate is None or context.target_role is None:
        raise HTTPException(status_code=422, detail="需要同时添加简历和目标岗位后才能分析")
    record = services.job_assessment_service.retry_context(context)
    if record.status.value == "queued":
        background_tasks.add_task(
            services.job_assessment_service.run_assessment,
            record.id,
        )
    return _conversation_context_payload(context, record)


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: UUID,
    request_body: RenameConversationRequest,
    request: Request,
) -> dict[str, object]:
    """重命名当前用户拥有的会话。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        conversation = services.conversation_repository.rename_conversation(
            actor.actor_id,
            conversation_id,
            request_body.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在、无访问权限或已删除")
    return _conversation_payload(conversation)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: UUID,
    request: Request,
) -> dict[str, object]:
    """永久删除当前用户会话及其服务端关联历史。"""

    actor = get_request_actor()
    services = get_career_services(request)
    deleted = services.conversation_repository.delete_conversation_permanently(
        actor.actor_id,
        conversation_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在或无访问权限")
    return {"deleted": True}


@router.post("/conversations/{conversation_id}/archive")
def archive_conversation(conversation_id: UUID, request: Request) -> dict[str, object]:
    """归档会话，保留历史但禁止后续继续写入。"""

    actor = get_request_actor()
    services = get_career_services(request)
    archived = services.conversation_repository.archive_conversation(actor.actor_id, conversation_id)
    if not archived:
        raise HTTPException(status_code=404, detail="会话不存在、无访问权限或不可归档")
    conversation = services.agent_loop.resume_conversation(actor.actor_id, conversation_id)
    return _conversation_payload(conversation)


@router.post("/conversations/{conversation_id}/intake", status_code=status.HTTP_202_ACCEPTED)
async def submit_intake(
    conversation_id: UUID,
    request_body: SubmitIntakeRequest,
    request: Request,
) -> dict[str, object]:
    """旧版文本入口：使用 307 保留请求体并转入 PostgreSQL Turn 队列。"""

    return RedirectResponse(
        url=f"/api/career/conversations/{conversation_id}/turns",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Deprecation": "true"},
    )

    actor = get_request_actor()
    services = get_career_services(request)
    selection = ModelSelectionRequest(
        mode=request_body.selection_mode,
        profile_id=request_body.model_profile_id,
    )
    try:
        interview_evidence = await asyncio.to_thread(
            _build_interview_evidence,
            services,
            actor,
            request_body.interview_experience_ids,
            request_body.text,
            conversation_id=conversation_id,
        )
        skill_activation = await asyncio.to_thread(
            _resolve_conversation_skill_activation,
            services,
            actor.actor_id,
            conversation_id,
            request_body.selected_skill_ids,
            request_body.text,
        )
        inbound_message = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation_id,
            actor_id=actor.actor_id,
            text=request_body.text,
            effective_text=skill_activation.user_task_text,
            job_url=request_body.job_url,
            model_selection=selection,
            interview_evidence=interview_evidence,
            activated_skills=skill_activation.skills,
        )
        result, response_result = await _run_queued_career_turn(
            services,
            inbound_message,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "turn": _turn_payload(response_result.turn),
        "message": _message_payload(result.persisted_message),
        "assistant_message": _message_payload(
            response_result.assistant_message,
            _response_model_metadata(response_result),
        ),
        "completed_steps": [step.value for step in result.completed_steps],
        "job_source": _job_source_payload(result),
        "activated_skills": _activated_skill_payloads(inbound_message.activated_skills),
        "skill_executions": _skill_execution_payloads(response_result.skill_executions),
    }


@router.post("/conversations/{conversation_id}/intake-stream")
async def stream_intake(
    conversation_id: UUID,
    request_body: SubmitIntakeRequest,
    request: Request,
) -> StreamingResponse:
    """旧版文本 SSE 入口：统一转入持久化 Turn 提交接口。"""

    return RedirectResponse(
        url=f"/api/career/conversations/{conversation_id}/turns",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Deprecation": "true"},
    )

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        interview_evidence = await asyncio.to_thread(
            _build_interview_evidence,
            services,
            actor,
            request_body.interview_experience_ids,
            request_body.text,
            conversation_id=conversation_id,
        )
        skill_activation = await asyncio.to_thread(
            _resolve_conversation_skill_activation,
            services,
            actor.actor_id,
            conversation_id,
            request_body.selected_skill_ids,
            request_body.text,
        )
        inbound_message = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation_id,
            actor_id=actor.actor_id,
            text=request_body.text,
            effective_text=skill_activation.user_task_text,
            job_url=request_body.job_url,
            model_selection=ModelSelectionRequest(
                mode=request_body.selection_mode,
                profile_id=request_body.model_profile_id,
            ),
            interview_evidence=interview_evidence,
            activated_skills=skill_activation.skills,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _create_career_streaming_response(
        services,
        inbound_message,
    )


@router.post(
    "/conversations/{conversation_id}/intake-with-materials",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_intake_with_materials(
    conversation_id: UUID,
    request: Request,
    text: str = Form(default=""),
    job_url: str | None = Form(default=None),
    selection_mode: ModelSelectionMode = Form(
        default=ModelSelectionMode.FREE_QUOTA_FIRST,
    ),
    model_profile_id: UUID | None = Form(default=None),
    interview_experience_ids: list[UUID] = Form(default_factory=list),
    selected_skill_ids: list[str] = Form(default_factory=list),
    resume_file: UploadFile | None = File(default=None),
    job_description_file: UploadFile | None = File(default=None),
) -> dict[str, object]:
    """旧版附件入口：统一转入完整解析文本持久化接口。"""

    return RedirectResponse(
        url=f"/api/career/conversations/{conversation_id}/turns-with-materials",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Deprecation": "true"},
    )

    if len(text) > 30_000:
        raise HTTPException(status_code=422, detail="咨询文本不能超过 30000 个字符")
    if job_url is not None and len(job_url) > 2_000:
        raise HTTPException(status_code=422, detail="职位链接不能超过 2000 个字符")
    if resume_file is None and job_description_file is None:
        raise HTTPException(status_code=422, detail="请至少上传一份简历或职位材料")

    actor = get_request_actor()
    services = get_career_services(request)
    attachments = []
    try:
        interview_evidence = await asyncio.to_thread(
            _build_interview_evidence,
            services,
            actor,
            interview_experience_ids,
            text,
            conversation_id=conversation_id,
        )
        skill_activation = await asyncio.to_thread(
            _resolve_conversation_skill_activation,
            services,
            actor.actor_id,
            conversation_id,
            selected_skill_ids,
            text,
        )
        if resume_file is not None:
            attachments.append(
                await services.temporary_attachment_store.save_upload(
                    resume_file,
                    _upload_kind(resume_file, is_resume=True),
                ),
            )
        if job_description_file is not None:
            attachments.append(
                await services.temporary_attachment_store.save_upload(
                    job_description_file,
                    _upload_kind(job_description_file, is_resume=False),
                ),
            )

        inbound_message = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation_id,
            actor_id=actor.actor_id,
            text=text,
            effective_text=skill_activation.user_task_text,
            job_url=job_url,
            model_selection=ModelSelectionRequest(
                mode=selection_mode,
                profile_id=model_profile_id,
            ),
            attachments=tuple(attachments),
            interview_evidence=interview_evidence,
            activated_skills=skill_activation.skills,
        )
        result, response_result = await _run_queued_career_turn(
            services,
            inbound_message,
            attachment_count=len(attachments),
        )
    except OSError as exc:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise _temporary_attachment_service_unavailable(exc) from exc
    except (LookupError, ValueError, RuntimeError) as exc:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise

    return {
        "turn": _turn_payload(response_result.turn),
        "message": _message_payload(result.persisted_message),
        "assistant_message": _message_payload(
            response_result.assistant_message,
            _response_model_metadata(response_result),
        ),
        "completed_steps": [step.value for step in result.completed_steps],
        "job_source": _job_source_payload(result),
        "activated_skills": _activated_skill_payloads(inbound_message.activated_skills),
        "skill_executions": _skill_execution_payloads(response_result.skill_executions),
        "attachment_processing": {
            "temporary_only": True,
            "count": len(attachments),
            "text_characters": _analyzed_material_characters(result.model_context),
            "resume_outline_characters": len(
                result.model_context.redacted_resume_outline,
            ),
            "notices": list(result.model_context.document_processing_notices),
            "items": [
                _attachment_processing_summary_payload(item)
                for item in result.model_context.attachment_processing_summaries
            ],
            "pdf_without_extractable_text_count": (
                result.model_context.pdf_without_extractable_text_count
            ),
            "cleaned_after_turn": True,
        },
    }


@router.post("/conversations/{conversation_id}/intake-with-materials-stream")
async def stream_intake_with_materials(
    conversation_id: UUID,
    request: Request,
    text: str = Form(default=""),
    job_url: str | None = Form(default=None),
    selection_mode: ModelSelectionMode = Form(
        default=ModelSelectionMode.FREE_QUOTA_FIRST,
    ),
    model_profile_id: UUID | None = Form(default=None),
    interview_experience_ids: list[UUID] = Form(default_factory=list),
    selected_skill_ids: list[str] = Form(default_factory=list),
    resume_file: UploadFile | None = File(default=None),
    job_description_file: UploadFile | None = File(default=None),
) -> StreamingResponse:
    """旧版附件 SSE 入口：统一转入持久化 Turn 提交接口。"""

    return RedirectResponse(
        url=f"/api/career/conversations/{conversation_id}/turns-with-materials",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Deprecation": "true"},
    )

    if len(text) > 30_000:
        raise HTTPException(status_code=422, detail="咨询文本不能超过 30000 个字符")
    if job_url is not None and len(job_url) > 2_000:
        raise HTTPException(status_code=422, detail="职位链接不能超过 2000 个字符")
    if resume_file is None and job_description_file is None:
        raise HTTPException(status_code=422, detail="请至少上传一份简历或职位材料")

    actor = get_request_actor()
    services = get_career_services(request)
    attachments = []
    try:
        interview_evidence = await asyncio.to_thread(
            _build_interview_evidence,
            services,
            actor,
            interview_experience_ids,
            text,
            conversation_id=conversation_id,
        )
        skill_activation = await asyncio.to_thread(
            _resolve_conversation_skill_activation,
            services,
            actor.actor_id,
            conversation_id,
            selected_skill_ids,
            text,
        )
        if resume_file is not None:
            attachments.append(
                await services.temporary_attachment_store.save_upload(
                    resume_file,
                    _upload_kind(resume_file, is_resume=True),
                ),
            )
        if job_description_file is not None:
            attachments.append(
                await services.temporary_attachment_store.save_upload(
                    job_description_file,
                    _upload_kind(job_description_file, is_resume=False),
                ),
            )
    except OSError as exc:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise _temporary_attachment_service_unavailable(exc) from exc
    except (LookupError, ValueError, RuntimeError) as exc:
        services.temporary_attachment_store.cleanup(tuple(attachments))
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    inbound_message = CareerInboundMessage(
        turn_id=uuid4(),
        conversation_id=conversation_id,
        actor_id=actor.actor_id,
        text=text,
        effective_text=skill_activation.user_task_text,
        job_url=job_url,
        model_selection=ModelSelectionRequest(
            mode=selection_mode,
            profile_id=model_profile_id,
        ),
        attachments=tuple(attachments),
        interview_evidence=interview_evidence,
        activated_skills=skill_activation.skills,
    )
    return _create_career_streaming_response(
        services,
        inbound_message,
        attachment_count=len(attachments),
    )


@router.get("/interview-library/collection-platforms")
def list_interview_collection_platforms(
    request: Request,
) -> dict[str, object]:
    """返回页面可展示的采集连接器边界，不返回账号、Cookie 或会话状态。"""

    services = get_career_services(request)
    return {
        "items": [
            {
                "key": policy.key,
                "label": policy.label,
                "can_run_keyword_search": policy.can_run_keyword_search,
                "connector_kind": policy.connector_kind.value,
                "policy_decision": policy.policy_decision,
                "ready": policy.ready,
                "unavailable_reason": policy.unavailable_reason,
            }
            for policy in services.interview_collection_service.list_platform_policies()
        ]
    }


@router.post(
    "/interview-library/collection-jobs",
    status_code=status.HTTP_201_CREATED,
)
def create_interview_collection_job(
    request_body: CreateInterviewCollectionJobRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, object]:
    """创建关键词发现任务；小红书关键词复用公开搜索页导入链路。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job = services.interview_collection_service.create_keyword_collection_job(
            actor.organization_id,
            created_by_actor_id=actor.actor_id,
            platform_key=request_body.platform_key,
            keyword=request_body.keyword,
            requested_limit=request_body.requested_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if job.platform_key == "xiaohongshu" and job.connector_kind.value == "url_import":
        background_tasks.add_task(
            services.interview_collection_service.run_xiaohongshu_import,
            actor.organization_id,
            job.id,
        )
    return _collection_job_payload(job)


@router.get("/interview-library/collection-jobs/{job_id}")
def get_interview_collection_job(
    job_id: UUID,
    request: Request,
) -> dict[str, object]:
    """读取采集任务及其候选资料，以便页面恢复中断后的导入动作。"""

    actor = get_request_actor()
    services = get_career_services(request)
    job = services.interview_library_repository.get_collection_job(
        actor.organization_id,
        job_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="采集任务不存在或无访问权限")
    candidates = services.interview_library_repository.list_collection_candidates(
        actor.organization_id,
        job_id,
    )
    return {
        **_collection_job_payload(job),
        "candidates": [_collection_candidate_payload(candidate) for candidate in candidates],
    }


@router.post(
    "/interview-library/collect-url",
    status_code=status.HTTP_201_CREATED,
)
def collect_interview_public_url(
    request_body: CollectInterviewUrlRequest,
    request: Request,
) -> dict[str, object]:
    """读取用户主动粘贴的公开 HTTPS 页面，生成待确认的候选资料。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job, candidate = services.interview_collection_service.collect_public_url(
            actor.organization_id,
            created_by_actor_id=actor.actor_id,
            source_url=request_body.source_url,
        )
    except CollectionOperationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job": _collection_job_payload(job),
        "candidate": _collection_candidate_payload(candidate),
    }


@router.post(
    "/interview-library/xiaohongshu-browser-imports",
    status_code=status.HTTP_201_CREATED,
)
def create_xiaohongshu_browser_import(
    request_body: CreateXiaohongshuBrowserImportRequest,
    request: Request,
) -> dict[str, object]:
    """创建关键词任务，等待当前浏览器助手读取小红书页面。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job = services.interview_collection_service.create_xiaohongshu_browser_collection_job(
            actor.organization_id,
            created_by_actor_id=actor.actor_id,
            keyword=request_body.keyword,
            requested_limit=request_body.requested_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"job": _collection_job_payload(job)}


@router.post(
    "/interview-library/public-web-imports",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_public_web_interview_import(
    request_body: CreatePublicWebImportRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, object]:
    """创建全网公开网页搜索任务，并在后台完成去重、解析与入库。"""

    actor = get_request_actor()
    services = get_career_services(request)
    if not services.interview_collection_service.public_web_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置全网公开信息收集服务，请先设置 FIRECRAWL_API_KEY。",
        )
    try:
        job = services.interview_collection_service.create_public_web_import_job(
            actor.organization_id,
            created_by_actor_id=actor.actor_id,
            keyword=request_body.keyword,
            requested_limit=request_body.requested_limit,
        )
    except CollectionOperationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    background_tasks.add_task(
        services.interview_collection_service.run_public_web_import,
        actor.organization_id,
        job.id,
    )
    return {"job": _collection_job_payload(job)}


@router.post(
    "/interview-library/collection-jobs/{job_id}/xiaohongshu-discoveries",
)
def register_xiaohongshu_browser_discoveries(
    job_id: UUID,
    request_body: RegisterXiaohongshuBrowserDiscoveriesRequest,
    request: Request,
) -> dict[str, object]:
    """保存不含 token 和图片的搜索卡片，支持任务恢复与跨批次去重。"""

    actor = get_request_actor()
    services = get_career_services(request)
    discoveries = tuple(
        XiaohongshuBrowserDiscovery(
            note_id=item.note_id,
            title=item.title,
            author_name=item.author_name,
            liked_count=item.liked_count,
        )
        for item in request_body.items
    )
    try:
        candidates = services.interview_collection_service.register_xiaohongshu_browser_discoveries(
            actor.organization_id,
            job_id,
            discoveries,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"candidates": [_collection_candidate_payload(item) for item in candidates]}


@router.post(
    "/interview-library/collection-jobs/{job_id}/xiaohongshu-notes",
    status_code=status.HTTP_202_ACCEPTED,
)
def process_xiaohongshu_browser_note(
    job_id: UUID,
    request_body: ProcessXiaohongshuBrowserNoteRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, object]:
    """接收一篇无详情 token 的笔记，并在后台复用现有 OCR/入库链路。"""

    actor = get_request_actor()
    services = get_career_services(request)
    note = request_body.to_domain()
    try:
        candidate = services.interview_collection_service.queue_xiaohongshu_browser_note(
            actor.organization_id,
            job_id,
            note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if candidate.status.value == "discovered":
        background_tasks.add_task(
            services.interview_collection_service.process_xiaohongshu_browser_note,
            actor.organization_id,
            job_id,
            note,
        )
    return {"candidate": _collection_candidate_payload(candidate)}


@router.post(
    "/interview-library/collection-jobs/{job_id}/pause",
)
def pause_xiaohongshu_browser_job(
    job_id: UUID,
    request_body: PauseXiaohongshuBrowserJobRequest,
    request: Request,
) -> dict[str, object]:
    """暂停需要登录/验证的任务，或记录用户主动停止。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job = services.interview_collection_service.pause_xiaohongshu_browser_job(
            actor.organization_id,
            job_id,
            error_code=request_body.error_code,
            error_message=request_body.error_message,
            cancelled=request_body.cancelled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"job": _collection_job_payload(job)}


@router.post(
    "/interview-library/collection-jobs/{job_id}/complete",
)
def complete_xiaohongshu_browser_job(
    job_id: UUID,
    request: Request,
) -> dict[str, object]:
    """确认所有搜索卡片已有结果后完成浏览器信息收集任务。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job = services.interview_collection_service.complete_xiaohongshu_browser_job(
            actor.organization_id,
            job_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"job": _collection_job_payload(job)}


@router.post(
    "/interview-library/xiaohongshu-imports",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_xiaohongshu_interview_import(
    request_body: CreateXiaohongshuImportRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, object]:
    """创建小红书公开资料批量导入任务，并交由后台逐条完成解析与入库。

    请求会立即返回任务编号，前端通过既有 collection job 查询接口轮询进度，避免多篇
    笔记、多图 OCR 与 LLM 分析长时间占住浏览器请求。
    """

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        job = services.interview_collection_service.create_xiaohongshu_import_job(
            actor.organization_id,
            created_by_actor_id=actor.actor_id,
            source_url=request_body.source_url,
            requested_limit=request_body.requested_limit,
            include_images=request_body.include_images,
            auto_import=request_body.auto_import,
        )
    except CollectionOperationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    background_tasks.add_task(
        services.interview_collection_service.run_xiaohongshu_import,
        actor.organization_id,
        job.id,
    )
    return {"job": _collection_job_payload(job)}


@router.post("/interview-library/collection-candidates/{candidate_id}/select")
def select_interview_collection_candidate(
    candidate_id: UUID,
    request: Request,
) -> dict[str, object]:
    """标记用户准备入库的候选资料，防止未读正文直接进入 RAG。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        candidate = services.interview_collection_service.select_candidate(
            actor.organization_id,
            candidate_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _collection_candidate_payload(candidate)


@router.post(
    "/interview-library/collection-candidates/{candidate_id}/import",
    status_code=status.HTTP_201_CREATED,
)
def import_interview_collection_candidate(
    candidate_id: UUID,
    request_body: ImportInterviewCollectionCandidateRequest,
    request: Request,
) -> dict[str, object]:
    """将候选网页 Markdown 导入现有面经库，并复用切片与检索链路。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        experience = services.interview_collection_service.ingest_selected_candidate(
            actor.organization_id,
            actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
            candidate_id=candidate_id,
            company_name=request_body.company_name,
            role_name=request_body.role_name,
            interview_date=request_body.interview_date,
            summary_text=request_body.summary_text,
            tags=tuple(request_body.tags),
            markdown_content=request_body.markdown_content,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _interview_experience_payload(experience, actor=actor)


@router.get("/interview-library/tree")
def list_interview_library_tree(
    request: Request,
    query: str | None = None,
) -> dict[str, object]:
    """读取公司 → 岗位 → 面经日期的树形导航数据。"""

    services = get_career_services(request)
    try:
        items = services.interview_library_repository.list_tree(query=query)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items}


@router.get("/interview-library/experiences/{experience_id}")
def get_interview_experience(
    experience_id: UUID,
    request: Request,
) -> dict[str, object]:
    """读取一份面经正文及其可追溯的来源元数据。"""

    actor = get_request_actor()
    services = get_career_services(request)
    experience = services.interview_library_repository.get_public_experience(experience_id)
    if experience is None:
        raise HTTPException(status_code=404, detail="面经不存在")
    sources = services.interview_library_repository.list_public_experience_sources(experience_id)
    return _interview_experience_payload(experience, sources=sources, actor=actor)


@router.post(
    "/interview-library/experiences",
    status_code=status.HTTP_201_CREATED,
)
def create_interview_experience(
    request_body: CreateInterviewExperienceRequest,
    request: Request,
) -> dict[str, object]:
    """把已解析且清洗完成的面经 Markdown 入库并建立可检索切片。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        experience = services.interview_library_service.ingest(
            actor.organization_id,
            InterviewExperienceDraft(
                company_name=request_body.company_name,
                role_name=request_body.role_name,
                interview_date=request_body.interview_date,
                markdown_content=request_body.markdown_content,
                source_type=request_body.source_type,
                source_platform=request_body.source_platform,
                source_url=request_body.source_url,
                summary_text=request_body.summary_text,
                tags=tuple(request_body.tags),
                job_name=request_body.job_name,
            ),
            trigger_type=(
                IngestionTriggerType.MANUAL_URL
                if request_body.source_url
                else IngestionTriggerType.MANUAL_UPLOAD
                if request_body.source_type is InterviewSourceType.MANUAL_UPLOAD
                else IngestionTriggerType.MANUAL_URL
                if request_body.source_type is InterviewSourceType.PUBLIC_URL
                else IngestionTriggerType.MANUAL_UPLOAD
            ),
            created_by_actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
        )
    except (LookupError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("面经保存失败，数据库事务已回滚")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="面经暂时无法保存，请稍后重试；已解析的内容仍保留在当前页面。",
        ) from exc
    return _interview_experience_payload(experience, actor=actor)


@router.post(
    "/interview-library/parse-file",
)
async def parse_interview_experience_file(
    request: Request,
    source_file: UploadFile = File(...),
    source_platform: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
) -> dict[str, object]:
    """解析面经附件并返回可编辑草稿，不直接持久化原文件或写入知识库。"""

    services = get_career_services(request)
    attachments = []
    try:
        attachment = await services.temporary_attachment_store.save_upload(
            source_file,
            _upload_kind(source_file, is_resume=True),
        )
        attachments.append(attachment)
        # 文档解析可能触发 Docling、OCR 或视觉模型；移至工作线程，避免阻塞流式进度与其他请求。
        parsed = await asyncio.to_thread(services.attachment_parser.parse, attachment)
        extracted_text = parsed.extracted_text.strip()
        if not extracted_text:
            detail = parsed.document_understanding_error or (
                "未能从文件中识别到可用文字；请上传更清晰的原图、可复制文本的 PDF，或改用粘贴正文。"
            )
            raise ValueError(detail)

        inferred = InterviewMaterialMetadataExtractor().extract(
            extracted_text,
            filename=attachment.original_filename,
            source_platform=source_platform,
        )
        company_name = inferred.company_name or "待归档公司"
        role_name = inferred.role_name or "未识别岗位"
        markdown_content = _interview_markdown_from_parsed_attachment(extracted_text)
        warnings = []
        if inferred.company_name is None:
            warnings.append("未可靠识别公司名称，请在确认入库前补充或修正。")
        if inferred.role_name is None:
            warnings.append("未可靠识别面试岗位，请在确认入库前补充或修正。")
        if parsed.document_understanding_error:
            warnings.append(parsed.document_understanding_error)
        return {
            "draft": {
                "company_name": company_name,
                "role_name": role_name,
                "interview_date": (
                    inferred.interview_date.isoformat()
                    if inferred.interview_date is not None
                    else None
                ),
                "source_platform": inferred.source_platform,
                "source_url": source_url,
                "tags": list(inferred.tags),
                "summary_text": inferred.summary_text,
            },
            "markdown_content": markdown_content,
            "recognition": {
                "confidence": inferred.confidence,
                "evidence": list(inferred.evidence),
                "parser": (
                    "cloud_vision"
                    if parsed.cloud_vision_result is not None
                    else "docling_ocr"
                ),
                "warnings": warnings,
            },
        }
    except OSError as exc:
        raise _temporary_attachment_service_unavailable(exc) from exc
    except (LookupError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - 防止临时文件解析错误被前端压成无意义 500
        logger.exception("面经文件预解析失败：%s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="文件解析服务发生内部错误，未保存任何原始文件；请稍后重试。",
        ) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))


@router.post(
    "/interview-library/parse-file-stream",
)
async def stream_parse_interview_experience_file(
    request: Request,
    source_file: UploadFile = File(...),
    source_platform: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
) -> StreamingResponse:
    """以 NDJSON 返回面经材料预解析进度；完成后给出与普通预解析接口一致的草稿。"""

    async def event_stream() -> Iterator[str]:
        def encode(event: str, **payload: object) -> str:
            return json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n"

        yield encode(
            "progress",
            percent=8,
            phase="正在接收材料",
            detail="文件已进入临时解析队列，原件不会写入面经库。",
        )
        parse_task = asyncio.create_task(
            parse_interview_experience_file(
                request=request,
                source_file=source_file,
                source_platform=source_platform,
                source_url=source_url,
            )
        )
        percent = 28
        yield encode(
            "progress",
            percent=percent,
            phase="正在识别版面与正文",
            detail="正在选择 Docling、OCR 或视觉理解链路。",
        )

        while not parse_task.done():
            await asyncio.sleep(0.75)
            next_percent = min(76, percent + 4)
            if next_percent == percent:
                # 解析仍在进行，但不重复发送相同百分比，避免浏览器收到无意义的进度事件。
                continue
            percent = next_percent
            yield encode(
                "progress",
                percent=percent,
                phase="正在提取面经信息",
                detail="正在读取正文、标题、公司和岗位线索。",
            )

        try:
            payload = await parse_task
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "文件解析未完成。"
            yield encode("error", message=detail)
            return
        except Exception as exc:  # pragma: no cover - 流式传输兜底
            logger.exception("面经文件流式预解析失败：%s", type(exc).__name__)
            yield encode("error", message="文件解析服务暂时不可用，请稍后重试。")
            return

        yield encode(
            "progress",
            percent=92,
            phase="正在生成可编辑草稿",
            detail="正在整理 Markdown 正文与自动识别字段。",
        )
        yield encode(
            "progress",
            percent=100,
            phase="识别完成",
            detail="已生成预填草稿，请核对后再保存并建立索引。",
        )
        yield encode("result", payload=payload)

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/interview-library/import-file",
    status_code=status.HTTP_201_CREATED,
)
async def import_interview_experience_file(
    request: Request,
    company_name: str = Form(default=""),
    role_name: str = Form(default=""),
    interview_date: date | None = Form(default=None),
    source_file: UploadFile = File(...),
    source_platform: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    summary_text: str | None = Form(default=None),
    tags_json: str = Form(default="[]"),
) -> dict[str, object]:
    """复用求职助手附件解析器导入面经，只持久化转换后的 Markdown。

    这里不会调用会话 Graph，也不会把原文件、临时路径或二进制内容写入历史记录。无论
    解析、入库成功或失败，临时目录都会在 finally 中清理。
    """

    actor = get_request_actor()
    services = get_career_services(request)
    attachments = []
    try:
        try:
            tags_value = json.loads(tags_json)
        except json.JSONDecodeError as exc:
            raise ValueError("标签格式无效，请传入 JSON 字符串数组") from exc
        if not isinstance(tags_value, list) or not all(
            isinstance(item, str) for item in tags_value
        ):
            raise ValueError("标签必须是字符串数组")

        attachment = await services.temporary_attachment_store.save_upload(
            source_file,
            _upload_kind(source_file, is_resume=True),
        )
        attachments.append(attachment)
        parsed = services.attachment_parser.parse(attachment)
        inferred = InterviewMaterialMetadataExtractor().extract(
            parsed.extracted_text,
            filename=attachment.original_filename,
            source_platform=source_platform,
        )
        effective_company_name = company_name.strip() or inferred.company_name or "待归档公司"
        effective_role_name = role_name.strip() or inferred.role_name or "未识别岗位"
        markdown_content = _interview_markdown_from_parsed_attachment(parsed.extracted_text)
        effective_tags = tuple(dict.fromkeys([*tags_value, *inferred.tags]))
        experience = services.interview_library_service.ingest(
            actor.organization_id,
            InterviewExperienceDraft(
                company_name=effective_company_name,
                role_name=effective_role_name,
                interview_date=interview_date or inferred.interview_date,
                markdown_content=markdown_content,
                source_type=InterviewSourceType.MANUAL_UPLOAD,
                source_platform=source_platform or inferred.source_platform,
                source_url=source_url,
                summary_text=summary_text or inferred.summary_text,
                tags=effective_tags,
            ),
            trigger_type=IngestionTriggerType.MANUAL_UPLOAD,
            created_by_actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
        )
    except OSError as exc:
        raise _temporary_attachment_service_unavailable(exc) from exc
    except (LookupError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))
    return _interview_experience_payload(experience, actor=actor)


@router.put("/interview-library/experiences/{experience_id}")
def update_interview_experience(
    experience_id: UUID,
    request_body: UpdateInterviewExperienceRequest,
    request: Request,
) -> dict[str, object]:
    """保存手动编辑的 Markdown，同时以同一版本重建检索切片。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        experience = services.interview_library_service.update_markdown(
            actor.organization_id,
            experience_id,
            actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
            company_name=request_body.company_name,
            role_name=request_body.role_name,
            markdown_content=request_body.markdown_content,
            summary_text=request_body.summary_text,
            tags=tuple(request_body.tags),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _interview_experience_payload(experience, actor=actor)


@router.delete("/interview-library/experiences/{experience_id}")
def delete_interview_experience(
    experience_id: UUID,
    request: Request,
) -> dict[str, bool]:
    """永久删除一份面经；数据库外键同步清理 RAG 切片与来源记录。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        services.interview_library_service.delete_experience(
            actor.organization_id,
            experience_id,
            actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/interview-library/mentions")
def search_interview_library_mentions(
    request: Request,
    query: str,
    limit: int = 8,
) -> dict[str, object]:
    """为求职助手的 @ 面经选择器返回轻量候选项。"""

    if not query.strip():
        return {"items": []}
    if limit < 1 or limit > 20:
        raise HTTPException(status_code=422, detail="面经候选数量必须位于 1 到 20 之间")

    actor = get_request_actor()
    services = get_career_services(request)
    items = services.interview_library_service.search_for_mention(
        actor.organization_id,
        query,
        limit=limit,
    )
    return {
        "items": [
            {
                "id": str(item.id),
                "company_name": item.company_name,
                "role_name": item.role_name,
                "job_name": item.job_name,
                "interview_date": (
                    item.interview_date.isoformat()
                    if item.interview_date is not None
                    else None
                ),
                "summary_text": item.summary_text,
                "tags": list(item.tags),
            }
            for item in items
        ]
    }


@router.get("/skills/mentions")
def search_skill_mentions(
    request: Request,
    query: str = Query(default="", max_length=80),
) -> dict[str, object]:
    """返回输入框可调用的 Skill 元数据，不提前读取 SKILL.md 正文。"""

    runtime: CareerSkillRuntime = request.app.state.career_skill_runtime
    items = runtime.list_mentions(query, limit=8)
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "description_zh": item.description_zh,
                "source_label": item.source_label,
                "command": f"/{item.name}",
            }
            for item in items
        ],
        "total": len(items),
    }


@router.get("/interview-library/search")
def retrieve_interview_library_chunks(
    request: Request,
    query: str,
    limit: int | None = None,
) -> dict[str, object]:
    """按 RAG 策略检索面经证据片段，供面经库与后续 ``@面经`` 对话共用。

    端点只返回必要的、带来源元数据的文本片段；不会返回原始上传文件、Embedding
    向量或模型凭据。未配置向量模型时会透明退化为 PostgreSQL 关键词检索，保证面经库
    在本地开发和生产冷启动时仍然可用。
    """

    normalized_query = query.strip()
    if not normalized_query:
        raise HTTPException(status_code=422, detail="检索关键词不能为空")

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        result = services.interview_retrieval_service.retrieve(
            actor.organization_id,
            normalized_query,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "query": result.query,
        "retrieval_mode": result.retrieval_mode,
        "degraded_reason": result.degraded_reason,
        "items": [_interview_chunk_candidate_payload(item) for item in result.candidates],
    }


@router.get("/model-profiles")
def list_model_profiles(request: Request) -> dict[str, object]:
    """列出模型设置页档案及其无密钥可用性状态。"""

    actor = get_request_actor()
    read_services = get_career_read_services(request)
    availability = read_services.model_gateway.list_availability(actor.organization_id)
    return {"items": [_availability_payload(item) for item in availability]}


@router.get("/free-model-catalog")
def list_free_model_catalog(request: Request) -> dict[str, object]:
    """返回官方免费额度目录与平台当前可供访客直接使用的状态。

    该接口只返回公开的接入说明和无密钥的模型档案摘要。任何 API Key 都留在服务端，
    因此已启用的免费连接可安全复用于后续登录体系下的普通访客。
    """

    actor = get_request_actor()
    read_services = get_career_read_services(request)
    availability = read_services.model_gateway.list_availability(actor.organization_id)
    return {"items": build_free_model_catalog_payload(availability)}


@router.put("/model-profiles/{profile_key}")
def upsert_model_profile(
    profile_key: str,
    request_body: UpsertModelProfileRequest,
    request: Request,
) -> dict[str, object]:
    """创建或更新一个不含密钥的模型档案。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        profile = services.model_profile_repository.upsert_profile(
            actor.organization_id,
            ModelProfileDraft(
                profile_key=profile_key,
                display_name=request_body.display_name,
                provider_key=request_body.provider_key,
                model_id=request_body.model_id,
                capabilities=frozenset(request_body.capabilities),
                cost_tier=request_body.cost_tier,
                priority=request_body.priority,
                enabled=request_body.enabled,
                api_base_url=request_body.api_base_url,
                provider_website_url=request_body.provider_website_url,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    availability = next(
        item
        for item in services.model_gateway.list_availability(actor.organization_id)
        if item.profile.id == profile.id
    )
    return _availability_payload(availability)


@router.post("/model-connections/test")
def test_model_connection(
    request_body: ModelConnectionRequest,
    request: Request,
) -> dict[str, object]:
    """真实验证浏览器填写的模型连接，但不写入档案或 API Key。"""

    services = get_career_services(request)
    response_preview = _verify_model_connection(services, request_body)
    return {
        "verified": True,
        "provider_key": request_body.provider_key,
        "model_id": request_body.model_id,
        "response_preview": response_preview[:120],
    }


@router.put("/model-connections/{profile_key}")
def save_model_connection(
    profile_key: str,
    request_body: ModelConnectionRequest,
    request: Request,
) -> dict[str, object]:
    """保存前再次真实验证连接，并原子保存档案和加密 API Key。"""

    actor = get_request_actor()
    services = get_career_services(request)
    _verify_model_connection(services, request_body)
    try:
        profile = services.model_profile_repository.upsert_profile(
            actor.organization_id,
            ModelProfileDraft(
                profile_key=profile_key,
                display_name=request_body.display_name,
                provider_key=request_body.provider_key,
                model_id=request_body.model_id,
                capabilities=frozenset(request_body.capabilities),
                cost_tier=request_body.cost_tier,
                priority=request_body.priority,
                enabled=request_body.enabled,
                api_base_url=request_body.api_base_url,
                provider_website_url=request_body.provider_website_url,
            ),
            api_key=request_body.api_key,
        )
    except (SQLAlchemyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    availability = next(
        item
        for item in services.model_gateway.list_availability(actor.organization_id)
        if item.profile.id == profile.id
    )
    return _availability_payload(availability)


@router.post("/model-resolution")
def resolve_model(
    request_body: ResolveModelRequest,
    request: Request,
) -> dict[str, object]:
    """预览当前选择会路由到哪个模型，但不触发真实模型调用。"""

    actor = get_request_actor()
    services = get_career_services(request)
    try:
        resolution = services.model_gateway.resolve(
            actor.organization_id,
            ModelSelectionRequest(
                mode=request_body.selection_mode,
                profile_id=request_body.model_profile_id,
                required_capabilities=frozenset(request_body.required_capabilities),
            ),
        )
    except (LookupError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _resolution_payload(resolution)


def _verify_model_connection(
    services: CareerAssistantServices,
    request_body: ModelConnectionRequest,
) -> str:
    """验证 OpenAI-compatible 请求能真实完成，失败时不保存任何用户配置。"""

    api_base_url = (request_body.api_base_url or "").strip().rstrip("/")
    if api_base_url and not api_base_url.startswith("https://"):
        raise HTTPException(
            status_code=422,
            detail="请求地址必须使用 HTTPS，且填写到 API Base URL 层级，不要包含 /chat/completions",
        )
    if api_base_url.endswith("/chat/completions"):
        raise HTTPException(
            status_code=422,
            detail="请求地址不应包含 /chat/completions，系统会自动补全该路径",
        )

    try:
        return services.model_connection_client.test_connection(
            ModelConnectionTarget(
                provider_key=request_body.provider_key,
                model_id=request_body.model_id,
                api_base_url=api_base_url or None,
            ),
            request_body.api_key,
        )
    except ModelInvocationError as exc:
        raise HTTPException(status_code=422, detail=f"模型连接测试未通过：{exc}") from exc


async def _stream_career_response(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
) -> AsyncIterator[str]:
    """排队取得执行权，再为真实处理流补充 SSE 心跳。"""

    reservation = await services.turn_coordinator.reserve(
        inbound_message.conversation_id,
    )
    lease = None
    processing_handed_off = False
    try:
        if reservation.is_queued:
            if reservation.queue_scope == "conversation":
                waiting_label = (
                    f"当前会话已有任务，前方还有 {reservation.queue_position} 个 Turn，"
                    "正在排队等待"
                )
            else:
                waiting_label = "当前求职任务较多，已进入全局队列等待"
            yield _sse_event("status", {"message": waiting_label})
            yield _sse_event(
                "progress",
                {
                    "key": "turn_queued",
                    "label": waiting_label,
                    "state": "running",
                },
            )

        lease = await reservation.acquire()
        if reservation.is_queued:
            yield _sse_event(
                "progress",
                {
                    "key": "turn_queued",
                    "label": "排队结束，开始处理本轮任务",
                    "state": "completed",
                },
            )

        processing_handed_off = True
        async for event in _stream_events_with_heartbeats(
            _stream_career_response_events(
                services,
                inbound_message,
                attachment_count=attachment_count,
            ),
            heartbeat_seconds=services.stream_heartbeat_seconds,
            expected_turn_seconds=services.turn_expected_seconds,
            on_processing_complete=lease.release,
        ):
            yield event
    finally:
        if not processing_handed_off:
            if lease is not None:
                lease.release()
            else:
                await reservation.cancel()
            services.temporary_attachment_store.cleanup(inbound_message.attachments)


async def _run_queued_career_turn(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
):
    """让非流式入口与 SSE 入口共用同一套会话和全局排队规则。"""

    return await services.turn_coordinator.run_sync(
        inbound_message.conversation_id,
        lambda: _run_career_turn(
            services,
            inbound_message,
            attachment_count=attachment_count,
        ),
    )


def _run_career_turn(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
):
    """执行一次非流式求职对话，并建立与流式接口一致的根 Trace。"""

    inbound_message = _attach_conversation_context(services, inbound_message)

    def execute_turn():
        intake_result = services.intake_graph.run(inbound_message)
        response_result = services.response_runner.run(
            inbound_message,
            intake_result,
        )
        return intake_result, response_result

    return trace_operation(
        run_name="career.turn",
        run_type="chain",
        inputs={
            "attachment_count": attachment_count,
            "interview_evidence_count": len(inbound_message.interview_evidence),
            "has_text": bool(inbound_message.text.strip()),
            "has_job_url": bool((inbound_message.job_url or "").strip()),
        },
        metadata={
            "component": "career_assistant",
            "stage": "turn",
            "streaming": False,
            "privacy_mode": "metadata_only",
        },
        tags=("career", "agent", "turn"),
        execute=execute_turn,
        summarize=lambda _: {"status": "completed"},
    )


def _attach_conversation_context(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
) -> CareerInboundMessage:
    """在服务端按会话归属读取已确认上下文，浏览器不能直接伪造 Prompt 数据。"""

    context = services.context_repository.get_conversation_context(
        inbound_message.actor_id,
        inbound_message.conversation_id,
    )
    if context is None:
        return inbound_message
    target = context.target_role
    target_context = ""
    if target is not None:
        target_context = (
            f"公司：{target.company_name}\n"
            f"岗位：{target.role_name}\n"
            f"岗位版本：v{target.version}\n\n"
            f"{target.job_text}"
        )
    return replace(
        inbound_message,
        candidate_profile_context=(
            context.candidate.resume_outline if context.candidate is not None else ""
        ),
        target_role_context=target_context,
        context_binding_version=context.binding_version,
    )


def _stream_career_response_events(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
) -> Iterator[str]:
    """建立一次求职对话的根 Trace，并原样透传浏览器 SSE 事件。"""

    # 该生成器由 SSE 事件泵线程推进；把 PostgreSQL 上下文读取留在这里，避免阻塞
    # FastAPI asyncio 事件循环。
    inbound_message = _attach_conversation_context(services, inbound_message)
    yield from trace_stream(
        run_name="career.turn",
        run_type="chain",
        inputs={
            "attachment_count": attachment_count,
            "interview_evidence_count": len(inbound_message.interview_evidence),
            "has_text": bool(inbound_message.text.strip()),
            "has_job_url": bool((inbound_message.job_url or "").strip()),
        },
        metadata={
            "component": "career_assistant",
            "stage": "turn",
            "privacy_mode": "metadata_only",
        },
        tags=("career", "agent", "turn"),
        execute=lambda: _stream_career_response_events_untraced(
            services,
            inbound_message,
            attachment_count=attachment_count,
        ),
        summarize_chunk=lambda chunk, index: {
            "event_index": index,
            "event_bytes": len(chunk.encode("utf-8")),
        },
    )


def _stream_career_response_events_untraced(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
) -> Iterator[str]:
    """将输入处理、模型增量和最终落库结果组织为浏览器可消费的 SSE 事件。

    事件约定保持稳定：``progress`` 是经 LangGraph 节点确认过的处理进展，``accepted``
    表示用户消息已写入会话历史，``delta`` 是模型正文 Token，``done`` 是本轮最终结果。
    前端不需要猜测请求是否仍在运行，也不会把临时消息重复追加为正式消息。
    """

    if attachment_count:
        initial_message = "正在校验附件并建立本轮上下文…"
    else:
        initial_message = "正在建立本轮对话上下文…"
    yield _sse_event("status", {"message": initial_message})
    yield _sse_event(
        "progress",
        {
            "key": "intake_started",
            "label": initial_message,
            "state": "running",
        },
    )

    try:
        intake_result = None
        for intake_event in services.intake_graph.stream(inbound_message):
            if intake_event.step is not None:
                yield _sse_event(
                    "progress",
                    _intake_progress_payload(
                        intake_event.step,
                        attachment_count=attachment_count,
                    ),
                )
            if intake_event.result is not None:
                intake_result = intake_event.result
        if intake_result is None:
            raise RuntimeError("输入处理未返回最终结果")
    except (LookupError, ValueError, RuntimeError) as exc:
        services.temporary_attachment_store.cleanup(inbound_message.attachments)
        yield _sse_event("error", {"detail": str(exc)})
        return
    except Exception:
        services.temporary_attachment_store.cleanup(inbound_message.attachments)
        yield _sse_event("error", {"detail": "材料处理出现异常，请稍后重新提交。"})
        return

    yield _sse_event(
        "accepted",
        {
            "message": _message_payload(intake_result.persisted_message),
            "turn": _turn_payload(intake_result.active_turn.turn),
        },
    )
    yield _sse_event("status", {"message": "材料处理完成，正在调用模型…"})
    yield _sse_event(
        "progress",
        {
            "key": "model_generation",
            "label": "上下文已就绪，正在组织分析建议…",
            "state": "running",
        },
    )
    try:
        for event in services.response_runner.stream(inbound_message, intake_result):
            if event.event_type == "delta" and event.content:
                yield _sse_event("delta", {"content": event.content})
                continue
            if event.event_type == "progress" and event.content:
                yield _sse_event(
                    "progress",
                    {
                        "key": (
                            "skill_execution"
                            if "Skill" in event.content
                            else "model_retry"
                        ),
                        "label": event.content,
                        "state": "running",
                    },
                )
                continue
            if event.result is None:
                yield _sse_event("error", {"detail": "模型响应未生成最终结果。"})
                return
            payload = _stream_result_payload(
                intake_result,
                event.result,
                attachment_count=attachment_count,
            )
            if event.event_type == "done":
                yield _sse_event(
                    "progress",
                    {
                        "key": "model_generation",
                        "label": "分析建议已生成",
                        "state": "completed",
                    },
                )
            yield _sse_event(event.event_type, payload)
            return
    except Exception:
        yield _sse_event(
            "error",
            {"detail": "模型响应处理出现异常，请稍后重试。"},
        )


def _create_career_streaming_response(
    services: CareerAssistantServices,
    inbound_message: CareerInboundMessage,
    *,
    attachment_count: int = 0,
) -> StreamingResponse:
    """立即建立 SSE，由异步生成器发送排队状态并等待执行权。"""

    return StreamingResponse(
        _stream_career_response(
            services,
            inbound_message,
            attachment_count=attachment_count,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


_SSE_STREAM_END = object()


async def _stream_events_with_heartbeats(
    events: Iterator[str],
    *,
    heartbeat_seconds: float,
    expected_turn_seconds: float,
    on_processing_complete: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """用 asyncio.Queue 将阻塞式处理流桥接为可保活的 SSE 流。

    Docling、OCR 或上游模型在开始产出前可能长时间无数据。这里用一个守护线程
    消费尚未异步化的业务事件，FastAPI 事件循环按固定周期输出 SSE 注释帧，浏览器与
    代理会据此保持连接。
    注释帧不包含业务数据，前端无需也不会展示它。超过预期耗时仅给出一次安全进度
    提示，绝不伪造模型思考过程，也不从线程外强制中断外部请求。
    """

    output_queue: asyncio.Queue[object] = asyncio.Queue()
    client_disconnected = Event()
    loop = asyncio.get_running_loop()

    def publish_from_thread(item: object) -> None:
        """只把仍有浏览器消费者的事件安全投递回 FastAPI 事件循环。"""

        if client_disconnected.is_set():
            return
        try:
            loop.call_soon_threadsafe(output_queue.put_nowait, item)
        except RuntimeError:
            # 应用已经关闭事件循环时不再投递；底层外部调用会按自身超时退出。
            return

    def finish_processing_from_thread() -> None:
        if on_processing_complete is None:
            return
        try:
            loop.call_soon_threadsafe(on_processing_complete)
        except RuntimeError:
            return

    def pump_events() -> None:
        try:
            for event in events:
                # 浏览器断开时，仍让 Graph / 模型流完整走到持久化收口；只丢弃
                # 已无人消费的 SSE 帧，避免未完成 Turn 在刷新页面后永久停在 running。
                publish_from_thread(event)
        except BaseException as exc:  # 保证 SSE 可得到友好错误，不让线程异常丢失。
            publish_from_thread(exc)
        finally:
            publish_from_thread(_SSE_STREAM_END)
            finish_processing_from_thread()

    worker = Thread(
        target=pump_events,
        name="career-sse-event-pump",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        if on_processing_complete is not None:
            on_processing_complete()
        raise
    started_at = monotonic()
    expected_duration_notified = False

    try:
        while True:
            try:
                queued_event = await asyncio.wait_for(
                    output_queue.get(),
                    timeout=heartbeat_seconds,
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                if (
                    not expected_duration_notified
                    and monotonic() - started_at >= expected_turn_seconds
                ):
                    expected_duration_notified = True
                    yield _sse_event(
                        "progress",
                        {
                            "key": "extended_processing",
                            "label": "当前材料处理时间较长，任务仍在继续，请保持页面开启。",
                            "state": "running",
                        },
                    )
                continue

            if queued_event is _SSE_STREAM_END:
                return
            if isinstance(queued_event, BaseException):
                yield _sse_event(
                    "error",
                    {"detail": "服务处理出现异常，请稍后重新提交。"},
                )
                return
            if not isinstance(queued_event, str):
                yield _sse_event(
                    "error",
                    {"detail": "服务流返回了无效数据，请稍后重新提交。"},
                )
                return
            yield queued_event
    finally:
        client_disconnected.set()


def _intake_progress_payload(
    step: AgentStepName,
    *,
    attachment_count: int,
) -> dict[str, str]:
    """将已完成的 Graph 节点翻译为面向用户的安全进展摘要。"""

    labels = {
        AgentStepName.VALIDATE_INPUT: "输入校验完成，正在建立本轮上下文…",
        AgentStepName.BUILD_CONTEXT: (
            "上下文已建立，正在解析附件内容…"
            if attachment_count
            else "上下文已建立，正在读取职位信息…"
        ),
        AgentStepName.PARSE_MATERIAL: (
            "附件内容已提取，正在读取职位信息…"
            if attachment_count
            else "当前没有附件，正在读取职位信息…"
        ),
        AgentStepName.EXTRACT_JOB_DESCRIPTION: "职位信息处理完成，正在整理对话上下文…",
        AgentStepName.REDACT_SENSITIVE_DATA: "隐私处理完成，正在保存本轮消息…",
        AgentStepName.PERSIST_HISTORY: "问题已保存，准备调用模型…",
        AgentStepName.CLEANUP_TEMPORARY_FILES: "临时材料已清理，正在生成回复…",
    }
    return {
        "key": step.value,
        "label": labels[step],
        "state": "completed",
    }


def _stream_result_payload(
    intake_result,
    response_result,
    *,
    attachment_count: int,
) -> dict[str, object]:
    """将流结束后的持久化记录转换为与既有接口一致的最终负载。"""

    payload: dict[str, object] = {
        "turn": _turn_payload(response_result.turn),
        "message": _message_payload(intake_result.persisted_message),
        "assistant_message": _message_payload(
            response_result.assistant_message,
            _response_model_metadata(response_result),
        ),
        "completed_steps": [step.value for step in intake_result.completed_steps],
        "job_source": _job_source_payload(intake_result),
        "activated_skills": _activated_skill_payloads(
            intake_result.model_context.activated_skills,
        ),
        "skill_executions": _skill_execution_payloads(response_result.skill_executions),
    }
    if attachment_count:
        payload["attachment_processing"] = {
            "temporary_only": True,
            "count": attachment_count,
            "text_characters": _analyzed_material_characters(
                intake_result.model_context,
            ),
            "resume_outline_characters": len(
                intake_result.model_context.redacted_resume_outline,
            ),
            "notices": list(intake_result.model_context.document_processing_notices),
            "items": [
                _attachment_processing_summary_payload(item)
                for item in intake_result.model_context.attachment_processing_summaries
            ],
            "pdf_without_extractable_text_count": (
                intake_result.model_context.pdf_without_extractable_text_count
            ),
            "cleaned_after_turn": True,
        }
    return payload


def _activated_skill_payloads(activated_skills) -> list[dict[str, object]]:
    """只返回调用结果元数据，绝不把 SKILL.md 正文暴露给浏览器。"""

    return [
        {
            "id": skill.skill_id,
            "name": skill.name,
            "description": skill.description,
            "status": "mounted",
            "execution_mode": skill.execution_mode.value,
            "tool_names": list(skill.tool_names),
            "invocation_source": skill.invocation_source,
            "primary": skill.primary,
        }
        for skill in activated_skills
    ]


def _skill_execution_payloads(executions) -> list[dict[str, object]]:
    """返回真实工具执行摘要，不向浏览器暴露工具原始 JSON。"""

    return [
        {
            "skill_name": execution.skill_name,
            "tool_name": execution.tool_name,
            "execution_mode": execution.execution_mode,
            "status": execution.status,
            "result_count": execution.result_count,
            "message": execution.message,
        }
        for execution in executions
    ]


def _job_source_payload(intake_result) -> dict[str, str | None]:
    """返回职位链接的安全处理状态，不泄露原始链接或页面正文。"""

    return {
        "status": intake_result.job_source_status,
        "message": intake_result.job_source_message,
    }


def _analyzed_material_characters(model_context) -> int:
    """统计实际提供给模型的附件文本，包含已归类的简历提纲。"""

    return len(
        model_context.redacted_material_text
        + model_context.redacted_resume_outline,
    )


def _attachment_processing_summary_payload(summary) -> dict[str, object]:
    """将无敏感的附件处理摘要转换为前端稳定 API 格式。"""

    return {
        "kind": summary.kind.value,
        "page_count": summary.page_count,
        "processing_route": summary.processing_route,
        "parser_name": summary.parser_name,
        "parser_status": summary.parser_status,
        "native_text_quality_score": summary.native_text_quality_score,
        "issue_codes": list(summary.issue_codes),
    }


def _sse_event(event_name: str, payload: dict[str, object]) -> str:
    """序列化单个 SSE 数据帧，确保中文正文不会被转义为乱码。"""

    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _upload_kind(upload: UploadFile, *, is_resume: bool) -> AttachmentKind:
    """按上传声明的媒体类型选择受限材料类别，后续由临时存储层双重校验。"""

    media_type = (upload.content_type or "").lower()
    if media_type == "application/pdf":
        return AttachmentKind.RESUME_PDF if is_resume else AttachmentKind.JOB_DESCRIPTION_FILE
    if media_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        return (
            AttachmentKind.RESUME_DOCUMENT
            if is_resume
            else AttachmentKind.JOB_DESCRIPTION_FILE
        )
    if media_type in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/x-ms-bmp",
        "image/tiff",
    }:
        return (
            AttachmentKind.RESUME_IMAGE
            if is_resume
            else AttachmentKind.JOB_DESCRIPTION_IMAGE
        )
    raise ValueError(
        "附件类型不受支持；支持 PDF、Word（DOC/DOCX）、Excel（XLS/XLSX）、JPG、PNG、WebP、BMP 或 TIFF",
    )


def _temporary_attachment_service_unavailable(exc: OSError) -> HTTPException:
    """把临时附件目录故障转换为不暴露宿主路径的服务不可用响应。"""

    logger.warning("临时附件服务不可用：%s", type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="附件临时处理服务暂时不可用，请稍后重试；原始文件未被保存。",
    )


def _interview_markdown_from_parsed_attachment(
    extracted_text: str,
) -> str:
    """返回解析器抽取出的连续正文，不把技术元数据混入面经内容。

    公司、岗位、来源和标签由独立字段持久化。这里保留 Docling/OCR 返回的标题、列表、
    表格与阅读顺序；空解析结果不允许进入知识库，避免后续向量检索命中空资料。
    """

    normalized_text = extracted_text.strip()
    if not normalized_text:
        raise ValueError(
            "未从材料中识别到可用文本；请上传更清晰的文件，或改用粘贴 Markdown 入库",
        )
    return normalized_text


def _collection_job_payload(job) -> dict[str, object]:
    """输出采集任务的稳定 Web 契约，不包含任何第三方登录凭证。"""

    return {
        "id": str(job.id),
        "platform_key": job.platform_key,
        "keyword": job.keyword,
        "requested_limit": job.requested_limit,
        "connector_kind": job.connector_kind.value,
        "status": job.status.value,
        "policy_decision": job.policy_decision,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "metadata": dict(job.metadata_json),
        "started_at": job.started_at.isoformat() if job.started_at is not None else None,
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at is not None else None
        ),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def _collection_candidate_payload(candidate) -> dict[str, object]:
    """输出候选资料的可审核正文；原始网页 HTML 和会话均不属于此契约。"""

    return {
        "id": str(candidate.id),
        "collection_job_id": str(candidate.collection_job_id),
        "source_url": candidate.source_url,
        "canonical_url": candidate.canonical_url,
        "source_platform": candidate.source_platform,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "published_at": (
            candidate.published_at.isoformat()
            if candidate.published_at is not None
            else None
        ),
        "markdown_content": candidate.extracted_markdown,
        "status": candidate.status.value,
        "error_code": candidate.error_code,
        "error_message": candidate.error_message,
        "metadata": dict(candidate.metadata_json),
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    }


def _interview_experience_payload(experience, *, sources=(), actor=None) -> dict[str, object]:
    """将面经领域记录转换为稳定的前端 JSON，不返回检索向量。"""

    can_write = bool(
        actor is not None
        and (
            actor.role is PlatformRole.ADMIN
            or getattr(experience, "created_by_actor_id", None) == actor.actor_id
        )
    )
    return {
        "id": str(experience.id),
        "company_id": str(experience.company_id),
        "company_name": experience.company_name,
        "job_name": experience.job_name,
        "role_name": experience.role_name,
        "interview_date": (
            experience.interview_date.isoformat()
            if experience.interview_date is not None
            else None
        ),
        "source_type": experience.source_type.value,
        "source_platform": experience.source_platform,
        "source_url": experience.source_url,
        "summary_text": experience.summary_text,
        "markdown_content": experience.markdown_content,
        "tags": list(experience.tags),
        "status": experience.status.value,
        "chunking_version": experience.chunking_version,
        "indexed_at": (
            experience.indexed_at.isoformat()
            if experience.indexed_at is not None
            else None
        ),
        "created_at": experience.created_at.isoformat(),
        "updated_at": experience.updated_at.isoformat(),
        "can_write": can_write,
        "sources": [
            {
                "id": str(source.id),
                "canonical_url": source.canonical_url,
                "source_url": source.source_url,
                "source_platform": source.source_platform,
                "is_primary": source.is_primary,
                "discovery_keywords": list(source.discovery_keywords),
                "first_seen_at": source.first_seen_at.isoformat(),
                "last_seen_at": source.last_seen_at.isoformat(),
            }
            for source in sources
        ],
    }


def _interview_chunk_candidate_payload(candidate) -> dict[str, object]:
    """将 RAG 候选片段转换为可引用的前端契约，不泄露向量与内部检索实现。"""

    return {
        "chunk_id": str(candidate.chunk.id),
        "experience_id": str(candidate.chunk.experience_id),
        "chunk_index": candidate.chunk.chunk_index,
        "company_name": candidate.company_name,
        "job_name": candidate.job_name,
        "role_name": candidate.role_name,
        "interview_date": (
            candidate.interview_date.isoformat()
            if candidate.interview_date is not None
            else None
        ),
        "source_url": candidate.source_url,
        "heading_path": candidate.chunk.heading_path.split(" > "),
        "content": candidate.chunk.content_text,
        "citation": " · ".join(
            part
            for part in (
                candidate.company_name,
                candidate.job_name,
                candidate.chunk.heading_path,
            )
            if part
        ),
    }


def _build_interview_evidence(
    services: CareerAssistantServices,
    actor: CareerRequestActor,
    experience_ids: list[UUID] | tuple[UUID, ...],
    query: str,
    *,
    conversation_id: UUID | None = None,
) -> tuple[InterviewEvidence, ...]:
    """将显式选择或岗位驱动的自动检索转换为可追溯的最小 RAG 证据。

    前端只能提交面经 ID；服务端重新校验资料存在性。未显式选择时，只对面试准备意图
    使用当前会话的公司、岗位与问题自动检索，避免普通闲聊被无关面经污染。
    """

    normalized_ids = tuple(dict.fromkeys(experience_ids))
    if len(normalized_ids) > 5:
        raise ValueError("单轮最多引用 5 份面经资料")

    for experience_id in normalized_ids:
        experience = services.interview_library_repository.get_experience(
            actor.organization_id,
            experience_id,
        )
        if experience is None:
            raise LookupError("引用的面经不存在或无访问权限")

    context = (
        services.context_repository.get_conversation_context(actor.actor_id, conversation_id)
        if conversation_id is not None
        else None
    )
    if not normalized_ids and (
        context is None
        or context.target_role is None
        or not _is_interview_preparation_query(query)
    ):
        return ()
    query_parts = []
    if context is not None and context.target_role is not None:
        query_parts.extend(
            (
                context.target_role.company_name,
                context.target_role.role_name,
            )
        )
    query_parts.append(query.strip() or "面经核心信息")
    query_for_retrieval = " ".join(part for part in query_parts if part).strip()[:360]
    retrieval_result = services.interview_retrieval_service.retrieve(
        actor.organization_id,
        query_for_retrieval,
        limit=6 if normalized_ids else 4,
        experience_ids=normalized_ids,
    )
    evidence: list[InterviewEvidence] = []
    for candidate in retrieval_result.candidates:
        heading = candidate.chunk.parent_heading or "面经片段"
        citation = f"[面经：{candidate.company_name} · {candidate.job_name} · {heading}]"
        evidence.append(
            InterviewEvidence(
                experience_id=candidate.chunk.experience_id,
                citation=citation,
                content=candidate.chunk.contextual_content[:1_600],
                source_url=candidate.source_url,
            ),
        )
    return tuple(evidence)


def _is_interview_preparation_query(query: str) -> bool:
    """只在面试准备意图下自动检索，避免普通职业闲聊被无关面经污染。"""

    normalized = query.casefold()
    markers = (
        "面试",
        "面经",
        "追问",
        "怎么答",
        "如何回答",
        "会问",
        "考察",
        "准备",
        "interview",
    )
    return any(marker in normalized for marker in markers)


def _conversation_payload(conversation) -> dict[str, object]:
    """把仓储会话模型转换为 API JSON。"""

    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "status": conversation.status,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "archived_at": conversation.archived_at.isoformat() if conversation.archived_at else None,
    }


def _candidate_profile_payload(profile) -> dict[str, object]:
    """返回可用于启动向导的基准简历元数据，不下发完整简历正文。"""

    return {
        "id": str(profile.id),
        "display_name": profile.display_name,
        "source_filename": profile.source_filename,
        "version": profile.version,
        "created_at": profile.created_at.isoformat(),
    }


def _greeting_payload(result: GreetingGenerationResult) -> dict[str, object]:
    """只返回审核需要的文案和证据摘要，不下发简历与完整 JD。"""

    return {
        "job_key": result.job_key,
        "message": result.message,
        "resume_evidence": [
            {"id": item.id, "summary": item.summary}
            for item in result.resume_evidence
        ],
        "jd_highlights": [
            {"id": item.id, "summary": item.summary}
            for item in result.jd_highlights
        ],
        "warnings": list(result.warnings),
        "model": {
            "provider_key": result.provider_key,
            "model_id": result.model_id,
        },
    }


def _target_role_payload(
    role,
    *,
    include_text: bool = True,
    requirements: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    effective_requirements = requirements if requirements is not None else role.requirements
    payload: dict[str, object] = {
        "id": str(role.id),
        "company_name": role.company_name,
        "role_name": role.role_name,
        "source_kind": role.source_kind,
        "source_label": role.source_label,
        "version": role.version,
        "requirements": list(effective_requirements),
        "created_at": role.created_at.isoformat(),
    }
    if include_text:
        payload["job_text"] = role.job_text
    return payload


def _conversation_context_payload(context, assessment_record=None) -> dict[str, object]:
    # requirement JSON 是创建岗位时的解析快照；当前旧算法仍负责降级展示所需的
    # 技能标签，但岗位匹配结果只读取异步 Judge 的持久化记录。
    requirements = (
        extract_job_requirements(context.target_role.job_text)
        if context.target_role is not None
        else ()
    )
    assessment = _job_assessment_payload(assessment_record)
    return {
        "binding_id": str(context.binding_id),
        "binding_version": context.binding_version,
        "candidate_profile": (
            _candidate_profile_payload(context.candidate)
            if context.candidate is not None
            else None
        ),
        "target_role": (
            _target_role_payload(context.target_role, requirements=requirements)
            if context.target_role is not None
            else None
        ),
        "assessment": assessment,
        "created_at": context.created_at.isoformat(),
    }


def _current_job_assessment(
    services: CareerAssistantReadServices | CareerAssistantServices,
    context,
):
    if context.candidate is None or context.target_role is None:
        return None
    return services.job_assessment_repository.get_current(
        context.candidate.actor_id,
        context.candidate.id,
        context.target_role.id,
    )


def _enqueue_job_assessment(
    services: CareerAssistantServices,
    background_tasks: BackgroundTasks,
    context,
) -> None:
    queued = services.job_assessment_service.enqueue_context(context)
    if queued is None:
        return
    record, _ = queued
    if record.status.value == "queued":
        background_tasks.add_task(
            services.job_assessment_service.run_assessment,
            record.id,
        )


def _refresh_job_assessment_after_response(request: Request, context) -> None:
    """在历史详情已经返回后恢复队列，并检查 Judge 配置版本更新。"""

    try:
        services = get_career_services(request)
        queued = services.job_assessment_service.enqueue_context(context)
        if queued is None:
            return
        record, _ = queued
        if record.status.value == "queued":
            services.job_assessment_service.run_assessment(record.id)
    except Exception:
        logger.exception("历史会话岗位评估后台刷新失败")


def _job_assessment_payload(record) -> dict[str, object] | None:
    if record is None:
        return None
    payload: dict[str, object] = {
        "id": str(record.id),
        "status": record.status.value,
        "attempt_count": record.attempt_count,
        "error_code": record.error_code,
        "judge_model": record.judge_model_id,
        "prompt_version": record.prompt_version,
    }
    if record.result:
        payload.update(record.result)
    return payload


def _message_payload(message, model_metadata=None) -> dict[str, object]:
    """把已脱敏消息转换为 API JSON。"""

    payload = {
        "id": str(message.id),
        "turn_id": str(message.turn_id) if message.turn_id else None,
        "role": message.role.value,
        "content": message.content_text,
        "is_redacted": message.is_redacted,
        "created_at": message.created_at.isoformat(),
    }
    if message.role is MessageRole.ASSISTANT and model_metadata is not None:
        payload["model"] = _model_metadata_payload(model_metadata)
    return payload


def _response_model_metadata(response_result):
    """只为确实调用过 Provider 的回复生成模型标签。"""

    if not response_result.model_was_invoked or response_result.model_resolution is None:
        return None
    resolution = response_result.model_resolution
    reported_model_id = response_result.provider_reported_model_id
    return {
        "provider_key": resolution.profile.provider_key,
        "requested_model_id": resolution.profile.model_id,
        "provider_reported_model_id": reported_model_id,
        "model_id": reported_model_id or resolution.profile.model_id,
        "source": "provider_response" if reported_model_id else "request",
    }


def _model_metadata_payload(model_metadata) -> dict[str, object]:
    if isinstance(model_metadata, dict):
        return model_metadata
    return {
        "provider_key": model_metadata.provider_key,
        "model_id": model_metadata.model_id,
        "requested_model_id": model_metadata.requested_model_id,
        "provider_reported_model_id": model_metadata.provider_reported_model_id,
        "source": model_metadata.source,
    }


def _model_selection_payload(selection: ModelSelectionRequest | None) -> dict[str, object] | None:
    """将会话最近一次模型选择转换为不含密钥的前端状态。"""

    if selection is None:
        return None
    return {
        "mode": selection.mode.value,
        "profile_id": str(selection.profile_id) if selection.profile_id else None,
    }


def _turn_payload(turn) -> dict[str, object]:
    """把运行状态转换为 API JSON，支持后续前端轮询。"""

    return {
        "id": str(turn.id),
        "conversation_id": str(turn.conversation_id),
        "status": turn.status.value,
        "input_kind_codes": list(turn.input_kind_codes),
        "requested_selection_mode": turn.requested_selection_mode.value,
        "created_at": turn.created_at.isoformat(),
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "completed_at": turn.completed_at.isoformat() if turn.completed_at else None,
    }


def _profile_payload(profile: ModelProfileRecord) -> dict[str, object]:
    """把无密钥模型档案转换为 API JSON。"""

    return {
        "id": str(profile.id),
        "profile_key": profile.profile_key,
        "display_name": profile.display_name,
        "provider_key": profile.provider_key,
        "model_id": profile.model_id,
        "capabilities": sorted(capability.value for capability in profile.capabilities),
        "cost_tier": profile.cost_tier.value,
        "priority": profile.priority,
        "enabled": profile.enabled,
        "api_base_url": profile.api_base_url,
        "provider_website_url": profile.provider_website_url,
    }


def _availability_payload(availability: ModelProfileAvailability) -> dict[str, object]:
    """合并档案与策略/凭证状态，不返回环境变量中的值。"""

    return {
        "profile": _profile_payload(availability.profile),
        "readiness": availability.readiness.value,
        "credential_env_name": availability.credential_env_name,
        "blocked_reason": availability.blocked_reason,
    }


def _resolution_payload(resolution: ModelResolution) -> dict[str, object]:
    """输出模型路由预览，供聊天页在发送前展示。"""

    return {
        "profile": _profile_payload(resolution.profile),
        "reason": resolution.reason.value,
        "readiness": resolution.readiness.value,
        "credential_env_name": resolution.credential_env_name,
    }


def _resume_record_payload(record, *, include_content: bool) -> dict[str, object]:
    """将简历优化历史转换为前端契约，不暴露服务器文件路径。"""

    payload: dict[str, object] = {
        "id": str(record.id),
        "job_title": record.job_title,
        "creator_name": record.creator_name,
        "owner_id": str(record.owner_id),
        "source_filename": record.source_filename,
        "source_media_type": record.source_media_type,
        "model_profile_id": str(record.model_profile_id) if record.model_profile_id else None,
        "model_display_name": record.model_display_name,
        "created_at": record.created_at.isoformat(),
    }

    payload["download_urls"] = {
        "original": f"/api/career/resume-optimizations/{str(record.id)}/download/original",
        "original_preview_pdf": f"/api/career/resume-optimizations/{str(record.id)}/download/original-preview",
        "optimized_text": f"/api/career/resume-optimizations/{str(record.id)}/download/optimized-text",
        "optimized_docx": f"/api/career/resume-optimizations/{str(record.id)}/download/optimized-docx",
        "optimized_pdf": f"/api/career/resume-optimizations/{str(record.id)}/download/optimized-pdf",
    }

    if include_content:
        payload.update(
            {
                "original_preview_markdown": record.original_preview_markdown,
                "job_description_text": record.job_description_text,
                "extra_prompt": record.extra_prompt,
                "suggestions": [suggestion.__dict__ for suggestion in record.suggestions],
            },
        )
    return payload


def _resume_owner_scope(user: PlatformUser) -> UUID | None:
    """管理员查看组织记录，普通用户只能查看自己的记录。"""

    return None if user.role is PlatformRole.ADMIN else user.id


@router.post("/resume-optimizations/analyze")
async def analyze_resume_optimization(
    request: Request,
    resume_file: UploadFile = File(...),
    job_description: str = Form(default=""),
    job_screenshots: list[UploadFile] | None = File(default=None),
    extra_prompt: str = Form(default=""),
    model_profile_id: UUID | None = Form(default=None),
    user: PlatformUser = Depends(get_current_platform_user),
) -> dict[str, object]:
    """解析简历和岗位材料并返回建议；分析阶段不创建历史。"""

    services = get_career_services(request)
    attachments = []
    suffix = Path(resume_file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise HTTPException(status_code=400, detail="简历仅支持 PDF、DOC、DOCX")
    try:
        resume = await services.temporary_attachment_store.save_upload(
            resume_file,
            AttachmentKind.RESUME_PDF if suffix == ".pdf" else AttachmentKind.RESUME_DOCUMENT,
        )
        attachments.append(resume)
        screenshots = []
        for upload in job_screenshots or []:
            screenshot = await services.temporary_attachment_store.save_upload(
                upload,
                AttachmentKind.JOB_DESCRIPTION_IMAGE,
            )
            screenshots.append(screenshot)
            attachments.append(screenshot)
        result = services.resume_optimization_service.analyze(
            user.organization_id,
            resume,
            job_description,
            tuple(screenshots),
            extra_prompt,
            model_profile_id,
        )
        return {
            "original_markdown": result.original_markdown,
            "job_description_text": result.job_description_text,
            "job_title": result.job_title,
            "analysis_summary": result.analysis_summary,
            "suggestions": [suggestion.__dict__ for suggestion in result.suggestions],
            "model_profile_id": str(result.model_profile_id),
            "model_display_name": result.model_display_name,
            "provider_key": result.provider_key,
            "model_id": result.model_id,
        }
    except (ValueError, LookupError, ModelInvocationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        services.temporary_attachment_store.cleanup(tuple(attachments))


@router.post("/resume-optimizations/generate")
def generate_resume_optimization(
    payload: GenerateOptimizedResumeRequest,
    request: Request,
    user: PlatformUser = Depends(get_current_platform_user),
) -> dict[str, str]:
    """根据用户确认的意见生成新简历；仍不创建历史。"""

    try:
        optimized = get_career_services(request).resume_optimization_service.generate(
            user.organization_id,
            payload.original_markdown,
            payload.job_description_text,
            tuple(item.to_domain() for item in payload.suggestions),
            payload.extra_prompt,
            payload.model_profile_id,
        )
    except (ValueError, LookupError, ModelInvocationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"optimized_markdown": optimized}


@router.post("/resume-optimizations")
async def save_resume_optimization(
    request: Request,
    original_file: UploadFile = File(...),
    original_preview_markdown: str = Form(...),
    job_title: str = Form(...),
    job_description_text: str = Form(...),
    extra_prompt: str = Form(default=""),
    suggestions_json: str = Form(...),
    optimized_markdown: str = Form(...),
    model_profile_id: UUID = Form(...),
    user: PlatformUser = Depends(get_current_platform_user),
) -> dict[str, object]:
    """用户显式保存后创建一条不可变历史。"""

    services = get_career_services(request)
    suffix = Path(original_file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise HTTPException(status_code=400, detail="简历仅支持 PDF、DOC、DOCX")
    descriptor = None
    try:
        suggestion_values = json.loads(suggestions_json)
        if not isinstance(suggestion_values, list):
            raise ValueError("修改意见格式无效")
        suggestions = tuple(
            ResumeSuggestionRequest.model_validate(item).to_domain()
            for item in suggestion_values
        )
        descriptor = await services.temporary_attachment_store.save_upload(
            original_file,
            AttachmentKind.RESUME_PDF if suffix == ".pdf" else AttachmentKind.RESUME_DOCUMENT,
        )
        record = services.resume_optimization_service.save_record(
            organization_id=user.organization_id,
            owner_id=user.id,
            creator_name=user.display_name,
            source_descriptor=descriptor,
            original_preview_markdown=original_preview_markdown,
            job_title=job_title,
            job_description_text=job_description_text,
            extra_prompt=extra_prompt,
            suggestions=suggestions,
            optimized_markdown=optimized_markdown,
            model_profile_id=model_profile_id,
        )
        return _resume_record_payload(record, include_content=True)
    except (ValueError, LookupError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if descriptor is not None:
            services.temporary_attachment_store.cleanup((descriptor,))


@router.get("/resume-optimizations")
def list_resume_optimizations(
    request: Request,
    job_title: str | None = Query(default=None, max_length=160),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=16, ge=1, le=64),
    user: PlatformUser = Depends(get_current_platform_user),
) -> dict[str, object]:
    """分页检索优化历史，并在数据库查询层执行用户范围过滤。"""

    records, total = get_career_services(request).resume_optimization_repository.list_records(
        user.organization_id,
        owner_id=_resume_owner_scope(user),
        job_title=job_title,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_resume_record_payload(item, include_content=False) for item in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/resume-optimizations/{record_id}")
def get_resume_optimization(
    record_id: UUID,
    request: Request,
    user: PlatformUser = Depends(get_current_platform_user),
) -> dict[str, object]:
    """读取某次历史的大画布内容。"""

    record = get_career_services(request).resume_optimization_repository.get(
        user.organization_id,
        record_id,
        owner_id=_resume_owner_scope(user),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="简历优化记录不存在")
    return _resume_record_payload(record, include_content=True)


@router.get("/resume-optimizations/{record_id}/download/{version}")
def download_resume_optimization(
    record_id: UUID,
    version: str,
    request: Request,
    user: PlatformUser = Depends(get_current_platform_user),
) -> FileResponse:
    """下载原始文件或优化后的文本 / DOCX / PDF 文件。"""

    record = get_career_services(request).resume_optimization_repository.get(
        user.organization_id,
        record_id,
        owner_id=_resume_owner_scope(user),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="简历优化记录不存在")
    if version == "original":
        path = Path(record.original_file_path)
        filename = record.source_filename
    elif version == "original-preview":
        path = Path(record.original_preview_pdf_path)
        filename = f"{Path(record.source_filename).stem}-原始预览.pdf"
    elif version == "optimized-text":
        path = Path(record.optimized_text_path)
        filename = f"{record.job_title}-优化简历.txt"
    elif version == "optimized-docx":
        path = Path(record.optimized_docx_path)
        filename = f"{record.job_title}-优化简历.docx"
    elif version == "optimized-pdf":
        path = Path(record.optimized_pdf_path)
        filename = f"{record.job_title}-优化简历.pdf"
    else:
        raise HTTPException(
            status_code=400,
            detail="下载版本仅支持 original、original-preview、optimized-text、optimized-docx、optimized-pdf",
        )
    if not path.is_file():
        raise HTTPException(status_code=404, detail="简历文件不存在")
    return FileResponse(path, filename=filename)
