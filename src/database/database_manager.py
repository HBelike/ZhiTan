from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from src.config.config_manager import AppConfig
from src.database.schema import SCHEMA_NAME, SCHEMA_STATEMENTS, SCHEMA_VERSION


class DatabaseManager:
    """负责 SQLite 数据库连接、初始化和 schema 创建。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.database_path = config.database_path
        self.logger = logging.getLogger(__name__)

    def initialize(self) -> None:
        """创建数据库目录和基础表，确保程序启动后具备持久化能力。"""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        with self.connection() as conn:
            self._apply_schema(conn)

        self.logger.info("数据库初始化完成：%s", self.database_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """创建一个启用外键和超时设置的 SQLite 连接。"""
        conn = sqlite3.connect(
            self.database_path,
            timeout=self.config.database_timeout_seconds,
        )
        conn.row_factory = sqlite3.Row

        try:
            self._configure_connection(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _configure_connection(self, conn: sqlite3.Connection) -> None:
        """配置连接级参数，让 SQLite 更适合后台服务使用。"""
        busy_timeout_ms = int(self.config.database_timeout_seconds * 1000)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        """在事务中创建所有基础表和索引。"""
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)

        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, name)
            VALUES (?, ?)
            """,
            (SCHEMA_VERSION, SCHEMA_NAME),
        )
