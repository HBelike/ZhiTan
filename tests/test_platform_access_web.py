from __future__ import annotations

from types import SimpleNamespace

from src.platform_access import web


def test_bootstrap_status_exposes_runtime_setup_capabilities(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "owner@example.test")
    monkeypatch.setenv("PLATFORM_PUBLIC_REGISTRATION_ENABLED", "false")
    monkeypatch.setenv("PLATFORM_CLI_BOOTSTRAP_ONLY", "true")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
    monkeypatch.delenv("PLATFORM_EMAIL_CODE_SECRET", raising=False)
    monkeypatch.setattr(
        web,
        "get_platform_access_service",
        lambda _request: SimpleNamespace(requires_bootstrap=lambda: True),
    )

    response = web.bootstrap_status(SimpleNamespace())

    assert response == {
        "requires_bootstrap": True,
        "admin_email": "owner@example.test",
        "public_registration_enabled": False,
        "email_auth_enabled": False,
        "cli_bootstrap_only": True,
        "bootstrap_command": "docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py",
    }
