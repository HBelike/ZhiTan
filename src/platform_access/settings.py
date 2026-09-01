"""平台访问模块的环境配置。"""

from __future__ import annotations

from collections.abc import Mapping
import os


DEFAULT_PLATFORM_ADMIN_EMAIL = "admin@example.com"


def load_platform_admin_email(environ: Mapping[str, str] | None = None) -> str:
    """读取并规范化唯一管理员邮箱。"""

    source = os.environ if environ is None else environ
    value = source.get("PLATFORM_ADMIN_EMAIL", DEFAULT_PLATFORM_ADMIN_EMAIL).strip().casefold()
    local, separator, domain = value.partition("@")
    if (
        not local
        or separator != "@"
        or "@" in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or "." not in domain
    ):
        raise ValueError("PLATFORM_ADMIN_EMAIL 必须是有效邮箱")
    return value
