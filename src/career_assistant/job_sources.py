"""公开职位链接的受限读取与内存提取服务。"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Final
from urllib.parse import urlparse

import httpx

from src.observability.langsmith_runtime import trace_operation


_MAX_REDIRECTS: Final = 3
_MAX_RESPONSE_BYTES: Final = 1_000_000
_MAX_VISIBLE_TEXT_CHARACTERS: Final = 15_000
_REQUEST_TIMEOUT_SECONDS: Final = 8.0
_SKIPPED_TAGS: Final = frozenset({"script", "style", "noscript", "svg", "template"})
_BOSS_HOST_SUFFIX: Final = "zhipin.com"
_BOSS_VERIFICATION_MARKERS: Final = (
    "请稍候",
    "安全验证",
    "访问验证",
    "正在验证",
    "请完成验证",
    "滑动验证",
)


class JobSourceError(ValueError):
    """对外只暴露安全、稳定的职位链接读取失败原因。"""


@dataclass(frozen=True)
class JobPostingSnapshot:
    """仅在当前 Agent Turn 内存在的公开职位页面快照。"""

    source_host: str
    title: str
    visible_text: str
    fetched_at: datetime


class _VisibleTextParser(HTMLParser):
    """从 HTML 中提取标题与可见正文，跳过脚本等非阅读内容。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._og_title = ""

    @property
    def title(self) -> str:
        return _normalize_text(self._og_title or " ".join(self._title_parts))

    @property
    def visible_text(self) -> str:
        return _normalize_text(" ".join(self._text_parts))[:_MAX_VISIBLE_TEXT_CHARACTERS]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in _SKIPPED_TAGS:
            self._ignored_depth += 1
        if normalized_tag == "title":
            self._in_title = True
        if normalized_tag == "meta":
            attributes = {key.lower(): (value or "") for key, value in attrs}
            if attributes.get("property", "").lower() == "og:title":
                self._og_title = attributes.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in _SKIPPED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        if normalized_tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._text_parts.append(data)


class JobPostingExtractor:
    """安全读取公开职位页；禁止访问内网、文件协议和不受控大响应。"""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def extract(self, raw_url: str) -> JobPostingSnapshot:
        """读取有限 HTML 内容并生成仅供当前分析使用的快照。"""

        return trace_operation(
            run_name="career.job_source.fetch",
            run_type="tool",
            inputs={"request_count": 1},
            metadata={
                "component": "career_job_source",
                "privacy_mode": "metadata_only",
            },
            tags=("career", "job-source", "tool"),
            execute=lambda: self._extract_untraced(raw_url),
            summarize=self._summarize_trace_result,
        )

    def _extract_untraced(self, raw_url: str) -> JobPostingSnapshot:
        """执行原有受限抓取；URL、主机、标题和正文均不进入 Trace。"""

        current_url = self._validate_public_url(raw_url)
        owns_client = self._client is None
        client = self._client or httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS),
            headers={
                "User-Agent": "CareerAssistant/0.1 public-job-reader",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            for _ in range(_MAX_REDIRECTS + 1):
                response = client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise JobSourceError("职位页面跳转地址无效")
                    current_url = self._validate_public_url(str(response.url.join(location)))
                    continue
                if response.status_code >= 400:
                    raise JobSourceError("职位页面当前不可访问，请粘贴职位描述")
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise JobSourceError("职位链接不是可解析的网页，请粘贴职位描述")
                content = response.content
                if len(content) > _MAX_RESPONSE_BYTES:
                    raise JobSourceError("职位页面内容过大，请粘贴职位描述")
                return self._parse_html(current_url, response.text)
            raise JobSourceError("职位页面跳转次数过多，请粘贴职位描述")
        except httpx.HTTPError as exc:
            raise JobSourceError("职位页面访问失败，请粘贴职位描述") from exc
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _summarize_trace_result(
        snapshot: JobPostingSnapshot,
    ) -> dict[str, str | int]:
        """只暴露页面提取是否成功及字符数，不暴露抓取内容。"""

        return {
            "status": "completed",
            "title_characters": len(snapshot.title),
            "visible_text_characters": len(snapshot.visible_text),
        }

    @staticmethod
    def _parse_html(url: str, html: str) -> JobPostingSnapshot:
        """解析已经受限读取的 HTML，不做任何网络访问。"""

        parser = _VisibleTextParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception as exc:
            raise JobSourceError("职位页面无法解析，请粘贴职位描述") from exc

        parsed_url = urlparse(url)
        visible_text = parser.visible_text
        if not visible_text:
            raise JobSourceError("职位页面未提供可读内容，请粘贴职位描述")
        JobPostingExtractor._reject_boss_verification_page(
            parsed_url.hostname or "",
            parser.title,
            visible_text,
        )
        return JobPostingSnapshot(
            source_host=parsed_url.hostname or "",
            title=parser.title[:300],
            visible_text=visible_text,
            fetched_at=datetime.now(UTC),
        )

    @staticmethod
    def _reject_boss_verification_page(
        hostname: str,
        title: str,
        visible_text: str,
    ) -> None:
        """识别 BOSS 直聘的验证页，避免把反爬提示误交给模型当作职位描述。"""

        normalized_host = hostname.rstrip(".").lower()
        if normalized_host != _BOSS_HOST_SUFFIX and not normalized_host.endswith(
            f".{_BOSS_HOST_SUFFIX}",
        ):
            return

        page_text = f"{title}\n{visible_text}"
        if any(marker in page_text for marker in _BOSS_VERIFICATION_MARKERS):
            raise JobSourceError(
                "该 BOSS 直聘链接返回了安全验证页，服务端无法读取职位详情。"
                "请复制职位描述后再提交。",
            )

    @staticmethod
    def _validate_public_url(raw_url: str) -> str:
        """校验协议、主机与 DNS 结果，拒绝 SSRF 常见目标。"""

        candidate = raw_url.strip()
        parsed_url = urlparse(candidate)
        if parsed_url.scheme not in {"http", "https"}:
            raise JobSourceError("职位链接仅支持 http 或 https 地址")
        if parsed_url.username or parsed_url.password or not parsed_url.hostname:
            raise JobSourceError("职位链接格式无效")
        if parsed_url.port not in {None, 80, 443}:
            raise JobSourceError("职位链接端口不受支持")

        hostname = parsed_url.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".local"):
            raise JobSourceError("职位链接不能指向本地网络")

        try:
            addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise JobSourceError("职位链接域名无法解析") from exc

        resolved_addresses = {address[4][0] for address in addresses}
        if not resolved_addresses or any(
            not ipaddress.ip_address(address).is_global
            for address in resolved_addresses
        ):
            raise JobSourceError("职位链接不能指向私有网络")
        return parsed_url.geturl()


def _normalize_text(value: str) -> str:
    """压缩 HTML 的空白字符，控制进入模型上下文的无效噪声。"""

    return " ".join(value.split())
