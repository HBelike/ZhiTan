"""全网公开面经收集的 Firecrawl 适配器与确定性清洗边界。

本模块只接收公开 HTTPS 地址，不携带用户 Cookie 或登录会话。Firecrawl 响应在此被
压缩成稳定数据对象；原始响应、HTML、图片地址和 API Key 都不会进入持久化层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import ipaddress
import os
import re
import socket
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx


DEFAULT_FIRECRAWL_API_URL = "https://api.firecrawl.dev"
DEFAULT_FIRECRAWL_TIMEOUT_SECONDS = 60.0
MAX_PUBLIC_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PUBLIC_IMAGES = 10
MAX_PUBLIC_IMAGE_REDIRECTS = 3

_TRACKING_KEYS = frozenset(
    {
        "spm",
        "from",
        "source",
        "ref",
        "referrer",
        "referer",
        "fbclid",
        "gclid",
        "yclid",
        "mc_cid",
        "mc_eid",
    },
)
_COLLECTION_WRAPPER_LINE = re.compile(
    r"^\s*(?:来源(?:地址|链接|url)?|原文(?:地址|链接)?|抓取时间|采集时间|图片地址)\s*[：:]\s*\S+\s*$",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*]\([^)]*\)\s*$")
_NOISE_LINE = re.compile(
    r"^\s*(?:cookie\s*(?:policy|设置|偏好)?|隐私设置|接受全部\s*cookie|返回顶部)\s*$",
    re.IGNORECASE,
)
_LIST_PREFIX = re.compile(r"^(\s*)[*+]\s+")
_MULTIPLE_SPACES = re.compile(r"[\t ]+")
_MULTIPLE_SLASHES = re.compile(r"/{2,}")
_KNOWN_PLATFORM_LABELS = {
    "xiaohongshu.com": "小红书",
    "nowcoder.com": "牛客",
    "zhihu.com": "知乎",
    "juejin.cn": "稀土掘金",
    "csdn.net": "CSDN",
    "segmentfault.com": "SegmentFault",
}


class PublicWebUrlError(ValueError):
    """公开网页地址不满足协议或公网边界。"""


class FirecrawlRequestError(RuntimeError):
    """Firecrawl 可展示、可分类的失败，不包含第三方完整响应。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class FirecrawlSettings:
    """仅在进程内保存的 Firecrawl 连接配置。"""

    api_key: str
    api_url: str = DEFAULT_FIRECRAWL_API_URL
    timeout_seconds: float = DEFAULT_FIRECRAWL_TIMEOUT_SECONDS


@dataclass(frozen=True)
class FirecrawlSearchResult:
    """搜索阶段的轻量 URL 候选，不携带页面正文。"""

    title: str | None
    url: str
    snippet: str | None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FirecrawlDocument:
    """单页解析结果；图片地址只允许在当前调用中用于 OCR。"""

    source_url: str
    final_url: str
    title: str | None
    markdown: str
    image_urls: tuple[str, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadedPublicImage:
    """只在内存和临时附件链路中流转的公开图片。"""

    index: int
    data: bytes
    media_type: str


def load_firecrawl_settings() -> FirecrawlSettings | None:
    """从环境变量读取 Firecrawl；未配置 Key 时明确返回不可用。"""

    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        return None
    api_url = os.getenv("FIRECRAWL_API_URL", DEFAULT_FIRECRAWL_API_URL).strip().rstrip("/")
    if not api_url:
        api_url = DEFAULT_FIRECRAWL_API_URL
    return FirecrawlSettings(api_key=api_key, api_url=api_url)


def canonicalize_public_url(value: str) -> str:
    """纯函数生成规范 URL，不为猜测 canonical 地址而访问目标站点。"""

    raw = str(value or "").strip()
    if not raw:
        raise PublicWebUrlError("公开网页地址不能为空。")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise PublicWebUrlError("公开网页地址格式无效。") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise PublicWebUrlError("仅支持公开 HTTPS 网页。")
    if parsed.username or parsed.password:
        raise PublicWebUrlError("公开网页地址不能包含账号或密码。")
    if port not in {None, 443}:
        raise PublicWebUrlError("公开网页只允许使用标准 HTTPS 端口。")

    hostname = _normalize_hostname(parsed.hostname)
    _reject_unsafe_hostname_literal(hostname)
    netloc = f"[{hostname}]" if ":" in hostname else hostname

    path = _MULTIPLE_SLASHES.sub("/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/") or "/"
    else:
        path = ""

    query_items = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    ]
    query_items.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(query_items, doseq=True)
    return urlunsplit(("https", netloc, path, query, ""))


