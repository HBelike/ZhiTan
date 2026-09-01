"""小红书公开面经页面的只读采集适配器。

这个模块只处理用户明确提交的、公开可访问的小红书 HTTPS 链接。它不会保存或发送
Cookie，不执行 JavaScript，也不会复刻私有接口、登录、验证码或反爬绕过逻辑。

可采集的输入分为两类：

* 单篇公开笔记：解析 HTML 中实际出现的标题、正文和图片链接；
* 个人页、收藏页、搜索页：只发现 HTML/公开初始化状态中真实暴露的笔记链接，再逐篇
  请求公开笔记页。

小红书部分页面的卡片列表只在登录后的浏览器运行时加载。若公开 HTML 没有暴露笔记
链接，本模块会明确返回 ``CollectionOperationError``，由上层提示用户改贴单篇公开
笔记链接或使用后续的用户授权浏览器导入能力。

本模块不调用 OCR 或 LLM。上层编排层可把 ``XiaohongshuDownloadedImage.data`` 交给
现有附件解析/OCR 链路，再把 ``XiaohongshuNote.markdown_content`` 及 OCR 文本交给模型
判断是否为有效面经。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import html as html_module
from html.parser import HTMLParser
import json
import re
from typing import Any, Final, Iterable
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx

from src.career_assistant.interview_library.collection import CollectionOperationError


MAX_SOURCE_HTML_BYTES: Final[int] = 3 * 1024 * 1024
MAX_NOTE_TEXT_CHARACTERS: Final[int] = 120_000
DEFAULT_NOTE_LIMIT: Final[int] = 20
MAX_NOTE_LIMIT: Final[int] = 50
DEFAULT_MAX_IMAGES_PER_NOTE: Final[int] = 12
DEFAULT_MAX_IMAGE_BYTES: Final[int] = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_IMAGE_BYTES: Final[int] = 32 * 1024 * 1024
MAX_REDIRECTS: Final[int] = 4

_NOTE_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/(?P<route>explore|discovery/item)/(?P<note_id>[^/?#]+)$",
    re.IGNORECASE,
)
_ABSOLUTE_XHS_NOTE_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://(?:[a-z0-9-]+\.)?xiaohongshu\.com/"
    r"(?:explore|discovery/item)/[^\s\"'<>?#]+(?:\?[^\s\"'<>#]+)?",
    re.IGNORECASE,
)
_INITIAL_STATE_MARKER: Final[str] = "window.__INITIAL_STATE__"
_JSON_LD_SCRIPT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"<script\b[^>]*\btype\s*=\s*([\"'])application/ld\+json\1[^>]*>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_INTERVIEW_SIGNAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)\s*(?:\d{1,2}\s*[.、．、)]|[一二三四五六七八九十]+\s*[、.．])|[？?]|面试|面经|一面|二面|三面|HR\s*面|技术面|笔试|复盘|凉经",
    re.IGNORECASE | re.MULTILINE,
)
_GENERIC_META_MARKERS: Final[tuple[str, ...]] = (
    "3亿人的生活经验，都在小红书",
    "三亿人的生活经验，都在小红书",
    "发现更多有趣",
    "你的生活指南",
)
_DYNAMIC_OR_BLOCKED_MARKERS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "verification_required",
        "页面要求完成安全验证或验证码，当前只读公开导入不会绕过该验证。请稍后重试，或改用可公开访问的单篇笔记链接。",
        "验证码",
    ),
    (
        "verification_required",
        "页面要求完成安全验证或验证码，当前只读公开导入不会绕过该验证。请稍后重试，或改用可公开访问的单篇笔记链接。",
        "安全验证",
    ),
    (
        "verification_required",
        "页面要求完成安全验证或验证码，当前只读公开导入不会绕过该验证。请稍后重试，或改用可公开访问的单篇笔记链接。",
        "人机验证",
    ),
    (
        "verification_required",
        "页面要求完成安全验证或验证码，当前只读公开导入不会绕过该验证。请稍后重试，或改用可公开访问的单篇笔记链接。",
        "captcha",
    ),
    (
        "login_required",
        "该页面要求登录后才能查看笔记列表。当前导入不会使用账号、Cookie 或登录态，请改贴公开单篇笔记链接。",
        "登录后查看",
    ),
    (
        "login_required",
        "该页面要求登录后才能查看笔记列表。当前导入不会使用账号、Cookie 或登录态，请改贴公开单篇笔记链接。",
        "请先登录",
    ),
    (
        "login_required",
        "该页面要求登录后才能查看笔记列表。当前导入不会使用账号、Cookie 或登录态，请改贴公开单篇笔记链接。",
        "扫码登录",
    ),
)


class XiaohongshuSourceKind(StrEnum):
    """用户提交的小红书 URL 类型。"""

    NOTE = "note"
    PROFILE = "profile"
    FAVORITES = "favorites"
    SEARCH = "search"


@dataclass(frozen=True)
class XiaohongshuFetchedHtml:
    """一次无 Cookie 的公开 HTML 读取结果。"""

    source_url: str
    final_url: str
    html: str
    content_type: str


@dataclass(frozen=True)
class XiaohongshuDiscoveredNote:
    """在公开页面中实际出现的笔记引用。

    ``source_url`` 保留页面给出的查询参数，便于需要公开签名参数的链接继续访问；
    ``canonical_url`` 则用于去重和后续持久化。
    """

    source_url: str
    canonical_url: str
    note_id: str


@dataclass(frozen=True)
class XiaohongshuNote:
    """已经从一篇公开笔记页归一化出的文本和图片引用。"""

    source_url: str
    canonical_url: str
    note_id: str
    title: str | None
    body_text: str
    image_urls: tuple[str, ...]
    published_at: str | None = None
    author_name: str | None = None
    # 标记正文来自公开初始化状态、meta 摘要或可见 DOM，供上层判断证据是否完整。
    # 旧的测试构造器未传入该字段时保持 unknown，不影响向后兼容。
    body_source: str = "unknown"

    @property
    def markdown_content(self) -> str:
        """返回交给上层 OCR/LLM 编排的标准化 Markdown，不包含原始 HTML。"""

        sections: list[str] = []
        if self.title:
            sections.append(f"# {self.title}")
        if self.body_text:
            sections.append(self.body_text)
        if self.image_urls:
            sections.append(
                "## 公开配图\n\n"
                + "\n".join(
                    f"- 配图 {index}: {url}"
                    for index, url in enumerate(self.image_urls, start=1)
                ),
            )
        return "\n\n".join(sections).strip()


@dataclass(frozen=True)
class XiaohongshuNoteFailure:
    """列表导入时单篇笔记失败的可展示信息，不中断其他公开笔记。"""

    source_url: str
    canonical_url: str
    error_code: str
    error_message: str


@dataclass(frozen=True)
class XiaohongshuCollectionResult:
    """一次公开 URL 采集的纯数据结果，尚未 OCR、LLM 判断或写库。"""

    source_url: str
    final_url: str
    source_kind: XiaohongshuSourceKind
    discovered_notes: tuple[XiaohongshuDiscoveredNote, ...]
    notes: tuple[XiaohongshuNote, ...]
    note_failures: tuple[XiaohongshuNoteFailure, ...]

    @property
    def has_usable_notes(self) -> bool:
        """至少有一篇正文或图片可交给后续 OCR/LLM 链路。"""

        return bool(self.notes)


@dataclass(frozen=True)
class XiaohongshuDownloadedImage:
    """受限下载后的单张公开配图，字节仅在当前任务内存中保留。"""

    index: int
    source_url: str
    final_url: str
    media_type: str
    data: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class XiaohongshuImageDownloadFailure:
    """一张图片下载失败的可展示信息，不影响其他配图继续下载。"""

    index: int
    source_url: str
    error_code: str
    error_message: str


@dataclass(frozen=True)
class XiaohongshuImageDownloadResult:
    """多图下载结果，供上层逐张调用 OCR/视觉模型。"""

    note_url: str
    images: tuple[XiaohongshuDownloadedImage, ...]
    failures: tuple[XiaohongshuImageDownloadFailure, ...]
    skipped_image_count: int
    total_bytes: int


class XiaohongshuPublicHttpClient:
    """用于公开小红书页面/图片读取的可注入 HTTP 客户端。

    生产默认创建独立的 ``httpx.Client``，请求前后都会清空 Cookie 容器；测试可传入
    ``httpx.MockTransport`` 或预建 ``httpx.Client``，无需访问互联网。
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("client 与 transport 不能同时传入")
        if timeout_seconds <= 0:
            raise ValueError("HTTP 超时时间必须大于零")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        """仅关闭本类创建的客户端；外部注入客户端仍由调用方管理。"""

        if self._owns_client:
            self._client.close()

    def fetch_html(self, source_url: str) -> XiaohongshuFetchedHtml:
        """读取公开 HTML；读取前后不携带 Cookie。"""

        final_url, media_type, payload = self._request_bytes(
            source_url,
            expected="html",
            max_bytes=MAX_SOURCE_HTML_BYTES,
        )
        try:
            html_text = payload.decode("utf-8")
        except UnicodeDecodeError:
            # 小红书公开页面通常是 UTF-8；替换不可识别字符比猜测错误编码更可控。
            html_text = payload.decode("utf-8", errors="replace")
        return XiaohongshuFetchedHtml(
            source_url=source_url,
            final_url=final_url,
            html=html_text,
            content_type=media_type,
        )

    def fetch_image(self, source_url: str, *, max_bytes: int) -> XiaohongshuDownloadedImage:
        """读取一张小红书 CDN 公开图片，调用方负责控制总量。"""

        final_url, media_type, payload = self._request_bytes(
            source_url,
            expected="image",
            max_bytes=max_bytes,
        )
        return XiaohongshuDownloadedImage(
            index=0,
            source_url=source_url,
            final_url=final_url,
            media_type=media_type,
            data=payload,
        )

    def _request_bytes(
        self,
        source_url: str,
        *,
        expected: str,
        max_bytes: int,
    ) -> tuple[str, str, bytes]:
        if max_bytes <= 0:
            raise ValueError("响应大小上限必须大于零")
        if expected == "html":
            _validate_xiaohongshu_page_url(source_url)
        elif expected == "image":
            _validate_xiaohongshu_asset_url(source_url)
        else:  # pragma: no cover - 仅由内部固定调用
            raise ValueError(f"不支持的响应类型：{expected}")

        current_url = source_url
        for redirect_count in range(MAX_REDIRECTS + 1):
            self._clear_cookies()
            request = self._client.build_request(
                "GET",
                current_url,
                headers={
                    "User-Agent": "InterviewLibraryXiaohongshuImport/1.0 (public-url-only)",
                    "Accept": (
                        "text/html,application/xhtml+xml"
                        if expected == "html"
                        else "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
                    ),
                },
            )
            # 即便调用方注入了带 Cookie 的 httpx.Client，也不允许该状态进入本链路。
            request.headers.pop("cookie", None)
            try:
                response = self._client.send(request, stream=True, follow_redirects=False)
            except httpx.TimeoutException as exc:
                raise CollectionOperationError(
                    "fetch_timeout",
                    "读取小红书公开页面超时，请稍后重试。",
                ) from exc
            except httpx.HTTPError as exc:
                raise CollectionOperationError(
                    "fetch_unavailable",
                    "暂时无法读取小红书公开页面，请检查链接或稍后重试。",
                ) from exc
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise CollectionOperationError(
                            "redirect_invalid",
                            "公开链接的跳转地址无效，无法继续读取。",
                        )
                    if redirect_count >= MAX_REDIRECTS:
                        raise CollectionOperationError(
                            "redirect_too_many",
                            "公开链接跳转次数过多，已停止读取。",
                        )
                    current_url = _upgrade_xiaohongshu_https_redirect(
                        urljoin(str(response.url), location),
                        expected=expected,
                    )
                    if expected == "html":
                        _validate_xiaohongshu_page_url(current_url)
                    else:
                        _validate_xiaohongshu_asset_url(current_url)
                    continue

                self._raise_for_http_status(response.status_code, expected=expected)
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if expected == "html" and media_type not in {"text/html", "application/xhtml+xml"}:
                    raise CollectionOperationError(
                        "unsupported_content_type",
                        "该小红书链接没有返回可解析的 HTML 页面。",
                    )
                if expected == "image" and not media_type.startswith("image/"):
                    raise CollectionOperationError(
                        "unsupported_image_type",
                        "笔记配图没有返回受支持的图片内容。",
                    )

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise CollectionOperationError(
                            "response_too_large",
                            "公开页面或配图超过本次导入的大小限制。",
                        )
                    chunks.append(chunk)
                return str(response.url), media_type, b"".join(chunks)
            except httpx.TimeoutException as exc:
                raise CollectionOperationError(
                    "fetch_timeout",
                    "读取小红书公开页面超时，请稍后重试。",
                ) from exc
            except httpx.HTTPError as exc:
                raise CollectionOperationError(
                    "fetch_unavailable",
                    "暂时无法读取小红书公开页面，请检查链接或稍后重试。",
                ) from exc
            finally:
                response.close()

        raise CollectionOperationError("redirect_too_many", "公开链接跳转次数过多，已停止读取。")

    @staticmethod
    def _raise_for_http_status(status_code: int, *, expected: str) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise CollectionOperationError(
                "access_restricted",
                "该公开链接当前拒绝访问或要求登录；导入不会使用账号、Cookie 或验证码绕过。",
            )
        if status_code == 404:
            label = "配图" if expected == "image" else "笔记"
            raise CollectionOperationError("source_not_found", f"该{label}链接不存在或已被删除。")
        if status_code == 429:
            raise CollectionOperationError(
                "rate_limited",
                "小红书暂时限制了公开读取频率，请稍后重试。",
            )
        raise CollectionOperationError(
            "fetch_unavailable",
            "小红书公开页面暂时不可读取，请稍后重试。",
        )

    def _clear_cookies(self) -> None:
        """让每个请求保持无状态，避免响应 Set-Cookie 跨请求复用。"""

        self._client.cookies.clear()


