from __future__ import annotations

import base64
import html
import logging
import os
import posixpath
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests

from src.config.config_manager import AppConfig


class GitHubRepositoryAssetError(RuntimeError):
    """GitHub 仓库图片素材检索失败。"""


@dataclass(frozen=True)
class GitHubRepositoryImageCandidate:
    """从 GitHub 仓库中发现的一张候选图片。

    这个结构不直接写数据库，只在 provider 内部用于排序和下载。
    """

    download_url: str
    source_kind: str
    source_path: str
    alt_text: str
    score: int
    reason: str


@dataclass(frozen=True)
class GitHubRepositoryImageResult:
    """GitHub 仓库图片成功落盘后的结果。"""

    output_path: Path
    source_url: str
    source_kind: str
    width: int
    height: int
    metadata: dict[str, Any]


class GitHubRepositoryAssetProvider:
    """从 GitHub 项目 README 或常见素材目录中挑选项目相关图片。

    它是图片链路里的省钱层：优先复用项目自己提供的截图、架构图、demo 图；
    找不到合适图片时返回 None，让 ImageTask 再决定是否调用 Seedream 或本地兜底。
    """

    _markdown_image_pattern = re.compile(
        r"!\[[^\]]*]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)",
        re.IGNORECASE,
    )
    _html_image_pattern = re.compile(
        r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>",
        re.IGNORECASE,
    )
    _html_alt_pattern = re.compile(
        r"\balt=[\"']([^\"']*)[\"']",
        re.IGNORECASE,
    )
    _positive_keywords = {
        "screenshot": 260,
        "screenshots": 260,
        "demo": 230,
        "preview": 220,
        "architecture": 210,
        "diagram": 200,
        "overview": 170,
        "hero": 160,
        "banner": 150,
        "cover": 130,
        "example": 120,
        "workflow": 70,
        "ui": 70,
        "logo": 35,
    }
    _negative_keywords = {
        "badge",
        "badges",
        "shield",
        "shields",
        "coverage",
        "codecov",
        "coveralls",
        "license",
        "stars",
        "downloads",
        "version",
        "npm",
        "pypi",
    }
    _blocked_hosts = {
        "img.shields.io",
        "badgen.net",
        "badge.fury.io",
        "travis-ci.org",
        "travis-ci.com",
        "circleci.com",
        "coveralls.io",
        "codecov.io",
    }
    _common_asset_dirs = (
        "",
        ".github",
        "docs",
        "doc",
        "assets",
        "asset",
        "images",
        "image",
        "screenshots",
        "media",
        "public",
        "static",
    )

    def __init__(self, config: AppConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

    def find_and_download_image(
        self,
        repository_full_name: str,
        output_path: Path,
    ) -> GitHubRepositoryImageResult | None:
        """为单个 GitHub 仓库寻找并下载一张可用项目图。

        输入是 owner/name 形式的仓库全名和输出路径。
        输出是成功落盘的图片结果；如果仓库没有合适图片，返回 None。
        """

        normalized_repository = repository_full_name.strip()
        if not self._is_valid_repository_full_name(normalized_repository):
            raise ValueError(f"GitHub 仓库全名不合法：{repository_full_name}")

        repository_payload = self._fetch_repository(normalized_repository)
        if repository_payload is None:
            self.logger.info("GitHub 仓库不存在或不可访问，跳过仓库图：repository=%s", normalized_repository)
            return None

        default_branch = str(repository_payload.get("default_branch") or "main").strip() or "main"
        html_url = str(repository_payload.get("html_url") or f"https://github.com/{normalized_repository}").strip()

        candidates = self._collect_candidates(
            repository_full_name=normalized_repository,
            default_branch=default_branch,
        )
        if not candidates:
            self.logger.info("GitHub 仓库没有发现可用候选图：repository=%s", normalized_repository)
            return None

        max_attempts = self.config.image_github_asset_max_candidate_attempts
        for candidate in sorted(candidates, key=lambda item: item.score, reverse=True)[:max_attempts]:
            try:
                downloaded = self._download_candidate(candidate=candidate, output_path=output_path)
            except Exception as exc:
                self.logger.info(
                    "GitHub 候选图不可用，继续尝试下一张：repository=%s source=%s error=%s",
                    normalized_repository,
                    candidate.download_url,
                    exc,
                )
                continue

            if downloaded is None:
                continue

            width, height = downloaded
            return GitHubRepositoryImageResult(
                output_path=output_path,
                source_url=candidate.download_url,
                source_kind=candidate.source_kind,
                width=width,
                height=height,
                metadata={
                    "repository_full_name": normalized_repository,
                    "repository_html_url": html_url,
                    "default_branch": default_branch,
                    "source_path": candidate.source_path,
                    "alt_text": candidate.alt_text,
                    "selection_score": candidate.score,
                    "selection_reason": candidate.reason,
                    "github_asset_used": True,
                    "cost_saving": True,
                },
            )

        self.logger.info("GitHub 仓库候选图都不适合落盘：repository=%s", normalized_repository)
        return None

    def _collect_candidates(
        self,
        repository_full_name: str,
        default_branch: str,
    ) -> list[GitHubRepositoryImageCandidate]:
        """合并 README 图片和常见目录图片候选。"""

        readme_candidates = self._collect_readme_image_candidates(
            repository_full_name=repository_full_name,
            default_branch=default_branch,
        )
        if readme_candidates:
            return self._deduplicate_candidates(readme_candidates)

        directory_candidates = self._collect_directory_image_candidates(
            repository_full_name=repository_full_name,
            default_branch=default_branch,
        )
        return self._deduplicate_candidates(directory_candidates)

    def _deduplicate_candidates(
        self,
        candidates: list[GitHubRepositoryImageCandidate],
    ) -> list[GitHubRepositoryImageCandidate]:
        """按下载 URL 去重候选图片。"""

        seen_urls: set[str] = set()
        deduplicated: list[GitHubRepositoryImageCandidate] = []
        for candidate in candidates:
            normalized_url = candidate.download_url.strip()
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            deduplicated.append(candidate)
        return deduplicated

    def _collect_readme_image_candidates(
        self,
        repository_full_name: str,
        default_branch: str,
    ) -> list[GitHubRepositoryImageCandidate]:
        """从 README Markdown 和 HTML img 标签里提取候选图片。"""

        readme_payload = self._fetch_json(
            path=f"/repos/{repository_full_name}/readme",
            params={"ref": default_branch},
            not_found_is_none=True,
        )
        if not isinstance(readme_payload, dict):
            return []

        readme_path = str(readme_payload.get("path") or "README.md")
        readme_dir = posixpath.dirname(readme_path)
        markdown_text = self._read_readme_text(readme_payload=readme_payload)
        if not markdown_text.strip():
            return []
        candidates: list[GitHubRepositoryImageCandidate] = []

        for order, match in enumerate(self._markdown_image_pattern.finditer(markdown_text), start=1):
            raw_reference = html.unescape(match.group(1).strip())
            image_url = self._resolve_repository_image_url(
                repository_full_name=repository_full_name,
                default_branch=default_branch,
                base_dir=readme_dir,
                reference=raw_reference,
            )
            if not image_url:
                continue

            score, reason = self._score_candidate(
                source_kind="readme_markdown",
                source_path=raw_reference,
                alt_text="",
                order=order,
            )
            if score <= 0:
                continue

            candidates.append(
                GitHubRepositoryImageCandidate(
                    download_url=image_url,
                    source_kind="readme_markdown",
                    source_path=raw_reference,
                    alt_text="",
                    score=score,
                    reason=reason,
                )
            )

        for order, match in enumerate(self._html_image_pattern.finditer(markdown_text), start=1):
            raw_reference = html.unescape(match.group(1).strip())
            tag_text = match.group(0)
            alt_match = self._html_alt_pattern.search(tag_text)
            alt_text = html.unescape(alt_match.group(1).strip()) if alt_match else ""
            image_url = self._resolve_repository_image_url(
                repository_full_name=repository_full_name,
                default_branch=default_branch,
                base_dir=readme_dir,
                reference=raw_reference,
            )
            if not image_url:
                continue

            score, reason = self._score_candidate(
                source_kind="readme_html",
                source_path=raw_reference,
                alt_text=alt_text,
                order=order,
            )
            if score <= 0:
                continue

            candidates.append(
                GitHubRepositoryImageCandidate(
                    download_url=image_url,
                    source_kind="readme_html",
                    source_path=raw_reference,
                    alt_text=alt_text,
                    score=score,
                    reason=reason,
                )
            )

        return candidates

    def _read_readme_text(self, readme_payload: dict[str, Any]) -> str:
        """优先从 GitHub API 的 base64 content 字段读取 README 文本。"""

        content = readme_payload.get("content")
        encoding = str(readme_payload.get("encoding") or "").lower()
        if isinstance(content, str) and encoding == "base64":
            try:
                compact_content = "".join(content.split())
                return base64.b64decode(compact_content).decode("utf-8", errors="replace")
            except Exception as exc:
                raise GitHubRepositoryAssetError(f"README base64 解码失败：{exc}") from exc

        download_url = str(readme_payload.get("download_url") or "").strip()
        if not download_url:
            return ""

        try:
            response = requests.get(
                download_url,
                headers=self._public_download_headers(),
                timeout=self.config.image_github_asset_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise GitHubRepositoryAssetError(f"README 下载失败：{exc}") from exc

        return response.text

    def _collect_directory_image_candidates(
        self,
        repository_full_name: str,
        default_branch: str,
    ) -> list[GitHubRepositoryImageCandidate]:
        """从仓库常见素材目录中浅层查找图片文件。"""

        candidates: list[GitHubRepositoryImageCandidate] = []
        order = 0
        for directory in self._common_asset_dirs:
            payload = self._fetch_json(
                path=f"/repos/{repository_full_name}/contents/{quote(directory, safe='/')}",
                params={"ref": default_branch},
                not_found_is_none=True,
            )
            if not isinstance(payload, list):
                continue

            for item in payload:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "") != "file":
                    continue
                item_path = str(item.get("path") or "").strip()
                item_url = str(item.get("download_url") or "").strip()
                if not item_path or not item_url:
                    continue
                if not self._extension_allowed(item_url):
                    continue

                order += 1
                score, reason = self._score_candidate(
                    source_kind="repository_directory",
                    source_path=item_path,
                    alt_text="",
                    order=order,
                )
                if score <= 0:
                    continue

                candidates.append(
                    GitHubRepositoryImageCandidate(
                        download_url=item_url,
                        source_kind="repository_directory",
                        source_path=item_path,
                        alt_text="",
                        score=score,
                        reason=reason,
                    )
                )
        return candidates

    def _download_candidate(
        self,
        candidate: GitHubRepositoryImageCandidate,
        output_path: Path,
    ) -> tuple[int, int] | None:
        """下载候选图片，校验尺寸后统一转成 PNG。"""

        if not self._extension_allowed(candidate.download_url):
            return None

        try:
            response = requests.get(
                candidate.download_url,
                headers=self._public_download_headers(),
                timeout=self.config.image_github_asset_timeout_seconds,
                stream=True,
            )
        except requests.RequestException as exc:
            raise GitHubRepositoryAssetError(f"候选图片下载失败：{exc}") from exc

        if response.status_code >= 400:
            raise GitHubRepositoryAssetError(f"候选图片下载返回状态码 {response.status_code}")

        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > self.config.image_github_asset_max_bytes:
                    raise GitHubRepositoryAssetError("候选图片超过大小限制")
            except ValueError:
                pass

        image_bytes = self._read_limited_response(response)
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise RuntimeError("缺少 Pillow，无法解析 GitHub 仓库图片") from exc

        try:
            image = Image.open(BytesIO(image_bytes))
            image = ImageOps.exif_transpose(image)
            image.load()
        except Exception as exc:
            raise GitHubRepositoryAssetError(f"候选图片无法被 Pillow 解析：{exc}") from exc

        width, height = image.size
        if not self._is_usable_image_size(width=width, height=height):
            raise GitHubRepositoryAssetError(f"候选图片尺寸过小：{width}x{height}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_image = self._normalize_image_for_png(image)
        normalized_image.save(output_path, format="PNG", optimize=True)
        return width, height

    def _read_limited_response(self, response: requests.Response) -> bytes:
        """按最大字节数读取响应体，避免异常大图撑爆内存。"""

        max_bytes = self.config.image_github_asset_max_bytes
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise GitHubRepositoryAssetError("候选图片下载内容超过大小限制")
            chunks.append(chunk)
        return b"".join(chunks)

    def _normalize_image_for_png(self, image: Any) -> Any:
        """把不同格式图片转换为微信公众号更稳定接受的 RGB PNG。"""

        from PIL import Image

        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba_image = image.convert("RGBA")
            canvas = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
            canvas.alpha_composite(rgba_image)
            return canvas.convert("RGB")
        return image.convert("RGB")

    def _fetch_repository(self, repository_full_name: str) -> dict[str, Any] | None:
        """读取仓库基础信息，主要为了拿 default_branch 和 html_url。"""

        payload = self._fetch_json(
            path=f"/repos/{repository_full_name}",
            params=None,
            not_found_is_none=True,
        )
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise GitHubRepositoryAssetError("GitHub 仓库详情响应不是对象")
        return payload

    def _fetch_json(
        self,
        path: str,
        params: dict[str, Any] | None,
        not_found_is_none: bool,
    ) -> Any:
        """调用 GitHub JSON API；Token 失效时自动退回未认证请求。"""

        url = f"{self.config.github_api_base_url.rstrip('/')}/{path.lstrip('/')}"
        token = os.getenv(self.config.github_token_env, "").strip()
        headers = self._api_headers(token=token or None)
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=self.config.image_github_asset_timeout_seconds,
        )
        if response.status_code == 401 and token:
            response = requests.get(
                url,
                headers=self._api_headers(token=None),
                params=params,
                timeout=self.config.image_github_asset_timeout_seconds,
            )

        if response.status_code == 404 and not_found_is_none:
            return None
        if response.status_code >= 400:
            raise GitHubRepositoryAssetError(
                f"GitHub API 请求失败：status_code={response.status_code} message={response.text[:300]}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise GitHubRepositoryAssetError("GitHub API 返回内容不是合法 JSON") from exc

    def _resolve_repository_image_url(
        self,
        repository_full_name: str,
        default_branch: str,
        base_dir: str,
        reference: str,
    ) -> str | None:
        """把 README 中的相对图片路径解析成可下载 URL。"""

        cleaned = reference.strip().strip("'\"")
        if not cleaned:
            return None
        if cleaned.startswith(("data:", "mailto:", "#")):
            return None
        if cleaned.startswith("//"):
            cleaned = "https:" + cleaned

        parsed = urlparse(cleaned)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return cleaned if self._extension_allowed(cleaned) and not self._is_ignored_image_reference(cleaned, "") else None

        relative_path = unquote(parsed.path or cleaned).replace("\\", "/")
        if not relative_path:
            return None
        if relative_path.startswith("/"):
            normalized_path = posixpath.normpath(relative_path).lstrip("/")
        else:
            normalized_path = posixpath.normpath(posixpath.join(base_dir, relative_path)).lstrip("/")
        if normalized_path.startswith("../") or normalized_path == "..":
            return None

        encoded_path = quote(normalized_path, safe="/")
        encoded_branch = quote(default_branch, safe="/")
        image_url = f"https://raw.githubusercontent.com/{repository_full_name}/{encoded_branch}/{encoded_path}"
        return image_url if self._extension_allowed(image_url) and not self._is_ignored_image_reference(image_url, "") else None

    def _score_candidate(
        self,
        source_kind: str,
        source_path: str,
        alt_text: str,
        order: int,
    ) -> tuple[int, str]:
        """给候选图片打分，优先截图、架构图、demo 图，排除 badge。"""

        joined = f"{source_path} {alt_text}".lower()
        if self._is_ignored_image_reference(source_path, alt_text):
            return 0, "ignored_badge_or_status_image"

        score = 1000 if source_kind.startswith("readme") else 520
        matched_keywords: list[str] = []
        for keyword, weight in self._positive_keywords.items():
            if keyword in joined:
                score += weight
                matched_keywords.append(keyword)

        score += max(0, 100 - order)
        reason = "keyword:" + ",".join(matched_keywords) if matched_keywords else "readme_order" if source_kind.startswith("readme") else "directory_order"
        return score, reason

    def _extension_allowed(self, url: str) -> bool:
        """检查 URL 文件扩展名是否在允许范围内。"""

        extension = self._extension_from_url(url)
        return extension in set(self.config.image_github_asset_allowed_extensions)

    def _extension_from_url(self, url: str) -> str:
        """从 URL 或路径中提取小写扩展名。"""

        parsed = urlparse(url)
        path = unquote(parsed.path or url).lower()
        return Path(path).suffix.lower()

    def _is_ignored_image_reference(self, source_path: str, alt_text: str) -> bool:
        """判断候选图片是否像 badge、CI 状态图或其它不适合做配图的小图。"""

        parsed = urlparse(source_path)
        host = parsed.netloc.lower()
        if host in self._blocked_hosts:
            return True

        joined = f"{source_path} {alt_text}".lower()
        path_name = Path(unquote(parsed.path or source_path).lower()).name
        for keyword in self._negative_keywords:
            if keyword in joined or keyword in path_name:
                return True
        return False

    def _is_usable_image_size(self, width: int, height: int) -> bool:
        """过滤小徽章和极小 logo，保留适合文章/视频展示的图片。"""

        if width >= 320 and height >= 160:
            return True
        if max(width, height) >= 512 and min(width, height) >= 96:
            return True
        return False

    def _api_headers(self, token: str | None) -> dict[str, str]:
        """构造 GitHub API 请求头，不把密钥写入日志。"""

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.github_api_version,
            "User-Agent": self.config.app_name,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _public_download_headers(self) -> dict[str, str]:
        """构造公开图片下载请求头，避免把 GitHub Token 发给外部图片域名。"""

        return {
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "User-Agent": self.config.app_name,
        }

    def _is_valid_repository_full_name(self, repository_full_name: str) -> bool:
        """校验 owner/name 形式，避免把异常字符串拼进 GitHub API URL。"""

        if repository_full_name.count("/") != 1:
            return False
        owner, name = repository_full_name.split("/", 1)
        return bool(owner.strip()) and bool(name.strip())