def validate_public_https_target(
    value: str,
    *,
    resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
) -> None:
    """解析主机并拒绝本机、私网、链路本地和保留地址。"""

    canonical = canonicalize_public_url(value)
    hostname = urlsplit(canonical).hostname
    if hostname is None:
        raise PublicWebUrlError("公开网页地址缺少域名。")
    try:
        addresses = {
            str(item[4][0]).split("%", maxsplit=1)[0]
            for item in resolver(hostname, 443, type=socket.SOCK_STREAM)
            if len(item) >= 5 and item[4]
        }
    except (OSError, socket.gaierror) as exc:
        raise PublicWebUrlError("无法解析公开网页域名。") from exc
    if not addresses:
        raise PublicWebUrlError("无法解析公开网页域名。")
    for address in addresses:
        try:
            candidate = ipaddress.ip_address(address)
        except ValueError as exc:
            raise PublicWebUrlError("公开网页域名解析结果无效。") from exc
        if not candidate.is_global:
            raise PublicWebUrlError("不能读取本机、内网或保留地址。")


def normalize_public_markdown(value: str) -> str:
    """删除采集包装并统一空白，生成可稳定计算哈希的 Markdown。"""

    normalized = unicodedata.normalize(
        "NFC",
        str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", ""),
    )
    output: list[str] = []
    in_code_fence = False
    for raw_line in normalized.split("\n"):
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
            output.append(raw_line.rstrip())
            continue
        if not in_code_fence and (
            _COLLECTION_WRAPPER_LINE.match(raw_line)
            or _MARKDOWN_IMAGE_LINE.match(raw_line)
            or _NOISE_LINE.match(raw_line)
        ):
            continue
        if in_code_fence:
            output.append(raw_line.rstrip())
            continue
        line = _LIST_PREFIX.sub(r"\1- ", raw_line.rstrip())
        line = _MULTIPLE_SPACES.sub(" ", line).rstrip()
        output.append(line)

    compact: list[str] = []
    previous_blank = True
    for line in output:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        compact.append(line)
        previous_blank = blank
    while compact and not compact[-1].strip():
        compact.pop()
    return "\n".join(compact).strip()


def hash_public_markdown(value: str) -> str:
    """对规范正文计算 SHA-256。"""

    return sha256(normalize_public_markdown(value).encode("utf-8")).hexdigest()


def infer_public_platform(value: str) -> str:
    """按规范域名生成用户可识别的平台名称。"""

    hostname = (urlsplit(value).hostname or "公开网页").lower().rstrip(".")
    for domain, label in _KNOWN_PLATFORM_LABELS.items():
        if hostname == domain or hostname.endswith(f".{domain}"):
            return label
    return hostname.removeprefix("www.") or "公开网页"