class XiaohongshuPublicCollector:
    """发现并解析公开小红书笔记，不做 OCR、LLM 判断和数据库写入。"""

    def __init__(self, http_client: XiaohongshuPublicHttpClient | None = None) -> None:
        self._http_client = http_client or XiaohongshuPublicHttpClient()

    def close(self) -> None:
        """释放内部 HTTP 客户端持有的连接。"""

        self._http_client.close()

    def collect(
        self,
        source_url: str,
        *,
        requested_limit: int = DEFAULT_NOTE_LIMIT,
    ) -> XiaohongshuCollectionResult:
        """采集单篇公开笔记或公开页面实际暴露的笔记链接。"""

        if not 1 <= requested_limit <= MAX_NOTE_LIMIT:
            raise ValueError(f"单次最多读取 {MAX_NOTE_LIMIT} 篇公开笔记")

        source_kind = _classify_source_url(source_url)
        fetched_page = self._http_client.fetch_html(source_url)
        final_page_error = _final_xiaohongshu_page_error(fetched_page.final_url)
        if final_page_error is not None:
            raise final_page_error
        document = _XiaohongshuHtmlDocument.parse(fetched_page.html, fetched_page.final_url)
        initial_state = _extract_initial_state(fetched_page.html)

        if source_kind is XiaohongshuSourceKind.NOTE:
            note = _parse_note_page(fetched_page, document, initial_state)
            reference = _note_reference_from_url(fetched_page.final_url)
            if reference is None:
                raise CollectionOperationError(
                    "source_redirect_invalid",
                    "链接跳转后不再是小红书公开笔记页，无法导入。",
                )
            return XiaohongshuCollectionResult(
                source_url=source_url,
                final_url=fetched_page.final_url,
                source_kind=source_kind,
                discovered_notes=(reference,),
                notes=(note,),
                note_failures=(),
            )

        discovered = _discover_notes(
            document=document,
            initial_state=initial_state,
            page_url=fetched_page.final_url,
        )
        if not discovered:
            _raise_for_unavailable_listing(fetched_page.html, source_kind)

        selected = discovered[:requested_limit]
        notes: list[XiaohongshuNote] = []
        failures: list[XiaohongshuNoteFailure] = []
        for reference in selected:
            try:
                note_page = self._http_client.fetch_html(reference.source_url)
                note_document = _XiaohongshuHtmlDocument.parse(note_page.html, note_page.final_url)
                note = _parse_note_page(
                    note_page,
                    note_document,
                    _extract_initial_state(note_page.html),
                )
                notes.append(note)
            except CollectionOperationError as exc:
                failures.append(
                    XiaohongshuNoteFailure(
                        source_url=reference.source_url,
                        canonical_url=reference.canonical_url,
                        error_code=exc.code,
                        error_message=exc.message,
                    ),
                )

        return XiaohongshuCollectionResult(
            source_url=source_url,
            final_url=fetched_page.final_url,
            source_kind=source_kind,
            discovered_notes=tuple(selected),
            notes=tuple(notes),
            note_failures=tuple(failures),
        )


