from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none, loads_json_or_empty


@dataclass(frozen=True)
class DraftRecordInput:
    """准备写入 draft_records 表的公众号草稿状态。"""

    layout_id: int
    status: str
    response: dict[str, Any]
    error_message: str | None
    wechat_draft_id: str | None = None
    wechat_media_id: str | None = None


@dataclass(frozen=True)
class DraftRecord:
    """draft_records 表的一条记录。"""

    id: int
    layout_id: int
    wechat_draft_id: str | None
    wechat_media_id: str | None
    status: str
    response: dict[str, Any]
    error_message: str | None
    created_at: str
    updated_at: str


class DraftRecordRepository:
    """负责公众号草稿记录的持久化读写。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def upsert(self, draft: DraftRecordInput) -> DraftRecord:
        """按 layout_id 幂等创建或更新草稿记录。"""
        self._validate(draft)
        with self.database_manager.connection() as conn:
            conn.execute(
                """
                INSERT INTO draft_records (
                    layout_id,
                    wechat_draft_id,
                    wechat_media_id,
                    status,
                    response_json,
                    error_message,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(layout_id) DO UPDATE SET
                    wechat_draft_id = COALESCE(excluded.wechat_draft_id, draft_records.wechat_draft_id),
                    wechat_media_id = COALESCE(excluded.wechat_media_id, draft_records.wechat_media_id),
                    status = excluded.status,
                    response_json = excluded.response_json,
                    error_message = excluded.error_message,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    draft.layout_id,
                    draft.wechat_draft_id,
                    draft.wechat_media_id,
                    draft.status,
                    dumps_json_or_none(draft.response),
                    draft.error_message,
                ),
            )
            row = conn.execute(
                """
                SELECT
                    id,
                    layout_id,
                    wechat_draft_id,
                    wechat_media_id,
                    status,
                    response_json,
                    error_message,
                    created_at,
                    updated_at
                FROM draft_records
                WHERE layout_id = ?
                """,
                (draft.layout_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError(f"写入 draft_records 后无法读取记录：layout_id={draft.layout_id}")
        return self._row_to_record(row)

    def get_by_layout_id(self, layout_id: int) -> DraftRecord | None:
        """按排版稿 ID 读取草稿记录。"""
        if layout_id <= 0:
            raise ValueError("layout_id 必须大于 0")

        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    layout_id,
                    wechat_draft_id,
                    wechat_media_id,
                    status,
                    response_json,
                    error_message,
                    created_at,
                    updated_at
                FROM draft_records
                WHERE layout_id = ?
                """,
                (layout_id,),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_record(row)

    def latest_ready_for_api(self) -> DraftRecord | None:
        """读取最近一条已通过前置检查、可调用微信草稿 API 的记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    layout_id,
                    wechat_draft_id,
                    wechat_media_id,
                    status,
                    response_json,
                    error_message,
                    created_at,
                    updated_at
                FROM draft_records
                WHERE status = 'ready_for_wechat_api'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None
        return self._row_to_record(row)

    def _validate(self, draft: DraftRecordInput) -> None:
        """在进入数据库前做边界检查。"""
        if draft.layout_id <= 0:
            raise ValueError("layout_id 必须大于 0")
        if draft.status.strip() not in {
            "created",
            "preflight_blocked",
            "ready_for_wechat_api",
            "draft_created",
            "failed",
        }:
            raise ValueError(f"不支持的草稿状态：{draft.status}")

    def _row_to_record(self, row: Any) -> DraftRecord:
        """把 sqlite3.Row 转成业务层使用的只读对象。"""
        return DraftRecord(
            id=int(row["id"]),
            layout_id=int(row["layout_id"]),
            wechat_draft_id=None if row["wechat_draft_id"] is None else str(row["wechat_draft_id"]),
            wechat_media_id=None if row["wechat_media_id"] is None else str(row["wechat_media_id"]),
            status=str(row["status"]),
            response=loads_json_or_empty(row["response_json"]),
            error_message=None if row["error_message"] is None else str(row["error_message"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
