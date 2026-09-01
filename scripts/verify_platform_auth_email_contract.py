"""验证注册、邮箱绑定与找回密码的 HTTP 契约。

本脚本不连接 PostgreSQL、不读取真实密钥，也不会发送邮件。它使用内存服务替身验证浏览器
所需的注册、验证码和密码重置接口，以及 Resend 投递失败时的中文错误能否原样返回。
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.platform_access.contracts import PlatformRole, PlatformUser
from src.platform_access.email_delivery import EmailDeliveryError
from src.platform_access.service import AuthenticatedSession
from src.platform_access.web import router


class FakeAuthService:
    """为接口契约验证提供确定性响应，不接触真实账号数据。"""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.user = PlatformUser(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            organization_id=UUID("22222222-2222-2222-2222-222222222222"),
            username="member-contract-test",
            display_name="接口契约用户",
            email="contract-test@example.com",
            email_verified_at=now,
            role=PlatformRole.USER,
            is_active=True,
            created_at=now,
        )
        self.last_reset: tuple[str, str, str] | None = None
        self.last_login_email: str | None = None
        self.reject_registration_delivery = False

    def send_login_code(self, *, email: str) -> dict[str, object]:
        """模拟邮箱验证码登录第一步。"""

        self.last_login_email = email
        return {
            "accepted": True,
            "challenge_id": "33333333-3333-3333-3333-333333333333",
            "expires_at": "2026-08-13T00:10:00+00:00",
        }

    def authenticate_with_code(self, **_: object) -> AuthenticatedSession:
        """模拟验证码登录成功后的会话。"""

        now = datetime.now(UTC)
        return AuthenticatedSession(
            user=self.user,
            raw_token="email-login-session-token",
            expires_at=now + timedelta(days=7),
            absolute_expires_at=now + timedelta(days=30),
        )

    def send_registration_code(self, **_: object) -> dict[str, object]:
        """模拟发送注册验证码或供应商拒绝投递。"""

        if self.reject_registration_delivery:
            raise EmailDeliveryError(
                "验证码邮件无法投递：当前发件地址使用 Resend 的 resend.dev 测试域名，"
                "它只能发送到 Resend 账户绑定的邮箱。请先在 Resend 验证自己的域名，"
                "再将 RESEND_FROM_ADDRESS 改为该域名下的地址后重试。",
                reason="resend_test_domain_restriction",
                status_code=403,
            )
        return {
            "accepted": True,
            "challenge_id": "11111111-1111-1111-1111-111111111111",
            "expires_at": "2026-08-13T00:10:00+00:00",
        }

    def verify_registration_code(self, **_: object) -> AuthenticatedSession:
        """模拟验证码正确后的已登录用户。"""

        now = datetime.now(UTC)
        return AuthenticatedSession(
            user=self.user,
            raw_token="contract-session-token",
            expires_at=now + timedelta(days=7),
            absolute_expires_at=now + timedelta(days=30),
        )

    def send_password_reset_code(self, **_: object) -> dict[str, object]:
        """模拟找回密码第一步。"""

        return {
            "accepted": True,
            "challenge_id": "22222222-2222-2222-2222-222222222222",
            "expires_at": "2026-08-13T00:10:00+00:00",
        }

    def reset_password(self, *, challenge_id: str, code: str, new_password: str) -> None:
        """记录找回密码确认参数，供断言调用链使用。"""

        self.last_reset = (challenge_id, code, new_password)


def main() -> None:
    """执行注册和重置密码接口的离线合同测试。"""

    original_value = os.environ.get("PLATFORM_PUBLIC_REGISTRATION_ENABLED")
    os.environ["PLATFORM_PUBLIC_REGISTRATION_ENABLED"] = "true"
    service = FakeAuthService()
    app = FastAPI()
    app.include_router(router)
    try:
        with patch("src.platform_access.web.get_platform_access_service", return_value=service):
            with TestClient(app) as client:
                registration_response = client.post(
                    "/api/auth/register/send-code",
                    json={
                        "email": "candidate@qq.com",
                        "display_name": "候选人",
                        "password": "valid-password-123",
                    },
                )
                assert registration_response.status_code == 200
                assert registration_response.json()["accepted"] is True

                verify_response = client.post(
                    "/api/auth/register/verify",
                    json={
                        "challenge_id": "11111111-1111-1111-1111-111111111111",
                        "code": "123456",
                    },
                )
                assert verify_response.status_code == 200
                assert verify_response.json()["user"]["email"] == "contract-test@example.com"
                assert "platform_session=" in verify_response.headers.get("set-cookie", "")

                login_code_response = client.post(
                    "/api/auth/email-login/send-code",
                    json={"email": "candidate@qq.com"},
                )
                assert login_code_response.status_code == 200
                assert login_code_response.json()["challenge_id"] == "33333333-3333-3333-3333-333333333333"
                assert service.last_login_email == "candidate@qq.com"

                login_verify_response = client.post(
                    "/api/auth/email-login/verify",
                    json={
                        "challenge_id": "33333333-3333-3333-3333-333333333333",
                        "code": "123456",
                    },
                )
                assert login_verify_response.status_code == 200
                assert login_verify_response.json()["user"]["email"] == "contract-test@example.com"
                assert "platform_session=" in login_verify_response.headers.get("set-cookie", "")

                reset_send_response = client.post(
                    "/api/auth/password-reset/send-code",
                    json={"email": "candidate@qq.com"},
                )
                assert reset_send_response.status_code == 200
                assert reset_send_response.json()["challenge_id"] == "22222222-2222-2222-2222-222222222222"

                reset_verify_response = client.post(
                    "/api/auth/password-reset/verify",
                    json={
                        "challenge_id": "22222222-2222-2222-2222-222222222222",
                        "code": "654321",
                        "new_password": "new-valid-password-123",
                    },
                )
                assert reset_verify_response.status_code == 200
                assert reset_verify_response.json() == {"ok": True}
                assert service.last_reset == (
                    "22222222-2222-2222-2222-222222222222",
                    "654321",
                    "new-valid-password-123",
                )

                service.reject_registration_delivery = True
                rejected_response = client.post(
                    "/api/auth/register/send-code",
                    json={
                        "email": "candidate@qq.com",
                        "display_name": "候选人",
                        "password": "valid-password-123",
                    },
                )
                assert rejected_response.status_code == 400
                assert "resend.dev 测试域名" in rejected_response.json()["detail"]
    finally:
        if original_value is None:
            os.environ.pop("PLATFORM_PUBLIC_REGISTRATION_ENABLED", None)
        else:
            os.environ["PLATFORM_PUBLIC_REGISTRATION_ENABLED"] = original_value

    print("platform_auth_email_contract_ok")


if __name__ == "__main__":
    main()