class XiaohongshuImageDownloader:
    """按篇下载公开配图，并将单图失败降级为结果中的 failure。"""

    def __init__(
        self,
        http_client: XiaohongshuPublicHttpClient,
        *,
        max_images_per_note: int = DEFAULT_MAX_IMAGES_PER_NOTE,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_IMAGE_BYTES,
    ) -> None:
        if max_images_per_note <= 0:
            raise ValueError("单篇图片数量上限必须大于零")
        if max_image_bytes <= 0 or max_total_bytes <= 0:
            raise ValueError("图片大小上限必须大于零")
        self._http_client = http_client
        self._max_images_per_note = max_images_per_note
        self._max_image_bytes = max_image_bytes
        self._max_total_bytes = max_total_bytes

    def download(self, note: XiaohongshuNote) -> XiaohongshuImageDownloadResult:
        """下载不超过上限的配图；上层拿到结果后应尽快 OCR 并释放字节。"""

        selected_urls = note.image_urls[: self._max_images_per_note]
        downloads: list[XiaohongshuDownloadedImage] = []
        failures: list[XiaohongshuImageDownloadFailure] = []
        total_bytes = 0

        for index, source_url in enumerate(selected_urls, start=1):
            remaining_bytes = self._max_total_bytes - total_bytes
            if remaining_bytes <= 0:
                failures.append(
                    XiaohongshuImageDownloadFailure(
                        index=index,
                        source_url=source_url,
                        error_code="image_total_limit_reached",
                        error_message="本篇笔记的配图总大小已达到导入上限。",
                    ),
                )
                continue
            try:
                downloaded = self._http_client.fetch_image(
                    source_url,
                    max_bytes=min(self._max_image_bytes, remaining_bytes),
                )
                downloads.append(
                    XiaohongshuDownloadedImage(
                        index=index,
                        source_url=downloaded.source_url,
                        final_url=downloaded.final_url,
                        media_type=downloaded.media_type,
                        data=downloaded.data,
                    ),
                )
                total_bytes += downloaded.size_bytes
            except CollectionOperationError as exc:
                failures.append(
                    XiaohongshuImageDownloadFailure(
                        index=index,
                        source_url=source_url,
                        error_code=exc.code,
                        error_message=exc.message,
                    ),
                )

        return XiaohongshuImageDownloadResult(
            note_url=note.canonical_url,
            images=tuple(downloads),
            failures=tuple(failures),
            skipped_image_count=max(0, len(note.image_urls) - len(selected_urls)),
            total_bytes=total_bytes,
        )


