from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.career_assistant.live_interview.contracts import ServerEvent
from src.career_assistant.live_interview.persistence import session_payload
from src.career_assistant.live_interview import web as live_web
from src.career_assistant.live_interview.contracts import LiveInterviewStatus
from src.career_assistant.live_interview.archive import LiveInterviewArchiveResult
from src.platform_access.contracts import PlatformRole, PlatformUser, SessionResolution


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeRepository:
    def __init__(self, record) -> None:
        self.record = record
        self.lookups: list[tuple[object, object, object]] = []

    def get_session(self, organization_id, actor_id, session_id):
        self.lookups.append((organization_id, actor_id, session_id))
        if (
            organization_id == self.record.organization_id
            and actor_id == self.record.actor_id
            and session_id == self.record.id
        ):
            return self.record
        return None

    def activate(self, organization_id, actor_id, session_id):
        return True

    def end(self, organization_id, actor_id, session_id, *, failed=False):
        return True


class FakeManager:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self.closed = False

    async def start(self):
        await self.queue.put(ServerEvent("session.ready", {"sample_rate": 24000}))

    async def handle(self, event):
        if event.type == "ping":
            await self.queue.put(ServerEvent("pong"))
        if event.type == "session.end":
            self.closed = True

    async def next_event(self):
        return await self.queue.get()

    async def close(self, reason):
        self.closed = True


def _record(*, organization_id=None, actor_id=None):
    from src.career_assistant.live_interview.persistence import LiveInterviewSessionRecord

    now = datetime.now(UTC)
    return LiveInterviewSessionRecord(
        id=uuid4(),
        organization_id=organization_id or live_web.DEFAULT_ORGANIZATION_ID,
        actor_id=actor_id or live_web.DEFAULT_ACTOR_ID,
        candidate_profile_id=uuid4(),
        target_role_profile_id=uuid4(),
        interview_experience_ids=(),
        asr_provider="fake",
        asr_model_profile_id=None,
        answer_model_profile_id=None,
        client_kind="desktop",
        candidate_audio_enabled=True,
        status=LiveInterviewStatus.PREPARING,
        started_at=None,
        ended_at=None,
        created_at=now,
        updated_at=now,
    )


def _authenticated_session() -> SessionResolution:
    now = datetime.now(UTC)
    return SessionResolution(
        user=PlatformUser(
            id=uuid4(),
            organization_id=uuid4(),
            username="interview-user",
            display_name="面试测试账号",
            email="interview@example.test",
            email_verified_at=now,
            role=PlatformRole.ADMIN,
            is_active=True,
            created_at=now,
        ),
        session_id=uuid4(),
        expires_at=now + timedelta(days=7),
        absolute_expires_at=now + timedelta(days=30),
    )


def test_session_payload_never_contains_audio() -> None:
    payload = session_payload(_record())
    assert "pcm" not in str(payload).lower()
    assert "raw_audio" not in str(payload).lower()


def test_create_request_preserves_desktop_defaults() -> None:
    payload = live_web.CreateLiveInterviewRequest()

    assert payload.client_kind == "desktop"
    assert payload.candidate_audio_enabled is True


def test_browser_session_records_capture_policy() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    repository = SimpleNamespace()
    captured: dict[str, object] = {}

    def create_session(*args, **kwargs):
        captured.update(kwargs)
        return replace(
            _record(),
            client_kind=kwargs["client_kind"],
            candidate_audio_enabled=kwargs["candidate_audio_enabled"],
        )

    repository.create_session = create_session
    with (
        patch.dict(os.environ, {"LIVE_INTERVIEW_FAKE_ASR": "1"}),
        patch.object(live_web, "get_live_repository", return_value=repository),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/sessions",
            json={"client_kind": "browser", "candidate_audio_enabled": False},
        )

    assert response.status_code == 201
    assert captured["client_kind"] == "browser"
    assert captured["candidate_audio_enabled"] is False
    assert response.json()["session"]["client_kind"] == "browser"
    assert response.json()["session"]["candidate_audio_enabled"] is False