class FirecrawlClient:
    """Firecrawl v2 `/search` 与 `/scrape` 的窄接口。"""

    def __init__(
        self,
        settings: FirecrawlSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.api_url,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=settings.timeout_seconds,
            transport=transport,
        )

    def search(self, keyword: str, *, limit: int) -> tuple[FirecrawlSearchResult, ...]:
        """只发现 URL、标题和摘要，不请求搜索结果正文。"""

        normalized_keyword = " ".join(str(keyword or "").split())
        if not normalized_keyword:
            raise ValueError("搜索关键词不能为空。")
        if not 1 <= int(limit) <= 100:
            raise ValueError("Firecrawl 搜索数量必须在 1 到 100 之间。")
        body = self._post(
            "/v2/search",
            {
                "query": f"{normalized_keyword} (面经 OR 面试经历 OR 面试题)",
                "limit": int(limit),
                "sources": ["web"],
                "country": "CN",
                "ignoreInvalidURLs": True,
            },
            operation="search",
        )
        data = body.get("data")
        items = data.get("web") if isinstance(data, Mapping) else None
        if not isinstance(items, list):
            return ()
        results: list[FirecrawlSearchResult] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            url = _optional_text(item.get("url"), limit=2_000)
            if url is None:
                continue
            metadata = {
                str(key): _json_scalar(value)
                for key, value in item.items()
                if key not in {"title", "description", "url", "markdown", "html", "links", "screenshot"}
                and _json_scalar(value) is not None
            }
            results.append(
                FirecrawlSearchResult(
                    title=_optional_text(item.get("title"), limit=500),
                    url=url,
                    snippet=_optional_text(item.get("description"), limit=1_000),
                    metadata=metadata,
                ),
            )
        return tuple(results)

    def scrape(self, source_url: str) -> FirecrawlDocument:
        """获取一篇公开网页的主 Markdown 与临时 OCR 候选图片。"""

        body = self._post(
            "/v2/scrape",
            {
                "url": source_url,
                "formats": ["markdown", "images"],
                "onlyMainContent": True,
                "removeBase64Images": True,
                "storeInCache": True,
                "timeout": 60_000,
            },
            operation="scrape",
        )
        data = body.get("data")
        if not isinstance(data, Mapping):
            raise FirecrawlRequestError(
                "firecrawl_invalid_response",
                "公开网页解析服务返回了无法识别的数据。",
                retryable=True,
            )
        metadata_value = data.get("metadata")
        metadata = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
        status_code = _coerce_int(metadata.get("statusCode"))
        if status_code in {404, 410}:
            raise FirecrawlRequestError(
                "page_not_found",
                "公开网页不存在或已经删除。",
                retryable=False,
                status_code=status_code,
            )
        markdown = str(data.get("markdown") or "").strip()
        final_url = _optional_text(metadata.get("url"), limit=2_000) or source_url
        image_values = data.get("images")
        image_urls = tuple(
            item.strip()
            for item in image_values
            if isinstance(item, str) and item.strip().lower().startswith("https://")
        ) if isinstance(image_values, list) else ()
        return FirecrawlDocument(
            source_url=source_url,
            final_url=final_url,
            title=_optional_text(metadata.get("title"), limit=500),
            markdown=markdown,
            image_urls=image_urls[:MAX_PUBLIC_IMAGES],
            metadata={
                key: value
                for key in ("cacheState", "cachedAt", "statusCode")
                if (value := _json_scalar(metadata.get(key))) is not None
            },
        )

    def close(self) -> None:
        self._client.close()

    def _post(
        self,
        path: str,
        payload: Mapping[str, object],
        *,
        operation: str,
    ) -> Mapping[str, object]:
        try:
            response = self._client.post(path, json=dict(payload))
        except httpx.TimeoutException as exc:
            raise FirecrawlRequestError(
                "firecrawl_timeout",
                "公开网页服务响应超时，请稍后重试。",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise FirecrawlRequestError(
                "firecrawl_network_error",
                "暂时无法连接公开网页服务，请稍后重试。",
                retryable=True,
            ) from exc

        if response.status_code in {401, 403}:
            raise FirecrawlRequestError(
                "firecrawl_auth_failed",
                "公开网页服务授权失败，请检查 FIRECRAWL_API_KEY。",
                retryable=False,
                status_code=response.status_code,
            )
        if response.status_code == 429:
            raise FirecrawlRequestError(
                "firecrawl_rate_limited",
                "公开网页服务当前请求过多，请稍后重试。",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code >= 500:
            raise FirecrawlRequestError(
                "firecrawl_unavailable",
                "公开网页服务暂时不可用，请稍后重试。",
                retryable=True,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise FirecrawlRequestError(
                f"firecrawl_{operation}_rejected",
                "公开网页服务拒绝了本次请求。",
                retryable=response.status_code in {408, 409, 425},
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise FirecrawlRequestError(
                "firecrawl_invalid_response",
                "公开网页服务返回了无法识别的数据。",
                retryable=True,
            ) from exc
        if not isinstance(body, Mapping) or body.get("success") is not True:
            raise FirecrawlRequestError(
                f"firecrawl_{operation}_failed",
                "公开网页服务未能完成本次请求。",
                retryable=True,
            )
        return body


class PublicWebImageDownloader:
    """受限下载公开正文图片，供现有临时附件 OCR 使用。"""

    _MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
    ) -> None:
        self._client = httpx.Client(timeout=20.0, follow_redirects=False, transport=transport)
        self._resolver = resolver

    def download(
        self,
        urls: Sequence[str],
        *,
        limit: int = MAX_PUBLIC_IMAGES,
    ) -> tuple[DownloadedPublicImage, ...]:
        resolved_limit = max(0, min(MAX_PUBLIC_IMAGES, int(limit)))
        images: list[DownloadedPublicImage] = []
        for index, url in enumerate(tuple(urls)[:resolved_limit], start=1):
            try:
                response = self._get_with_safe_redirects(url)
                media_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].lower()
                if media_type not in self._MEDIA_TYPES:
                    continue
                data = response.content
                if not data or len(data) > MAX_PUBLIC_IMAGE_BYTES:
                    continue
                images.append(DownloadedPublicImage(index=index, data=data, media_type=media_type))
            except (httpx.HTTPError, PublicWebUrlError):
                continue
        return tuple(images)

    def close(self) -> None:
        self._client.close()

    def _get_with_safe_redirects(self, value: str) -> httpx.Response:
        current = value
        for redirect_count in range(MAX_PUBLIC_IMAGE_REDIRECTS + 1):
            canonical = canonicalize_public_url(current)
            validate_public_https_target(canonical, resolver=self._resolver)
            response = self._client.get(canonical, headers={"Accept": "image/avif,image/webp,image/png,image/jpeg"})
            if response.is_redirect:
                location = response.headers.get("location")
                if not location or redirect_count >= MAX_PUBLIC_IMAGE_REDIRECTS:
                    raise httpx.TooManyRedirects("公开图片重定向次数过多。", request=response.request)
                current = urljoin(canonical, location)
                continue
            response.raise_for_status()
            return response
        raise httpx.TooManyRedirects("公开图片重定向次数过多。")


def _normalize_hostname(value: str) -> str:
    hostname = value.rstrip(".").lower()
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PublicWebUrlError("公开网页域名格式无效。") from exc


def _reject_unsafe_hostname_literal(hostname: str) -> None:
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        raise PublicWebUrlError("不能读取本机、内网或保留地址。")
    try:
        candidate = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not candidate.is_global:
        raise PublicWebUrlError("不能读取本机、内网或保留地址。")


def _is_tracking_key(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith("utm_") or normalized in _TRACKING_KEYS


def _optional_text(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split()).strip()
    return normalized[:limit] if normalized else None


def _json_scalar(value: object) -> object | None:
    return value if isinstance(value, (str, int, float, bool)) else None


def _coerce_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
