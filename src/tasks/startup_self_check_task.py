from __future__ import annotations

from typing import Any

from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class StartupSelfCheckTask(BaseTask):
    """启动自检任务，用于验证数据库和任务状态记录链路。"""

    task_name = "StartupSelfCheckTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """检查数据库基础表和 schema 版本是否可读取。"""
        with context.database_manager.connection() as conn:
            table_count_row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchone()
            migration_row = conn.execute(
                """
                SELECT version, name
                FROM schema_migrations
                ORDER BY version DESC
                LIMIT 1
                """
            ).fetchone()

        table_count = int(table_count_row["total"])
        if table_count <= 0:
            raise RuntimeError("数据库表数量异常：未读取到任何表")

        if migration_row is None:
            raise RuntimeError("数据库迁移记录不存在")

        return {
            "checked_tables": table_count,
            "schema_version": int(migration_row["version"]),
            "schema_name": str(migration_row["name"]),
        }
