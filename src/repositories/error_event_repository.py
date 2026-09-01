from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none, loads_json_or_empty


@dataclass(frozen=True)
class ErrorEventRecord:
    """error_events 表的一条异常事件记录。"""

    id: int
    task_run_id: int | None
    task_name: str | None
    severity: str
    message: str
    error_type: str | None
    analysis: str | None
    metadata: dict[str, Any]
    created_at: str | None


class ErrorEventRepository:
    """负责记录和读取 Task 失败事件，供 CatTask 分析。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def create(
        self,
        severity: str,
        message: str,
        task_run_id: int | None = None,
        task_name: str | None = None,
        error_type: str | None = None,
        stack_trace: str | None = None,
        analysis: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ErrorEventRecord:
        """创建一条异常事件记录。"""
        if not severity.strip():
            raise ValueError("severity 不能为空")
        if not message.strip():
            raise ValueError("message 不能为空")

        metadata_json = dumps_json_or_none(metadata)
        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO error_events (
                    task_run_id,
                    task_name,
                    severity,
                    message,
                    error_type,
                    stack_trace,
                    analysis,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_run_id,
                    task_name,
                    severity,
                    message,
                    error_type,
                    stack_trace,
                    analysis,
                    metadata_json,
                ),
            )
            row = conn.execute(
                """
                SELECT
                    id,
                    task_run_id,
                    task_name,
                    severity,
                    message,
                    error_type,
                    analysis,
                    metadata_json,
                    created_at
                FROM error_events
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        if row is None:
            raise RuntimeError("创建 error_events 记录后无法读取该记录")

        return self._row_to_record(row)

    def count_by_task_run_id(self, task_run_id: int) -> int:
        """统计某次任务运行关联的异常数量。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM error_events
                WHERE task_run_id = ?
                """,
                (task_run_id,),
            ).fetchone()

        return int(row["total"])

    def list_recent(self, limit: int = 10) -> list[ErrorEventRecord]:
        """读取最近的异常事件。"""
        if limit <= 0:
            raise ValueError("limit 必须大于 0")

        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    task_run_id,
                    task_name,
                    severity,
                    message,
                    error_type,
                    analysis,
                    metadata_json,
                    created_at
                FROM error_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: Any) -> ErrorEventRecord:
        """把 sqlite3.Row 转成只读数据对象。"""
        return ErrorEventRecord(
            id=int(row["id"]),
            task_run_id=None if row["task_run_id"] is None else int(row["task_run_id"]),
            task_name=None if row["task_name"] is None else str(row["task_name"]),
            severity=str(row["severity"]),
            message=str(row["message"]),
            error_type=None if row["error_type"] is None else str(row["error_type"]),
            analysis=None if row["analysis"] is None else str(row["analysis"]),
            metadata=loads_json_or_empty(row["metadata_json"]),
            created_at=None if row["created_at"] is None else str(row["created_at"]),
        )
