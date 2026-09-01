"""通过服务器交互式终端安全创建平台的首个管理员。

生产用法（不要加 ``-T``，以便密码输入不回显）：
    docker compose --env-file .env.production -f docker-compose.production.yml \
      exec -it career-api python scripts/bootstrap_first_admin.py

该脚本不接受密码或 API Key 命令行参数，也不会输出数据库 URL、密码或摘要；
固定管理员邮箱可以作为操作提示显示。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.persistence.database import CareerDatabase
from src.platform_access.bootstrap import FirstAdminBootstrapError, bootstrap_first_admin
from src.platform_access.contracts import PLATFORM_ADMIN_EMAIL
from src.platform_access.repository import PlatformAccessRepository


def main() -> int:
    """执行状态检查或受控的首次管理员创建，并返回适合运维脚本判断的退出码。"""

    parser = argparse.ArgumentParser(description="通过交互式终端初始化平台首个管理员")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查是否仍需初始化；不读取任何交互输入，也不创建账户",
    )
    arguments = parser.parse_args()

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        print("首次管理员初始化未执行：缺少 CAREER_DATABASE_URL", file=sys.stderr)
        return 2

    database: CareerDatabase | None = None
    password = ""
    confirmed_password = ""
    try:
        database = CareerDatabase(database_url)
        repository = PlatformAccessRepository(database)
        if arguments.check:
            if repository.has_active_admin():
                print("first_admin_bootstrap_already_initialized")
            else:
                print("first_admin_bootstrap_pending")
            return 0

        _require_interactive_terminal()
        print(f"将创建唯一管理员 {PLATFORM_ADMIN_EMAIL}。密码不会显示、记录或作为命令行参数传递。")
        email = PLATFORM_ADMIN_EMAIL
        display_name = _prompt_required("管理员显示名称: ")
        password = getpass.getpass("管理员密码（至少 8 位）: ")
        confirmed_password = getpass.getpass("再次输入管理员密码: ")
        if password != confirmed_password:
            print("首次管理员初始化未执行：两次输入的密码不一致", file=sys.stderr)
            return 2
        confirmation = input("输入 INITIALIZE 确认创建首个管理员: ").strip()
        if confirmation != "INITIALIZE":
            print("首次管理员初始化已取消")
            return 0

        user = bootstrap_first_admin(
            repository,
            email=email,
            display_name=display_name,
            password=password,
        )
    except (FirstAdminBootstrapError, ValueError) as exc:
        print(f"首次管理员初始化未执行：{exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        # SQLAlchemy 错误可能包含连接串或驱动细节，不能透传到终端日志。
        print("首次管理员初始化失败：无法访问平台数据库或数据库迁移尚未完成", file=sys.stderr)
        return 2
    finally:
        password = ""
        confirmed_password = ""
        if database is not None:
            database.close()

    # 不回显邮箱、用户名或其他可识别信息。
    print(f"first_admin_bootstrap_ok role={user.role.value}")
    return 0


def _require_interactive_terminal() -> None:
    """拒绝非交互式执行，防止密码被管道、CI 日志或历史文件收集。"""

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise FirstAdminBootstrapError("必须在交互式 TTY 中执行；Docker Compose 请使用 exec -it，且不要使用 -T")


def _prompt_required(label: str) -> str:
    """读取非空文本输入，避免将身份信息放到 shell history。"""

    value = input(label).strip()
    if not value:
        raise FirstAdminBootstrapError("输入不能为空")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
