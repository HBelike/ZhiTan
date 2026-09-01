from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web import readiness


def test_readiness_report_is_ready_only_when_every_check_passes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        readiness,
        "_check_database_and_migrations",
        lambda **_kwargs: (
            readiness.ReadinessCheck("database", True),
            readiness.ReadinessCheck("migrations", True),
        ),
    )
    monkeypatch.setattr(
        readiness,
        "_check_writable_path",
        lambda _path: readiness.ReadinessCheck("application_data", True),
    )

    report = readiness.check_application_readiness(
        database_url="postgresql+psycopg://user:password@database/app",
        alembic_config_path=tmp_path / "alembic.ini",
        writable_paths=(tmp_path / "data",),
    )

    assert report.ready is True
    assert all(check.ok for check in report.checks)


def test_readiness_report_preserves_failed_checks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        readiness,
        "_check_database_and_migrations",
        lambda **_kwargs: (
            readiness.ReadinessCheck("database", False, "数据库连接失败"),
            readiness.ReadinessCheck("migrations", False, "无法验证迁移版本"),
        ),
    )
    monkeypatch.setattr(
        readiness,
        "_check_writable_path",
        lambda _path: readiness.ReadinessCheck(
            "application_data",
            False,
            "持久目录不可写",
        ),
    )

    report = readiness.check_application_readiness(
        database_url="postgresql+psycopg://user:password@database/app",
        alembic_config_path=tmp_path / "alembic.ini",
        writable_paths=(tmp_path / "data",),
    )

    assert report.ready is False
    assert [check.name for check in report.checks] == [
        "database",
        "migrations",
        "application_data",
    ]


def test_ready_route_is_public_and_returns_503_when_dependencies_fail(monkeypatch, tmp_path: Path) -> None:
    app = FastAPI()
    report = readiness.ReadinessReport(
        ready=False,
        checks=(readiness.ReadinessCheck("database", False, "数据库连接失败"),),
    )
    monkeypatch.setattr(readiness, "check_application_readiness", lambda **_kwargs: report)
    readiness.install_readiness_api(app, tmp_path)

    response = TestClient(app).get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "checks": [
            {"name": "database", "ok": False, "detail": "数据库连接失败"},
        ],
    }
