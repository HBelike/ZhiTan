from __future__ import annotations

import re


DEFAULT_WECHAT_TITLE = "GitHub 技术周报"
WECHAT_TITLE_MAX_CHARS = 32


def normalize_wechat_title(title: str) -> str:
    """清理标题空白，但不改变标题内容。"""

    normalized = re.sub(r"\s+", " ", str(title or "")).strip()
    return normalized or DEFAULT_WECHAT_TITLE


def validate_wechat_title(
    title: str,
    max_chars: int = WECHAT_TITLE_MAX_CHARS,
) -> str:
    """按微信 ``draft/add`` 的字段合同校验标题，不做静默截断。"""

    normalized = normalize_wechat_title(title)
    if len(normalized) > max_chars:
        raise ValueError(
            f"微信公众号草稿标题不能超过 {max_chars} 个字："
            f"当前 {len(normalized)} 个字"
        )

    return normalized
