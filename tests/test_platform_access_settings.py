"""平台访问环境配置测试。"""

from __future__ import annotations

import pytest

from src.platform_access.settings import load_platform_admin_email


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
