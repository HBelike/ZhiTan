"""求职助手的独立 PostgreSQL 连接管理。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine


class CareerDatabase:
    """封装线程安全的 SQLAlchemy Engine 与事务边界。

    该类只接受 PostgreSQL 连接串，显式阻止接入项目旧有的 SQLite 数据库。Engine
    可以由 FastAPI 的多个请求线程共享；每个仓储操作都会从连接池取得独立连接，并由
    ``transaction`` 在成功时提交、发生异常时回滚。
    """

    def __init__(self, database_url: str, connect_timeout_seconds: int = 10) -> None:
        """创建数据库连接池，但不在构造时发起网络连接。"""

        normalized_url = database_url.strip()
        if not normalized_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("求职助手仅支持 PostgreSQL 连接串")

        if connect_timeout_seconds <= 0:
            raise ValueError("数据库连接超时必须大于零")

        self._engine: Engine = create_engine(
            normalized_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": connect_timeout_seconds},
        )

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """提供一个自动提交或回滚的数据库事务。

        调用者只应在 ``with`` 块中执行仓储 SQL。块内发生任何异常时，SQLAlchemy 会先
        回滚事务再重新抛出异常；因此不会留下半写入的历史记录。
        """

        with self._engine.begin() as connection:
            yield connection

    def close(self) -> None:
        """释放连接池；供应用关闭钩子和验证脚本调用。"""

        self._engine.dispose()
