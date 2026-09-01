from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


ARTICLE_VISUAL_SPEC_VERSION = "article_visual_spec_v1"
FIGURE_ROLES = frozenset(
    {"summary_card", "flow", "architecture", "comparison", "timeline"}
)


class ArticleVisualSpecError(ValueError):
    """ArticleVisualSpec 不满足结构、证据或模板容量合同时抛出。"""


@dataclass(frozen=True)
class ValidatedArticleVisualSpec:
    """已完成全部渲染前校验、可以安全交给模板层的不可变结果。"""

    value: dict[str, Any]
    canonical_json: str
    repository_full_name: str
    figure_role: str
    visible_texts: tuple[str, ...]
    node_ids: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]


def build_render_key(
    spec: ValidatedArticleVisualSpec,
    template_version: str,
    renderer_version: str,
    font_version: str,
) -> str:
    """把规格及全部会影响像素的版本编译为稳定 render key。"""

    versions = (template_version, renderer_version, font_version)
    if any(not isinstance(value, str) or not value.strip() for value in versions):
        raise ArticleVisualSpecError("render key 的模板、渲染器和字体版本不能为空")
    source = "\n".join((spec.canonical_json, *versions))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
