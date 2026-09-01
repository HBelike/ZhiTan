"""求职助手独立配置读取器。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "career_assistant.yaml"


@dataclass(frozen=True)
class ModelGatewaySettings:
    """模型路由的非敏感配置。

    这里仅保存 API Key 的环境变量名称，例如 ``GROQ_API_KEY``，从不保存 Key 内容。
    """

    allow_paid_profiles: bool
    enable_local_ollama_profile: bool
    provider_credential_env: dict[str, str | None]


@dataclass(frozen=True)
class JobAssessmentSettings:
    """岗位 Judge 的固定模型、Prompt 版本与重试边界。"""

    judge_profile_key: str
    prompt_version: str
    max_attempts: int


@dataclass(frozen=True)
class OnlineAssessmentSettings:
    """线上笔试题目理解、答案生成与隔离执行配置。"""

    problem_extractor_profile_key: str
    answer_profile_key: str
    piston_base_url: str
    request_timeout_seconds: float
    max_test_cases: int
    max_repair_rounds: int


def load_online_assessment_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> OnlineAssessmentSettings:
    """读取线上笔试固定模型与执行器边界，不读取或保存任何 API Key。"""

    root_config = _load_career_root_config(config_path)
    config = root_config.get("online_assessment")
    if not isinstance(config, dict):
        raise ValueError("career_assistant.yaml 缺少 online_assessment 配置段")

    def profile_key(name: str) -> str:
        value = config.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"online_assessment.{name} 必须是非空模型档案键")
        normalized = value.strip().lower()
        if len(normalized) > 64 or not all(
            character.isalnum() or character in {"-", "_"} for character in normalized
        ):
            raise ValueError(f"online_assessment.{name} 格式无效")
        return normalized

    piston_base_url = _read_optional_url(config, "piston_base_url")
    if piston_base_url is None or not piston_base_url.startswith(("http://", "https://")):
        raise ValueError("online_assessment.piston_base_url 必须是 http 或 https 地址")
    max_test_cases = config.get("max_test_cases")
    if isinstance(max_test_cases, bool) or not isinstance(max_test_cases, int) or not 1 <= max_test_cases <= 20:
        raise ValueError("online_assessment.max_test_cases 必须是 1 到 20 之间的整数")
    max_repair_rounds = config.get("max_repair_rounds")
    if isinstance(max_repair_rounds, bool) or not isinstance(max_repair_rounds, int) or not 0 <= max_repair_rounds <= 2:
        raise ValueError("online_assessment.max_repair_rounds 必须是 0 到 2 之间的整数")

    return OnlineAssessmentSettings(
        problem_extractor_profile_key=profile_key("problem_extractor_profile_key"),
        answer_profile_key=profile_key("answer_profile_key"),
        piston_base_url=piston_base_url.rstrip("/"),
        request_timeout_seconds=_read_positive_number(config, "request_timeout_seconds"),
        max_test_cases=max_test_cases,
        max_repair_rounds=max_repair_rounds,
    )


@dataclass(frozen=True)
class CareerRuntimeSettings:
    """Web 进程处理求职 Turn 的运行时边界。

    ``max_concurrent_turns`` 用于保护个人部署实例不被少量长文档请求占满；
    ``stream_heartbeat_seconds`` 用于在 Docling 或云模型尚未返回内容时维持 SSE
    连接。``turn_timeout_seconds`` 是面向用户的预期耗时阈值，而非强制中断开关：
    正在执行的外部 HTTP 请求不能被安全地从线程外杀死，实际中断仍由各阶段的
    transport timeout 负责。
    """

    max_concurrent_turns: int
    turn_timeout_seconds: float
    stream_heartbeat_seconds: float


@dataclass(frozen=True)
class CareerTurnWorkerSettings:
    """PostgreSQL Agent Worker 的进程内并发与租约配置。"""

    worker_id: str
    global_concurrency: int
    worker_concurrency: int
    lease_seconds: float
    heartbeat_seconds: float
    poll_seconds: float

    def validate(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("Worker ID 不能为空")
        if not 1 <= self.global_concurrency <= 64:
            raise ValueError("全局 Agent 并发必须在 1 到 64 之间")
        if not 1 <= self.worker_concurrency <= self.global_concurrency:
            raise ValueError("单 Worker 并发必须在 1 到全局并发之间")
        if self.heartbeat_seconds <= 0:
            raise ValueError("Worker 心跳间隔必须大于零")
        if self.lease_seconds < self.heartbeat_seconds * 3:
            raise ValueError("Worker 租约至少应为心跳间隔的三倍")
        if self.poll_seconds <= 0:
            raise ValueError("Worker 轮询间隔必须大于零")


def load_career_turn_worker_settings() -> CareerTurnWorkerSettings:
    """从环境变量读取跨进程 Agent 队列设置。"""

    import socket

    settings = CareerTurnWorkerSettings(
        worker_id=os.getenv("CAREER_AGENT_WORKER_ID", "").strip()
        or f"{socket.gethostname()}-{os.getpid()}",
        global_concurrency=_read_env_int("CAREER_AGENT_GLOBAL_CONCURRENCY", 8),
        worker_concurrency=_read_env_int("CAREER_AGENT_WORKER_CONCURRENCY", 4),
        lease_seconds=_read_env_float("CAREER_AGENT_LEASE_SECONDS", 90.0),
        heartbeat_seconds=_read_env_float("CAREER_AGENT_HEARTBEAT_SECONDS", 20.0),
        poll_seconds=(
            _read_env_float("CAREER_AGENT_EVENT_POLL_MILLISECONDS", 300.0) / 1000
        ),
    )
    settings.validate()
    return settings


@dataclass(frozen=True)
class CareerMemoryWorkerSettings:
    """会话压缩后台任务的独立轮询与租约配置。"""

    worker_id: str
    poll_seconds: float = 1.0
    lease_seconds: float = 120.0
    worker_concurrency: int = 1

    def validate(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("记忆 Worker ID 不能为空")
        if self.poll_seconds <= 0:
            raise ValueError("记忆 Worker 轮询间隔必须大于零")
        if self.lease_seconds < 10:
            raise ValueError("记忆 Worker 租约不能少于 10 秒")
        if not 1 <= self.worker_concurrency <= 8:
            raise ValueError("记忆 Worker 并发必须在 1 到 8 之间")


def load_career_memory_worker_settings() -> CareerMemoryWorkerSettings:
    import socket

    settings = CareerMemoryWorkerSettings(
        worker_id=os.getenv("CAREER_MEMORY_WORKER_ID", "").strip()
        or f"{socket.gethostname()}-{os.getpid()}-memory",
        poll_seconds=_read_env_float("CAREER_MEMORY_POLL_SECONDS", 1.0),
        lease_seconds=_read_env_float("CAREER_MEMORY_LEASE_SECONDS", 120.0),
        worker_concurrency=_read_env_int("CAREER_MEMORY_WORKER_CONCURRENCY", 1),
    )
    settings.validate()
    return settings


def _read_env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    return value


def _read_env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    return value


@dataclass(frozen=True)
class AttachmentProcessingSettings:
    """临时材料处理的隐私边界配置。"""

    max_size_bytes: int
    ttl_seconds: int
    max_pdf_pages: int
    redaction_enabled: bool = False


@dataclass(frozen=True)
class DocumentUnderstandingSettings:
    """独立文档理解服务的配置。

    该服务负责扫描 PDF 的 OCR、双栏阅读顺序和表格结构恢复。解析服务地址是部署配置，
    API Key 只通过环境变量读取，不写入 YAML、数据库或历史对话。
    """

    enabled: bool
    service_base_url: str | None
    api_key_env: str | None
    request_timeout_seconds: float
    force_ocr: bool
    table_mode: str
    max_attempts: int = 1
    retry_backoff_seconds: float = 0.6


@dataclass(frozen=True)
class CloudVisionConnectionSettings:
    """一条平台级图片理解连接的非敏感配置。

    图片理解属于求职助手的基础能力，不是聊天页可由访客切换的模型档案。
    ``api_key_env`` 和 ``api_base_url_env`` 只保存环境变量名称，真实 Key 始终
    留在服务端 Secret 中。所有连接均要求兼容 OpenAI Chat Completions 的图片
    输入格式，从而可以替换 Qwen、Gemini 或其他兼容 Provider，而不改变附件链路。
    """

    connection_key: str
    enabled: bool
    provider_key: str
    model_id: str
    api_key_env: str
    api_base_url_env: str
    request_timeout_seconds: float
    max_completion_tokens: int


@dataclass(frozen=True)
class CloudVisionSettings:
    """平台内置图片理解服务的连接池配置。

    ``active_connection_key`` 决定当前生产调用的视觉模型；``fallback_connection_keys``
    仅在管理员显式填写后才会生效。这样既能在模型下线时快速切换，又不会把免费
    聊天模型目录误当作图片解析服务，更不会由普通访客改变平台成本与密钥边界。
    """

    enabled: bool
    active_connection_key: str
    fallback_connection_keys: tuple[str, ...]
    connections: tuple[CloudVisionConnectionSettings, ...]
    max_attempts: int = 1
    retry_backoff_seconds: float = 0.8

    def connection_for(self, connection_key: str) -> CloudVisionConnectionSettings:
        """按稳定连接标识返回配置；启动期即校验失败，避免请求期才出现歧义。"""

        for connection in self.connections:
            if connection.connection_key == connection_key:
                return connection
        raise LookupError(f"未找到平台图片理解连接：{connection_key}")

    def resolution_order(self) -> tuple[CloudVisionConnectionSettings, ...]:
        """返回主连接与管理员明确允许的故障回退顺序。"""

        return tuple(
            self.connection_for(connection_key)
            for connection_key in (
                self.active_connection_key,
                *self.fallback_connection_keys,
            )
        )


@dataclass(frozen=True)
class LegacyOfficeConversionSettings:
    """旧版 DOC/XLS 经 Gotenberg 转为 PDF 的独立服务配置。"""

    enabled: bool
    service_base_url: str | None
    request_timeout_seconds: float
    max_converted_size_bytes: int


@dataclass(frozen=True)
class ResponseGenerationSettings:
    """模型回复的时长与持久化边界。

    系统输出保护上限、HTTP 超时和历史回复长度从 YAML 统一读取；单轮真实
    上限还会按模型能力和上下文剩余空间收紧。API Key 不属于该配置。
    """

    max_completion_tokens: int
    request_timeout_seconds: float
    max_persisted_response_characters: int
    max_attempts: int = 1
    retry_backoff_seconds: float = 0.8


@dataclass(frozen=True)
class InterviewEmbeddingSettings:
    """面经库向量化连接的非敏感运行时配置。

    默认关闭向量请求；仅在部署环境显式提供模型、API Base URL 和 Key 后启用。
    这样文本资料入库和关键词检索不会因为外部模型额度、网络或密钥缺失而中断。
    """

    enabled: bool
    provider_key: str
    model_id: str | None
    api_key_env: str | None
    api_base_url_env: str | None
    expected_dimensions: int
    max_batch_size: int
    request_timeout_seconds: float


@dataclass(frozen=True)
class InterviewRetrievalSettings:
    """面经 RAG 的召回与融合排序边界。"""

    lexical_candidate_limit: int
    semantic_candidate_limit: int
    final_limit: int
    reciprocal_rank_fusion_k: int
    embedding: InterviewEmbeddingSettings


def load_model_gateway_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> ModelGatewaySettings:
    """从求职助手专用 YAML 读取模型网关开关和 Provider 凭证位置。"""

    root_config = _load_career_root_config(config_path)

    model_policy = root_config.get("model_policy")
    model_gateway = root_config.get("model_gateway")
    if not isinstance(model_policy, dict) or not isinstance(model_gateway, dict):
        raise ValueError("career_assistant.yaml 缺少模型策略或模型网关配置")

    credential_config = model_gateway.get("provider_credentials")
    if not isinstance(credential_config, dict):
        raise ValueError("model_gateway.provider_credentials 必须是对象")

    normalized_credentials: dict[str, str | None] = {}
    for provider_key, env_name in credential_config.items():
        normalized_provider_key = _normalize_provider_key(provider_key)
        normalized_credentials[normalized_provider_key] = _normalize_env_name(env_name)

    return ModelGatewaySettings(
        allow_paid_profiles=_read_boolean(model_policy, "allow_paid_profiles"),
        enable_local_ollama_profile=_read_boolean(
            model_policy,
            "enable_local_ollama_profile",
        ),
        provider_credential_env=normalized_credentials,
    )


def load_job_assessment_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> JobAssessmentSettings:
    """读取岗位分析配置，拒绝退回聊天模型或无限重试。"""

    root_config = _load_career_root_config(config_path)
    raw = root_config.get("job_assessment")
    if not isinstance(raw, dict):
        raise ValueError("career_assistant.yaml 缺少 job_assessment 配置段")
    profile_key = str(raw.get("judge_profile_key") or "").strip().lower()
    prompt_version = str(raw.get("prompt_version") or "").strip()
    max_attempts = raw.get("max_attempts")
    if not profile_key:
        raise ValueError("job_assessment.judge_profile_key 不能为空")
    if not prompt_version:
        raise ValueError("job_assessment.prompt_version 不能为空")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
        raise ValueError("job_assessment.max_attempts 必须是 1 到 3 之间的整数")
    return JobAssessmentSettings(
        judge_profile_key=profile_key,
        prompt_version=prompt_version,
        max_attempts=max_attempts,
    )


def load_career_runtime_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> CareerRuntimeSettings:
    """读取 SSE 保活与并发保护配置，并在服务启动期拒绝危险阈值。"""

    root_config = _load_career_root_config(config_path)
    runtime_config = root_config.get("runtime")
    if not isinstance(runtime_config, dict):
        raise ValueError("career_assistant.yaml 缺少 runtime 配置段")

    max_concurrent_turns = runtime_config.get("max_concurrent_turns")
    if (
        isinstance(max_concurrent_turns, bool)
        or not isinstance(max_concurrent_turns, int)
        or not 1 <= max_concurrent_turns <= 16
    ):
        raise ValueError("runtime.max_concurrent_turns 必须是 1 到 16 之间的整数")

    turn_timeout_seconds = _read_positive_number(
        runtime_config,
        "turn_timeout_seconds",
    )
    if not 30 <= turn_timeout_seconds <= 900:
        raise ValueError("runtime.turn_timeout_seconds 必须在 30 到 900 秒之间")

    stream_heartbeat_seconds = _read_positive_number(
        runtime_config,
        "stream_heartbeat_seconds",
    )
    if not 3 <= stream_heartbeat_seconds <= 60:
        raise ValueError("runtime.stream_heartbeat_seconds 必须在 3 到 60 秒之间")

    return CareerRuntimeSettings(
        max_concurrent_turns=max_concurrent_turns,
        turn_timeout_seconds=turn_timeout_seconds,
        stream_heartbeat_seconds=stream_heartbeat_seconds,
    )


def load_attachment_processing_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> AttachmentProcessingSettings:
    """读取临时附件大小与存活时间，避免在业务代码中散落安全阈值。"""

    root_config = _load_career_root_config(config_path)
    privacy_config = root_config.get("privacy")
    document_processing_config = root_config.get("document_processing")
    if not isinstance(privacy_config, dict) or not isinstance(document_processing_config, dict):
        raise ValueError("career_assistant.yaml 缺少 privacy 或 document_processing 配置段")

    max_size_bytes = privacy_config.get("max_attachment_size_bytes")
    ttl_seconds = privacy_config.get("temporary_attachment_ttl_seconds")
    redaction_enabled = privacy_config.get("redaction_enabled")
    max_pdf_pages = document_processing_config.get("max_pdf_pages")
    if not isinstance(max_size_bytes, int) or max_size_bytes <= 0:
        raise ValueError("privacy.max_attachment_size_bytes 必须是正整数")
    if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ValueError("privacy.temporary_attachment_ttl_seconds 必须是正整数")
    if not isinstance(redaction_enabled, bool):
        raise ValueError("privacy.redaction_enabled 必须是布尔值")
    if isinstance(max_pdf_pages, bool) or not isinstance(max_pdf_pages, int) or not 1 <= max_pdf_pages <= 500:
        raise ValueError("document_processing.max_pdf_pages 必须是 1 到 500 之间的整数")

    return AttachmentProcessingSettings(
        max_size_bytes=max_size_bytes,
        ttl_seconds=ttl_seconds,
        max_pdf_pages=max_pdf_pages,
        redaction_enabled=redaction_enabled,
    )


def load_document_understanding_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> DocumentUnderstandingSettings:
    """读取 Docling 服务配置；未启用时保留空地址而不创建网络客户端。"""

    root_config = _load_career_root_config(config_path)
    document_processing = root_config.get("document_processing")
    docling_config = (
        document_processing.get("docling_serve")
        if isinstance(document_processing, dict)
        else None
    )
    if not isinstance(docling_config, dict):
        raise ValueError("career_assistant.yaml 缺少 document_processing.docling_serve 配置段")

    enabled = _read_boolean(docling_config, "enabled")
    service_base_url = _read_optional_url(docling_config, "service_base_url")
    api_key_env = _normalize_env_name(docling_config.get("api_key_env"))
    request_timeout_seconds = _read_positive_number(
        docling_config,
        "request_timeout_seconds",
    )
    max_attempts = _read_retry_max_attempts(docling_config, "max_attempts")
    retry_backoff_seconds = _read_positive_number(
        docling_config,
        "retry_backoff_seconds",
    )
    force_ocr = _read_boolean(docling_config, "force_ocr")
    table_mode = docling_config.get("table_mode")
    if table_mode not in {"fast", "accurate"}:
        raise ValueError("document_processing.docling_serve.table_mode 必须是 fast 或 accurate")
    if enabled and service_base_url is None:
        raise ValueError("启用 Docling 文档解析服务时必须填写 service_base_url")

    return DocumentUnderstandingSettings(
        enabled=enabled,
        service_base_url=service_base_url,
        api_key_env=api_key_env,
        request_timeout_seconds=request_timeout_seconds,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        force_ocr=force_ocr,
        table_mode=table_mode,
    )


def load_cloud_vision_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> CloudVisionSettings:
    """读取平台级图片理解连接池，不读取或返回任何密钥明文。"""

    root_config = _load_career_root_config(config_path)
    document_processing = root_config.get("document_processing")
    cloud_vision_config = (
        document_processing.get("cloud_vision")
        if isinstance(document_processing, dict)
        else None
    )
    if not isinstance(cloud_vision_config, dict):
        raise ValueError("career_assistant.yaml 缺少 document_processing.cloud_vision 配置段")

    enabled = _read_boolean(cloud_vision_config, "enabled")
    configured_connections = cloud_vision_config.get("connections")
    if not isinstance(configured_connections, dict) or not configured_connections:
        raise ValueError(
            "document_processing.cloud_vision.connections 必须至少包含一条连接配置",
        )

    connections: list[CloudVisionConnectionSettings] = []
    connection_keys: set[str] = set()
    for raw_connection_key, raw_connection in configured_connections.items():
        connection_key = _normalize_provider_key(raw_connection_key)
        if connection_key in connection_keys:
            raise ValueError(f"图片理解连接标识重复：{connection_key}")
        if not isinstance(raw_connection, dict):
            raise ValueError(
                f"document_processing.cloud_vision.connections.{connection_key} 必须是对象",
            )
        connections.append(
            _load_cloud_vision_connection_settings(connection_key, raw_connection),
        )
        connection_keys.add(connection_key)

    active_connection_key = _normalize_provider_key(
        cloud_vision_config.get("active_connection_key"),
    )
    if active_connection_key not in connection_keys:
        raise ValueError(
            "document_processing.cloud_vision.active_connection_key 必须指向已配置连接",
        )

    fallback_connection_keys = _read_cloud_vision_fallback_connection_keys(
        cloud_vision_config,
        active_connection_key=active_connection_key,
        available_connection_keys=connection_keys,
    )
    settings = CloudVisionSettings(
        enabled=enabled,
        active_connection_key=active_connection_key,
        fallback_connection_keys=fallback_connection_keys,
        connections=tuple(connections),
        max_attempts=_read_retry_max_attempts(cloud_vision_config, "max_attempts"),
        retry_backoff_seconds=_read_positive_number(
            cloud_vision_config,
            "retry_backoff_seconds",
        ),
    )
    if enabled and not settings.connection_for(active_connection_key).enabled:
        raise ValueError("启用图片理解服务时，当前主连接必须同时启用")
    for connection_key in fallback_connection_keys:
        if not settings.connection_for(connection_key).enabled:
            raise ValueError("图片理解备用连接必须同时启用")
    return settings


def _load_cloud_vision_connection_settings(
    connection_key: str,
    config: dict[str, object],
) -> CloudVisionConnectionSettings:
    """校验一条 OpenAI-compatible 图片理解连接的服务端元数据。"""

    model_id = config.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError(
            f"图片理解连接 {connection_key} 的 model_id 必须是非空字符串",
        )
    api_key_env = _normalize_env_name(config.get("api_key_env"))
    api_base_url_env = _normalize_env_name(config.get("api_base_url_env"))
    if api_key_env is None or api_base_url_env is None:
        raise ValueError(
            f"图片理解连接 {connection_key} 必须配置 API Key 与 API Host 的环境变量名",
        )
    max_completion_tokens = config.get("max_completion_tokens")
    if (
        isinstance(max_completion_tokens, bool)
        or not isinstance(max_completion_tokens, int)
        or not 64 <= max_completion_tokens <= 16_384
    ):
        raise ValueError(
            f"图片理解连接 {connection_key} 的 max_completion_tokens 必须是 64 到 16384 之间的整数",
        )
    return CloudVisionConnectionSettings(
        connection_key=connection_key,
        enabled=_read_boolean(config, "enabled"),
        provider_key=_normalize_provider_key(config.get("provider_key")),
        model_id=model_id.strip(),
        api_key_env=api_key_env,
        api_base_url_env=api_base_url_env,
        request_timeout_seconds=_read_positive_number(
            config,
            "request_timeout_seconds",
        ),
        max_completion_tokens=max_completion_tokens,
    )


def _read_cloud_vision_fallback_connection_keys(
    config: dict[str, object],
    *,
    active_connection_key: str,
    available_connection_keys: set[str],
) -> tuple[str, ...]:
    """读取显式备用连接，不允许隐式回退到可能收费的模型。"""

    raw_keys = config.get("fallback_connection_keys", [])
    if not isinstance(raw_keys, list):
        raise ValueError(
            "document_processing.cloud_vision.fallback_connection_keys 必须是列表",
        )
    fallback_keys: list[str] = []
    for raw_key in raw_keys:
        connection_key = _normalize_provider_key(raw_key)
        if connection_key == active_connection_key:
            raise ValueError("图片理解备用连接不能与主连接相同")
        if connection_key not in available_connection_keys:
            raise ValueError(f"图片理解备用连接不存在：{connection_key}")
        if connection_key in fallback_keys:
            raise ValueError(f"图片理解备用连接重复：{connection_key}")
        fallback_keys.append(connection_key)
    return tuple(fallback_keys)


def load_legacy_office_conversion_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> LegacyOfficeConversionSettings:
    """读取旧版 Office 转换服务配置；未启用时不创建网络客户端。"""

    root_config = _load_career_root_config(config_path)
    document_processing = root_config.get("document_processing")
    gotenberg_config = (
        document_processing.get("gotenberg")
        if isinstance(document_processing, dict)
        else None
    )
    if not isinstance(gotenberg_config, dict):
        raise ValueError("career_assistant.yaml 缺少 document_processing.gotenberg 配置段")

    enabled = _read_boolean(gotenberg_config, "enabled")
    service_base_url = _read_optional_url(gotenberg_config, "service_base_url")
    request_timeout_seconds = _read_positive_number(
        gotenberg_config,
        "request_timeout_seconds",
    )
    max_converted_size_bytes = gotenberg_config.get("max_converted_size_bytes")
    if (
        isinstance(max_converted_size_bytes, bool)
        or not isinstance(max_converted_size_bytes, int)
        or max_converted_size_bytes <= 0
    ):
        raise ValueError("document_processing.gotenberg.max_converted_size_bytes 必须是正整数")
    if enabled and service_base_url is None:
        raise ValueError("启用旧版 Office 转换服务时必须填写 service_base_url")

    return LegacyOfficeConversionSettings(
        enabled=enabled,
        service_base_url=service_base_url,
        request_timeout_seconds=request_timeout_seconds,
        max_converted_size_bytes=max_converted_size_bytes,
    )


def load_response_generation_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> ResponseGenerationSettings:
    """读取模型长回复配置，并在启动时拦截不安全或不可持久化的阈值。"""

    root_config = _load_career_root_config(config_path)
    response_generation = root_config.get("response_generation")
    if not isinstance(response_generation, dict):
        raise ValueError("career_assistant.yaml 缺少 response_generation 配置段")

    max_completion_tokens = response_generation.get("max_completion_tokens")
    if (
        isinstance(max_completion_tokens, bool)
        or not isinstance(max_completion_tokens, int)
        or not 1 <= max_completion_tokens <= 100_000
    ):
        raise ValueError(
            "response_generation.max_completion_tokens 必须是 1 到 100000 之间的整数",
        )

    max_persisted_response_characters = response_generation.get(
        "max_persisted_response_characters",
    )
    if (
        isinstance(max_persisted_response_characters, bool)
        or not isinstance(max_persisted_response_characters, int)
        or not 1 <= max_persisted_response_characters <= 500_000
    ):
        raise ValueError(
            "response_generation.max_persisted_response_characters 必须是 1 到 500000 之间的整数",
        )

    return ResponseGenerationSettings(
        max_completion_tokens=max_completion_tokens,
        request_timeout_seconds=_read_positive_number(
            response_generation,
            "request_timeout_seconds",
        ),
        max_attempts=_read_retry_max_attempts(
            response_generation,
            "max_attempts",
        ),
        retry_backoff_seconds=_read_positive_number(
            response_generation,
            "retry_backoff_seconds",
        ),
        max_persisted_response_characters=max_persisted_response_characters,
    )


def load_interview_retrieval_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> InterviewRetrievalSettings:
    """读取面经库 RAG 配置，并在启动期阻止向量维度与数据库索引不一致。

    当前 ``interview_chunks.embedding`` 固定为 ``vector(1024)``。维度必须与写入
    模型完全一致，不能为了兼容不同模型而静默截断或补零，否则余弦距离没有意义。
    """

    root_config = _load_career_root_config(config_path)
    library_config = root_config.get("interview_library")
    retrieval_config = (
        library_config.get("retrieval")
        if isinstance(library_config, dict)
        else None
    )
    if not isinstance(retrieval_config, dict):
        raise ValueError("career_assistant.yaml 缺少 interview_library.retrieval 配置段")

    embedding_config = retrieval_config.get("embedding")
    if not isinstance(embedding_config, dict):
        raise ValueError("interview_library.retrieval.embedding 必须是对象")

    embedding_enabled = _read_boolean(embedding_config, "enabled")
    raw_model_id = embedding_config.get("model_id")
    model_id = raw_model_id.strip() if isinstance(raw_model_id, str) and raw_model_id.strip() else None
    api_key_env = _normalize_env_name(embedding_config.get("api_key_env"))
    api_base_url_env = _normalize_env_name(embedding_config.get("api_base_url_env"))
    if embedding_enabled and (model_id is None or api_key_env is None or api_base_url_env is None):
        raise ValueError(
            "启用面经向量化前必须配置 model_id、api_key_env 和 api_base_url_env",
        )

    expected_dimensions = embedding_config.get("expected_dimensions")
    if isinstance(expected_dimensions, bool) or expected_dimensions != 1024:
        raise ValueError("面经向量索引当前仅支持 expected_dimensions=1024")

    max_batch_size = embedding_config.get("max_batch_size")
    if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or not 1 <= max_batch_size <= 64:
        raise ValueError("面经向量化批量大小必须在 1 到 64 之间")

    lexical_candidate_limit = retrieval_config.get("lexical_candidate_limit")
    semantic_candidate_limit = retrieval_config.get("semantic_candidate_limit")
    final_limit = retrieval_config.get("final_limit")
    rrf_k = retrieval_config.get("reciprocal_rank_fusion_k")
    if (
        isinstance(lexical_candidate_limit, bool)
        or not isinstance(lexical_candidate_limit, int)
        or not 4 <= lexical_candidate_limit <= 100
    ):
        raise ValueError("关键词候选数量必须在 4 到 100 之间")
    if (
        isinstance(semantic_candidate_limit, bool)
        or not isinstance(semantic_candidate_limit, int)
        or not 4 <= semantic_candidate_limit <= 100
    ):
        raise ValueError("向量候选数量必须在 4 到 100 之间")
    if isinstance(final_limit, bool) or not isinstance(final_limit, int) or not 1 <= final_limit <= 20:
        raise ValueError("最终引用数量必须在 1 到 20 之间")
    if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or not 1 <= rrf_k <= 200:
        raise ValueError("RRF 参数必须在 1 到 200 之间")

    return InterviewRetrievalSettings(
        lexical_candidate_limit=lexical_candidate_limit,
        semantic_candidate_limit=semantic_candidate_limit,
        final_limit=final_limit,
        reciprocal_rank_fusion_k=rrf_k,
        embedding=InterviewEmbeddingSettings(
            enabled=embedding_enabled,
            provider_key=_normalize_provider_key(embedding_config.get("provider_key")),
            model_id=model_id,
            api_key_env=api_key_env,
            api_base_url_env=api_base_url_env,
            expected_dimensions=expected_dimensions,
            max_batch_size=max_batch_size,
            request_timeout_seconds=_read_positive_number(
                embedding_config,
                "request_timeout_seconds",
            ),
        ),
    )


def _load_career_root_config(config_path: Path) -> dict[str, object]:
    """读取并校验求职助手根配置，供新增独立配置段复用。"""

    if not config_path.is_file():
        raise FileNotFoundError(f"未找到求职助手配置文件：{config_path}")
    with config_path.open("r", encoding="utf-8") as config_file:
        config_text = config_file.read()
    raw_config = yaml.safe_load(_expand_environment_template(config_text)) or {}
    root_config = raw_config.get("career_assistant")
    if not isinstance(root_config, dict):
        raise ValueError("career_assistant.yaml 缺少 career_assistant 配置段")
    return root_config


_ENVIRONMENT_VALUE_PATTERN = re.compile(
    r"\$\{(?P<name>[A-Z_][A-Z0-9_]*)(?::-(?P<default>[^}]*))?\}",
)


def _expand_environment_template(config_text: str) -> str:
    """展开部署 YAML 中的非敏感环境变量占位符。

    仅支持 ``${NAME}`` 和 ``${NAME:-default}`` 两种形式。它让同一份 YAML
    在本地使用 ``127.0.0.1``、在 Docker 网络中使用服务名，而不复制一份生产
    配置。这里绝不读取或打印 API Key；Key 本身仍只能由对应的 ``*_ENV`` 配置
    间接引用。
    """

    def replace(match: re.Match[str]) -> str:
        environment_value = os.getenv(match.group("name"))
        if environment_value:
            return environment_value
        default_value = match.group("default")
        return default_value if default_value is not None else ""

    return _ENVIRONMENT_VALUE_PATTERN.sub(replace, config_text)


def _read_boolean(config: dict[str, object], key: str) -> bool:
    """严格读取布尔开关，防止 YAML 中的字符串值造成错误路由。"""

    value = config.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值")
    return value


def _read_optional_url(config: dict[str, object], key: str) -> str | None:
    """读取可选服务地址；URL 的路径级校验由实际客户端完成。"""

    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空 URL 字符串或 null")
    return value.strip()


def _read_positive_number(config: dict[str, object], key: str) -> float:
    """读取正数秒数，拒绝 YAML 布尔值被隐式当作整数。"""

    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{key} 必须是大于零的数字")
    return float(value)


def _read_retry_max_attempts(config: dict[str, object], key: str) -> int:
    """读取受控重试次数；最多两次网络调用，避免故障时无限消耗额度。"""

    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2:
        raise ValueError(f"{key} 必须是 1 到 2 之间的整数")
    return value


def _normalize_provider_key(value: object) -> str:
    """校验 Provider 标识，保持数据库、配置和 WebUI 的键一致。"""

    if not isinstance(value, str):
        raise ValueError("Provider 标识必须是字符串")
    normalized_value = value.strip().lower()
    if not normalized_value or len(normalized_value) > 50:
        raise ValueError("Provider 标识不能为空且不能超过 50 个字符")
    if not all(character.isalnum() or character in {"-", "_"} for character in normalized_value):
        raise ValueError("Provider 标识只能包含小写字母、数字、短横线或下划线")
    return normalized_value


def _normalize_env_name(value: object) -> str | None:
    """校验凭证环境变量名称；null 表示 Provider 不需要云端 API Key。"""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("凭证环境变量名称必须是字符串或 null")
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError("凭证环境变量名称不能为空")
    if not all(character.isupper() or character.isdigit() or character == "_" for character in normalized_value):
        raise ValueError("凭证环境变量名称只能包含大写字母、数字和下划线")
    return normalized_value
