from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.repositories.generated_content_repository import GeneratedContentForLayout
from src.repositories.media_asset_repository import MediaAssetRecord


LOCAL_WECHAT_IMAGE_SCHEME = "wechat-image-asset"
DEFAULT_MAX_PROJECT_IMAGE_COUNT = 5


def resolve_expected_project_image_count(layout_payload: dict[str, Any]) -> int:
    """读取本次排版实际需要的项目图数量，并兼容旧排版记录。"""

    layout_stats = layout_payload.get("layout_stats", {})
    if not isinstance(layout_stats, dict):
        return DEFAULT_MAX_PROJECT_IMAGE_COUNT

    configured_count = layout_stats.get("expected_image_count")
    if configured_count is not None:
        try:
            return max(0, int(configured_count))
        except (TypeError, ValueError):
            return DEFAULT_MAX_PROJECT_IMAGE_COUNT

    try:
        inferred_count = int(layout_stats.get("embedded_image_count", 0)) + int(
            layout_stats.get("missing_image_count", 0)
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_PROJECT_IMAGE_COUNT
    return inferred_count if inferred_count > 0 else DEFAULT_MAX_PROJECT_IMAGE_COUNT


@dataclass(frozen=True)
class ArticleLayoutBuildResult:
    """公众号排版服务的输出结果。"""

    article_html: str
    cover_asset_id: int | None
    expected_image_count: int
    embedded_image_count: int
    missing_image_count: int
    block_count: int
    style_version: str


class ArticleLayoutService:
    """把审核通过的 Markdown 内容整理为微信公众号友好的技术笔记 HTML。

    设计目标参考用户给出的技术博客截图：
    - 不做大面积营销风 hero 卡片；
    - 正文以短段落、充足行距、轻量标题为主；
    - 行内代码使用浅灰底和红色 monospace；
    - 项目图片和一句解释绑定出现，像源码阅读笔记里的架构图。
    """

    style_version = "wechat-source-note-zh-v4"
    _link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
    _ordered_item_pattern = re.compile(r"^\d+[.)]\s+(.+)$")

    def build(
        self,
        content: GeneratedContentForLayout,
        media_assets: list[MediaAssetRecord],
    ) -> ArticleLayoutBuildResult:
        """构建完整公众号 HTML，并把动态 N 张项目图按项目顺序嵌入正文。"""

        if not content.title.strip():
            raise ValueError("排版内容标题不能为空")
        if not content.article_markdown.strip():
            raise ValueError("排版内容正文不能为空")

        image_assets = self._group_image_assets(media_assets)
        project_visual_cards, cover_asset_id = self._build_project_visual_cards(
            content=content,
            image_assets=image_assets,
        )
        body_html, body_block_count, inserted_repositories = self._render_markdown(
            content.article_markdown,
            project_visual_cards=project_visual_cards,
        )
        fallback_image_html = self._render_unplaced_project_visual_cards(
            project_visual_cards=project_visual_cards,
            inserted_repositories=inserted_repositories,
        )
        expected_image_count = len(project_visual_cards)
        embedded_image_count = sum(1 for item in project_visual_cards.values() if item["did_embed"])
        missing_image_count = sum(1 for item in project_visual_cards.values() if not item["did_embed"])

        html_blocks = [
            self._render_article_start(),
            self._render_hero(content),
            body_html,
            fallback_image_html,
            self._render_footer(content),
            "</section>",
        ]
        article_html = "\n".join(block for block in html_blocks if block.strip())
        return ArticleLayoutBuildResult(
            article_html=article_html,
            cover_asset_id=cover_asset_id,
            expected_image_count=expected_image_count,
            embedded_image_count=embedded_image_count,
            missing_image_count=missing_image_count,
            block_count=body_block_count,
            style_version=self.style_version,
        )

    def build_payload(
        self,
        content: GeneratedContentForLayout,
        result: ArticleLayoutBuildResult,
        media_assets: list[MediaAssetRecord],
    ) -> dict[str, Any]:
        """生成用于调试、审计和后续 DeliverTask 的结构化元数据。"""

        return {
            "style_version": result.style_version,
            "source_content": {
                "content_id": content.id,
                "week_end": content.week_end,
                "status": content.status,
                "updated_at": content.updated_at,
            },
            "layout_stats": {
                "block_count": result.block_count,
                "expected_image_count": result.expected_image_count,
                "embedded_image_count": result.embedded_image_count,
                "missing_image_count": result.missing_image_count,
                "media_asset_count": len(media_assets),
                "cover_asset_id": result.cover_asset_id,
            },
            "wechat_constraints": {
                "inline_css": True,
                "external_script": False,
                "local_image_scheme": LOCAL_WECHAT_IMAGE_SCHEME,
                "requires_public_images_before_delivery": result.missing_image_count > 0,
            },
        }

    def _render_article_start(self) -> str:
        """输出最外层容器，全部样式内联，避免公众号编辑器丢失外部 CSS。"""

        return (
            '<section data-layout-version="wechat-source-note-zh-v4" '
            'style="box-sizing:border-box;margin:0 auto;padding:4px 2px 0;'
            'max-width:680px;background:#ffffff;'
            'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,'
            'PingFang SC,Hiragino Sans GB,Microsoft YaHei,sans-serif;'
            'color:#26313f;line-height:2.02;font-size:17px;letter-spacing:.01em;">'
        )

    def _render_hero(self, content: GeneratedContentForLayout) -> str:
        """正文不再渲染营销风头图，标题与摘要交给公众号原生标题区域承载。"""

        return ""

    def _render_markdown(
        self,
        markdown_text: str,
        project_visual_cards: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[str, int, set[str]]:
        """用轻量 Markdown 解析器转换常见正文结构，保证输出 HTML 安全可控。"""

        blocks: list[str] = []
        unordered_items: list[str] = []
        ordered_items: list[str] = []
        code_lines: list[str] = []
        in_code_block = False
        cards = project_visual_cards or {}
        inserted_repositories: set[str] = set()
        pending_repository_for_card: str | None = None

        def append_block(block: str, *, can_attach_pending_card: bool = True) -> None:
            nonlocal pending_repository_for_card
            if not block:
                return
            blocks.append(block)
            if (
                can_attach_pending_card
                and pending_repository_for_card
                and pending_repository_for_card not in inserted_repositories
            ):
                card = cards[pending_repository_for_card]
                blocks.append(str(card["html"]))
                inserted_repositories.add(pending_repository_for_card)
                pending_repository_for_card = None

        def flush_pending_card() -> None:
            nonlocal pending_repository_for_card
            if pending_repository_for_card and pending_repository_for_card not in inserted_repositories:
                blocks.append(str(cards[pending_repository_for_card]["html"]))
                inserted_repositories.add(pending_repository_for_card)
            pending_repository_for_card = None

        def flush_lists() -> None:
            nonlocal unordered_items, ordered_items
            if unordered_items:
                append_block(self._render_list(unordered_items, ordered=False))
                unordered_items = []
            if ordered_items:
                append_block(self._render_list(ordered_items, ordered=True))
                ordered_items = []

        def find_project_repository_for_heading(heading_text: str) -> str | None:
            for repository_full_name, card in cards.items():
                if repository_full_name in inserted_repositories:
                    continue
                rank = str(card.get("rank", "")).strip()
                repo_short_name = repository_full_name.split("/", 1)[-1]
                rank_markers = (
                    [f"项目 {rank}", f"项目{rank}", f"第 {rank}", f"第{rank}"]
                    if rank
                    else []
                )
                if (
                    repository_full_name in heading_text
                    or repo_short_name in heading_text
                    or any(marker in heading_text for marker in rank_markers)
                ):
                    return repository_full_name
            return None

        for raw_line in markdown_text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()

            if stripped.startswith("```"):
                if in_code_block:
                    append_block(self._render_code_block(code_lines))
                    code_lines = []
                    in_code_block = False
                else:
                    flush_lists()
                    in_code_block = True
                    code_lines = []
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            if not stripped:
                flush_lists()
                continue

            heading_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
            if heading_match:
                flush_lists()
                flush_pending_card()
                level = min(len(heading_match.group(1)), 4)
                heading_text = heading_match.group(2)
                append_block(self._render_heading(heading_text, level), can_attach_pending_card=False)
                pending_repository_for_card = find_project_repository_for_heading(heading_text)
                continue

            if stripped in {"---", "***", "___"}:
                flush_lists()
                append_block('<section style="height:1px;background:#eef2f7;margin:28px 0;"></section>')
                continue

            if stripped.startswith(">"):
                flush_lists()
                quote_text = stripped.lstrip(">").strip()
                append_block(self._render_quote(quote_text))
                continue

            if stripped.startswith(("- ", "* ")):
                if ordered_items:
                    flush_lists()
                unordered_items.append(stripped[2:].strip())
                continue

            ordered_match = self._ordered_item_pattern.match(stripped)
            if ordered_match:
                if unordered_items:
                    flush_lists()
                ordered_items.append(ordered_match.group(1).strip())
                continue

            flush_lists()
            append_block(self._render_paragraph(stripped))

        flush_lists()
        if in_code_block:
            append_block(self._render_code_block(code_lines))
        flush_pending_card()

        return "\n".join(blocks), len(blocks), inserted_repositories

    def _render_heading(self, text: str, level: int) -> str:
        """渲染标题：主章节用蓝色胶囊，小项目标题保持源码笔记感。"""

        rendered = self._render_inline(text)
        if level <= 2:
            return (
                '<h2 style="margin:34px 0 16px;font-size:22px;line-height:1.45;'
                'color:#111827;font-weight:700;">'
                f"{rendered}</h2>"
            )
        if level == 3:
            return (
                '<section style="text-align:center;margin:34px 0 22px;">'
                '<span style="display:inline-block;padding:8px 20px;border-radius:8px;'
                'background:#14558a;color:#ffffff;font-size:17px;line-height:1.45;'
                'font-weight:700;box-shadow:0 4px 12px rgba(20,85,138,.18);">'
                f"{rendered}</span></section>"
            )
        return (
            '<h3 style="margin:30px 0 10px;padding-left:10px;border-left:4px solid #2563eb;'
            'font-size:18px;line-height:1.55;color:#111827;font-weight:700;">'
            f"{rendered}</h3>"
        )

    def _render_paragraph(self, text: str) -> str:
        """渲染普通段落。"""

        return (
            '<p style="margin:18px 0;color:#2f3742;line-height:2.02;font-size:17px;">'
            f"{self._render_inline(text)}</p>"
        )

    def _render_quote(self, text: str) -> str:
        """渲染引用块，用于关键观察或结论。"""

        return (
            '<blockquote style="margin:20px 0;padding:12px 16px;border-left:4px solid #2563eb;'
            'background:#f8fbff;color:#1f3a5f;border-radius:8px;line-height:1.9;font-size:16px;">'
            f"{self._render_inline(text)}</blockquote>"
        )

    def _render_list(self, items: list[str], ordered: bool) -> str:
        """渲染有序或无序列表。"""

        tag = "ol" if ordered else "ul"
        style = (
            "margin:18px 0 20px;padding-left:26px;color:#2f3742;line-height:1.95;font-size:17px;"
            if ordered
            else "margin:18px 0 20px;padding-left:24px;color:#2f3742;line-height:1.95;font-size:17px;"
        )
        item_html = "".join(
            f'<li style="margin:8px 0;">{self._render_inline(item)}</li>' for item in items if item.strip()
        )
        return f'<{tag} style="{style}">{item_html}</{tag}>'

    def _render_code_block(self, code_lines: list[str]) -> str:
        """渲染代码块，保留换行并做 HTML 转义。"""

        code = html.escape("\n".join(code_lines).strip(), quote=True)
        if not code:
            return ""
        return (
            '<pre style="margin:20px 0;padding:14px 16px;border-radius:10px;'
            'background:#0f172a;color:#dbeafe;overflow:auto;font-size:13px;'
            'line-height:1.75;font-family:SFMono-Regular,Consolas,Menlo,monospace;">'
            f"<code>{code}</code></pre>"
        )

    def _build_project_visual_cards(
        self,
        content: GeneratedContentForLayout,
        image_assets: dict[str, list[MediaAssetRecord]],
    ) -> tuple[dict[str, dict[str, Any]], int | None]:
        """按项目顺序构建一对一图片说明卡，供正文项目标题后插入。"""

        cards: dict[str, dict[str, Any]] = {}
        cover_asset_id: int | None = None
        for index, prompt_item in enumerate(content.image_prompts, start=1):
            repository_full_name = str(prompt_item.get("repository_full_name", "")).strip()
            if not repository_full_name:
                continue
            candidates = image_assets.get(repository_full_name, [])
            asset = candidates[0] if candidates else None
            if cover_asset_id is None and asset is not None:
                cover_asset_id = asset.id
            summary_text = str(
                prompt_item.get("summary_text", "") or prompt_item.get("project_summary_text", "")
            ).strip()
            if not summary_text:
                summary_text = f"这张图用于辅助理解 {repository_full_name} 的核心流程和模块关系。"

            card_html, did_embed = self._render_project_visual_card(
                index=index,
                repository_full_name=repository_full_name,
                summary_text=summary_text,
                asset=asset,
            )
            cards[repository_full_name] = {
                "html": card_html,
                "did_embed": did_embed,
                "asset_id": None if asset is None else asset.id,
                "rank": str(prompt_item.get("rank", index) or index),
            }
        return cards, cover_asset_id

    def _render_unplaced_project_visual_cards(
        self,
        project_visual_cards: dict[str, dict[str, Any]],
        inserted_repositories: set[str],
    ) -> str:
        """无法匹配正文标题时，把图片作为补充图解自然放到文末。"""

        unplaced_cards = [
            str(card["html"])
            for repository_full_name, card in project_visual_cards.items()
            if repository_full_name not in inserted_repositories
        ]
        if not unplaced_cards:
            return ""
        return (
            '<section style="text-align:center;margin:34px 0 18px;">'
            '<span style="display:inline-block;padding:8px 20px;border-radius:8px;'
            'background:#14558a;color:#ffffff;font-size:17px;font-weight:700;">补充图解</span>'
            "</section>\n"
            + "\n".join(unplaced_cards)
        )

    def _render_project_visual_card(
        self,
        index: int,
        repository_full_name: str,
        summary_text: str,
        asset: MediaAssetRecord | None,
    ) -> tuple[str, bool]:
        """渲染单个项目的一对一插图：图片和概要绑定出现。"""

        rendered_summary = self._render_inline(summary_text)
        image_url = self._public_image_url(asset) if asset is not None else None
        escaped_repo = html.escape(repository_full_name, quote=True)

        if image_url:
            image_block = (
                f'<img src="{html.escape(image_url, quote=True)}" alt="{escaped_repo}" '
                f'data-asset-id="{asset.id}" style="display:block;width:100%;border-radius:10px;'
                'margin:0 auto;background:#ffffff;border:1px solid #edf2f7;" />'
            )
            did_embed = True
        elif asset is not None:
            local_placeholder_url = f"{LOCAL_WECHAT_IMAGE_SCHEME}://{asset.id}"
            image_block = (
                f'<img src="{html.escape(local_placeholder_url, quote=True)}" alt="{escaped_repo}" '
                f'data-asset-id="{asset.id}" data-local-wechat-placeholder="true" '
                'style="display:block;width:100%;border-radius:10px;margin:0 auto;'
                'background:#ffffff;border:1px dashed #93c5fd;" />'
            )
            did_embed = False
        else:
            image_block = (
                '<section style="margin:0;padding:42px 16px;border-radius:10px;border:1px dashed #93c5fd;'
                'background:#f8fbff;color:#64748b;text-align:center;font-size:14px;">配图待生成或待上传</section>'
            )
            did_embed = False

        card_html = (
            '<figure style="margin:20px 0 28px;padding:0;text-align:center;">'
            f"{image_block}"
            '<figcaption style="margin:10px auto 0;max-width:92%;color:#64748b;'
            'font-size:14px;line-height:1.75;text-align:left;">'
            f'<span style="color:#2563eb;font-weight:700;">图 {index}</span>｜{rendered_summary}'
            "</figcaption></figure>"
        )
        return card_html, did_embed

    def _render_footer(self, content: GeneratedContentForLayout) -> str:
        """不在正文末尾渲染自动化说明，避免破坏技术博客阅读感。"""

        return ""

    def _render_inline(self, text: str) -> str:
        """渲染安全的行内文本，支持链接、粗体和行内代码。"""

        rendered_parts: list[str] = []
        last_index = 0
        for match in self._link_pattern.finditer(text):
            rendered_parts.append(self._render_plain_inline(text[last_index : match.start()]))
            label = match.group(1).strip()
            url = match.group(2).strip()
            if self._is_safe_http_url(url):
                rendered_parts.append(
                    '<a href="'
                    + html.escape(url, quote=True)
                    + '" style="color:#2563eb;text-decoration:none;border-bottom:1px solid #bfdbfe;">'
                    + self._render_plain_inline(label)
                    + "</a>"
                )
            else:
                rendered_parts.append(self._render_plain_inline(match.group(0)))
            last_index = match.end()
        rendered_parts.append(self._render_plain_inline(text[last_index:]))
        return "".join(rendered_parts)

    def _render_plain_inline(self, text: str) -> str:
        """对非链接文本做转义和基础格式化。"""

        escaped = html.escape(text, quote=True)
        escaped = re.sub(
            r"`([^`]+)`",
            (
                r'<code style="padding:2px 7px;border-radius:6px;background:#f3f4f6;'
                r'color:#e11d48;font-size:92%;font-family:SFMono-Regular,Consolas,Menlo,monospace;">\1</code>'
            ),
            escaped,
        )
        escaped = re.sub(r"\*\*([^*]+)\*\*", r'<strong style="color:#111827;font-weight:700;">\1</strong>', escaped)
        return escaped

    def _group_image_assets(self, media_assets: list[MediaAssetRecord]) -> dict[str, list[MediaAssetRecord]]:
        """按 repository_full_name 组织图片素材，方便与动态 N 个项目 prompt 对齐。"""

        grouped: dict[str, list[MediaAssetRecord]] = {}
        for asset in media_assets:
            if asset.asset_type != "image":
                continue
            if asset.status == "replaced":
                continue
            repository_full_name = str(asset.metadata.get("repository_full_name", "")).strip()
            if not repository_full_name:
                repository_full_name = "__unmatched__"
            grouped.setdefault(repository_full_name, []).append(asset)

        for assets in grouped.values():
            assets.sort(key=lambda item: (self._image_provider_priority(item.provider), item.id))
        return grouped

    def _image_provider_priority(self, provider: str) -> int:
        """同一项目有多张图时，优先使用火山方舟原始生图。"""

        priorities = {
            "seedream": 0,
            "github_repository_asset": 1,
            "local_tech_card": 2,
        }
        return priorities.get(provider, 9)

    def _public_image_url(self, asset: MediaAssetRecord | None) -> str | None:
        """读取可直接放入公众号 HTML 的公网图片 URL。"""

        if asset is None:
            return None
        remote_url = str(asset.metadata.get("remote_url", "")).strip()
        if self._is_safe_http_url(remote_url):
            return remote_url
        if self._is_safe_http_url(asset.path):
            return asset.path
        return None

    def _is_safe_http_url(self, url: str) -> bool:
        """只允许 http/https 链接进入最终 HTML。"""

        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
