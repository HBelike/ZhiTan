from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import check_career_worker_health as worker_health


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


def test_worker_health_executes_database_probe_and_disposes_engine(monkeypatch) -> None:
    connection = _Connection()
    engine = SimpleNamespace(
        connect=lambda: connection,
        dispose=lambda: setattr(engine, "disposed", True),
        disposed=False,
    )
    monkeypatch.setattr(worker_health, "create_engine", lambda *_args, **_kwargs: engine)

    worker_health.check_worker_database("postgresql+psycopg://user:secret@database/app")

    assert connection.statements == ["SELECT 1"]
    assert engine.disposed is True


def test_worker_health_requires_database_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CAREER_DATABASE_URL", raising=False)

    assert worker_health.main() == 2
    assert "CAREER_DATABASE_URL" in capsys.readouterr().err


def test_worker_health_redacts_database_error(monkeypatch, capsys) -> None:
    database_url = "postgresql+psycopg://user:super-secret@database/app"
    monkeypatch.setenv("CAREER_DATABASE_URL", database_url)
    monkeypatch.setattr(
        worker_health,
        "check_worker_database",
        lambda _url: (_ for _ in ()).throw(RuntimeError(database_url)),
    )

    assert worker_health.main() == 1
    stderr = capsys.readouterr().err
    assert "RuntimeError" in stderr
    assert "super-secret" not in stderr
