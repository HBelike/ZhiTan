"""平台访问模块的环境配置。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os


DEFAULT_PLATFORM_ADMIN_EMAIL = "admin@example.com"


@dataclass(frozen=True, slots=True)
class PlatformAccessSettings:
    """平台访问能力的运行时环境快照。"""

    admin_email: str
    auth_required: bool
    public_registration_enabled: bool
    email_auth_enabled: bool
    cli_bootstrap_only: bool


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


def _load_boolean(source: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw_value = source.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"环境变量 {name} 必须是 true/false、1/0、yes/no 或 on/off")


def load_platform_access_settings(
    environ: Mapping[str, str] | None = None,
) -> PlatformAccessSettings:
    """在服务初始化或请求时读取配置，避免模块 import 时冻结环境变量。"""

    source = os.environ if environ is None else environ
    email_auth_enabled = all(
        source.get(name, "").strip()
        for name in (
            "RESEND_API_KEY",
            "RESEND_FROM_ADDRESS",
            "PLATFORM_EMAIL_CODE_SECRET",
        )
    )
    return PlatformAccessSettings(
        admin_email=load_platform_admin_email(source),
        auth_required=_load_boolean(source, "PLATFORM_AUTH_REQUIRED", default=False),
        public_registration_enabled=_load_boolean(
            source,
            "PLATFORM_PUBLIC_REGISTRATION_ENABLED",
            default=True,
        ),
        email_auth_enabled=email_auth_enabled,
        cli_bootstrap_only=_load_boolean(
            source,
            "PLATFORM_CLI_BOOTSTRAP_ONLY",
            default=False,
        ),
    )
