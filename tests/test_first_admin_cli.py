from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from scripts import bootstrap_first_admin as cli
from src.platform_access.contracts import PlatformRole, PlatformUser


class _Database:
    def close(self) -> None:
        return None


def _admin() -> PlatformUser:
    return PlatformUser(
        id=uuid4(),
        organization_id=uuid4(),
        username="admin-test",
        display_name="Owner",
        email="owner@example.test",
        email_verified_at=datetime.now(UTC),
        role=PlatformRole.ADMIN,
        is_active=True,
        created_at=datetime.now(UTC),
    )


def test_reset_password_cli_uses_hidden_interactive_input(monkeypatch, capsys) -> None:
    password = "replacement-password"
    captured: dict[str, str] = {}
    password_inputs = iter((password, password))
    monkeypatch.setenv("CAREER_DATABASE_URL", "postgresql://not-used-in-test")
    monkeypatch.setattr(cli, "CareerDatabase", lambda _url: _Database())
    monkeypatch.setattr(cli, "PlatformAccessRepository", lambda _database, admin_email: object())
    monkeypatch.setattr(cli, "load_platform_admin_email", lambda: "owner@example.test")
    monkeypatch.setattr(cli, "_require_interactive_terminal", lambda: None)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _label: next(password_inputs))
    monkeypatch.setattr("builtins.input", lambda _label: "RESET")

    def reset(_repository, *, email: str, password: str) -> PlatformUser:
        captured.update(email=email, password=password)
        return _admin()

    monkeypatch.setattr(cli, "reset_configured_admin_password", reset)
    monkeypatch.setattr(cli.sys, "argv", ["bootstrap_first_admin.py", "--reset-password"])

    assert cli.main() == 0
    output = capsys.readouterr()
    assert captured == {"email": "owner@example.test", "password": password}
    assert "first_admin_password_reset_ok role=admin" in output.out
    assert password not in output.out
    assert password not in output.err