class _XiaohongshuHtmlDocument(HTMLParser):
    """不执行脚本的最小 HTML 读取器，只保留公开 DOM 中的必要字段。"""

    _SKIPPED_TAGS: Final[set[str]] = {"script", "style", "noscript", "svg", "canvas", "template"}
    _BLOCK_TAGS: Final[set[str]] = {"article", "main", "section", "p", "li", "h1", "h2", "h3", "h4", "br", "div"}

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._page_url = page_url
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._visible_parts: list[str] = []
        self.anchor_urls: list[str] = []
        self.image_urls: list[str] = []
        # 同名 meta 在小红书页面中可能先是平台营销文案、后才是笔记 SEO 文案；必须
        # 保留所有值再按信息质量选择，不能只取第一个。
        self.meta: dict[str, list[str]] = {}
        self.canonical_href: str | None = None

    @classmethod
    def parse(cls, html: str, page_url: str) -> _XiaohongshuHtmlDocument:
        """解析 HTML 字符串；输入来自容量受控的公开响应。"""

        parser = cls(page_url)
        try:
            parser.feed(html)
            parser.close()
        except Exception as exc:  # pragma: no cover - HTMLParser 对畸形页面通常可恢复
            raise CollectionOperationError("html_parse_failed", "小红书页面结构异常，暂时无法解析。") from exc
        return parser

    @property
    def title(self) -> str | None:
        return _compact_text(" ".join(self._title_parts)) or None

    @property
    def visible_text(self) -> str:
        lines: list[str] = []
        for raw_line in "\n".join(self._visible_parts).splitlines():
            line = _compact_text(raw_line)
            if not line or line.lower() in {"小红书", "登录", "注册", "下载 app", "打开 app"}:
                continue
            if not lines or lines[-1] != line:
                lines.append(line)
        return "\n\n".join(lines)[:MAX_NOTE_TEXT_CHARACTERS]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if normalized_tag in self._SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if normalized_tag == "title":
            self._in_title = True
        if normalized_tag == "a":
            href = attributes.get("href", "")
            if href:
                self.anchor_urls.append(urljoin(self._page_url, href))
        elif normalized_tag == "img":
            source = attributes.get("src") or attributes.get("data-src") or attributes.get("data-origin")
            if source:
                self.image_urls.append(urljoin(self._page_url, source))
        elif normalized_tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").lower()
            value = attributes.get("content", "").strip()
            if key and value:
                values = self.meta.setdefault(key, [])
                if value not in values:
                    values.append(value)
        elif normalized_tag == "link":
            if attributes.get("rel", "").lower() == "canonical" and attributes.get("href"):
                self.canonical_href = urljoin(self._page_url, attributes["href"])
        if not self._skip_depth and normalized_tag in self._BLOCK_TAGS:
            self._visible_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if normalized_tag == "title":
            self._in_title = False
        if not self._skip_depth and normalized_tag in self._BLOCK_TAGS:
            self._visible_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._visible_parts.append(data)


def _classify_source_url(source_url: str) -> XiaohongshuSourceKind:
    """确认输入为受支持的小红书公开页面类型。"""

    _validate_xiaohongshu_page_url(source_url)
    parsed = urlparse(source_url)
    if _note_reference_from_url(source_url) is not None:
        return XiaohongshuSourceKind.NOTE
    path = parsed.path.lower().rstrip("/")
    if path.startswith("/user/profile"):
        query = parse_qs(parsed.query)
        if (query.get("tab") or [""])[0].lower() == "fav":
            return XiaohongshuSourceKind.FAVORITES
        return XiaohongshuSourceKind.PROFILE
    if path.startswith("/search_result") or path.startswith("/search"):
        return XiaohongshuSourceKind.SEARCH
    raise CollectionOperationError(
        "unsupported_xiaohongshu_url",
        "仅支持小红书公开笔记、个人页/收藏页和搜索页链接。",
    )


