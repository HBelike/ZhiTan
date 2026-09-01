"""面经库公开资料采集的合规编排层。

本模块只负责两类输入：用户明确提供的公开 URL，和平台关键词检索任务的状态管理。
它不保存第三方账号、密码或 Cookie，也不尝试绕过验证码、反爬机制或访问控制。平台
关键词任务在没有已获授权的官方 API/浏览器连接器时会明确停在
``needs_user_interaction``，而不是伪造“已抓到正文”的结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import ipaddress
import json
import logging
import re
import socket
from typing import Any, Final, Mapping
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import UUID

from src.career_assistant.attachments import AttachmentParser, TemporaryAttachmentStore
from src.career_assistant.contracts import (
    AttachmentKind,
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    InterviewCollectionCandidateRecord,
    InterviewCollectionJobRecord,
    InterviewSourceType,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.public_web import (
    FirecrawlClient,
    PublicWebImageDownloader,
)
from src.career_assistant.interview_library.public_web_collection import (
    PublicWebCollectionCoordinator,
)
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)
from src.career_assistant.interview_library.models import IngestionTriggerType
from src.career_assistant.interview_library.metadata import (
    InterviewMaterialMetadataExtractor,
)
from src.career_assistant.model_clients import (
    ChatMessage,
    ModelInvocationError,
    OpenAICompatibleChatClient,
)
from src.career_assistant.model_gateway import ModelGateway, ModelReadiness
from src.observability.langsmith_runtime import trace_operation


MAX_ARTICLE_BYTES: Final[int] = 3 * 1024 * 1024
MAX_ARTICLE_CHARACTERS: Final[int] = 260_000
MAX_LLM_ANALYSIS_CHARACTERS: Final[int] = 24_000
MAX_ANALYSIS_QUESTIONS: Final[int] = 30
MIN_OCR_EVIDENCE_CHARACTERS: Final[int] = 24
MIN_VISIBLE_DOM_EVIDENCE_CHARACTERS: Final[int] = 80
MIN_BODY_EVIDENCE_WITH_UNREAD_IMAGES: Final[int] = 180
_EVIDENCE_QUESTION_SIGNAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)\s*(?:\d{1,2}\s*[.、．、)]|[一二三四五六七八九十]+\s*[、.．])|[？?]",
    re.MULTILINE,
)


LOGGER = logging.getLogger(__name__)


def _count_interview_question_signals(value: str) -> int:
    """统计编号题和问号，避免把网页摘要误当作完整的题目文本。"""

    normalized = str(value or "")
    count = normalized.count("？") + normalized.count("?")
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if len(line) > 2 and line[0].isdigit() and line[1] in ".、．)）":
            count += 1
        elif len(line) > 2 and line[0] in "一二三四五六七八九十" and line[1] in "、.．":
            count += 1
    return count


class CollectionOperationError(RuntimeError):
    """向路由层传递可展示、不可泄露内部细节的采集错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PlatformCollectionPolicy:
    """前端展示和服务端执行共用的平台采集边界。"""

    key: str
    label: str
    can_run_keyword_search: bool
    connector_kind: CollectionConnectorKind
    policy_decision: str
    ready: bool = True
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ExtractedPublicArticle:
    """从一个用户明确提交的公开页面中安全抽取的文本结果。"""

    source_url: str
    canonical_url: str
    title: str | None
    markdown_content: str


@dataclass(frozen=True)
class XiaohongshuBrowserDiscovery:
    """浏览器搜索页返回的无会话卡片信息。"""

    note_id: str
    title: str | None = None
    author_name: str | None = None
    liked_count: str | None = None


@dataclass(frozen=True)
class XiaohongshuBrowserNote:
    """浏览器详情页提交给后端的文本与临时图片引用。"""

    note_id: str
    title: str | None
    body_text: str
    image_urls: tuple[str, ...]
    published_at: str | None = None
    author_name: str | None = None
    tags: tuple[str, ...] = ()
    body_source: str = "browser_page"
    source_error_code: str | None = None
    source_error_message: str | None = None

    @property
    def canonical_url(self) -> str:
        return f"https://www.xiaohongshu.com/explore/{self.note_id}"

    @property
    def source_url(self) -> str:
        # 浏览器使用的临时签名 URL 不能越过 Web UI 边界。
        return self.canonical_url

    @property
    def markdown_content(self) -> str:
        sections: list[str] = []
        if self.title:
            sections.append(f"# {self.title}")
        if self.body_text:
            sections.append(self.body_text)
        if self.tags:
            sections.append(" ".join(f"#{tag}" for tag in self.tags))
        return "\n\n".join(sections).strip()


