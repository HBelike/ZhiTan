"""验证 Quickstart 的 HTTP、迁移和 Worker 运行契约。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _request_json(response: Any, expected_status: int, label: str) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise RuntimeError(f"{label} 返回 {response.status_code}，期望 {expected_status}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} 未返回 JSON object")
    return payload


def verify_pre_bootstrap(base_url: str, *, session: Any = requests) -> None:
    base = base_url.rstrip("/")
    _request_json(session.get(f"{base}/api/health", timeout=10), 200, "health")
    ready = _request_json(session.get(f"{base}/api/ready", timeout=10), 200, "ready")
    if not ready.get("ready") or not all(check.get("ok") for check in ready.get("checks", [])):
        raise RuntimeError("ready 响应包含失败检查")
    bootstrap = _request_json(
        session.get(f"{base}/api/auth/bootstrap-status", timeout=10),
        200,
        "bootstrap-status",
    )
    if bootstrap.get("requires_bootstrap") is not True:
        raise RuntimeError("首次启动前应处于 bootstrap pending 状态")
    _request_json(
        session.get(f"{base}/api/navigation/modules", timeout=10),
        401,
        "anonymous protected API",
    )


def verify_post_bootstrap(
    base_url: str,
    *,
    admin_email: str,
    password: str,
    session: Any = requests,
) -> None:
    base = base_url.rstrip("/")
    bootstrap = _request_json(
        session.get(f"{base}/api/auth/bootstrap-status", timeout=10),
        200,
        "bootstrap-status",
    )
    if bootstrap.get("requires_bootstrap") is not False:
        raise RuntimeError("管理员创建后 bootstrap 状态仍未关闭")
    login = _request_json(
        session.post(
            f"{base}/api/auth/login",
            json={"identity": admin_email, "password": password},
            timeout=10,
        ),
        200,
        "password login",
    )
    if login.get("user", {}).get("role") != "admin":
        raise RuntimeError("密码登录未返回管理员身份")
    current_user = _request_json(session.get(f"{base}/api/auth/me", timeout=10), 200, "auth me")
    if current_user.get("user", {}).get("role") != "admin":
        raise RuntimeError("登录 Cookie 未通过受保护接口验证")


def _compose_command(project: str, *arguments: str) -> list[str]:
    command = ["docker", "compose"]
    if project:
        command.extend(["--project-name", project])
    command.extend(["--env-file", ".env.quickstart", *arguments])
    return command


def verify_compose_runtime(project: str) -> None:
    migration_id = subprocess.run(
        _compose_command(project, "ps", "-a", "-q", "career-migrate"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not migration_id:
        raise RuntimeError("未找到 career-migrate 容器")
    migration_state = json.loads(
        subprocess.run(
            ["docker", "inspect", migration_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout,
    )[0]
    if migration_state["State"]["ExitCode"] != 0:
        raise RuntimeError("career-migrate 未成功退出")

    worker_id = subprocess.run(
        _compose_command(project, "ps", "-q", "career-agent-worker"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not worker_id:
        raise RuntimeError("career-agent-worker 未运行")
    worker_state = json.loads(
        subprocess.run(
            ["docker", "inspect", worker_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout,
    )[0]
    if worker_state.get("RestartCount") != 0:
        raise RuntimeError("career-agent-worker 出现重启")
    if worker_state["State"].get("Health", {}).get("Status") != "healthy":
        raise RuntimeError("career-agent-worker 未处于 healthy")

    logs = subprocess.run(
        _compose_command(project, "logs", "--no-color", "career-api", "career-agent-worker", "career-web"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if "Traceback (most recent call last)" in logs:
        raise RuntimeError("Quickstart 日志包含 Python Traceback")


def _read_password() -> str:
    if sys.stdin.isatty():
        raise RuntimeError("post-bootstrap 验证只接受 stdin 密码")
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise RuntimeError("stdin 密码为空")
    return password


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 ZhiTan Quickstart")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--compose-project", default="")
    parser.add_argument("--phase", choices=("pre", "post"), default="pre")
    parser.add_argument("--admin-email", default="")
    arguments = parser.parse_args()
    try:
        if arguments.phase == "pre":
            verify_pre_bootstrap(arguments.base_url)
            verify_compose_runtime(arguments.compose_project)
        else:
            if not arguments.admin_email:
                raise RuntimeError("post phase 必须提供 --admin-email")
            password = _read_password()
            try:
                verify_post_bootstrap(
                    arguments.base_url,
                    admin_email=arguments.admin_email,
                    password=password,
                    session=requests.Session(),
                )
            finally:
                password = ""
    except (RuntimeError, requests.RequestException, subprocess.SubprocessError) as exc:
        print(f"quickstart_verification_failed: {exc}", file=sys.stderr)
        return 1
    print(f"quickstart_{arguments.phase}_verification_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
