"""平台访问环境配置测试。"""

from __future__ import annotations

import pytest

from src.platform_access.settings import load_platform_access_settings, load_platform_admin_email


def test_platform_admin_email_comes_from_environment() -> None:
    assert (
        load_platform_admin_email({"PLATFORM_ADMIN_EMAIL": " Admin@Example.COM "})
        == "admin@example.com"
    )


def test_platform_admin_email_uses_public_example_by_default() -> None:
    assert load_platform_admin_email({}) == "admin@example.com"


def test_platform_admin_email_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="PLATFORM_ADMIN_EMAIL"):
        load_platform_admin_email({"PLATFORM_ADMIN_EMAIL": "not-an-email"})


def test_platform_access_settings_disable_unconfigured_email_flows() -> None:
    settings = load_platform_access_settings(
        {
            "PLATFORM_ADMIN_EMAIL": "owner@example.test",
            "PLATFORM_AUTH_REQUIRED": "true",
            "PLATFORM_PUBLIC_REGISTRATION_ENABLED": "false",
            "PLATFORM_CLI_BOOTSTRAP_ONLY": "true",
        },
    )

    assert settings.admin_email == "owner@example.test"
    assert settings.auth_required is True
    assert settings.public_registration_enabled is False
    assert settings.cli_bootstrap_only is True
    assert settings.email_auth_enabled is False


def test_platform_access_settings_enable_email_flows_only_with_complete_delivery_config() -> None:
    settings = load_platform_access_settings(
        {
            "PLATFORM_ADMIN_EMAIL": "owner@example.test",
            "RESEND_API_KEY": "test-key",
            "RESEND_FROM_ADDRESS": "ZhiTan <noreply@example.test>",
            "PLATFORM_EMAIL_CODE_SECRET": "test-secret",
        },
    )

    assert settings.email_auth_enabled is True