class _ArticleTextParser(HTMLParser):
    """最小依赖的 HTML 正文归一器，不保留原始 HTML、脚本或样式。"""

    _SKIPPED_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
    _BLOCK_TAGS = {"article", "main", "section", "p", "li", "h1", "h2", "h3", "h4", "br", "div"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._parts: list[str] = []

    @property
    def title(self) -> str | None:
        value = " ".join(" ".join(self._title_parts).split()).strip()
        return value[:300] or None

    @property
    def text(self) -> str:
        normalized_lines: list[str] = []
        for item in "\n".join(self._parts).splitlines():
            compact = " ".join(item.split()).strip()
            if not compact:
                continue
            if compact.lower() in {"登录", "注册", "下载 app", "打开 app", "举报", "cookie 设置"}:
                continue
            if not normalized_lines or normalized_lines[-1] != compact:
                normalized_lines.append(compact)
        return "\n\n".join(normalized_lines)

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        normalized_tag = tag.lower()
        if normalized_tag in self._SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if normalized_tag == "title":
            self._in_title = True
        if not self._skip_depth and normalized_tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if normalized_tag == "title":
            self._in_title = False
        if not self._skip_depth and normalized_tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._parts.append(data)


class PublicUrlArticleExtractor:
    """提取用户提交的公开 HTTPS URL，并在请求前执行基础 SSRF 防护。

    此类不是通用爬虫：不执行 JavaScript、不携带用户会话、不模拟登录、不处理验证码。
    对 JS 渲染页、受限页或空正文会返回明确的可恢复错误。
    """

    def extract(self, source_url: str) -> ExtractedPublicArticle:
        self._validate_public_https_url(source_url)
        request = Request(
            source_url,
            headers={
                "User-Agent": "InterviewLibraryUrlImport/1.0 (+user-submitted-public-url)",
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - URL 已做协议与私网校验
                final_url = response.geturl()
                self._validate_public_https_url(final_url)
                content_type = response.headers.get_content_type().lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise CollectionOperationError(
                        "unsupported_content_type",
                        "该链接不是可解析的 HTML 页面，请改用粘贴正文或上传文件。",
                    )
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read(MAX_ARTICLE_BYTES + 1)
        except CollectionOperationError:
            raise
        except TimeoutError as exc:
            raise CollectionOperationError("fetch_timeout", "页面读取超时，请稍后重试或粘贴正文。") from exc
        except OSError as exc:
            raise CollectionOperationError(
                "fetch_unavailable",
                "暂时无法读取该公开页面；它可能需要登录、限制了访问，或网络不可达。",
            ) from exc

        if len(payload) > MAX_ARTICLE_BYTES:
            raise CollectionOperationError("response_too_large", "页面内容过大，请改用粘贴正文或导入文件。")

        decoded = payload.decode(charset, errors="replace")
        parser = _ArticleTextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except Exception as exc:  # pragma: no cover - HTMLParser 对畸形页面通常可恢复
            raise CollectionOperationError("html_parse_failed", "页面结构异常，暂时无法提取正文。") from exc

        text = parser.text[:MAX_ARTICLE_CHARACTERS].strip()
        if len(text) < 80:
            raise CollectionOperationError(
                "article_empty",
                "未从页面提取到足够正文；该站点可能依赖登录或前端渲染，请粘贴正文后导入。",
            )
        title = parser.title
        markdown = f"# {title}\n\n{text}" if title else text
        return ExtractedPublicArticle(
            source_url=source_url,
            canonical_url=final_url,
            title=title,
            markdown_content=markdown,
        )

    @staticmethod
    def _validate_public_https_url(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise CollectionOperationError("invalid_url", "仅支持用户明确提交的公开 HTTPS 链接。")
        if parsed.username or parsed.password:
            raise CollectionOperationError("invalid_url", "链接不能包含账号或密码。")
        try:
            port = parsed.port
        except ValueError as exc:
            raise CollectionOperationError("invalid_url", "链接端口格式无效。") from exc
        if port not in {None, 443}:
            raise CollectionOperationError("unsafe_url", "公开链接仅允许使用标准 HTTPS 端口。")

        hostname = parsed.hostname.lower().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"}:
            raise CollectionOperationError("unsafe_url", "不能读取本机或内网地址。")
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise CollectionOperationError("dns_unavailable", "无法解析链接域名，请检查地址后重试。") from exc
        if not addresses:
            raise CollectionOperationError("dns_unavailable", "无法解析链接域名，请检查地址后重试。")
        for address in addresses:
            try:
                candidate = ipaddress.ip_address(address)
            except ValueError as exc:
                raise CollectionOperationError("unsafe_url", "链接域名解析结果无效。") from exc
            if (
                candidate.is_private
                or candidate.is_loopback
                or candidate.is_link_local
                or candidate.is_multicast
                or candidate.is_reserved
                or candidate.is_unspecified
            ):
                raise CollectionOperationError("unsafe_url", "不能读取本机、内网或保留地址。")


@dataclass(frozen=True)
class InterviewEvidenceAnalysis:
    """LLM 对一条公开图文资料的可持久化判断结果。

    该对象只承载从正文和 OCR 文本中导出的短字段；原始 HTML、图片字节、模型密钥和
    上游完整响应均不会进入数据库。`is_valid_interview` 为 ``None`` 表示正文已保留，
    但当前未能完成模型甄别，需要用户修复模型连接后重试或人工处理。
    """

    is_valid_interview: bool | None
    confidence: float | None
    company_name: str | None
    role_name: str | None
    interview_date: date | None
    interview_stage: str | None
    tags: tuple[str, ...]
    summary_text: str | None
    questions: tuple[str, ...]
    normalized_markdown: str | None
    rejection_reason: str | None
    analyzer_status: str
    analyzer_message: str | None
    model_label: str | None

    def as_metadata(self) -> dict[str, object]:
        """压缩为候选资料的 JSON 元数据，供 WebUI 展示和自动入库判断。"""

        return {
            "is_valid_interview": self.is_valid_interview,
            "confidence": self.confidence,
            "company_name": self.company_name,
            "role_name": self.role_name,
            "interview_date": (
                self.interview_date.isoformat()
                if self.interview_date is not None
                else None
            ),
            "interview_stage": self.interview_stage,
            "tags": list(self.tags),
            "summary_text": self.summary_text,
            "question_count": len(self.questions),
            "questions": list(self.questions),
            "rejection_reason": self.rejection_reason,
            "status": self.analyzer_status,
            "message": self.analyzer_message,
            "model": self.model_label,
        }

    @classmethod
    def insufficient_source(cls, message: str) -> "InterviewEvidenceAnalysis":
        """生成“采集不完整、不能下结论”的结果。

        标题、页面摘要或空 OCR 不能证明一篇笔记不是面经，因此这里刻意不保留公司、
        岗位和置信度等推测字段，也不会把它降级成“已过滤”。
        """

        return cls(
            is_valid_interview=None,
            confidence=None,
            company_name=None,
            role_name=None,
            interview_date=None,
            interview_stage=None,
            tags=(),
            summary_text=None,
            questions=(),
            normalized_markdown=None,
            rejection_reason=None,
            analyzer_status="insufficient_source",
            analyzer_message=message,
            model_label=None,
        )


class InterviewEvidenceAnalyzer:
    """复用平台模型连接，对公开材料做可解释的面经有效性与字段甄别。

    调用者只注入 ModelGateway 和统一的 OpenAI-compatible Client，因此页面中保存的
    模型档案、API Base URL 和加密 Key 会沿用求职助手现有配置。模型暂不可用时返回
    ``pending_model``，保留原始解析 Markdown 供人工处理，绝不把失败伪装为“无效”。
    """

    _SYSTEM_PROMPT: Final[str] = """你是面经库资料审核器。只根据给定的公开笔记正文和图片识别文本做事实提取，不能编造公司、岗位、问题或日期。
判断 `is_valid_interview` 为 true 的最低条件是：材料明确包含真实或可信的面试经历、面试流程、面试题、面试复盘、岗位考察内容中的至少一项，并且可提取至少一个具体问题或考察点。招聘广告、课程推广、纯求职吐槽、与面试无关内容应为 false。

必须只输出一个 JSON 对象，不要 Markdown 包裹，不要解释。字段固定为：
{
  "is_valid_interview": true,
  "confidence": 0.0,
  "company_name": "string or null",
  "role_name": "string or null",
  "interview_date": "YYYY-MM-DD or null",
  "interview_stage": "string or null",
  "tags": ["最多10个技术或面试标签"],
  "summary_text": "不超过220字的事实摘要",
  "questions": ["最多30条，从原文可证实的面试问题或考察点"],
  "normalized_markdown": "只要材料含有可读的面试流程、题目、复盘或岗位考察内容，就给出忠实、连续、可编辑的中文 Markdown 正文，不超过8000字。删除平台口号、来源链接、公开配图列表、配图编号、OCR/解析标签和采集说明；保留原文可证实的题目、流程和必要上下文。不添加标题、不编造；没有可读的面经相关正文时为 null。即使 is_valid_interview 为 false，也必须在有可读正文时返回该字段供人工审核。",
  "rejection_reason": "无效时给出简短原因；有效时为 null"
}
若字段无法从材料判断必须填 null 或空数组。"""

    def __init__(
        self,
        model_gateway: ModelGateway | None,
        model_connection_client: OpenAICompatibleChatClient | None,
        *,
        metadata_extractor: InterviewMaterialMetadataExtractor | None = None,
    ) -> None:
        self._model_gateway = model_gateway
        self._model_connection_client = model_connection_client
        self._metadata_extractor = metadata_extractor or InterviewMaterialMetadataExtractor()

    def analyze(
        self,
        organization_id: UUID,
        *,
        source_url: str,
        title: str | None,
        markdown_content: str,
    ) -> InterviewEvidenceAnalysis:
        """执行一次面经甄别；网络或配置失败时返回待处理结果而非抛弃正文。"""

        return trace_operation(
            run_name="career.interview_evidence.analyze",
            run_type="chain",
            inputs={
                "input_characters": len(markdown_content),
                "has_title": bool(title),
                "has_source_url": bool(source_url),
            },
            metadata={
                "component": "interview_evidence_analyzer",
                "source_kind": "public_material",
                "stage": "evidence_analysis",
            },
            execute=lambda: self._analyze(
                organization_id,
                source_url=source_url,
                title=title,
                markdown_content=markdown_content,
            ),
            summarize=lambda result: {
                "status": result.analyzer_status,
                "is_valid_interview": result.is_valid_interview,
                "question_count": len(result.questions),
                "has_normalized_markdown": bool(result.normalized_markdown),
                "output_characters": len(result.normalized_markdown or ""),
            },
            tags=("career.interview", "analysis"),
        )

    def _analyze(
        self,
        organization_id: UUID,
        *,
        source_url: str,
        title: str | None,
        markdown_content: str,
    ) -> InterviewEvidenceAnalysis:
        """执行一次真实甄别；原始材料只在进程内传递，不进入 Trace。"""

        local = self._metadata_extractor.extract(
            markdown_content,
            filename=title or "公开笔记",
            source_platform="小红书",
        )
        if self._model_gateway is None or self._model_connection_client is None:
            return self._pending_analysis(
                local,
                "平台尚未初始化模型调用器，已保留正文等待人工处理。",
            )

        try:
            resolution = self._model_gateway.resolve(
                organization_id,
                ModelSelectionRequest(
                    mode=ModelSelectionMode.FREE_QUOTA_FIRST,
                    required_capabilities=frozenset({ModelCapability.TEXT}),
                ),
            )
        except (LookupError, PermissionError, ValueError) as exc:
            return self._pending_analysis(local, str(exc))

        if resolution.readiness is not ModelReadiness.READY or not resolution.credential:
            return self._pending_analysis(
                local,
                "当前没有可调用的模型连接，已保留正文等待人工处理。",
            )

        evidence = markdown_content[:MAX_LLM_ANALYSIS_CHARACTERS]
        user_prompt = (
            f"来源 URL：{source_url}\n"
            f"笔记标题：{title or '未提供'}\n\n"
            "以下是已从公开正文和公开配图中提取的文本：\n"
            f"---\n{evidence}\n---"
        )
        try:
            response = self._model_connection_client.complete(
                resolution.profile,
                resolution.credential_env_name,
                [
                    ChatMessage(role="system", content=self._SYSTEM_PROMPT),
                    ChatMessage(role="user", content=user_prompt),
                ],
                api_key=resolution.credential,
            )
            payload = self._parse_model_json(response)
            return self._normalize_analysis(
                payload,
                local_company=local.company_name,
                local_role=local.role_name,
                local_date=local.interview_date,
                local_tags=local.tags,
                model_label=resolution.profile.display_name,
            )
        except (ModelInvocationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            LOGGER.warning("小红书候选的 LLM 甄别未完成：%s", exc)
            return self._pending_analysis(
                local,
                "模型甄别暂未完成：请检查模型连接后重试，或在候选资料中人工确认。",
            )

    @staticmethod
    def _parse_model_json(value: str) -> Mapping[str, Any]:
        """兼容模型偶发添加的 Markdown 围栏，但仅接受一个 JSON 对象。"""

        compact = value.strip()
        if compact.startswith("```"):
            compact = "\n".join(
                line for line in compact.splitlines()
                if not line.strip().startswith("```")
            ).strip()
        decoder = json.JSONDecoder()
        for index, character in enumerate(compact):
            if character != "{":
                continue
            parsed, _ = decoder.raw_decode(compact[index:])
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("模型未按约定返回 JSON 对象")

    def _normalize_analysis(
        self,
        payload: Mapping[str, Any],
        *,
        local_company: str | None,
        local_role: str | None,
        local_date: date | None,
        local_tags: tuple[str, ...],
        model_label: str,
    ) -> InterviewEvidenceAnalysis:
        """收敛模型松散字段，限制长度并保留本地规则可证实的兜底值。"""

        requested_valid = payload.get("is_valid_interview")
        confidence = self._bounded_float(payload.get("confidence"))
        company_name = self._short_text(payload.get("company_name"), 120) or local_company
        role_name = self._short_text(payload.get("role_name"), 160) or local_role
        interview_date = self._parse_interview_date(payload.get("interview_date")) or local_date
        interview_stage = self._short_text(payload.get("interview_stage"), 40)
        tags = self._string_list(payload.get("tags"), limit=10, item_limit=40)
        if not tags:
            tags = tuple(local_tags[:10])
        questions = self._string_list(
            payload.get("questions"),
            limit=MAX_ANALYSIS_QUESTIONS,
            item_limit=600,
        )
        is_valid = requested_valid if isinstance(requested_valid, bool) else False
        if is_valid and not questions:
            # 只写“面经”但没有任何具体题目/考察点的内容不进入自动入库，避免低质量噪声。
            is_valid = False
            rejection_reason = "未提取到可验证的面试问题或考察点。"
        else:
            rejection_reason = self._short_text(payload.get("rejection_reason"), 400)
        if not is_valid and not rejection_reason:
            rejection_reason = "内容未满足可归档面经的证据要求。"

        normalized_markdown = self._markdown_text(
            payload.get("normalized_markdown"),
            30_000,
        )
        return InterviewEvidenceAnalysis(
            is_valid_interview=is_valid,
            confidence=confidence,
            company_name=company_name,
            role_name=role_name,
            interview_date=interview_date,
            interview_stage=interview_stage,
            tags=tags,
            summary_text=self._short_text(payload.get("summary_text"), 1_200),
            questions=questions,
            # “是否允许自动归档”和“是否有可供人工编辑的正文”是两件事。低置信度
            # 或不完整的材料仍可能含有真实题目，不能因为自动判定为 false 就丢弃。
            normalized_markdown=normalized_markdown,
            rejection_reason=None if is_valid else rejection_reason,
            analyzer_status="completed",
            analyzer_message=None,
            model_label=model_label,
        )

    def _pending_analysis(self, local, message: str) -> InterviewEvidenceAnalysis:  # type: ignore[no-untyped-def]
        """生成可人工处理的候选字段，防止模型故障导致已解析正文消失。"""

        return InterviewEvidenceAnalysis(
            is_valid_interview=None,
            confidence=local.confidence,
            company_name=local.company_name,
            role_name=local.role_name,
            interview_date=local.interview_date,
            interview_stage=None,
            tags=local.tags[:10],
            summary_text=local.summary_text,
            questions=(),
            normalized_markdown=None,
            rejection_reason=None,
            analyzer_status="pending_model",
            analyzer_message=message,
            model_label=None,
        )

    @staticmethod
    def _short_text(value: object, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        return normalized[:limit] or None

    @staticmethod
    def _markdown_text(value: object, limit: int) -> str | None:
        """保留模型整理 Markdown 的换行结构，避免把标题和题目压成一行。"""

        if not isinstance(value, str):
            return None
        lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
        normalized = "\n".join(lines).strip()
        return normalized[:limit] or None

    @staticmethod
    def _string_list(value: object, *, limit: int, item_limit: int) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        items: list[str] = []
        for candidate in value:
            if not isinstance(candidate, str):
                continue
            normalized = " ".join(candidate.split()).strip()[:item_limit]
            if normalized and normalized not in items:
                items.append(normalized)
            if len(items) >= limit:
                break
        return tuple(items)

    @staticmethod
    def _bounded_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < 0:
            return 0.0
        if number > 1:
            # 兼容少数模型把 0~1 置信度错误地输出成百分数，例如 82。
            return round(number / 100, 2) if number <= 100 else None
        return round(number, 2)

    @staticmethod
    def _parse_interview_date(value: object) -> date | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if len(normalized) == 7:
            normalized = f"{normalized}-01"
        try:
            return date.fromisoformat(normalized)
        except ValueError:
            return None


class InterviewCollectionService:
    """编排候选发现、公开 URL 抽取与“候选后入库”流程。

    所有网络读取都发生在短生命周期方法中，数据库事务只覆盖状态/文本写入；因此
    不会把慢网页、模型调用或用户交互锁在 PostgreSQL 事务里。
    """

    _POLICIES: Final[dict[str, PlatformCollectionPolicy]] = {
        "xiaohongshu": PlatformCollectionPolicy(
            key="xiaohongshu",
            label="小红书",
            can_run_keyword_search=True,
            connector_kind=CollectionConnectorKind.USER_AUTHORIZED_BROWSER,
            policy_decision=(
                "通过用户已授权的浏览器页面读取关键词卡片和公开笔记；"
                "不导出账号、Cookie 或临时访问参数，不绕过登录、验证码或访问限制。"
            ),
        ),
        "nowcoder": PlatformCollectionPolicy(
            key="nowcoder",
            label="牛客",
            can_run_keyword_search=False,
            connector_kind=CollectionConnectorKind.USER_AUTHORIZED_BROWSER,
            policy_decision="需要平台允许的用户授权会话或官方能力；当前不保存账号密码或 Cookie。",
        ),
        "maimai": PlatformCollectionPolicy(
            key="maimai",
            label="脉脉",
            can_run_keyword_search=False,
            connector_kind=CollectionConnectorKind.USER_AUTHORIZED_BROWSER,
            policy_decision="需要平台允许的用户授权会话或官方能力；当前不保存账号密码或 Cookie。",
        ),
        "public_url": PlatformCollectionPolicy(
            key="public_url",
            label="公开链接",
            can_run_keyword_search=False,
            connector_kind=CollectionConnectorKind.URL_IMPORT,
            policy_decision="只读取用户明确提交的公开 HTTPS 页面，不执行登录、验证码绕过或会话复用。",
        ),
        "public_web": PlatformCollectionPolicy(
            key="public_web",
            label="全网公开网页",
            can_run_keyword_search=True,
            connector_kind=CollectionConnectorKind.PUBLIC_API,
            policy_decision=PublicWebCollectionCoordinator.POLICY_DECISION,
        ),
    }

    def __init__(
        self,
        repository: InterviewLibraryRepository,
        library_service: InterviewLibraryService,
        *,
        article_extractor: PublicUrlArticleExtractor | None = None,
        model_gateway: ModelGateway | None = None,
        model_connection_client: OpenAICompatibleChatClient | None = None,
        temporary_attachment_store: TemporaryAttachmentStore | None = None,
        attachment_parser: AttachmentParser | None = None,
        evidence_analyzer: InterviewEvidenceAnalyzer | None = None,
        xiaohongshu_source_adapter: Any | None = None,
        firecrawl_client: FirecrawlClient | None = None,
        public_image_downloader: PublicWebImageDownloader | None = None,
    ) -> None:
        self._repository = repository
        self._library_service = library_service
        self._article_extractor = article_extractor or PublicUrlArticleExtractor()
        self._temporary_attachment_store = temporary_attachment_store
        self._attachment_parser = attachment_parser
        self._evidence_analyzer = evidence_analyzer or InterviewEvidenceAnalyzer(
            model_gateway,
            model_connection_client,
        )
        self._firecrawl_client = firecrawl_client
        self._public_image_downloader = (
            public_image_downloader
            if public_image_downloader is not None
            else PublicWebImageDownloader() if firecrawl_client is not None else None
        )
        self._public_web_coordinator = (
            PublicWebCollectionCoordinator(
                repository,
                library_service,
                firecrawl_client,
                self._evidence_analyzer,
                temporary_attachment_store=temporary_attachment_store,
                attachment_parser=attachment_parser,
                image_downloader=self._public_image_downloader,
            )
            if firecrawl_client is not None
            else None
        )
        # ``xiaohongshu_collection`` 为复用既有 CollectionOperationError 会反向导入
        # 本模块；因此必须在类已初始化的运行路径内局部导入，避免模块加载时循环导入。
        if xiaohongshu_source_adapter is None:
            from src.career_assistant.interview_library.xiaohongshu_collection import (
                XiaohongshuPublicSourceAdapter,
            )

            xiaohongshu_source_adapter = XiaohongshuPublicSourceAdapter()
        self._xiaohongshu_source_adapter = xiaohongshu_source_adapter

    def close(self) -> None:
        """释放小红书适配器的短生命周期 HTTP 连接。"""

        close = getattr(self._xiaohongshu_source_adapter, "close", None)
        if callable(close):
            close()
        if self._firecrawl_client is not None:
            self._firecrawl_client.close()
        if self._public_image_downloader is not None:
            self._public_image_downloader.close()

    def list_platform_policies(self) -> list[PlatformCollectionPolicy]:
        """返回可展示的采集平台能力，不暴露内部密钥或会话引用。"""

        policies = list(self._POLICIES.values())
        return [
            PlatformCollectionPolicy(
                key=policy.key,
                label=policy.label,
                can_run_keyword_search=policy.can_run_keyword_search,
                connector_kind=policy.connector_kind,
                policy_decision=policy.policy_decision,
                ready=self.public_web_ready() if policy.key == "public_web" else True,
                unavailable_reason=(
                    None
                    if policy.key != "public_web" or self.public_web_ready()
                    else "未配置全网公开信息收集服务，请先设置 FIRECRAWL_API_KEY。"
                ),
            )
            for policy in policies
        ]

    def public_web_ready(self) -> bool:
        """返回全网公开信息收集是否已配置。"""

        return self._public_web_coordinator is not None

    def create_public_web_import_job(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        keyword: str,
        requested_limit: int,
    ) -> InterviewCollectionJobRecord:
        """创建 Firecrawl 全网公开面经搜索任务。"""

        if self._public_web_coordinator is None:
            raise CollectionOperationError(
                "firecrawl_not_configured",
                "未配置全网公开信息收集服务，请先设置 FIRECRAWL_API_KEY。",
            )
        return self._public_web_coordinator.create_job(
            organization_id,
            created_by_actor_id=created_by_actor_id,
            keyword=keyword,
            requested_limit=requested_limit,
        )

    def run_public_web_import(self, organization_id: UUID, job_id: UUID) -> None:
        """在后台执行全网公开面经搜索、去重、解析和自动入库。"""

        if self._public_web_coordinator is None:
            return
        self._public_web_coordinator.run(organization_id, job_id)

    def create_xiaohongshu_browser_collection_job(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        keyword: str,
        requested_limit: int,
    ) -> InterviewCollectionJobRecord:
        """创建等待当前 Chrome 登录态执行的小红书关键词任务。"""

        normalized_keyword = " ".join(keyword.split())
        if not normalized_keyword:
            raise ValueError("请输入小红书搜索关键词。")
        if not 5 <= requested_limit <= 50:
            raise ValueError("小红书单次信息收集数量必须在 5 到 50 之间")
        return self._repository.create_collection_job(
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            platform_key="xiaohongshu",
            keyword=normalized_keyword,
            requested_limit=requested_limit,
            connector_kind=CollectionConnectorKind.USER_AUTHORIZED_BROWSER,
            policy_decision=self._POLICIES["xiaohongshu"].policy_decision,
            metadata_json={
                "source_kind": "xiaohongshu_browser_keyword",
                "search_keyword": normalized_keyword,
                "include_images": True,
                "auto_import": True,
                "phase": "connect",
                "progress_percent": 0,
                "progress_message": "任务已创建，正在等待浏览器助手连接小红书。",
                "summary": self._new_xiaohongshu_summary(),
            },
        )

    def register_xiaohongshu_browser_discoveries(
        self,
        organization_id: UUID,
        job_id: UUID,
        discoveries: tuple[XiaohongshuBrowserDiscovery, ...],
    ) -> list[InterviewCollectionCandidateRecord]:
        """登记不含 token 和图片的搜索卡片，并识别跨关键词重复来源。"""

        job = self._require_xiaohongshu_browser_job(organization_id, job_id)
        if job.status in {CollectionJobStatus.SUCCEEDED, CollectionJobStatus.FAILED, CollectionJobStatus.CANCELLED}:
            raise ValueError("该信息收集任务已经结束，不能继续登记卡片。")
        limited = discoveries[: job.requested_limit]
        for discovery in limited:
            note_id = self._normalize_xiaohongshu_note_id(discovery.note_id)
            canonical_url = self._xiaohongshu_canonical_url(note_id)
            existing_candidate = self._repository.get_collection_candidate_by_canonical_url(
                organization_id,
                job.id,
                canonical_url,
            )
            if existing_candidate is not None:
                continue
            existing_experience = self._repository.get_experience_by_source_url(
                organization_id,
                canonical_url,
            )
            metadata: dict[str, object] = {
                "source_kind": "xiaohongshu_browser_note",
                "note_id": note_id,
                "author_name": (discovery.author_name or "")[:120],
                "liked_count": (discovery.liked_count or "")[:40],
                "processing_state": "waiting",
            }
            status = CollectionCandidateStatus.DISCOVERED
            if existing_experience is not None:
                status = CollectionCandidateStatus.IMPORTED
                metadata.update({
                    "duplicate": True,
                    "processing_state": "duplicate",
                    "imported_experience_id": str(existing_experience.id),
                })
            self._repository.create_collection_candidate(
                organization_id,
                collection_job_id=job.id,
                source_url=canonical_url,
                canonical_url=canonical_url,
                source_platform="小红书",
                title=discovery.title,
                snippet=discovery.title,
                status=status,
                metadata_json=metadata,
            )

        candidates = self._repository.list_collection_candidates(organization_id, job.id)
        summary = self._summary_from_metadata(job.metadata_json)
        summary["discovered_count"] = len(candidates)
        duplicate_count = sum(
            1 for candidate in candidates if candidate.metadata_json.get("duplicate") is True
        )
        summary["processed_count"] = max(summary["processed_count"], duplicate_count)
        self._update_xiaohongshu_job(
            organization_id,
            job.id,
            phase="fetch",
            progress_percent=10,
            progress_message=f"已读取 {len(candidates)} 张卡片，正在逐篇解析笔记。",
            summary=summary,
        )
        return candidates

    def queue_xiaohongshu_browser_note(
        self,
        organization_id: UUID,
        job_id: UUID,
        note: XiaohongshuBrowserNote,
    ) -> InterviewCollectionCandidateRecord:
        """校验并标记一篇浏览器笔记等待后台 OCR，不保存图片地址。"""

        job = self._require_xiaohongshu_browser_job(organization_id, job_id)
        if job.status in {CollectionJobStatus.SUCCEEDED, CollectionJobStatus.FAILED, CollectionJobStatus.CANCELLED}:
            raise ValueError("该信息收集任务已经结束，不能继续提交笔记。")
        note_id = self._normalize_xiaohongshu_note_id(note.note_id)
        candidate = self._repository.get_collection_candidate_by_canonical_url(
            organization_id,
            job.id,
            self._xiaohongshu_canonical_url(note_id),
        )
        if candidate is None:
            raise LookupError("该笔记不在当前搜索卡片中，请重新执行关键词搜索。")
        if candidate.status is not CollectionCandidateStatus.DISCOVERED:
            return candidate
        return self._repository.update_collection_candidate(
            organization_id,
            candidate.id,
            title=note.title,
            status=CollectionCandidateStatus.DISCOVERED,
            clear_errors=True,
            metadata_json={
                "processing_state": "queued",
                "author_name": (note.author_name or "")[:120],
                "browser_tag_count": len(note.tags),
                # 图片 URL 仅保留在 BackgroundTasks 的内存参数中。
                "declared_image_count": len(note.image_urls),
            },
        )

    def process_xiaohongshu_browser_note(
        self,
        organization_id: UUID,
        job_id: UUID,
        note: XiaohongshuBrowserNote,
    ) -> None:
        """后台处理一篇浏览器笔记，复用公开 URL 导入的 OCR 与分析链路。"""

        job = self._require_xiaohongshu_browser_job(organization_id, job_id)
        candidate = self._repository.get_collection_candidate_by_canonical_url(
            organization_id,
            job.id,
            note.canonical_url,
        )
        if candidate is None or candidate.status is not CollectionCandidateStatus.DISCOVERED:
            return
        self._repository.update_collection_candidate(
            organization_id,
            candidate.id,
            metadata_json={"processing_state": "running"},
        )
        summary_before = self._summary_from_metadata(job.metadata_json)
        self._update_xiaohongshu_job(
            organization_id,
            job.id,
            phase="ocr",
            progress_percent=min(
                92,
                10 + int(84 * summary_before["processed_count"] / max(job.requested_limit, 1)),
            ),
            progress_message=f"正在解析笔记并识别配图文字：{note.title or note.note_id}",
            summary=summary_before,
        )
        if note.source_error_code:
            result = self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.BLOCKED,
                error_code=note.source_error_code[:80],
                error_message=(note.source_error_message or "该笔记当前无法在 Web 端读取。")[:1000],
                metadata_json={
                    "processing_state": "skipped",
                    "source_restricted": True,
                },
            )
        else:
            try:
                result = self._process_xiaohongshu_note(
                    organization_id,
                    collection_job_id=job.id,
                    note=note,
                    include_images=True,
                    auto_import=True,
                    candidate_id=candidate.id,
                )
            except Exception as exc:
                LOGGER.exception("浏览器小红书笔记处理失败：job=%s note=%s", job.id, note.note_id)
                result = self._repository.update_collection_candidate(
                    organization_id,
                    candidate.id,
                    status=CollectionCandidateStatus.FAILED,
                    error_code="note_processing_failed",
                    error_message="该笔记的正文、图片识别或结构化处理未完成，可稍后重新收集。",
                    metadata_json={
                        "processing_state": "failed",
                        "error_class": exc.__class__.__name__,
                    },
                )

        refreshed_job = self._require_xiaohongshu_browser_job(organization_id, job.id)
        summary = self._summary_from_metadata(refreshed_job.metadata_json)
        summary["processed_count"] += 1
        analysis = self._candidate_analysis(result)
        if result.status is CollectionCandidateStatus.IMPORTED:
            summary["imported_count"] += 1
            summary["valid_count"] += 1
        elif result.status in {CollectionCandidateStatus.FAILED, CollectionCandidateStatus.BLOCKED}:
            summary["failed_count"] += 1
        elif analysis.get("is_valid_interview") is True:
            summary["valid_count"] += 1
        elif analysis.get("is_valid_interview") is False:
            summary["rejected_count"] += 1
        else:
            summary["incomplete_count"] += 1
        progress = 10 + int(84 * summary["processed_count"] / max(job.requested_limit, 1))
        self._update_xiaohongshu_job(
            organization_id,
            job.id,
            phase="import",
            progress_percent=min(progress, 94),
            progress_message=(
                f"已处理 {summary['processed_count']}/{summary['discovered_count']} 条笔记，"
                "正在更新面经库。"
            ),
            summary=summary,
        )

    def pause_xiaohongshu_browser_job(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        error_code: str,
        error_message: str,
        cancelled: bool = False,
    ) -> InterviewCollectionJobRecord:
        """在需要用户处理登录/验证或主动停止时保留当前批次。"""

        job = self._require_xiaohongshu_browser_job(organization_id, job_id)
        if job.status in {CollectionJobStatus.SUCCEEDED, CollectionJobStatus.FAILED, CollectionJobStatus.CANCELLED}:
            return job
        return self._repository.update_collection_job_status(
            organization_id,
            job.id,
            status=(CollectionJobStatus.CANCELLED if cancelled else CollectionJobStatus.NEEDS_USER_INTERACTION),
            error_code=(error_code or "browser_interaction_required")[:80],
            error_message=(error_message or "请检查小红书页面后继续。")[:1000],
            metadata_json={
                "phase": "paused",
                "progress_message": (error_message or "任务已暂停。")[:500],
            },
        )

    def complete_xiaohongshu_browser_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord:
        """在所有已发现候选均离开处理中状态后完成浏览器任务。"""

        job = self._require_xiaohongshu_browser_job(organization_id, job_id)
        candidates = self._repository.list_collection_candidates(organization_id, job.id)
        pending = [
            candidate for candidate in candidates
            if candidate.status is CollectionCandidateStatus.DISCOVERED
        ]
        if pending:
            raise ValueError(f"仍有 {len(pending)} 条笔记尚未处理完成。")
        summary = self._summary_from_metadata(job.metadata_json)
        return self._repository.update_collection_job_status(
            organization_id,
            job.id,
            status=CollectionJobStatus.SUCCEEDED,
            error_code=None,
            error_message=None,
            metadata_json={
                "phase": "import",
                "progress_percent": 100,
                "progress_message": (
                    f"收集完成：发现 {summary['discovered_count']} 条，"
                    f"有效 {summary['valid_count']} 条，新入库 {summary['imported_count']} 条。"
                ),
                "summary": summary,
            },
        )

    def create_xiaohongshu_import_job(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        source_url: str,
        requested_limit: int,
        include_images: bool,
        auto_import: bool,
    ) -> InterviewCollectionJobRecord:
        """登记一次由用户主动提交的小红书公开资料导入。

        此方法只写入任务描述和开关，不会在 HTTP 请求中抓取页面。真正的页面读取、图片
        OCR、LLM 甄别与入库由 ``run_xiaohongshu_import`` 在 FastAPI 后台任务执行，避免
        长任务占用浏览器请求，也避免把任何 Cookie 或原始图片写进数据库。
        """

        normalized_url = source_url.strip()
        self._validate_xiaohongshu_source_url(normalized_url)
        if not 1 <= requested_limit <= 50:
            raise ValueError("小红书单次导入数量必须在 1 到 50 之间")

        metadata = {
            "source_kind": "xiaohongshu_public_url",
            "source_url": normalized_url,
            "include_images": bool(include_images),
            "auto_import": bool(auto_import),
            "phase": "discover",
            "progress_percent": 0,
            "progress_message": "任务已创建，正在等待后台读取公开页面。",
            "summary": self._new_xiaohongshu_summary(),
        }
        return self._repository.create_collection_job(
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            platform_key="xiaohongshu",
            keyword=normalized_url,
            requested_limit=requested_limit,
            connector_kind=CollectionConnectorKind.URL_IMPORT,
            policy_decision=(
                "仅读取用户主动提交的小红书公开页面中实际暴露的笔记和公开配图；"
                "不保存账号、Cookie 或原始图片，不绕过登录、验证码或动态访问限制。"
            ),
            metadata_json=metadata,
        )

    def run_xiaohongshu_import(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> None:
        """建立小红书公开资料导入父链，不上传 URL、正文、图片或组织标识。"""

        trace_operation(
            run_name="career.interview_collection.xiaohongshu_import",
            run_type="chain",
            inputs={
                "source_kind": "xiaohongshu_public_url",
                "background_job": True,
            },
            metadata={
                "component": "interview_collection",
                "source_kind": "xiaohongshu_public_url",
                "privacy_mode": "metadata_only",
            },
            tags=("career", "interview-library", "xiaohongshu"),
            execute=lambda: self._run_xiaohongshu_import_untraced(
                organization_id,
                job_id,
            ),
            summarize=lambda result: {
                "status": result["status"],
                "completed": result["completed"],
                "count": result["count"],
                "error_class": result["error_class"],
            },
        )

    def _run_xiaohongshu_import_untraced(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> dict[str, object]:
        """按“发现 → 正文 → OCR → LLM → 入库”执行一项小红书公开导入任务。

        由 FastAPI ``BackgroundTasks`` 调用。任务内部每处理一篇笔记就更新一次进度，
        单篇读取、单图 OCR 或单篇模型失败只会形成可见候选失败记录，不会阻断同批的其他
        笔记；只有入口页面本身不可读或数据库无法写入时，才会将整项任务标为失败。
        """

        job = self._repository.claim_collection_job(organization_id, job_id)
        if job is None:
            existing_job = self._repository.get_collection_job(organization_id, job_id)
            if existing_job is None:
                LOGGER.warning("小红书导入任务不存在或已失去归属：%s", job_id)
                return {
                    "status": "skipped",
                    "completed": False,
                    "count": 0,
                    "error_class": "CollectionJobUnavailable",
                }

            LOGGER.info(
                "小红书导入任务已被其他后台调用抢占或已结束，跳过重复执行：job=%s status=%s",
                job_id,
                existing_job.status.value,
            )
            existing_summary = self._summary_from_metadata(existing_job.metadata_json)
            return {
                "status": existing_job.status.value,
                "completed": existing_job.status is CollectionJobStatus.SUCCEEDED,
                "count": existing_summary["processed_count"],
                "error_class": (
                    "ExistingCollectionJobFailed"
                    if existing_job.status is CollectionJobStatus.FAILED
                    else None
                ),
            }

        existing_metadata = dict(job.metadata_json)
        include_images = bool(existing_metadata.get("include_images", True))
        # 历史任务缺少该字段时也默认走人工确认，避免升级后意外自动写入面经库。
        auto_import = bool(existing_metadata.get("auto_import", False))
        summary = self._summary_from_metadata(existing_metadata)

        try:
            self._update_xiaohongshu_job(
                organization_id,
                job.id,
                phase="discover",
                progress_percent=6,
                progress_message="正在读取公开页面并发现可导入笔记。",
                summary=summary,
            )
            source_url = str(existing_metadata.get("source_url") or job.keyword).strip()
            result = self._xiaohongshu_source_adapter.collect(
                source_url,
                requested_limit=job.requested_limit,
            )
            summary["discovered_count"] = len(result.discovered_notes)
            self._update_xiaohongshu_job(
                organization_id,
                job.id,
                phase="fetch",
                progress_percent=14,
                progress_message=(
                    f"已发现 {summary['discovered_count']} 条公开笔记，正在读取正文。"
                ),
                summary=summary,
            )

            for failure in result.note_failures:
                self._create_xiaohongshu_failure_candidate(
                    organization_id,
                    job.id,
                    source_url=failure.source_url,
                    canonical_url=failure.canonical_url,
                    error_code=failure.error_code,
                    error_message=failure.error_message,
                    phase="fetch",
                )
                summary["failed_count"] += 1

            note_count = len(result.notes)
            for note_index, note in enumerate(result.notes, start=1):
                try:
                    candidate = self._process_xiaohongshu_note(
                        organization_id,
                        collection_job_id=job.id,
                        note=note,
                        include_images=include_images,
                        auto_import=auto_import,
                    )
                    summary["processed_count"] += 1
                    analysis = self._candidate_analysis(candidate)
                    if analysis.get("is_valid_interview") is True:
                        summary["valid_count"] += 1
                    elif analysis.get("is_valid_interview") is False:
                        summary["rejected_count"] += 1
                    elif analysis.get("status") == "insufficient_source":
                        summary["incomplete_count"] += 1
                    if candidate.status is CollectionCandidateStatus.IMPORTED:
                        summary["imported_count"] += 1
                    elif candidate.status is CollectionCandidateStatus.FAILED:
                        summary["failed_count"] += 1
                except CollectionOperationError as exc:
                    self._create_xiaohongshu_failure_candidate(
                        organization_id,
                        job.id,
                        source_url=note.source_url,
                        canonical_url=note.canonical_url,
                        error_code=exc.code,
                        error_message=exc.message,
                        phase="process",
                    )
                    summary["failed_count"] += 1
                except (OSError, ValueError) as exc:
                    LOGGER.warning(
                        "小红书笔记处理未完成：job=%s note=%s error=%s",
                        job.id,
                        note.note_id,
                        exc,
                    )
                    self._create_xiaohongshu_failure_candidate(
                        organization_id,
                        job.id,
                        source_url=note.source_url,
                        canonical_url=note.canonical_url,
                        error_code="note_processing_failed",
                        error_message="该笔记的正文、配图或结构化处理未完成，请查看其他候选结果。",
                        phase="process",
                    )
                    summary["failed_count"] += 1

                progress_percent = 14 + int(80 * note_index / max(note_count, 1))
                self._update_xiaohongshu_job(
                    organization_id,
                    job.id,
                    phase="import",
                    progress_percent=min(progress_percent, 94),
                    progress_message=(
                        f"已处理 {note_index}/{note_count} 条笔记，正在更新面经库。"
                    ),
                    summary=summary,
                )

            self._repository.update_collection_job_status(
                organization_id,
                job.id,
                status=CollectionJobStatus.SUCCEEDED,
                error_code=None,
                error_message=None,
                metadata_json={
                    "phase": "import",
                    "progress_percent": 100,
                    "progress_message": (
                        f"导入完成：发现 {summary['discovered_count']} 条，"
                        f"有效 {summary['valid_count']} 条，已写入 {summary['imported_count']} 条。"
                    ),
                    "summary": summary,
                },
            )
            return {
                "status": "succeeded",
                "completed": True,
                "count": summary["processed_count"],
                "error_class": None,
            }
        except CollectionOperationError as exc:
            self._finish_xiaohongshu_job_failed(
                organization_id,
                job.id,
                summary=summary,
                error_code=exc.code,
                error_message=exc.message,
            )
            return {
                "status": "failed",
                "completed": False,
                "count": summary["processed_count"],
                "error_class": exc.__class__.__name__,
            }
        except Exception as exc:
            LOGGER.exception("小红书导入任务发生未预期错误：job=%s", job.id)
            self._finish_xiaohongshu_job_failed(
                organization_id,
                job.id,
                summary=summary,
                error_code="xiaohongshu_import_failed",
                error_message="小红书公开资料导入未完成，请查看已生成的候选结果后重试。",
            )
            return {
                "status": "failed",
                "completed": False,
                "count": summary["processed_count"],
                "error_class": exc.__class__.__name__,
            }

    def _process_xiaohongshu_note(
        self,
        organization_id: UUID,
        *,
        collection_job_id: UUID,
        note: Any,
        include_images: bool,
        auto_import: bool,
        candidate_id: UUID | None = None,
    ) -> InterviewCollectionCandidateRecord:
        """处理一篇已读取的笔记，并在必要时把有效面经自动写入现有面经库。"""

        collection_job = self._repository.get_collection_job(
            organization_id,
            collection_job_id,
        )
        if collection_job is None:
            raise LookupError("采集任务不存在")
        markdown_content, image_metadata = self._build_xiaohongshu_markdown(
            note,
            include_images=include_images,
        )
        evidence = image_metadata.get("evidence")
        has_reviewable_evidence = self._has_reviewable_xiaohongshu_evidence(evidence)
        if (
            isinstance(evidence, Mapping)
            and evidence.get("is_complete") is False
            and not has_reviewable_evidence
        ):
            analysis = InterviewEvidenceAnalysis.insufficient_source(
                self._incomplete_source_message(evidence),
            )
        else:
            analysis = self._evidence_analyzer.analyze(
                organization_id,
                source_url=note.canonical_url,
                title=note.title,
                markdown_content=markdown_content,
            )
        # 模型的“有效性”只用于自动入库；候选是否可供人工审核取决于是否已有正文。
        # 这样像“东方财富二面”这类 OCR 已拿到题目、但页面材料不完整的情况仍可编辑保存。
        candidate_markdown = analysis.normalized_markdown or (
            markdown_content if has_reviewable_evidence else ""
        )
        normalized_markdown = self._strip_xiaohongshu_collection_wrappers(candidate_markdown)
        candidate_status = (
            CollectionCandidateStatus.FETCHED
            if normalized_markdown
            else CollectionCandidateStatus.EMPTY
        )
        content_hash = sha256(normalized_markdown.encode("utf-8")).hexdigest()
        candidate_metadata = {
            "source_kind": "xiaohongshu_note",
            "note_id": note.note_id,
            "author_name": note.author_name,
            "tags": list(getattr(note, "tags", ()) or ()),
            "processing_state": "completed",
            "images": image_metadata,
            "analysis": analysis.as_metadata(),
        }
        if candidate_id is None:
            candidate = self._repository.create_collection_candidate(
                organization_id,
                collection_job_id=collection_job_id,
                source_url=note.source_url,
                canonical_url=note.canonical_url,
                source_platform="小红书",
                title=note.title,
                snippet=(analysis.summary_text or self._candidate_snippet(markdown_content)),
                published_at=self._parse_xiaohongshu_published_at(note.published_at),
                extracted_markdown=normalized_markdown,
                content_hash=content_hash,
                status=candidate_status,
                metadata_json=candidate_metadata,
            )
        else:
            candidate = self._repository.update_collection_candidate(
                organization_id,
                candidate_id,
                title=note.title,
                snippet=(analysis.summary_text or self._candidate_snippet(markdown_content)),
                published_at=self._parse_xiaohongshu_published_at(note.published_at),
                extracted_markdown=normalized_markdown,
                content_hash=content_hash,
                status=candidate_status,
                clear_errors=True,
                metadata_json=candidate_metadata,
            )
        if not auto_import or not self._can_auto_import(analysis, image_metadata):
            return candidate

        try:
            experience = self._library_service.ingest(
                organization_id,
                InterviewExperienceDraft(
                    company_name=analysis.company_name or "",
                    role_name=analysis.role_name or "",
                    interview_date=analysis.interview_date,
                    markdown_content=normalized_markdown,
                    source_type=InterviewSourceType.PUBLIC_URL,
                    source_platform="小红书",
                    source_url=note.canonical_url,
                    summary_text=analysis.summary_text or note.title,
                    tags=analysis.tags,
                    # 同公司同岗位的不同公开笔记不能相互覆盖，哈希使名称稳定且可追溯。
                    job_name=self._xiaohongshu_job_name(
                        analysis.role_name or "未知岗位",
                        content_hash,
                    ),
                ),
                trigger_type=IngestionTriggerType.MANUAL_URL,
                created_by_actor_id=collection_job.created_by_actor_id,
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            LOGGER.warning(
                "小红书候选已解析，但自动入库未完成：candidate=%s error=%s",
                candidate.id,
                exc,
            )
            return self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                status=CollectionCandidateStatus.FETCHED,
                metadata_json={
                    "auto_import_error": "已识别为有效面经，但自动写入未完成；可在候选列表手动导入。",
                },
            )

        return self._repository.update_collection_candidate_status(
            organization_id,
            candidate.id,
            status=CollectionCandidateStatus.IMPORTED,
            metadata_json={"imported_experience_id": str(experience.id)},
        )

    def _build_xiaohongshu_markdown(
        self,
        note: Any,
        *,
        include_images: bool,
    ) -> tuple[str, dict[str, object]]:
        """把公开笔记正文与逐张 OCR 文本归一化为一份候选 Markdown。"""

        image_metadata: dict[str, object] = {
            "body_source": str(getattr(note, "body_source", "unknown") or "unknown"),
            "body_text_characters": len(str(getattr(note, "body_text", "") or "").strip()),
            "body_question_signal_count": self._question_signal_count(
                str(getattr(note, "body_text", "") or ""),
            ),
            "declared_count": len(note.image_urls),
            "downloaded_count": 0,
            "ocr_success_count": 0,
            "ocr_text_characters": 0,
            "ocr_question_signal_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "failures": [],
        }
        sections = [note.markdown_content]
        if not include_images or not note.image_urls:
            if not include_images and note.image_urls:
                image_metadata["skipped_count"] = len(note.image_urls)
            return (
                "\n\n".join(part for part in sections if part).strip(),
                self._finalize_xiaohongshu_evidence(note, image_metadata),
            )

        image_result = self._xiaohongshu_source_adapter.download_images(note)
        image_metadata["downloaded_count"] = len(image_result.images)
        image_metadata["skipped_count"] = image_result.skipped_image_count
        failures: list[dict[str, object]] = [
            {
                "index": failure.index,
                "code": failure.error_code,
                "message": failure.error_message,
            }
            for failure in image_result.failures
        ]
        image_metadata["failed_count"] = len(failures)

        if self._temporary_attachment_store is None or self._attachment_parser is None:
            if image_result.images:
                failures.append(
                    {
                        "index": None,
                        "code": "image_parser_unavailable",
                        "message": "图片已发现，但当前环境未启用图片解析服务。",
                    },
                )
            image_metadata["failed_count"] = len(failures)
            image_metadata["failures"] = failures[:12]
            return (
                "\n\n".join(part for part in sections if part).strip(),
                self._finalize_xiaohongshu_evidence(note, image_metadata),
            )

        attachments = []
        ocr_sections: list[str] = []
        try:
            for image in image_result.images:
                try:
                    attachment = self._temporary_attachment_store.save_bytes(
                        image.data,
                        self._xiaohongshu_image_filename(image.index, image.media_type),
                        image.media_type,
                        AttachmentKind.INTERVIEW_EVIDENCE_IMAGE,
                    )
                    attachments.append(attachment)
                    parsed = self._attachment_parser.parse(attachment)
                    extracted_text = parsed.extracted_text.strip()
                    if extracted_text:
                        ocr_sections.append(
                            f"### 配图 {image.index} 的文字识别\n\n{extracted_text}",
                        )
                        image_metadata["ocr_success_count"] = int(
                            image_metadata["ocr_success_count"],
                        ) + 1
                        image_metadata["ocr_text_characters"] = int(
                            image_metadata["ocr_text_characters"],
                        ) + len(extracted_text)
                        image_metadata["ocr_question_signal_count"] = int(
                            image_metadata["ocr_question_signal_count"],
                        ) + self._question_signal_count(extracted_text)
                    else:
                        failures.append(
                            {
                                "index": image.index,
                                "code": "image_text_unavailable",
                                "message": (
                                    parsed.document_understanding_error
                                    or "未从该配图提取到可用文字。"
                                )[:300],
                            },
                        )
                except (OSError, ValueError) as exc:
                    LOGGER.info("小红书配图解析失败：note=%s image=%s", note.note_id, image.index)
                    failures.append(
                        {
                            "index": image.index,
                            "code": "image_parse_failed",
                            "message": self._safe_error_message(exc, "该配图未能完成解析。"),
                        },
                    )
        finally:
            self._temporary_attachment_store.cleanup(tuple(attachments))

        image_metadata["failed_count"] = len(failures)
        image_metadata["failures"] = failures[:12]
        if ocr_sections:
            sections.append("## 配图文字识别\n\n" + "\n\n".join(ocr_sections))
        return (
            "\n\n".join(part for part in sections if part).strip(),
            self._finalize_xiaohongshu_evidence(note, image_metadata),
        )

    @classmethod
    def _finalize_xiaohongshu_evidence(
        cls,
        note: Any,
        image_metadata: dict[str, object],
    ) -> dict[str, object]:
        """记录正文/OCR 的完整性，避免把“没有拿全”交给 LLM 误判为无效。

        小红书的 meta description 仅是摘要；可见 DOM 还可能只是平台外壳。因此只有公开
        初始化正文、可验证的 DOM 正文，或实际 OCR 文本才能作为面经甄别证据。若存在
        配图但正文不足且 OCR 未成功，本条进入“采集不完整”而非“已过滤”。
        """

        body_source = str(image_metadata.get("body_source") or "unknown")
        body_characters = int(image_metadata.get("body_text_characters") or 0)
        body_questions = int(image_metadata.get("body_question_signal_count") or 0)
        declared_count = int(image_metadata.get("declared_count") or 0)
        downloaded_count = int(image_metadata.get("downloaded_count") or 0)
        ocr_success_count = int(image_metadata.get("ocr_success_count") or 0)
        ocr_characters = int(image_metadata.get("ocr_text_characters") or 0)
        ocr_questions = int(image_metadata.get("ocr_question_signal_count") or 0)

        # OCR 文本可能恰好是一道简短但完整的问题。此时题目结构比字符数更可靠，
        # 不应因 24 个字符的阈值把真实面经误判为“采集不完整”。
        has_ocr_evidence = ocr_success_count > 0 and (
            ocr_characters >= MIN_OCR_EVIDENCE_CHARACTERS or ocr_questions > 0
        )
        if body_source in {"initial_state", "structured", "browser_page", "unknown"}:
            has_body_evidence = body_characters > 0
        elif body_source == "visible_dom":
            has_body_evidence = (
                body_questions > 0 or body_characters >= MIN_VISIBLE_DOM_EVIDENCE_CHARACTERS
            )
        else:
            # 页面 meta 只能做标题/摘要展示，不能作为面经有效性的唯一依据。
            has_body_evidence = False

        question_signal_count = body_questions + ocr_questions
        reasons: list[str] = []
        if not has_body_evidence:
            if body_source == "meta":
                reasons.append("当前仅获得页面摘要，未获得可验证的笔记正文")
            elif body_source == "visible_dom":
                reasons.append("当前只获得疑似页面外壳的可见文本，未确认笔记正文")
            else:
                reasons.append("未获得可供甄别的笔记正文")

        body_can_cover_unread_images = (
            has_body_evidence
            and (
                body_characters >= MIN_BODY_EVIDENCE_WITH_UNREAD_IMAGES
                or question_signal_count > 0
            )
        )
        if declared_count > 0 and not has_ocr_evidence and not body_can_cover_unread_images:
            if downloaded_count == 0:
                reasons.append("已发现配图，但公开图片未能下载或读取")
            else:
                reasons.append("已发现配图，但未从配图提取到可用文字")

        is_complete = (has_body_evidence or has_ocr_evidence) and not reasons
        image_metadata["evidence"] = {
            "is_complete": is_complete,
            "body_source": body_source,
            "body_text_characters": body_characters,
            "image_declared_count": declared_count,
            "image_downloaded_count": downloaded_count,
            "ocr_success_count": ocr_success_count,
            "ocr_text_characters": ocr_characters,
            "question_signal_count": question_signal_count,
            "insufficiency_reasons": reasons,
        }
        return image_metadata

    @staticmethod
    def _question_signal_count(value: str) -> int:
        """仅用于判断文本是否像可追溯题目，不把规则结果当成面经结论。"""

        normalized = str(value or "")
        numbered_questions = re.findall(r"(?:^|\n)\s*\d{1,2}\s*[.、．、)]", normalized)
        chinese_numbered_questions = re.findall(
            r"(?:^|\n)\s*[一二三四五六七八九十]+\s*[、.．]",
            normalized,
        )
        chinese_question_marks = normalized.count("？")
        ascii_question_marks = normalized.count("?")
        return _count_interview_question_signals(normalized)

    @staticmethod
    def _incomplete_source_message(evidence: Mapping[str, object]) -> str:
        reasons = evidence.get("insufficiency_reasons")
        if isinstance(reasons, list):
            readable = [str(item).strip() for item in reasons if str(item).strip()]
            if readable:
                return "；".join(readable)[:500] + "。本条未作有效性判定，也不会自动入库。"
        return "未获得完整正文或配图文字，本条未作有效性判定，也不会自动入库。"

    @staticmethod
    def _has_reviewable_xiaohongshu_evidence(evidence: object) -> bool:
        """判断不完整页面中是否仍有足够材料交给 Agent 和人工审核。

        该判断不把来源完整度放宽为自动入库资格，只避免把已经读到的真实题目误当成
        空页面。自动入库仍由 ``_can_auto_import`` 的完整度校验严格控制。
        """

        if not isinstance(evidence, Mapping):
            return False
        question_count = int(evidence.get("question_signal_count") or 0)
        ocr_characters = int(evidence.get("ocr_text_characters") or 0)
        body_characters = int(evidence.get("body_text_characters") or 0)
        return (
            question_count > 0
            or ocr_characters >= MIN_OCR_EVIDENCE_CHARACTERS
            or body_characters >= MIN_VISIBLE_DOM_EVIDENCE_CHARACTERS
        )

    @staticmethod
    def _strip_xiaohongshu_collection_wrappers(markdown_content: str) -> str:
        """移除采集包装信息，仅把可编辑的真实正文交给候选审核区。

        原始证据包仍保存在采集过程和元数据中，便于追溯；这里不删除真实的面试轮次
        标题、问题编号或复盘段落，只移除平台口号、图片 URL 和解析过程标题。
        """

        if not markdown_content:
            return ""
        wrapper_heading = re.compile(
            r"^#{1,6}\s*(?:公开配图|配图文字识别|解析原文|合并面经材料|材料\s*\d+.*|配图\s*\d+\s*的文字识别)\s*$",
            re.IGNORECASE,
        )
        image_reference = re.compile(
            r"^-\s*配图\s*\d+\s*:\s*https?://\S+\s*$",
            re.IGNORECASE,
        )
        source_title = re.compile(r"^#\s*.+?\s*-\s*小红书\s*$")
        cleaned_lines: list[str] = []
        for raw_line in markdown_content.replace("\r\n", "\n").split("\n"):
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                cleaned_lines.append("")
                continue
            if stripped == "来小红书，和全世界最有趣的人做朋友":
                continue
            if source_title.match(stripped) or wrapper_heading.match(stripped):
                continue
            if image_reference.match(stripped):
                continue
            if "xhscdn.com/" in stripped and "http" in stripped:
                continue
            cleaned_lines.append(line)

        compact_lines: list[str] = []
        previous_blank = False
        for line in cleaned_lines:
            is_blank = not line.strip()
            if is_blank and previous_blank:
                continue
            compact_lines.append(line)
            previous_blank = is_blank
        return "\n".join(compact_lines).strip()

    def _create_xiaohongshu_failure_candidate(
        self,
        organization_id: UUID,
        collection_job_id: UUID,
        *,
        source_url: str,
        canonical_url: str,
        error_code: str,
        error_message: str,
        phase: str,
    ) -> InterviewCollectionCandidateRecord:
        """将单篇失败以候选记录保存，方便用户看到“哪一篇、在哪一步”失败。"""

        return self._repository.create_collection_candidate(
            organization_id,
            collection_job_id=collection_job_id,
            source_url=source_url,
            canonical_url=canonical_url,
            source_platform="小红书",
            status=CollectionCandidateStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            metadata_json={
                "source_kind": "xiaohongshu_note",
                "failure_phase": phase,
            },
        )

    def _update_xiaohongshu_job(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        phase: str,
        progress_percent: int,
        progress_message: str,
        summary: Mapping[str, int],
    ) -> None:
        """写入可轮询的阶段与计数，不在数据库中写入抓取原文或图片字节。"""

        self._repository.update_collection_job_status(
            organization_id,
            job_id,
            status=CollectionJobStatus.RUNNING,
            error_code=None,
            error_message=None,
            metadata_json={
                "phase": phase,
                "progress_percent": max(0, min(100, progress_percent)),
                "progress_message": progress_message[:500],
                "summary": dict(summary),
            },
        )

    def _finish_xiaohongshu_job_failed(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        summary: Mapping[str, int],
        error_code: str,
        error_message: str,
    ) -> None:
        """将入口页或不可恢复错误收敛为可读任务状态，避免任务永久停在运行中。"""

        try:
            self._repository.update_collection_job_status(
                organization_id,
                job_id,
                status=CollectionJobStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
                metadata_json={
                    "phase": "failed",
                    "progress_percent": 100,
                    "progress_message": error_message[:500],
                    "summary": dict(summary),
                },
            )
        except Exception:
            LOGGER.exception("写入小红书任务失败状态时发生错误：job=%s", job_id)

    @staticmethod
    def _validate_xiaohongshu_source_url(source_url: str) -> None:
        """在创建任务前给出清晰错误；具体公开页解析仍由专用适配器负责。"""

        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() != "https" or not hostname:
            raise CollectionOperationError("invalid_url", "请粘贴小红书公开 HTTPS 链接。")
        if hostname != "xiaohongshu.com" and not hostname.endswith(".xiaohongshu.com"):
            raise CollectionOperationError("unsupported_platform", "当前入口仅支持小红书公开链接。")
        if parsed.username or parsed.password:
            raise CollectionOperationError("invalid_url", "链接中不能包含账号或密码信息。")
        try:
            port = parsed.port
        except ValueError as exc:
            raise CollectionOperationError("invalid_url", "链接端口格式不正确。") from exc
        if port not in {None, 443}:
            raise CollectionOperationError("invalid_url", "仅支持标准 HTTPS 端口 443。")

    def _require_xiaohongshu_browser_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord:
        job = self._repository.get_collection_job(organization_id, job_id)
        if job is None:
            raise LookupError("信息收集任务不存在或无访问权限")
        if (
            job.platform_key != "xiaohongshu"
            or job.connector_kind is not CollectionConnectorKind.USER_AUTHORIZED_BROWSER
        ):
            raise ValueError("该任务不是小红书浏览器信息收集任务。")
        return job

    @staticmethod
    def _normalize_xiaohongshu_note_id(value: str) -> str:
        normalized = str(value or "").strip()
        if not re.fullmatch(r"[0-9a-zA-Z]{24}", normalized):
            raise ValueError("小红书笔记标识不正确。")
        return normalized

    @classmethod
    def _xiaohongshu_canonical_url(cls, note_id: str) -> str:
        return f"https://www.xiaohongshu.com/explore/{cls._normalize_xiaohongshu_note_id(note_id)}"

    @staticmethod
    def _new_xiaohongshu_summary() -> dict[str, int]:
        return {
            "discovered_count": 0,
            "processed_count": 0,
            "valid_count": 0,
            "imported_count": 0,
            "rejected_count": 0,
            "incomplete_count": 0,
            "failed_count": 0,
        }

    @classmethod
    def _summary_from_metadata(cls, metadata: Mapping[str, object]) -> dict[str, int]:
        raw = metadata.get("summary")
        if not isinstance(raw, Mapping):
            return cls._new_xiaohongshu_summary()
        result = cls._new_xiaohongshu_summary()
        for key in result:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                result[key] = max(0, value)
        return result

    @staticmethod
    def _candidate_analysis(candidate: InterviewCollectionCandidateRecord) -> Mapping[str, object]:
        analysis = candidate.metadata_json.get("analysis")
        return analysis if isinstance(analysis, Mapping) else {}

    @staticmethod
    def _candidate_snippet(markdown_content: str) -> str:
        return " ".join(markdown_content.split())[:500]

    @staticmethod
    def _can_auto_import(
        analysis: InterviewEvidenceAnalysis,
        image_metadata: Mapping[str, object],
    ) -> bool:
        evidence = image_metadata.get("evidence")
        source_is_complete = isinstance(evidence, Mapping) and evidence.get("is_complete") is True
        return (
            source_is_complete
            and
            analysis.is_valid_interview is True
            and bool(analysis.company_name)
            and bool(analysis.role_name)
            and bool(analysis.questions)
            and analysis.confidence is not None
            and analysis.confidence >= 0.6
        )

    @staticmethod
    def _xiaohongshu_job_name(role_name: str, content_hash: str) -> str:
        """用来源哈希区分同公司同岗位的多篇笔记，避免数据库 upsert 覆盖历史。"""

        normalized_role = " ".join(role_name.split())[:180] or "未知岗位"
        return f"{normalized_role} · 小红书 · {content_hash[:8]}"

    @staticmethod
    def _xiaohongshu_image_filename(index: int, media_type: str) -> str:
        extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "image/x-ms-bmp": ".bmp",
            "image/tiff": ".tiff",
        }
        return f"xiaohongshu-image-{index}{extensions.get(media_type.lower(), '.png')}"

    @staticmethod
    def _parse_xiaohongshu_published_at(value: object) -> datetime | None:
        """兼容公开状态中的 ISO 时间、日期和毫秒级时间戳。"""

        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        try:
            if normalized.isdigit():
                timestamp = int(normalized)
                if timestamp > 100_000_000_000:
                    timestamp //= 1000
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if len(normalized) == 10:
                return datetime.combine(date.fromisoformat(normalized), datetime.min.time(), tzinfo=timezone.utc)
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _safe_error_message(exc: Exception, fallback: str) -> str:
        message = " ".join(str(exc).split()).strip()
        return (message[:300] if message else fallback)

    def create_keyword_collection_job(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        platform_key: str,
        keyword: str,
        requested_limit: int,
    ) -> InterviewCollectionJobRecord:
        """创建平台关键词任务；小红书复用公开搜索页导入链路。"""

        policy = self._POLICIES.get(platform_key.strip().lower())
        if policy is None or policy.key == "public_url":
            raise ValueError("请选择小红书、牛客或脉脉等已登记的平台。")
        normalized_keyword = " ".join(keyword.split())
        if not normalized_keyword:
            raise ValueError("请输入检索关键词。")
        if policy.key == "xiaohongshu":
            # 兼容既有公开关键词入口；新的“信息收集”按钮使用专用浏览器任务接口。
            return self.create_xiaohongshu_keyword_import_job(
                organization_id,
                created_by_actor_id=created_by_actor_id,
                keyword=normalized_keyword,
                requested_limit=requested_limit,
            )
        job = self._repository.create_collection_job(
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            platform_key=policy.key,
            keyword=normalized_keyword,
            requested_limit=requested_limit,
            connector_kind=policy.connector_kind,
            policy_decision=policy.policy_decision,
        )
        if not policy.can_run_keyword_search:
            return self._repository.update_collection_job_status(
                organization_id,
                job.id,
                status=CollectionJobStatus.NEEDS_USER_INTERACTION,
                error_code="connector_not_authorized",
                error_message="该平台尚未接入官方 API 或用户授权浏览器连接器，不能自动检索正文。",
            )
        return job

    def create_xiaohongshu_keyword_import_job(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        keyword: str,
        requested_limit: int,
    ) -> InterviewCollectionJobRecord:
        """把关键词转换为公开搜索 URL，并交给已有的小红书导入编排执行。"""

        normalized_keyword = " ".join(keyword.split())
        if not normalized_keyword:
            raise ValueError("请输入小红书搜索关键词。")
        if not 1 <= requested_limit <= 50:
            raise ValueError("小红书单次采集数量必须在 1 到 50 之间")

        policy = self._POLICIES["xiaohongshu"]
        source_url = "https://www.xiaohongshu.com/search_result?" + urlencode(
            {"keyword": normalized_keyword}
        )
        metadata = {
            "source_kind": "xiaohongshu_keyword_search",
            "source_url": source_url,
            "search_keyword": normalized_keyword,
            "include_images": True,
            # 关键词任务同样必须先展示 Agent 整理后的候选，由用户确认后再入库。
            "auto_import": False,
            "phase": "discover",
            "progress_percent": 0,
            "progress_message": "任务已创建，正在等待后台读取公开搜索结果。",
            "summary": self._new_xiaohongshu_summary(),
        }
        return self._repository.create_collection_job(
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            platform_key=policy.key,
            keyword=normalized_keyword,
            requested_limit=requested_limit,
            connector_kind=CollectionConnectorKind.URL_IMPORT,
            policy_decision=policy.policy_decision,
            metadata_json=metadata,
        )

    def collect_public_url(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        source_url: str,
    ) -> tuple[InterviewCollectionJobRecord, InterviewCollectionCandidateRecord]:
        """读取用户明确提交的公开文章，生成待选择候选项。"""

        return trace_operation(
            run_name="career.interview_collection.public_url_import",
            run_type="chain",
            inputs={
                "requested_count": 1,
            },
            metadata={
                "component": "interview_collection",
                "source_kind": "public_url",
                "stage": "candidate_import",
            },
            execute=lambda: self._collect_public_url(
                organization_id,
                created_by_actor_id=created_by_actor_id,
                source_url=source_url,
            ),
            summarize=lambda result: {
                "status": result[0].status.value,
                "candidate_count": 1,
                "candidate_status": result[1].status.value,
                "output_characters": len(result[1].extracted_markdown or ""),
            },
            tags=("career.interview", "public_url_import"),
        )

    def _collect_public_url(
        self,
        organization_id: UUID,
        *,
        created_by_actor_id: UUID,
        source_url: str,
    ) -> tuple[InterviewCollectionJobRecord, InterviewCollectionCandidateRecord]:
        """执行一次公开页面读取与候选持久化；仅由观测包装入口调用。"""

        job = self._repository.create_collection_job(
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            platform_key="public_url",
            keyword=source_url,
            requested_limit=1,
            connector_kind=CollectionConnectorKind.URL_IMPORT,
            policy_decision=self._POLICIES["public_url"].policy_decision,
        )
        self._repository.update_collection_job_status(
            organization_id,
            job.id,
            status=CollectionJobStatus.RUNNING,
        )
        try:
            article = self._article_extractor.extract(source_url)
            platform = self._infer_platform(article.canonical_url)
            content_hash = sha256(article.markdown_content.encode("utf-8")).hexdigest()
            candidate = self._repository.create_collection_candidate(
                organization_id,
                collection_job_id=job.id,
                source_url=article.source_url,
                canonical_url=article.canonical_url,
                source_platform=platform,
                title=article.title,
                snippet=article.markdown_content.replace("\n", " ")[:500],
                extracted_markdown=article.markdown_content,
                content_hash=content_hash,
                status=CollectionCandidateStatus.FETCHED,
            )
            completed = self._repository.update_collection_job_status(
                organization_id,
                job.id,
                status=CollectionJobStatus.SUCCEEDED,
            )
            return completed, candidate
        except CollectionOperationError as exc:
            self._repository.update_collection_job_status(
                organization_id,
                job.id,
                status=CollectionJobStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise

    def select_candidate(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> InterviewCollectionCandidateRecord:
        """把已读取的候选标记为待入库，供页面填补公司/岗位/日期。"""

        candidate = self._repository.get_collection_candidate(organization_id, candidate_id)
        if candidate is None:
            raise LookupError("候选资料不存在或无访问权限")
        if not candidate.extracted_markdown:
            raise ValueError("候选资料尚未获得可用正文，不能入库。")
        return self._repository.set_collection_candidate_status(
            organization_id,
            candidate_id,
            status=CollectionCandidateStatus.SELECTED,
        )

    def ingest_selected_candidate(
        self,
        organization_id: UUID,
        *,
        actor_id: UUID,
        can_manage_all: bool = False,
        candidate_id: UUID,
        company_name: str,
        role_name: str,
        interview_date,
        summary_text: str | None,
        tags: tuple[str, ...],
        markdown_content: str | None = None,
    ):
        """将用户确认后的候选正文转换为 Markdown 面经并进入既有 RAG 流程。

        ``markdown_content`` 是审核台提交的人工修订正文；传入时先持久化到候选，再用于
        入库，确保面经库、RAG 切片和页面中显示的是用户最后确认的同一份内容。
        """

        candidate = self.select_candidate(organization_id, candidate_id)
        job = self._repository.get_collection_job(organization_id, candidate.collection_job_id)
        if job is None:
            raise LookupError("采集任务不存在")
        if not can_manage_all and job.created_by_actor_id != actor_id:
            raise PermissionError("仅采集任务发起人或管理员可以保存该候选面经")
        reviewed_markdown = (markdown_content or "").strip()
        if reviewed_markdown:
            reviewed_hash = sha256(reviewed_markdown.encode("utf-8")).hexdigest()
            candidate = self._repository.update_collection_candidate(
                organization_id,
                candidate.id,
                extracted_markdown=reviewed_markdown,
                content_hash=reviewed_hash,
                status=CollectionCandidateStatus.SELECTED,
                metadata_json={
                    "manual_review": {
                        "body_confirmed": True,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
            )
        if not candidate.extracted_markdown:
            raise ValueError("候选资料未包含可入库正文。")
        experience = self._library_service.ingest(
            organization_id,
            InterviewExperienceDraft(
                company_name=company_name,
                role_name=role_name,
                interview_date=interview_date,
                markdown_content=candidate.extracted_markdown,
                source_type=InterviewSourceType.PUBLIC_URL,
                source_platform=candidate.source_platform,
                source_url=candidate.canonical_url,
                summary_text=summary_text or candidate.title,
                tags=tags,
            ),
            trigger_type=IngestionTriggerType.MANUAL_URL,
            created_by_actor_id=job.created_by_actor_id or actor_id,
            can_manage_all=can_manage_all,
        )
        self._repository.set_collection_candidate_status(
            organization_id,
            candidate.id,
            status=CollectionCandidateStatus.IMPORTED,
            metadata_json={
                "imported_experience_id": str(experience.id),
                "manual_review": {
                    "body_confirmed": True,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                },
            },
        )
        if self._public_web_coordinator is not None:
            self._public_web_coordinator.attach_candidate_sources(
                organization_id,
                candidate,
                experience.id,
            )
        return experience

    @classmethod
    def _infer_platform(cls, source_url: str) -> str:
        hostname = (urlparse(source_url).hostname or "").lower()
        labels = {
            "xiaohongshu.com": "小红书",
            "nowcoder.com": "牛客",
            "maimai.cn": "脉脉",
            "zhihu.com": "知乎",
        }
        for domain, label in labels.items():
            if hostname == domain or hostname.endswith(f".{domain}"):
                return label
        return hostname or "公开网页"
