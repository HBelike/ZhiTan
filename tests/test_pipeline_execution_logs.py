"""管理员手动工作流实时日志契约。"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform_access import web as platform_web
from src.platform_access.contracts import PlatformRole, PlatformUser
from src.platform_access.pipeline_logs import PipelineExecutionLogHandler
from src.platform_access.web import _pipeline_event_stream, _pipeline_sse_event


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "versions" / "20260830_31_pipeline_execution_events.py"


def _record(*, message: str, level: int = logging.INFO, thread: int | None = None, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="src.tasks.base_task.SummaryTask",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    if thread is not None:
        record.thread = thread
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_migration_creates_ordered_pipeline_event_store() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision = "20260830_31"' in source
    assert 'down_revision = "20260828_30"' in source
    assert "CREATE TABLE career_assistant.pipeline_execution_events" in source
    assert "execution_request_id UUID NOT NULL" in source
    assert "REFERENCES career_assistant.pipeline_execution_requests(id) ON DELETE CASCADE" in source
    assert "CREATE INDEX ix_pipeline_execution_events_request_cursor" in source


def test_log_handler_captures_only_its_pipeline_thread_and_maps_task_fields() -> None:
    appended: list[dict[str, object]] = []
    handler = PipelineExecutionLogHandler(
        append_event=lambda **payload: appended.append(payload),
        thread_id=120,
    )

    handler.handle(_record(message="其他线程", thread=121))
    handler.handle(
        _record(
            message="任务开始",
            thread=120,
            pipeline_event_type="task_started",
            pipeline_task_name="SummaryTask",
            pipeline_task_run_id="summary-1",
        )
    )

    assert appended == [
        {
            "event_type": "task_started",
            "level": "INFO",
            "message": "任务开始",
            "task_name": "SummaryTask",
            "task_run_id": "summary-1",
        }
    ]


def test_log_handler_ignores_debug_and_survives_persistence_failure() -> None:
    attempted: list[str] = []

    def fail_once(**payload: object) -> None:
        attempted.append(str(payload["message"]))
        raise RuntimeError("数据库短暂失败")

    handler = PipelineExecutionLogHandler(append_event=fail_once, thread_id=8)

    handler.handle(_record(message="debug", level=logging.DEBUG, thread=8))
    handler.handle(_record(message="业务日志", thread=8))

    assert attempted == ["业务日志"]


def test_log_handler_derives_task_name_for_regular_task_business_logs() -> None:
    appended: list[dict[str, object]] = []
    handler = PipelineExecutionLogHandler(
        append_event=lambda **payload: appended.append(payload),
        thread_id=18,
    )

    handler.handle(_record(message="文章摘要生成完成", thread=18))

    assert appended[0]["event_type"] == "log"
    assert appended[0]["task_name"] == "SummaryTask"


def test_pipeline_sse_event_contains_cursor_type_and_json_payload() -> None:
    event = {
        "id": 9,
        "event_type": "task_succeeded",
        "level": "INFO",
        "message": "任务成功",
    }

    payload = _pipeline_sse_event(event)

    assert payload.startswith("id: 9\nevent: task_succeeded\n")
    assert '"message": "任务成功"' in payload
    assert payload.endswith("\n\n")


def test_pipeline_event_stream_emits_incremental_events_then_stops_on_terminal_run() -> None:
    run_id = uuid4()
    user = SimpleNamespace(id=uuid4())

    class FakeService:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def list_manual_pipeline_events(self, _user: object, _run_id: str, *, after_id: int, limit: int) -> dict[str, object]:
            self.calls.append(after_id)
            if after_id == 0:
                return {
                    "item": {"id": str(run_id), "status": "running"},
                    "events": [
                        {
                            "id": 3,
                            "event_type": "run_started",
                            "level": "INFO",
                            "message": "工作流开始",
                        }
                    ],
                }
            return {
                "item": {"id": str(run_id), "status": "succeeded"},
                "events": [],
            }

    service = FakeService()

    async def collect() -> list[str]:
        return [
            item
            async for item in _pipeline_event_stream(
                service,
                user,
                run_id,
                after_id=0,
                poll_seconds=0,
            )
        ]

    chunks = asyncio.run(collect())

    assert service.calls == [0, 3]
    assert len(chunks) == 1
    assert "event: run_started" in chunks[0]


def test_admin_pipeline_log_routes_return_history_and_terminal_sse(monkeypatch) -> None:
    now = datetime.now(UTC)
    run_id = uuid4()
    admin = PlatformUser(
        id=uuid4(),
        organization_id=uuid4(),
        username="pipeline-admin",
        display_name="工作流管理员",
        email="admin@example.com",
        email_verified_at=now,
        role=PlatformRole.ADMIN,
        is_active=True,
        created_at=now,
    )
    event = {
        "id": 5,
        "event_type": "run_succeeded",
        "level": "INFO",
        "task_name": None,
        "task_run_id": None,
        "message": "工作流完成",
        "created_at": now.isoformat(),
    }

    class FakeService:
        def get_manual_pipeline_request(self, _user: object, _request_id: str) -> dict[str, object]:
            return {"id": str(run_id), "status": "succeeded"}

        def list_manual_pipeline_events(self, _user: object, _request_id: str, *, after_id: int, limit: int) -> dict[str, object]:
            return {
                "item": {"id": str(run_id), "status": "succeeded"},
                "events": [event] if after_id < 5 else [],
            }

    service = FakeService()
    monkeypatch.setattr(platform_web, "get_platform_access_service", lambda _request: service)
    app = FastAPI()
    app.include_router(platform_web.router)
    app.dependency_overrides[platform_web.require_admin] = lambda: admin
    client = TestClient(app)

    history = client.get(f"/api/admin/pipeline-runs/{run_id}/logs?after_id=0")
    stream = client.get(f"/api/admin/pipeline-runs/{run_id}/logs/stream?after_id=0")

    assert history.status_code == 200
    assert history.json()["events"][0]["id"] == 5
    assert stream.status_code == 200
    assert "event: run_succeeded" in stream.text
    assert "id: 5" in stream.text


def test_pipeline_log_history_rejects_negative_cursor(monkeypatch) -> None:
    now = datetime.now(UTC)
    admin = PlatformUser(
        id=uuid4(),
        organization_id=uuid4(),
        username="pipeline-admin",
        display_name="工作流管理员",
        email=None,
        email_verified_at=None,
        role=PlatformRole.ADMIN,
        is_active=True,
        created_at=now,
    )
    app = FastAPI()
    app.include_router(platform_web.router)
    app.dependency_overrides[platform_web.require_admin] = lambda: admin
    client = TestClient(app)

    response = client.get(f"/api/admin/pipeline-runs/{uuid4()}/logs?after_id=-1")

    assert response.status_code == 422
    assert response.json()["detail"] == "after_id 不能为负数"