def test_session_can_start_without_reference_materials() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    repository = SimpleNamespace()
    repository.create_session = lambda *args, **kwargs: replace(
        _record(),
        candidate_profile_id=None,
        target_role_profile_id=None,
        interview_experience_ids=(),
        asr_model_profile_id=kwargs["asr_model_profile_id"],
        answer_model_profile_id=kwargs["answer_model_profile_id"],
    )
    asr_model_id = uuid4()
    answer_model_id = uuid4()

    with (
        patch.object(live_web, "get_live_repository", return_value=repository),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/sessions",
            json={
                "asr_model_profile_id": str(asr_model_id),
                "answer_model_profile_id": str(answer_model_id),
            },
        )

    assert response.status_code == 201
    assert response.json()["session"]["candidate_profile_id"] is None
    assert response.json()["session"]["target_role_profile_id"] is None


def test_session_rejects_missing_transcription_configuration() -> None:
    app = FastAPI()
    app.include_router(live_web.router)

    with (
        patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "", "OPENAI_API_KEY": "", "LIVE_INTERVIEW_FAKE_ASR": ""},
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/sessions",
            json={"answer_model_profile_id": str(uuid4())},
        )

    assert response.status_code == 422
    assert "实时转写模型" in response.json()["detail"]


def test_session_uses_dashscope_environment_transcription() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    repository = SimpleNamespace()
    captured: dict[str, object] = {}

    def create_session(*args, **kwargs):
        captured.update(kwargs)
        return replace(
            _record(),
            asr_provider=kwargs["asr_provider"],
            asr_model_profile_id=kwargs["asr_model_profile_id"],
            answer_model_profile_id=kwargs["answer_model_profile_id"],
        )

    repository.create_session = create_session
    with (
        patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "configured",
                "OPENAI_API_KEY": "",
                "LIVE_INTERVIEW_FAKE_ASR": "",
            },
        ),
        patch.object(live_web, "get_live_repository", return_value=repository),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/sessions",
            json={"answer_model_profile_id": str(uuid4())},
        )

    assert response.status_code == 201
    assert captured["asr_provider"] == "dashscope"


def test_environment_asr_prefers_dashscope_and_hides_key() -> None:
    with patch.dict(
        os.environ,
        {
            "DASHSCOPE_API_KEY": "secret-value",
            "OPENAI_API_KEY": "also-secret",
            "LIVE_INTERVIEW_FAKE_ASR": "",
        },
    ):
        config = live_web._environment_asr_config()

    assert config["readiness"] == "ready"
    assert config["provider_key"] == "dashscope"
    assert config["model_id"] == "qwen-audio-3.0-asr-flash-streaming"
    assert "secret-value" not in str(config)


def test_setup_options_describe_all_supported_desktop_browsers() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    services = SimpleNamespace(
        model_gateway=SimpleNamespace(list_availability=lambda _organization_id: []),
    )

    with (
        patch.object(live_web, "get_live_read_services", return_value=services),
        TestClient(app) as client,
    ):
        response = client.get("/api/career/live-interviews/setup-options")

    assert response.status_code == 200
    assert response.json()["audio_policy"]["platform"] == (
        "桌面版 Chrome、Edge、Firefox 或 Windows 10/11 Electron"
    )