def _parse_note_page(
    fetched_page: XiaohongshuFetchedHtml,
    document: _XiaohongshuHtmlDocument,
    initial_state: Any | None,
) -> XiaohongshuNote:
    """优先读取公开初始化状态，再降级到公开 meta/DOM。"""

    reference = _note_reference_from_url(fetched_page.final_url)
    if reference is None:
        raise CollectionOperationError(
            "source_redirect_invalid",
            "链接跳转后不再是小红书公开笔记页，无法导入。",
        )
    note_entity = _find_note_entity(initial_state, reference.note_id)
    json_ld_article = _select_json_ld_article(_extract_json_ld_values(fetched_page.html))
    title = _first_nonempty(
        _lookup_text(note_entity, ("title", "noteTitle", "note_title")),
        _json_ld_text(json_ld_article, ("headline", "name", "title")),
        _best_meta_text(document, ("og:title", "twitter:title", "title")),
        document.title,
    )
    state_body = _lookup_text(
        note_entity,
        ("desc", "content", "description", "noteContent", "note_content"),
    )
    json_ld_body = _json_ld_text(json_ld_article, ("description", "articleBody", "text"))
    meta_body = _best_meta_text(document, ("og:description", "description", "twitter:description"))
    structured_body = _first_nonempty(state_body, json_ld_body, meta_body)
    # 验证/登录页经常只把提示语渲染进 body。没有结构化正文时，不能把提示语误入库。
    if not structured_body:
        page_error = _operation_error_from_page_markers(fetched_page.html)
        if page_error is not None:
            raise page_error
    body_source, body_text = _select_note_body(
        ("initial_state", state_body),
        ("json_ld", json_ld_body),
        ("meta", meta_body),
        ("visible_dom", document.visible_text),
    )
    image_urls = _merge_urls(
        _extract_note_entity_images(note_entity),
        _extract_json_ld_images(json_ld_article),
        _filter_xhs_image_urls(
            _meta_values(document, ("og:image", "twitter:image", "image")),
        ),
        _filter_xhs_image_urls(document.image_urls),
    )
    published_at = _first_nonempty(
        _lookup_text(note_entity, ("time", "publishTime", "publish_time", "createTime", "create_time")),
        _json_ld_text(json_ld_article, ("datePublished", "dateCreated")),
        _best_meta_text(document, ("article:published_time",)),
    )
    author_name = _first_nonempty(
        _lookup_author_name(note_entity),
        _json_ld_author_name(json_ld_article),
    )

    if not body_text and not image_urls:
        _raise_for_note_unavailable(fetched_page.html)
    return XiaohongshuNote(
        source_url=fetched_page.source_url,
        canonical_url=reference.canonical_url,
        note_id=reference.note_id,
        title=title,
        body_text=body_text,
        image_urls=image_urls,
        published_at=published_at,
        author_name=author_name,
        body_source=body_source,
    )


def _discover_notes(
    *,
    document: _XiaohongshuHtmlDocument,
    initial_state: Any | None,
    page_url: str,
) -> tuple[XiaohongshuDiscoveredNote, ...]:
    """只返回公开 HTML 或公开初始状态中出现的真实笔记引用。"""

    values: list[str] = [*document.anchor_urls]
    values.extend(_find_note_urls_in_value(initial_state))
    values.extend(_find_note_ids_in_state(initial_state))
    discovered: dict[str, XiaohongshuDiscoveredNote] = {}
    for value in values:
        candidate = urljoin(page_url, value)
        reference = _note_reference_from_url(candidate)
        if reference is None:
            continue
        discovered.setdefault(reference.canonical_url, reference)
    return tuple(discovered.values())


def _raise_for_unavailable_listing(html: str, source_kind: XiaohongshuSourceKind) -> None:
    """把动态空壳、登录页与验证码页区分为对用户有帮助的错误。"""

    page_error = _operation_error_from_page_markers(html)
    if page_error is not None:
        raise page_error
    label = {
        XiaohongshuSourceKind.PROFILE: "个人页",
        XiaohongshuSourceKind.FAVORITES: "收藏页",
        XiaohongshuSourceKind.SEARCH: "搜索页",
    }[source_kind]
    raise CollectionOperationError(
        "source_content_unavailable",
        f"该小红书{label}未在公开 HTML 中暴露笔记列表。请改贴公开单篇笔记链接；当前导入不会读取登录态、Cookie 或动态私有接口。",
    )


def _raise_for_note_unavailable(html: str) -> None:
    """单篇笔记没有暴露正文/图片时给出明确、可操作的失败原因。"""

    page_error = _operation_error_from_page_markers(html)
    if page_error is not None:
        raise page_error
    raise CollectionOperationError(
        "note_content_empty",
        "该公开笔记没有暴露可解析的正文或配图，可能已删除、受限或依赖登录后的动态渲染。",
    )


def _operation_error_from_page_markers(html: str) -> CollectionOperationError | None:
    """识别明显的登录/验证码占位页，避免把提示文本当作面经正文。"""

    lowered = html.lower()
    for code, message, marker in _DYNAMIC_OR_BLOCKED_MARKERS:
        if marker.lower() in lowered:
            return CollectionOperationError(code, message)
    return None


def _extract_initial_state(html: str) -> Any | None:
    """安全提取 JSON 形式的 ``window.__INITIAL_STATE__``，绝不执行页面脚本。"""

    marker_index = html.find(_INITIAL_STATE_MARKER)
    if marker_index < 0:
        return _extract_json_script_state(html)
    equals_index = html.find("=", marker_index, marker_index + 160)
    if equals_index < 0:
        return None
    value_start = _find_json_value_start(html, equals_index + 1)
    if value_start is None:
        return None
    raw_json = _extract_balanced_json(html, value_start)
    if raw_json is None:
        return None
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        # 不能用 eval/JSON5 处理不可信脚本；无效状态按不可用处理即可。
        return None


