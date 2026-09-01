from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    """单个就绪条件的脱敏结果。"""

    name: str
    ok: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """应用依赖的聚合就绪状态。"""

    ready: bool
    checks: tuple[ReadinessCheck, ...]


def _check_database_and_migrations(
    *,
    database_url: str,
    alembic_config_path: Path,
) -> tuple[ReadinessCheck, ReadinessCheck]:
    if not database_url:
        return (
            ReadinessCheck("database", False, "CAREER_DATABASE_URL 未配置"),
            ReadinessCheck("migrations", False, "数据库未连接，无法验证迁移版本"),
        )

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            database_check = ReadinessCheck("database", True)

            alembic_config = Config(str(alembic_config_path))
            alembic_config.set_main_option("sqlalchemy.url", database_url)
            expected_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
            if current_heads != expected_heads:
                return (
                    database_check,
                    ReadinessCheck("migrations", False, "数据库迁移未处于 Alembic head"),
                )
            return database_check, ReadinessCheck("migrations", True)
    except Exception as exc:
        return (
            ReadinessCheck("database", False, f"数据库检查失败：{type(exc).__name__}"),
            ReadinessCheck("migrations", False, "数据库未就绪，无法验证迁移版本"),
        )
    finally:
        engine.dispose()


def _check_writable_path(path: Path) -> ReadinessCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".zhitan-ready-", dir=path):
            pass
    except Exception as exc:
        return ReadinessCheck(
            "application_data",
            False,
            f"持久目录不可写：{type(exc).__name__}",
        )
    return ReadinessCheck("application_data", True)


def check_application_readiness(
    *,
    database_url: str,
    alembic_config_path: Path,
    writable_paths: Sequence[Path],
) -> ReadinessReport:
    """验证数据库、迁移版本和关键持久目录，不泄露运行配置。"""

    checks = [
        *_check_database_and_migrations(
            database_url=database_url,
            alembic_config_path=alembic_config_path,
        ),
        *(_check_writable_path(path) for path in writable_paths),
    ]
    return ReadinessReport(
        ready=all(check.ok for check in checks),
        checks=tuple(checks),
    )


def install_readiness_api(app: FastAPI, project_root: Path) -> None:
    """注册不需要登录的依赖就绪探针。"""

    @app.get("/api/ready")
    def ready() -> JSONResponse:
        application_data_dir = Path(
            os.environ.get("CAREER_APPLICATION_DATA_DIR", str(project_root / "data")),
        )
        report = check_application_readiness(
            database_url=os.environ.get("CAREER_DATABASE_URL", "").strip(),
            alembic_config_path=project_root / "alembic.ini",
            writable_paths=(application_data_dir,),
        )
        return JSONResponse(
            content=asdict(report),
            status_code=200 if report.ready else 503,
        )