def test_browser_live_interview_proxy_and_permissions_are_enabled() -> None:
    caddyfile = (PROJECT_ROOT / "docker" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    nginx_config = (PROJECT_ROOT / "docker" / "nginx" / "default.conf").read_text(encoding="utf-8")
    vite_config = (PROJECT_ROOT / "web-ui" / "vite.config.js").read_text(encoding="utf-8")

    assert "microphone=(self)" in caddyfile
    assert "proxy_set_header Upgrade $http_upgrade;" in nginx_config
    assert "proxy_set_header Connection $connection_upgrade;" in nginx_config
    assert "ws: true" in vite_config


def test_desktop_launch_endpoint_starts_windows_capture_tool() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    result = SimpleNamespace(status="launching", message="面试大师正在启动")

    with (
        patch.object(live_web, "launch_windows_desktop_assistant", return_value=result) as launch,
        TestClient(app) as client,
    ):
        response = client.post("/api/career/live-interviews/desktop/launch")

    assert response.status_code == 200
    assert response.json() == {"status": "launching", "message": "面试大师正在启动"}
    launch.assert_called_once_with(api_base_url="http://testserver")


def test_desktop_launch_endpoint_returns_actionable_error() -> None:
    app = FastAPI()
    app.include_router(live_web.router)

    with (
        patch.object(
            live_web,
            "launch_windows_desktop_assistant",
            side_effect=live_web.DesktopLauncherError("采集组件尚未安装完成"),
        ),
        TestClient(app) as client,
    ):
        response = client.post("/api/career/live-interviews/desktop/launch")

    assert response.status_code == 409
    assert response.json()["detail"] == "采集组件尚未安装完成"


def test_live_interview_archive_preview_returns_only_questions() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    session_id = uuid4()
    archive_service = SimpleNamespace(
        preview=lambda **kwargs: SimpleNamespace(
            questions=("如何设计 Agent？", "如何处理重试？"),
            started_at=_record().created_at,
            ended_at=_record().created_at,
            question_count=2,
            question_preview=("如何设计 Agent？", "如何处理重试？"),
        )
    )

    with (
        patch.object(live_web, "get_live_archive_service", return_value=archive_service),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/archive/preview",
            json={"session_ids": [str(session_id)]},
        )

    assert response.status_code == 200
    assert response.json()["question_count"] == 2
    assert response.json()["question_preview"] == ["如何设计 Agent？", "如何处理重试？"]
    assert "answer" not in str(response.json()).lower()
    assert "audio" not in str(response.json()).lower()


def test_live_interview_archive_endpoint_returns_saved_experience() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    session_id = uuid4()
    experience_id = uuid4()
    now = _record().created_at
    archive_service = SimpleNamespace(
        archive=lambda **kwargs: LiveInterviewArchiveResult(
            experience=SimpleNamespace(
                id=experience_id,
                company_name="字节跳动",
                role_name="AI Agent 开发工程师",
                job_name="AI Agent 开发工程师 · 2026-08-24",
                interview_date=now.date(),
            ),
            questions=("如何设计 Agent？",),
            started_at=now,
            ended_at=now,
        )
    )

    with (
        patch.object(live_web, "get_live_archive_service", return_value=archive_service),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/archive",
            json={
                "session_ids": [str(session_id)],
                "company_name": "字节跳动",
                "role_name": "AI Agent 开发工程师",
                "interview_date": "2026-08-24",
            },
        )

    assert response.status_code == 201
    assert response.json()["experience"]["id"] == str(experience_id)
    assert response.json()["question_count"] == 1
    assert response.json()["question_preview"] == ["如何设计 Agent？"]


def test_live_interview_archive_endpoint_maps_empty_questions_to_422() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    archive_service = SimpleNamespace(archive=lambda **kwargs: (_ for _ in ()).throw(ValueError("本场未识别到可归档的问题")))

    with (
        patch.object(live_web, "get_live_archive_service", return_value=archive_service),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/archive",
            json={
                "session_ids": [str(uuid4())],
                "company_name": "示例公司",
                "role_name": "后端开发",
                "interview_date": "2026-08-24",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "本场未识别到可归档的问题"


def test_websocket_accepts_ping_and_ends_cleanly_in_local_auth_mode() -> None:
    record = _record()
    repository = FakeRepository(record)
    manager = FakeManager()
    app = FastAPI()
    app.include_router(live_web.router)

    with (
        patch.object(live_web, "_repository_for_websocket", return_value=repository),
        patch.object(live_web, "_build_live_manager", new=AsyncMock(return_value=manager)),
        TestClient(app) as client,
    ):
        with client.websocket_connect(f"/api/career/live-interviews/{record.id}/stream") as ws:
            assert ws.receive_json()["type"] == "session.ready"
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
            ws.send_json({"type": "session.end"})

    assert manager.closed


def test_websocket_optional_auth_uses_logged_in_actor(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "false")
    session = _authenticated_session()
    record = _record(
        organization_id=session.user.organization_id,
        actor_id=session.user.id,
    )
    repository = FakeRepository(record)
    manager = FakeManager()
    access_service = SimpleNamespace(resolve_session=lambda _raw_token: session)
    app = FastAPI()
    app.include_router(live_web.router)

    with (
        patch.object(live_web, "get_platform_access_service", return_value=access_service),
        patch.object(live_web, "_repository_for_websocket", return_value=repository),
        patch.object(live_web, "_build_live_manager", new=AsyncMock(return_value=manager)),
        TestClient(app) as client,
    ):
        client.cookies.set(live_web.SESSION_COOKIE_NAME, "valid-session")
        with client.websocket_connect(f"/api/career/live-interviews/{record.id}/stream") as ws:
            assert ws.receive_json()["type"] == "session.ready"
            ws.send_json({"type": "session.end"})

    assert repository.lookups[0] == (
        session.user.organization_id,
        session.user.id,
        record.id,
    )


def test_websocket_optional_auth_without_cookie_keeps_default_actor(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "false")
    websocket = SimpleNamespace(cookies={}, close=AsyncMock())

    with patch.object(
        live_web,
        "get_platform_access_service",
        side_effect=AssertionError("没有 Cookie 时不应初始化身份服务"),
    ):
        actor = asyncio.run(live_web._resolve_websocket_actor(websocket))

    assert actor.organization_id == live_web.DEFAULT_ORGANIZATION_ID
    assert actor.actor_id == live_web.DEFAULT_ACTOR_ID
    websocket.close.assert_not_awaited()


def test_websocket_optional_auth_with_invalid_cookie_keeps_default_actor(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "false")
    websocket = SimpleNamespace(
        cookies={live_web.SESSION_COOKIE_NAME: "expired-session"},
        close=AsyncMock(),
    )
    access_service = SimpleNamespace(resolve_session=lambda _raw_token: None)

    with patch.object(live_web, "get_platform_access_service", return_value=access_service):
        actor = asyncio.run(live_web._resolve_websocket_actor(websocket))

    assert actor.organization_id == live_web.DEFAULT_ORGANIZATION_ID
    assert actor.actor_id == live_web.DEFAULT_ACTOR_ID
    websocket.close.assert_not_awaited()


def test_websocket_required_auth_rejects_invalid_session(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "true")
    websocket = SimpleNamespace(
        cookies={live_web.SESSION_COOKIE_NAME: "invalid-session"},
        close=AsyncMock(),
    )
    access_service = SimpleNamespace(resolve_session=lambda _raw_token: None)

    with patch.object(live_web, "get_platform_access_service", return_value=access_service):
        actor = asyncio.run(live_web._resolve_websocket_actor(websocket))

    assert actor is None
    websocket.close.assert_awaited_once_with(code=4401, reason="请先登录后继续")


def test_websocket_rejects_missing_session_with_4404() -> None:
    record = _record()
    app = FastAPI()
    app.include_router(live_web.router)
    with (
        patch.object(live_web, "_repository_for_websocket", return_value=FakeRepository(record)),
        TestClient(app) as client,
    ):
        missing_id = uuid4()
        try:
            with client.websocket_connect(f"/api/career/live-interviews/{missing_id}/stream"):
                raise AssertionError("不存在的会话不应连接成功")
        except WebSocketDisconnect as exc:
            assert exc.code == 4404
