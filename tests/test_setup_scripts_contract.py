from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_quickstart_scripts_share_the_python_config_generator() -> None:
    powershell = _read("scripts/setup_quickstart.ps1")
    posix = _read("scripts/setup_quickstart.sh")

    assert "scripts/setup_common.py quickstart" in powershell
    assert "scripts/setup_common.py quickstart" in posix
    assert "docker compose --env-file .env.quickstart up -d --build --wait" in powershell
    assert "docker compose --env-file .env.quickstart up -d --build --wait" in posix


def test_development_scripts_use_only_the_project_virtual_environment() -> None:
    powershell = _read("scripts/setup_dev.ps1")
    posix = _read("scripts/setup_dev.sh")

    assert ".venv\\Scripts\\python.exe" in powershell
    assert ".venv/bin/python" in posix
    assert "npm --prefix web-ui ci" in powershell
    assert "npm --prefix web-ui ci" in posix


def test_admin_password_reset_is_interactive_only() -> None:
    source = _read("scripts/bootstrap_first_admin.py")

    assert '"--reset-password"' in source
    assert "getpass.getpass" in source
    assert "输入 RESET 确认重置管理员密码" in source
    assert '"--password"' not in source
