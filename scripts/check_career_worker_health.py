"""验证求职 Agent Worker 依赖的 PostgreSQL 是否可用。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, text


def check_worker_database(database_url: str) -> None:
    """执行最小数据库探针，并确保连接池立即释放。"""

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def main() -> int:
    database_url = os.environ.get("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        print("CAREER_DATABASE_URL 未配置", file=sys.stderr)
        return 2
    try:
        check_worker_database(database_url)
    except Exception as exc:
        print(f"Worker 数据库检查失败：{type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
