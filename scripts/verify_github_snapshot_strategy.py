from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.application import Application
from src.config.config_manager import AppConfig
from src.database.database_manager import DatabaseManager
from src.repositories.weekly_ranking_repository import WeeklyRankingRepository
from src.tasks.task_result import TaskResult


def _build_database(project_root: Path) -> DatabaseManager:
    config = AppConfig(
        project_root=project_root,
        config_path=project_root / "config.yaml",
        raw={
            "app": {"name": "github-snapshot-verifier"},
            "database": {"path": "data/app.db", "timeout_seconds": 5},
        },
    )
    database_manager = DatabaseManager(config=config)
    database_manager.initialize()
    return database_manager


def _seed_weekly_snapshot(database_manager: DatabaseManager) -> None:
    with database_manager.connection() as conn:
        conn.execute(
            """
            INSERT INTO repositories (
                github_id, owner, name, full_name, html_url, stars
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1001, "example", "agent-kit", "example/agent-kit", "https://github.com/example/agent-kit", 1234),
        )
        repository_id = int(
            conn.execute(
                "SELECT id FROM repositories WHERE full_name = ?",
                ("example/agent-kit",),
            ).fetchone()["id"]
        )
        conn.execute(
            """
            INSERT INTO weekly_rankings (
                week_start, week_end, repository_id, rank,
                current_stars, star_growth, growth_rate, score, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-08-10", "2026-08-14", repository_id, 1, 1234, 88, 0.0768, 98.5, "测试快照"),
        )


def _record_pipeline_tasks(application: Application, method_name: str) -> list[str]:
    executed: list[str] = []
    application.logger = logging.getLogger("github-snapshot-verifier")

    def run_task(task_class: type[object]) -> TaskResult:
        executed.append(task_class.__name__)
        return TaskResult(task_name=task_class.__name__, run_id=f"verify-{len(executed)}")

    application._run_task = run_task  # type: ignore[method-assign]
    getattr(application, method_name)()
    return executed


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="github-snapshot-") as temp_dir:
        project_root = Path(temp_dir)
        database_manager = _build_database(project_root)
        _seed_weekly_snapshot(database_manager)

        status = WeeklyRankingRepository(database_manager).latest_snapshot_status()
        assert status is not None
        assert status["week_start"] == "2026-08-10"
        assert status["week_end"] == "2026-08-14"
        assert status["project_count"] == 1
        assert status["updated_at"]

    application = Application(Path.cwd())
    manual_tasks = _record_pipeline_tasks(application, "_run_once_pipeline_unlocked")
    assert manual_tasks[0] == "SummaryTask"
    assert "SearchTask" not in manual_tasks

    weekly_tasks = _record_pipeline_tasks(application, "_run_weekly_content_production_job_unlocked")
    assert weekly_tasks[0] == "SearchTask"

    print("github_snapshot_strategy_ok")


if __name__ == "__main__":
    main()
