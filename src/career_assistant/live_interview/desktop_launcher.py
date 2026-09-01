"""从本地求职助手服务启动 Windows 实时面试采集器。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from typing import Any


class DesktopLauncherError(RuntimeError):
    """桌面采集器当前无法启动。"""


@dataclass(frozen=True)
class DesktopLaunchResult:
    status: str
    message: str


PopenFactory = Callable[..., Any]
RunFactory = Callable[..., Any]
BackgroundFactory = Callable[[Callable[[], None]], None]
_bootstrap_lock = Lock()
_bootstrap_active = False


def _start_background(task: Callable[[], None]) -> None:
    Thread(target=task, name="live-interview-electron-bootstrap", daemon=True).start()


def _launch_process(
    command: Sequence[str],
    desktop_root: Path,
    popen_factory: PopenFactory,
) -> Any:
    creation_flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    return popen_factory(
        command,
        cwd=str(desktop_root),
        close_fds=True,
        creationflags=creation_flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_windows_desktop_assistant(
    *,
    api_base_url: str = "http://127.0.0.1:8000",
    workspace_root: Path | None = None,
    platform_name: str | None = None,
    node_executable: str | None = None,
    popen_factory: PopenFactory = subprocess.Popen,
    run_factory: RunFactory = subprocess.run,
    background_factory: BackgroundFactory = _start_background,
) -> DesktopLaunchResult:
    """启动 Electron 采集器；重复启动由 Electron 单实例机制负责聚焦。"""

    active_platform = platform_name or sys.platform
    if active_platform != "win32":
        raise DesktopLauncherError("面试大师的双路音频采集目前仅支持 Windows 10/11")

    root = workspace_root or Path(__file__).resolve().parents[3]
    desktop_root = root / "desktop-interview-assistant"
    electron_executable = desktop_root / "node_modules" / "electron" / "dist" / "electron.exe"
    electron_installer = desktop_root / "node_modules" / "electron" / "install.js"
    renderer_entry = desktop_root / "dist-renderer" / "index.html"

    if not renderer_entry.is_file():
        raise DesktopLauncherError(
            "Windows 采集组件尚未构建，请在 desktop-interview-assistant 中执行 npm run build"
        )

    preparing = not electron_executable.is_file()
    if preparing:
        active_node = node_executable or shutil.which("node.exe") or shutil.which("node")
        if not electron_installer.is_file() or not active_node:
            raise DesktopLauncherError(
                "Windows 采集组件尚未安装，请先在 desktop-interview-assistant 中执行 npm ci"
            )

        global _bootstrap_active
        should_start = False

        def prepare_and_launch() -> None:
            global _bootstrap_active
            try:
                completed = run_factory(
                    (active_node, str(electron_installer)),
                    cwd=str(desktop_root),
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if completed.returncode == 0 and electron_executable.is_file():
                    _launch_process(
                        (str(electron_executable), ".", f"--api-base-url={api_base_url}"),
                        desktop_root,
                        popen_factory,
                    )
            finally:
                with _bootstrap_lock:
                    _bootstrap_active = False

        with _bootstrap_lock:
            if not _bootstrap_active:
                _bootstrap_active = True
                should_start = True
        if should_start:
            background_factory(prepare_and_launch)
    else:
        try:
            _launch_process(
                (str(electron_executable), ".", f"--api-base-url={api_base_url}"),
                desktop_root,
                popen_factory,
            )
        except OSError as exc:
            raise DesktopLauncherError("Windows 采集组件启动失败，请重新安装桌面依赖") from exc

    return DesktopLaunchResult(
        status="preparing" if preparing else "launching",
        message=(
            "首次启动正在后台准备 Windows 采集组件，完成后会自动打开面试大师"
            if preparing
            else "面试大师正在启动；若窗口已经打开，将自动切换到现有窗口"
        ),
    )
