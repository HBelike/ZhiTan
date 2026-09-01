from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none


@dataclass(frozen=True)
class TaskRunRecord:
    """task_runs 表的一条任务运行记录。"""

    id: int
    run_id: str
    task_name: str
    status: str
    retry_count: int
    error_message: str | None
    metadata_json: str | None


class TaskRunRepository:
    """负责创建和更新 Task 运行状态。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def create(self, task_name: str, metadata: dict[str, Any] | None = None) -> TaskRunRecord:
        """创建一条 created 状态的任务运行记录。"""
        run_id = self._new_run_id(task_name)
        metadata_json = dumps_json_or_none(metadata)

        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO task_runs (run_id, task_name, status, metadata_json)
                VALUES (?, ?, 'created', ?)
                """,
                (run_id, task_name, metadata_json),
            )
            row = conn.execute(
                """
                SELECT id, run_id, task_name, status, retry_count, error_message, metadata_json
                FROM task_runs
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        if row is None:
            raise RuntimeError("创建 task_runs 记录后无法读取该记录")

        return self._row_to_record(row)

    def mark_running(self, task_run_id: int) -> None:
        """把任务标记为 running，并记录开始时间。"""
        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE task_runs
                SET status = 'running',
                    started_at = COALESCE(started_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (task_run_id,),
            )
            self._ensure_updated(cursor.rowcount, task_run_id)

    def mark_succeeded(
        self,
        task_run_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """把任务标记为 succeeded，并记录结束时间。"""
        metadata_json = dumps_json_or_none(metadata)
        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE task_runs
                SET status = 'succeeded',
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    error_message = NULL,
                    metadata_json = COALESCE(?, metadata_json),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (metadata_json, task_run_id),
            )
            self._ensure_updated(cursor.rowcount, task_run_id)

    def mark_failed(
        self,
        task_run_id: int,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """把任务标记为 failed，并保存错误摘要。"""
        metadata_json = dumps_json_or_none(metadata)
        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE task_runs
                SET status = 'failed',
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    error_message = ?,
                    metadata_json = COALESCE(?, metadata_json),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (error_message, metadata_json, task_run_id),
            )
            self._ensure_updated(cursor.rowcount, task_run_id)

    def get_by_run_id(self, run_id: str) -> TaskRunRecord | None:
        """按 run_id 读取任务运行记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, run_id, task_name, status, retry_count, error_message, metadata_json
                FROM task_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_record(row)

    def list_recent(self, limit: int = 10) -> list[TaskRunRecord]:
        """读取最近的任务运行记录，用于验证和未来 Web UI 展示。"""
        if limit <= 0:
            raise ValueError("limit 必须大于 0")

        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, run_id, task_name, status, retry_count, error_message, metadata_json
                FROM task_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def _new_run_id(self, task_name: str) -> str:
        """生成稳定可追踪的任务运行 ID。"""
        safe_name = task_name.strip().replace(" ", "_")
        if not safe_name:
            raise ValueError("task_name 不能为空")
        return f"{safe_name}-{uuid.uuid4().hex}"

    def _row_to_record(self, row: Any) -> TaskRunRecord:
        """把 sqlite3.Row 转成只读数据对象。"""
        return TaskRunRecord(
            id=int(row["id"]),
            run_id=str(row["run_id"]),
            task_name=str(row["task_name"]),
            status=str(row["status"]),
            retry_count=int(row["retry_count"]),
            error_message=row["error_message"],
            metadata_json=row["metadata_json"],
        )

    def _ensure_updated(self, rowcount: int, task_run_id: int) -> None:
        """确保更新语句确实命中了目标任务。"""
        if rowcount != 1:
            raise RuntimeError(f"未找到 task_runs 记录：id={task_run_id}")