def _extract_json_script_state(html: str) -> Any | None:
    """兼容少数以 application/json script 标签公开初始状态的页面。"""

    match = re.search(
        r"<script[^>]+(?:id|data-state)=[\"'](?:__INITIAL_STATE__|initial-state)[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    try:
        return json.loads(html_module.unescape(match.group(1)).strip())
    except json.JSONDecodeError:
        return None


def _extract_json_ld_values(html: str) -> tuple[Any, ...]:
    """读取公开 JSON-LD，不执行脚本，也不对 JSON 原文做 HTML 反转义。

    小红书的 JSON-LD 可能在字符串里合法保留 ``&quot;``。若先调用
    ``html.unescape`` 会把它变成未转义双引号，反而破坏 JSON。因此这里直接
    ``json.loads`` 原始 script 内容，失败的块独立忽略。
    """

    values: list[Any] = []
    for match in _JSON_LD_SCRIPT_PATTERN.finditer(html):
        raw = match.group(2).strip()
        if not raw:
            continue
        try:
            values.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return tuple(values)


def _select_json_ld_article(values: Iterable[Any]) -> dict[str, Any] | None:
    """从 JSON-LD 图中选择最像小红书笔记的 Article 节点。"""

    candidates: list[tuple[int, dict[str, Any]]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            raw_type = value.get("@type")
            type_values = raw_type if isinstance(raw_type, list) else [raw_type]
            normalized_types = {str(item).lower() for item in type_values if item}
            description = _json_ld_text(value, ("description", "articleBody", "text"))
            headline = _json_ld_text(value, ("headline", "name", "title"))
            if (
                normalized_types & {"article", "socialmediaposting", "creativework"}
                or description
                or headline
            ):
                score = len(description or "") * 2 + len(headline or "")
                if normalized_types & {"article", "socialmediaposting"}:
                    score += 1_000
                score += _note_text_score(description or "", "json_ld")
                candidates.append((score, value))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for item in values:
        visit(item)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _json_ld_text(value: dict[str, Any] | None, keys: tuple[str, ...]) -> str | None:
    return _lookup_text(value, keys)


def _json_ld_author_name(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict):
        return None
    author = value.get("author")
    if isinstance(author, dict):
        return _lookup_text(author, ("name", "nickname", "nickName"))
    if isinstance(author, str):
        return _compact_text(author) or None
    return None


def _extract_json_ld_images(value: dict[str, Any] | None) -> tuple[str, ...]:
    """仅从 Article 的 image/thumbnailUrl/contentUrl 字段提取可信 CDN 图。"""

    if not isinstance(value, dict):
        return ()
    urls: list[str] = []

    def visit(candidate: Any) -> None:
        if isinstance(candidate, str):
            normalized = _normalize_xiaohongshu_asset_url(candidate)
            if normalized:
                urls.append(normalized)
        elif isinstance(candidate, dict):
            for key in ("url", "contentUrl", "thumbnailUrl", "image"):
                if key in candidate:
                    visit(candidate[key])
        elif isinstance(candidate, list):
            for item in candidate:
                visit(item)

    for key in ("image", "thumbnailUrl", "contentUrl"):
        if key in value:
            visit(value[key])
    return _merge_urls(urls)


def _meta_values(document: _XiaohongshuHtmlDocument, keys: Iterable[str]) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        values.extend(document.meta.get(key.lower(), ()))
    return tuple(values)


def _best_meta_text(document: _XiaohongshuHtmlDocument, keys: Iterable[str]) -> str | None:
    """从重复的 SEO meta 中选取信息量最高的一条，而不是固定取第一条。"""

    selected = _select_note_body(*(("meta", value) for value in _meta_values(document, keys)))
    return selected[1] or None


def _select_note_body(*candidates: tuple[str, str | None]) -> tuple[str, str]:
    """按题目/面试信号和长度选择正文；平台口号不能压过真实面试题。"""

    selected: list[tuple[int, int, int, str, str]] = []
    source_priority = {
        "initial_state": 4,
        "json_ld": 3,
        "visible_dom": 2,
        "meta": 1,
    }
    for source, raw_value in candidates:
        normalized = _normalize_note_text(raw_value)
        if not normalized:
            continue
        # 正文来源的可信度优先于换行/空格造成的微小信号差异。公开 JSON-LD
        # 是页面主动暴露的结构化笔记内容，优先级应高于从整页文本中抽取的 DOM。
        source_bonus = source_priority.get(source, 0) * 5_000
        selected.append(
            (
                _note_text_score(normalized, source) + source_bonus,
                len(normalized),
                source_priority.get(source, 0),
                source,
                normalized,
            ),
        )
    if not selected:
        return "unknown", ""
    selected.sort(reverse=True)
    _, _, _, source, text = selected[0]
    return source, text


def _note_text_score(value: str, source: str) -> int:
    normalized = _compact_text(value)
    if not normalized:
        return -10_000
    score = min(len(normalized), 12_000)
    score += len(_INTERVIEW_SIGNAL_PATTERN.findall(value)) * 380
    if any(marker in normalized for marker in _GENERIC_META_MARKERS):
        score -= 20_000
    if source == "meta" and len(normalized) < 100:
        score -= 600
    return score


def _find_json_value_start(value: str, start: int) -> int | None:
    for index in range(start, len(value)):
        if value[index] in "{[":
            return index
        if value[index] == ";":
            return None
    return None


def _extract_balanced_json(value: str, start: int) -> str | None:
    """用括号/字符串状态机截取 JSON 值，不依赖正则截断嵌套对象。"""

    opening = value[start]
    if opening not in "{[":
        return None
    matching = {"{": "}", "[": "]"}
    stack: list[str] = [matching[opening]]
    in_string = False
    escaped = False
    for index in range(start + 1, len(value)):
        char = value[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in matching:
            stack.append(matching[char])
        elif char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return value[start : index + 1]
    return None


def _find_note_urls_in_value(value: Any | None) -> Iterable[str]:
    """遍历已解析 JSON，仅收集其中出现的显式小红书笔记 URL。"""

    if isinstance(value, str):
        yield from _ABSOLUTE_XHS_NOTE_URL_PATTERN.findall(value)
        return
    if isinstance(value, dict):
        for child in value.values():
            yield from _find_note_urls_in_value(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _find_note_urls_in_value(child)


def _find_note_ids_in_state(value: Any | None) -> Iterable[str]:
    """从具有笔记语义字段的公开状态对象中发现 noteId，避免扫描普通 id。"""

    if isinstance(value, dict):
        note_id = _lookup_text(value, ("noteId", "note_id", "noteIdStr", "note_id_str"))
        if note_id and _looks_like_note_entity(value):
            yield f"https://www.xiaohongshu.com/explore/{note_id}"
        for child in value.values():
            yield from _find_note_ids_in_state(child)
    elif isinstance(value, list):
        for child in value:
            yield from _find_note_ids_in_state(child)


def _find_note_entity(value: Any | None, note_id: str) -> dict[str, Any] | None:
    """从公开初始状态中寻找与当前 URL 对应、信息最完整的笔记对象。"""

    candidates: list[tuple[int, dict[str, Any]]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            candidate_id = _lookup_text(item, ("noteId", "note_id", "noteIdStr", "note_id_str"))
            if candidate_id == note_id:
                candidates.append((_score_note_entity(item), item))
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _score_note_entity(value: dict[str, Any]) -> int:
    score = 0
    for key in ("title", "noteTitle", "desc", "content", "description", "imageList", "images", "image_list"):
        if key in value and value[key] not in (None, "", [], {}):
            score += 2
    if _looks_like_note_entity(value):
        score += 3
    return score


def _looks_like_note_entity(value: dict[str, Any]) -> bool:
    keys = {key.lower() for key in value}
    return bool(
        keys
        & {
            "title",
            "notetitle",
            "desc",
            "content",
            "description",
            "imagelist",
            "image_list",
            "images",
            "cover",
        },
    )


def _extract_note_entity_images(value: dict[str, Any] | None) -> tuple[str, ...]:
    """从笔记对象中提取 CDN 图片，排除头像、Logo 等非正文图片。"""

    if value is None:
        return ()
    discovered: list[str] = []

    def visit(item: Any, path: tuple[str, ...]) -> None:
        if isinstance(item, str):
            if any("avatar" in component.lower() or "logo" in component.lower() for component in path):
                return
            if _is_xiaohongshu_asset_url(item):
                discovered.append(item)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, (*path, str(key)))
        elif isinstance(item, list):
            for child in item:
                visit(child, path)

    visit(value, ())
    return _merge_urls(discovered)


def _filter_xhs_image_urls(values: Iterable[str]) -> tuple[str, ...]:
    return _merge_urls(
        normalized
        for value in values
        if (normalized := _normalize_xiaohongshu_asset_url(value)) is not None
    )


def _normalize_xiaohongshu_asset_url(value: str) -> str | None:
    """将可信小红书 CDN 的 HTTP 图链规范化为 HTTPS，拒绝 data URI 和第三方链接。"""

    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() == "http" and _is_xiaohongshu_asset_host(parsed.hostname or ""):
        parsed = parsed._replace(scheme="https")
    normalized = urlunparse(parsed)
    return normalized if _is_xiaohongshu_asset_url(normalized) else None


def _lookup_text(value: Any | None, keys: tuple[str, ...]) -> str | None:
    """仅从当前字典读取短文本，避免递归时把无关字段当正文。"""

    if not isinstance(value, dict):
        return None
    expected = {key.lower() for key in keys}
    for key, candidate in value.items():
        if key.lower() not in expected:
            continue
        if isinstance(candidate, (str, int, float)):
            normalized = _compact_text(str(candidate))
            if normalized:
                return normalized
    return None


def _lookup_author_name(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    for key in ("user", "author", "userInfo", "user_info"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            author = _lookup_text(candidate, ("nickname", "nickName", "name", "userName"))
            if author:
                return author
    return _lookup_text(value, ("nickname", "nickName", "authorName", "author_name"))


def _normalize_note_text(value: str | None) -> str:
    if not value:
        return ""
    decoded = html_module.unescape(value)
    decoded = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.IGNORECASE)
    decoded = re.sub(r"<[^>]+>", "", decoded)
    lines = [_compact_text(line) for line in decoded.replace("\r", "\n").split("\n")]
    return "\n\n".join(line for line in lines if line)[:MAX_NOTE_TEXT_CHARACTERS]


def _first_nonempty(*values: str | None) -> str | None:
    for value in values:
        compact = _compact_text(value or "")
        if compact:
            return compact
    return None


def _compact_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _merge_urls(*value_groups: Iterable[str]) -> tuple[str, ...]:
    """保留 URL 查询参数（可能是公开图片签名），并按首次出现顺序去重。"""

    ordered: dict[str, None] = {}
    for values in value_groups:
        for value in values:
            candidate = value.strip()
            if candidate:
                ordered.setdefault(candidate, None)
    return tuple(ordered)


def _note_reference_from_url(value: str) -> XiaohongshuDiscoveredNote | None:
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not _is_xiaohongshu_page_host(parsed.hostname or ""):
        return None
    normalized_path = parsed.path.rstrip("/") or "/"
    match = _NOTE_PATH_PATTERN.match(normalized_path)
    if match is None:
        return None
    note_id = match.group("note_id")
    route = match.group("route")
    canonical_path = f"/{route}/{note_id}"
    canonical_url = urlunparse(("https", "www.xiaohongshu.com", canonical_path, "", "", ""))
    source_url = urlunparse(
        (
            "https",
            (parsed.hostname or "www.xiaohongshu.com").lower(),
            canonical_path,
            "",
            parsed.query,
            "",
        ),
    )
    return XiaohongshuDiscoveredNote(
        source_url=source_url,
        canonical_url=canonical_url,
        note_id=note_id,
    )


def _validate_xiaohongshu_page_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise CollectionOperationError("invalid_url", "仅支持用户提交的小红书公开 HTTPS 链接。")
    if parsed.username or parsed.password:
        raise CollectionOperationError("invalid_url", "链接不能包含账号或密码。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CollectionOperationError("invalid_url", "链接端口格式无效。") from exc
    if port not in {None, 443}:
        raise CollectionOperationError("invalid_url", "小红书公开链接仅允许标准 HTTPS 端口。")
    if not _is_xiaohongshu_page_host(parsed.hostname):
        raise CollectionOperationError("invalid_url", "仅支持 xiaohongshu.com 的公开链接。")


def _final_xiaohongshu_page_error(value: str) -> CollectionOperationError | None:
    """把站点重定向到的访问限制页转换为可供界面展示的业务错误。

    小红书经常以 HTTP 200 的同站 ``/login`` 或 ``/404?error_code=300031`` 页面响应
    无登录的服务端请求。若继续把它们交给笔记解析，最终只会得到“链接格式无效”或“内容为空”，
    用户无法判断应重试、改贴公开笔记，还是需要使用后续的浏览器授权导入。
    """

    parsed = urlparse(value)
    if not _is_xiaohongshu_page_host(parsed.hostname or ""):
        return None
    path = parsed.path.lower().rstrip("/")
    if path == "/login":
        return CollectionOperationError(
            "login_required",
            "该小红书页面要求登录后才能读取笔记列表。请改贴公开单篇笔记链接；当前导入不会使用账号、Cookie 或登录态。",
        )
    query = parse_qs(parsed.query)
    error_code = (query.get("error_code") or [""])[0]
    if path == "/404" and error_code == "300031":
        return CollectionOperationError(
            "access_restricted",
            "小红书暂不向当前无登录请求开放这篇笔记，导入不会绕过该访问限制。请稍后重试，或改用可公开读取的来源。",
        )
    return None


def _upgrade_xiaohongshu_https_redirect(value: str, *, expected: str) -> str:
    """将站点返回的同站 HTTP 降级跳转改为 HTTPS 后再继续读取。

    小红书的部分旧路径会先返回 ``http://www.xiaohongshu.com/...``，随后再跳回 HTTPS。
    导入链路不需要也不应真正请求这个中间 HTTP 地址；仅当目标仍是允许的小红书页面或图片
    域名时，直接把协议提升为 HTTPS。其他站点、其他协议和携带账号信息的跳转仍会交给后续
    URL 校验拒绝，避免把重定向当成任意外部请求。
    """

    parsed = urlparse(value)
    if parsed.scheme.lower() != "http" or parsed.username or parsed.password:
        return value
    hostname = parsed.hostname or ""
    is_allowed = (
        _is_xiaohongshu_page_host(hostname)
        if expected == "html"
        else _is_xiaohongshu_asset_host(hostname)
    )
    if not is_allowed:
        return value
    return urlunparse(parsed._replace(scheme="https"))


def _validate_xiaohongshu_asset_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise CollectionOperationError("invalid_image_url", "笔记配图必须是公开 HTTPS 地址。")
    if parsed.username or parsed.password:
        raise CollectionOperationError("invalid_image_url", "笔记配图地址不能包含账号或密码。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CollectionOperationError("invalid_image_url", "笔记配图端口格式无效。") from exc
    if port not in {None, 443} or not _is_xiaohongshu_asset_host(parsed.hostname):
        raise CollectionOperationError("invalid_image_url", "仅下载小红书公开 CDN 配图。")


def _is_xiaohongshu_page_host(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    return normalized == "xiaohongshu.com" or normalized.endswith(".xiaohongshu.com")


def _is_xiaohongshu_asset_host(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    return _is_xiaohongshu_page_host(normalized) or normalized == "xhscdn.com" or normalized.endswith(".xhscdn.com")


def _is_xiaohongshu_asset_url(value: str) -> bool:
    try:
        _validate_xiaohongshu_asset_url(value)
    except CollectionOperationError:
        return False
    return True


# 与 Collection 编排层约定的公开接口别名。CollectionOperationError 本身已经携带
# `code` 和 `message`，因此无需包一层并丢失可展示错误信息。
XiaohongshuCollectionError = CollectionOperationError
XiaohongshuImage = XiaohongshuDownloadedImage


class XiaohongshuPublicSourceAdapter:
    """面向编排层的精简适配器接口。

    它在不引入数据库、OCR 或 LLM 依赖的前提下提供发现、单篇提取和配图下载能力。
    ``discover`` 只返回成功解析的公开笔记；需要按篇显示失败原因时，上层可调用
    ``collect`` 并读取 ``note_failures``。
    """

    def __init__(
        self,
        http_client: XiaohongshuPublicHttpClient | None = None,
        *,
        max_images_per_note: int = DEFAULT_MAX_IMAGES_PER_NOTE,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_total_image_bytes: int = DEFAULT_MAX_TOTAL_IMAGE_BYTES,
    ) -> None:
        self._http_client = http_client or XiaohongshuPublicHttpClient()
        self._collector = XiaohongshuPublicCollector(self._http_client)
        self._image_downloader = XiaohongshuImageDownloader(
            self._http_client,
            max_images_per_note=max_images_per_note,
            max_image_bytes=max_image_bytes,
            max_total_bytes=max_total_image_bytes,
        )

    def close(self) -> None:
        """释放适配器自行创建的 HTTP 连接。"""

        self._collector.close()

    def collect(
        self,
        source_url: str,
        *,
        requested_limit: int = DEFAULT_NOTE_LIMIT,
    ) -> XiaohongshuCollectionResult:
        """返回包含发现、解析成功和单篇失败明细的完整结果。"""

        return self._collector.collect(source_url, requested_limit=requested_limit)

    def discover(
        self,
        source_url: str,
        requested_limit: int = DEFAULT_NOTE_LIMIT,
    ) -> list[XiaohongshuNote]:
        """发现并提取公开笔记，符合编排层所需的 ``list[Note]`` 契约。"""

        return list(self.collect(source_url, requested_limit=requested_limit).notes)

    def extract_note(self, source_url: str) -> XiaohongshuNote:
        """读取一篇公开笔记；非笔记 URL 会在 URL 分类阶段被明确拒绝。"""

        if _classify_source_url(source_url) is not XiaohongshuSourceKind.NOTE:
            raise CollectionOperationError(
                "unsupported_xiaohongshu_url",
                "提取单篇内容时请提供小红书公开笔记链接。",
            )
        result = self.collect(source_url, requested_limit=1)
        if not result.notes:  # pragma: no cover - 单篇成功路径总会返回一篇
            raise CollectionOperationError("note_content_empty", "未提取到可用的公开笔记内容。")
        return result.notes[0]

    def download_image(self, image: str | XiaohongshuImage) -> bytes:
        """下载单张公开配图，返回字节给附件/OCR 链路。

        已下载对象直接返回内存中的字节，URL 则执行一次受单图上限约束的无 Cookie 读取。
        """

        if isinstance(image, XiaohongshuDownloadedImage):
            return image.data
        return self._http_client.fetch_image(image, max_bytes=self._image_downloader._max_image_bytes).data

    def download_images(self, note: XiaohongshuNote) -> XiaohongshuImageDownloadResult:
        """下载一篇笔记的全部受限配图，并保留逐张失败明细。"""

        return self._image_downloader.download(note)


__all__ = [
    "DEFAULT_MAX_IMAGE_BYTES",
    "DEFAULT_MAX_IMAGES_PER_NOTE",
    "DEFAULT_MAX_TOTAL_IMAGE_BYTES",
    "DEFAULT_NOTE_LIMIT",
    "MAX_NOTE_LIMIT",
    "XiaohongshuCollectionResult",
    "XiaohongshuCollectionError",
    "XiaohongshuDiscoveredNote",
    "XiaohongshuDownloadedImage",
    "XiaohongshuFetchedHtml",
    "XiaohongshuImageDownloadFailure",
    "XiaohongshuImageDownloadResult",
    "XiaohongshuImageDownloader",
    "XiaohongshuImage",
    "XiaohongshuNote",
    "XiaohongshuNoteFailure",
    "XiaohongshuPublicCollector",
    "XiaohongshuPublicHttpClient",
    "XiaohongshuPublicSourceAdapter",
    "XiaohongshuSourceKind",
]
