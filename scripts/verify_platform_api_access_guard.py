"""验证生产 API 登录保护、二元角色与 Career Actor 注入。

本脚本不连接 PostgreSQL、不读取真实密钥，也不会发送邮件或模型请求；它使用内存中的
会话服务替身，专门覆盖上线前最关键的服务端授权边界。
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.web.router import get_request_actor
from src.platform_access.contracts import PlatformRole, PlatformUser, SessionResolution
from src.platform_access.web import require_admin
from src.web.api import create_app


class FakeAccessService:
    """仅为访问保护验证提供固定会话，不包含任何生产认证实现。"""

    def __init__(self, session: SessionResolution | None) -> None:
        self._session = session

    def resolve_session(self, raw_token: str) -> SessionResolution | None:
        return self._session if raw_token == "test-session" else None


def _session_for(role: PlatformRole) -> SessionResolution:
    now = datetime.now(UTC)
    user = PlatformUser(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        organization_id=UUID("22222222-2222-2222-2222-222222222222"),
        username="deployment-check",
        display_name="部署验证",
        email="deployment-check@example.invalid",
        email_verified_at=now,
        role=role,
        is_active=True,
        created_at=now,
    )
    return SessionResolution(
        user=user,
        session_id=uuid4(),
        expires_at=now + timedelta(days=1),
        absolute_expires_at=now + timedelta(days=7),
    )


def main() -> None:
    """构造应用后覆盖会话服务，验证匿名、普通用户和管理员三条路径。"""

    original_values = {
        "PLATFORM_AUTH_REQUIRED": os.environ.get("PLATFORM_AUTH_REQUIRED"),
    }
    os.environ.update(
        {
            "PLATFORM_AUTH_REQUIRED": "true",
        }
    )
    try:
        app = create_app(PROJECT_ROOT)

        @app.get("/api/_deployment-auth-check")
        def deployment_auth_check() -> dict[str, str]:
            actor = get_request_actor()
            return {
                "organization_id": str(actor.organization_id),
                "actor_id": str(actor.actor_id),
            }

        with patch("src.web.api.get_platform_access_service", return_value=FakeAccessService(None)):
            with TestClient(app) as client:
                assert client.get("/api/health").status_code == 200
                assert client.get("/api/_deployment-auth-check").status_code == 401

        user_session = _session_for(PlatformRole.USER)
        with patch("src.web.api.get_platform_access_service", return_value=FakeAccessService(user_session)):
            with TestClient(app) as client:
                client.cookies.set("platform_session", "test-session")
                response = client.get("/api/_deployment-auth-check")
                assert response.status_code == 200
                assert response.json() == {
                    "organization_id": str(user_session.user.organization_id),
                    "actor_id": str(user_session.user.id),
                }

        try:
            require_admin(user_session.user)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 403
        else:
            raise AssertionError("普通用户不应通过管理员接口校验")

        admin_session = _session_for(PlatformRole.ADMIN)
        with patch("src.web.api.get_platform_access_service", return_value=FakeAccessService(admin_session)):
            with TestClient(app) as client:
                client.cookies.set("platform_session", "test-session")
                response = client.get("/api/_deployment-auth-check")
                assert response.status_code == 200
                assert response.json() == {
                    "organization_id": str(admin_session.user.organization_id),
                    "actor_id": str(admin_session.user.id),
                }
    finally:
        for key, value in original_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("platform_api_access_guard_ok")


if __name__ == "__main__":
    main()
