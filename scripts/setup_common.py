"""为 Quickstart 和源码开发生成相互隔离的本地配置。"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import stat
from collections.abc import Mapping
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PortConflictError(RuntimeError):
    """目标监听端口已被其他进程占用。"""


def generate_urlsafe_secret(byte_count: int = 32) -> str:
    """生成可以安全放入 dotenv 和 PostgreSQL URL 的随机值。"""

    return secrets.token_urlsafe(byte_count)


def assert_ports_available(bindings: Mapping[str, tuple[str, int]]) -> None:
    """在不启动服务的前提下验证目标 TCP 端口可绑定。"""

    conflicts: list[str] = []
    for name, (host, port) in bindings.items():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((host, port))
        except OSError:
            conflicts.append(f"{name}={port}")
    if conflicts:
        raise PortConflictError(
            "以下端口已被占用：" + ", ".join(conflicts) + "。请修改实例环境文件后重试。",
        )


def render_env(
    template_path: Path,
    destination_path: Path,
    values: Mapping[str, str],
) -> None:
    """基于公开模板独占创建实例环境文件，绝不覆盖现有 Secret。"""

    if destination_path.exists():
        raise FileExistsError(f"{destination_path.name} 已存在")
    rendered: list[str] = []
    seen: set[str] = set()
    for line in template_path.read_text(encoding="utf-8").splitlines():
        key, separator, _value = line.partition("=")
        if separator and key in values:
            rendered.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            rendered.append(line)
    for key, value in values.items():
        if key not in seen:
            rendered.append(f"{key}={value}")
    destination_path.write_text("\n".join(rendered) + "\n", encoding="utf-8", errors="strict", newline="")
    if os.name != "nt":
        destination_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _ensure_env(
    template_name: str,
    destination_name: str,
    values: Mapping[str, str],
) -> Path:
    destination = PROJECT_ROOT / destination_name
    if destination.exists():
        print(f"保留现有 {destination_name}；未覆盖任何本地配置。")
        return destination
    render_env(PROJECT_ROOT / template_name, destination, values)
    print(f"已生成 {destination_name}。")
    return destination


def setup_quickstart() -> None:
    destination = _ensure_env(
        ".env.quickstart.example",
        ".env.quickstart",
        {
            "ZHITAN_ENV_FILE": ".env.quickstart",
            "CAREER_POSTGRES_PASSWORD": generate_urlsafe_secret(),
            "PLATFORM_EMAIL_CODE_SECRET": generate_urlsafe_secret(),
        },
    )
    values = _read_env(destination)
    port = int(values.get("ZHITAN_HTTP_PORT", "18081"))
    assert_ports_available({"ZHITAN_HTTP_PORT": ("127.0.0.1", port)})
    print("配置检查通过。启动命令：")
    print("docker compose --env-file .env.quickstart up -d --build --wait")


def setup_development() -> None:
    password = generate_urlsafe_secret()
    database_name = "zhitan_dev"
    database_user = "zhitan_dev"
    database_port = 54329
    destination = _ensure_env(
        ".env.career-assistant.example",
        ".env.career-assistant",
        {
            "CAREER_POSTGRES_DB": database_name,
            "CAREER_POSTGRES_USER": database_user,
            "CAREER_POSTGRES_PASSWORD": password,
            "CAREER_POSTGRES_PORT": str(database_port),
            "CAREER_DATABASE_URL": (
                f"postgresql+psycopg://{database_user}:{password}"
                f"@127.0.0.1:{database_port}/{database_name}"
            ),
            "PLATFORM_EMAIL_CODE_SECRET": generate_urlsafe_secret(),
        },
    )
    values = _read_env(destination)
    assert_ports_available(
        {
            "CAREER_POSTGRES_PORT": (
                "127.0.0.1",
                int(values.get("CAREER_POSTGRES_PORT", "54329")),
            ),
            "PREVIEW_SERVER_PORT": (
                "127.0.0.1",
                int(values.get("PREVIEW_SERVER_PORT", "18080")),
            ),
            "VITE_PORT": ("127.0.0.1", int(values.get("VITE_PORT", "5173"))),
        },
    )
    print("源码开发端口检查通过。")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 ZhiTan 本地隔离配置")
    parser.add_argument("mode", choices=("quickstart", "dev"))
    arguments = parser.parse_args()
    try:
        if arguments.mode == "quickstart":
            setup_quickstart()
        else:
            setup_development()
    except (FileExistsError, PortConflictError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
