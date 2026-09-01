"""仅供隔离 CI smoke 使用的临时管理员创建入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.persistence.database import CareerDatabase
from src.platform_access.bootstrap import FirstAdminBootstrapError, bootstrap_first_admin
from src.platform_access.repository import PlatformAccessRepository
from src.platform_access.settings import load_platform_admin_email


def read_password_from_stdin() -> str:
    """从 CI 管道读取一行密码，拒绝交互终端以免误作生产入口。"""

    if sys.stdin.isatty():
        raise RuntimeError("ephemeral helper 只接受 CI stdin")
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise ValueError("测试密码不能为空")
    return password


def main() -> int:
    if os.environ.get("ZHITAN_EPHEMERAL_TEST_MODE", "").strip().casefold() != "true":
        print("拒绝执行：ZHITAN_EPHEMERAL_TEST_MODE 必须显式设为 true", file=sys.stderr)
        return 2
    database_url = os.environ.get("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        print("拒绝执行：CAREER_DATABASE_URL 未配置", file=sys.stderr)
        return 2

    database: CareerDatabase | None = None
    password = ""
    try:
        password = read_password_from_stdin()
        admin_email = load_platform_admin_email()
        database = CareerDatabase(database_url)
        repository = PlatformAccessRepository(database, admin_email=admin_email)
        bootstrap_first_admin(
            repository,
            email=admin_email,
            display_name="ZhiTan CI Administrator",
            password=password,
        )
    except (FirstAdminBootstrapError, RuntimeError, ValueError, SQLAlchemyError) as exc:
        print(f"ephemeral_admin_failed type={type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        password = ""
        if database is not None:
            database.close()

    print("ephemeral_admin_created role=admin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
