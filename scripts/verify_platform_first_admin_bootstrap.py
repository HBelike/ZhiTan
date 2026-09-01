"""验证首次管理员 CLI 初始化与生产 Web 禁用开关。

本脚本不连接 PostgreSQL、不读取真实密钥，也不会创建真实账户。它只使用内存仓储和
FastAPI 路由替身，覆盖部署前最关键的首账号边界。
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.platform_access.bootstrap import FirstAdminBootstrapError, bootstrap_first_admin
from src.platform_access.contracts import PLATFORM_ADMIN_EMAIL, PlatformRole, PlatformUser
from src.platform_access.service import PlatformAccessService
from src.platform_access.web import router


class FakeFirstAdminRepository:
    """内存仓储替身，只记录初始化用例的输入而不持久化敏感信息。"""

    def __init__(self, *, active_admin: bool = False, existing_email: bool = False, deny_create: bool = False) -> None:
        self.active_admin = active_admin
        self.existing_email = existing_email
        self.deny_create = deny_create
        self.created: dict[str, object] | None = None

    def has_active_admin(self) -> bool:
        """模拟平台是否已有可登录管理员。"""

        return self.active_admin

    def find_user_by_email(self, email: str) -> tuple[PlatformUser, str] | None:
        """模拟邮箱是否已绑定历史账号。"""

        if not self.existing_email:
            return None
        return _fake_user(), "scrypt$test"

    def create_first_admin(self, **values: object) -> PlatformUser:
        """记录不含明文密码的写入参数，并模拟并发抢占。"""

        if self.deny_create:
            raise PermissionError("管理员已初始化")
        self.created = values
        return _fake_user()


class FakeBootstrapStatusService:
    """仅为路由状态验证提供 bootstrap 状态。"""

    def requires_bootstrap(self) -> bool:
        """模拟尚未创建首个管理员。"""

        return True


def _fake_user() -> PlatformUser:
    """构造不含真实身份信息的管理员视图。"""

    now = datetime.now(UTC)
    return PlatformUser(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        organization_id=UUID("22222222-2222-2222-2222-222222222222"),
        username="admin-test",
        display_name="部署验证管理员",
        email=PLATFORM_ADMIN_EMAIL,
        email_verified_at=now,
        role=PlatformRole.ADMIN,
        is_active=True,
        created_at=now,
    )


def main() -> None:
    """验证正常、拒绝与生产 Web 隔离三条安全路径。"""

    _verify_bootstrap_use_case()
    _verify_reserved_admin_email_cannot_register_as_user()
    _verify_cli_only_web_guard()
    print("platform_first_admin_bootstrap_ok")


def _verify_bootstrap_use_case() -> None:
    """验证密码只被散列传给仓储，以及所有拒绝条件可读。"""

    repository = FakeFirstAdminRepository()
    user = bootstrap_first_admin(
        repository,
        email=PLATFORM_ADMIN_EMAIL.upper(),
        display_name="部署管理员",
        password="safe-password-123",
    )
    assert user.role is PlatformRole.ADMIN
    assert repository.created is not None
    assert repository.created["email"] == PLATFORM_ADMIN_EMAIL
    assert str(repository.created["username"]).startswith("admin-")
    assert str(repository.created["password_hash"]).startswith("scrypt$")
    assert repository.created["password_hash"] != "safe-password-123"

    for rejected_repository in (
        FakeFirstAdminRepository(active_admin=True),
        FakeFirstAdminRepository(existing_email=True),
        FakeFirstAdminRepository(deny_create=True),
    ):
        try:
            bootstrap_first_admin(
                rejected_repository,
                email=PLATFORM_ADMIN_EMAIL,
                display_name="部署管理员",
                password="safe-password-123",
            )
        except FirstAdminBootstrapError:
            pass
        else:
            raise AssertionError("首个管理员拒绝路径未生效")

    try:
        bootstrap_first_admin(
            FakeFirstAdminRepository(),
            email="other@example.com",
            display_name="错误管理员",
            password="safe-password-123",
        )
    except FirstAdminBootstrapError as exc:
        assert PLATFORM_ADMIN_EMAIL in str(exc)
    else:
        raise AssertionError("非固定邮箱不应被创建为管理员")


def _verify_reserved_admin_email_cannot_register_as_user() -> None:
    """固定管理员邮箱不能通过普通注册链路占用为 user。"""

    repository = FakeFirstAdminRepository(active_admin=True)
    service = PlatformAccessService(repository)
    try:
        service.send_registration_code(
            email=PLATFORM_ADMIN_EMAIL,
            display_name="错误普通用户",
            password="safe-password-123",
        )
    except ValueError as exc:
        assert "管理员专用" in str(exc)
    else:
        raise AssertionError("管理员专用邮箱不应进入普通注册链路")

    ordinary_user = replace(
        _fake_user(),
        email="ordinary@example.com",
        role=PlatformRole.USER,
    )
    try:
        service.send_bind_email_code(user=ordinary_user, email=PLATFORM_ADMIN_EMAIL)
    except ValueError as exc:
        assert "管理员专用" in str(exc)
    else:
        raise AssertionError("管理员专用邮箱不应绑定到普通用户")


def _verify_cli_only_web_guard() -> None:
    """验证生产开关启用后，公网 bootstrap API 无法创建或消费挑战。"""

    original_values = {
        "PLATFORM_CLI_BOOTSTRAP_ONLY": os.environ.get("PLATFORM_CLI_BOOTSTRAP_ONLY"),
        "PLATFORM_PUBLIC_REGISTRATION_ENABLED": os.environ.get("PLATFORM_PUBLIC_REGISTRATION_ENABLED"),
    }
    os.environ.update(
        {
            "PLATFORM_CLI_BOOTSTRAP_ONLY": "true",
            "PLATFORM_PUBLIC_REGISTRATION_ENABLED": "true",
        },
    )
    try:
        app = FastAPI()
        app.include_router(router)
        with patch("src.platform_access.web.get_platform_access_service", return_value=FakeBootstrapStatusService()):
            with TestClient(app) as client:
                status_response = client.get("/api/auth/bootstrap-status")
                assert status_response.status_code == 200
                assert status_response.json() == {
                    "requires_bootstrap": True,
                    "public_registration_enabled": True,
                    "cli_bootstrap_only": True,
                }
                send_response = client.post(
                    "/api/auth/bootstrap/send-code",
                    json={
                        "email": "admin@example.com",
                        "display_name": "部署管理员",
                        "password": "safe-password-123",
                    },
                )
                assert send_response.status_code == 403
                assert "服务器交互式终端" in str(send_response.json().get("detail", ""))
                verify_response = client.post(
                    "/api/auth/bootstrap/verify",
                    json={"challenge_id": "12345678", "code": "123456"},
                )
                assert verify_response.status_code == 403
    finally:
        for key, value in original_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
