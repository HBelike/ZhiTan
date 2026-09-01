from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.web.router import get_request_actor
from src.platform_access.contracts import PlatformRole, PlatformUser, SessionResolution
from src.web import api as web_api


def _admin_session() -> SessionResolution:
    now = datetime.now(UTC)
    return SessionResolution(
        user=PlatformUser(
            id=uuid4(),
            organization_id=uuid4(),
            username="admin-current",
            display_name="唯一管理员",
            email="admin@example.com",
            email_verified_at=now,
            role=PlatformRole.ADMIN,
            is_active=True,
            created_at=now,
        ),
        session_id=uuid4(),
        expires_at=now + timedelta(days=7),
        absolute_expires_at=now + timedelta(days=30),
    )


def _client(monkeypatch, *, session: SessionResolution | None) -> TestClient:
    app = FastAPI()
    service = SimpleNamespace(resolve_session=lambda _raw_token: session)
    monkeypatch.setattr(web_api, "get_platform_access_service", lambda _request: service)
    monkeypatch.setattr(web_api, "refresh_platform_session_cookie", lambda *_args: None)
    app.middleware("http")(web_api.enforce_platform_access)

    @app.get("/api/actor-probe")
    def actor_probe() -> dict[str, str]:
        actor = get_request_actor()
        return {"actor_id": str(actor.actor_id), "role": actor.role.value}

    return TestClient(app)


def test_readiness_endpoint_is_public_when_authentication_is_required(monkeypatch) -> None:
    app = FastAPI()
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "true")
    app.middleware("http")(web_api.enforce_platform_access)

    @app.get("/api/ready")
    def ready() -> dict[str, bool]:
        return {"ready": True}

    response = TestClient(app).get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_optional_auth_still_propagates_logged_in_admin(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "false")
    session = _admin_session()
    client = _client(monkeypatch, session=session)
    client.cookies.set("platform_session", "valid-session")

    response = client.get("/api/actor-probe")

    assert response.status_code == 200
    assert response.json()["actor_id"] == str(session.user.id)
    assert response.json()["role"] == "admin"


def test_optional_auth_without_cookie_keeps_default_actor(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "false")
    client = _client(monkeypatch, session=None)

    response = client.get("/api/actor-probe")

    assert response.status_code == 200
    assert response.json()["role"] == "user"


def test_required_auth_rejects_invalid_session(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "true")
    client = _client(monkeypatch, session=None)
    client.cookies.set("platform_session", "invalid-session")

    response = client.get("/api/actor-probe")

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录后继续"}
