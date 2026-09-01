from __future__ import annotations

from pathlib import Path

import pytest

from src.career_assistant.live_interview.desktop_launcher import (
    DesktopLauncherError,
    launch_windows_desktop_assistant,
)


def _ready_workspace(root: Path) -> Path:
    desktop_root = root / "desktop-interview-assistant"
    electron = desktop_root / "node_modules" / "electron" / "dist" / "electron.exe"
    renderer = desktop_root / "dist-renderer" / "index.html"
    electron.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    electron.touch()
    renderer.touch()
    return desktop_root


def test_launcher_starts_built_electron_from_workspace(tmp_path: Path) -> None:
    desktop_root = _ready_workspace(tmp_path)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_popen(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return object()

    result = launch_windows_desktop_assistant(
        api_base_url="http://127.0.0.1:18080",
        workspace_root=tmp_path,
        platform_name="win32",
        popen_factory=fake_popen,
    )

    assert result.status == "launching"
    assert calls[0][0] == (
        str(desktop_root / "node_modules" / "electron" / "dist" / "electron.exe"),
        ".",
        "--api-base-url=http://127.0.0.1:18080",
    )
    assert calls[0][1]["cwd"] == str(desktop_root)
    assert calls[0][1]["close_fds"] is True
    assert calls[0][1]["stdout"] is not None


def test_launcher_hides_first_download_and_opens_ui_after_install(tmp_path: Path) -> None:
    desktop_root = tmp_path / "desktop-interview-assistant"
    installer = desktop_root / "node_modules" / "electron" / "install.js"
    electron = desktop_root / "node_modules" / "electron" / "dist" / "electron.exe"
    renderer = desktop_root / "dist-renderer" / "index.html"
    installer.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    installer.touch()
    renderer.touch()
    run_calls = []
    popen_calls = []

    def fake_run(command, **kwargs):
        run_calls.append((tuple(command), kwargs))
        electron.parent.mkdir(parents=True)
        electron.touch()
        return type("Completed", (), {"returncode": 0})()

    def fake_popen(command, **kwargs):
        popen_calls.append((tuple(command), kwargs))
        return object()

    result = launch_windows_desktop_assistant(
        api_base_url="http://127.0.0.1:18080",
        workspace_root=tmp_path,
        platform_name="win32",
        node_executable="C:\\NodeJS\\node.exe",
        popen_factory=fake_popen,
        run_factory=fake_run,
        background_factory=lambda task: task(),
    )

    assert result.status == "preparing"
    assert run_calls[0][0] == ("C:\\NodeJS\\node.exe", str(installer))
    assert run_calls[0][1]["stdout"] is not None
    assert popen_calls[0][0] == (
        str(electron),
        ".",
        "--api-base-url=http://127.0.0.1:18080",
    )
    assert "后台准备" in result.message


def test_launcher_rejects_non_windows_platform(tmp_path: Path) -> None:
    with pytest.raises(DesktopLauncherError, match="仅支持 Windows"):
        launch_windows_desktop_assistant(
            workspace_root=tmp_path,
            platform_name="linux",
        )


def test_launcher_explains_missing_electron_package(tmp_path: Path) -> None:
    renderer = tmp_path / "desktop-interview-assistant" / "dist-renderer" / "index.html"
    renderer.parent.mkdir(parents=True)
    renderer.touch()
    with pytest.raises(DesktopLauncherError, match="尚未安装"):
        launch_windows_desktop_assistant(
            workspace_root=tmp_path,
            platform_name="win32",
            node_executable="C:\\NodeJS\\node.exe",
        )
