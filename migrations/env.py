from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool


config = context.config


def load_project_environment() -> None:
    """在 Alembic 进程中加载求职助手的私有运行配置。

    迁移与 Web 服务必须使用同一份 PostgreSQL 连接配置；否则开发者在
    ``.env.career-assistant`` 中配置完成后，执行迁移仍会因环境变量缺失失败。
    该函数只读取本地文件，不输出连接串或任何密钥。
    """

    environment_path = Path(__file__).resolve().parents[1] / ".env.career-assistant"
    if not environment_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # requirements-career-assistant 已声明 python-dotenv；这里保留显式报错，
        # 避免迁移在未加载私有配置时误连其他数据库。
        raise RuntimeError("检测到 .env.career-assistant，但未安装 python-dotenv")
    load_dotenv(dotenv_path=environment_path, override=False)


load_project_environment()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_database_url() -> str:
    """读取求职助手专用 PostgreSQL 连接串，禁止回退到旧 SQLite 配置。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        database_url = config.get_main_option("sqlalchemy.url", "").strip()

    if not database_url:
        raise RuntimeError(
            "未配置 CAREER_DATABASE_URL；求职助手不会使用现有 SQLite 数据库。",
        )

    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("CAREER_DATABASE_URL 必须是 PostgreSQL 连接串")

    return database_url


def run_migrations_offline() -> None:
    """生成 SQL 时使用 PostgreSQL 方言，不连接真实数据库。"""

    context.configure(
        url=get_database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在独立 PostgreSQL 连接中执行迁移。"""

    engine = create_engine(
        get_database_url(),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 10},
    )

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=None,
                include_schemas=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
