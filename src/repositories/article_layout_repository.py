from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none, loads_json_or_empty


@dataclass(frozen=True)
class ArticleLayoutInput:
    """准备写入 article_layouts 表的公众号排版稿。"""

    content_id: int
    title: str
    digest: str
    article_html: str
    cover_asset_id: int | None
    payload: dict[str, Any]
    status: str


@dataclass(frozen=True)
class ArticleLayoutRecord:
    """article_layouts 表的一条排版记录。"""

    id: int
    content_id: int
    title: str
    digest: str
    article_html: str
    cover_asset_id: int | None
    payload: dict[str, Any]
    status: str
    created_at: str
    updated_at: str


class ArticleLayoutRepository:
    """负责公众号排版稿的持久化读写。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def upsert(self, layout: ArticleLayoutInput) -> ArticleLayoutRecord:
        """按 content_id 幂等创建或更新一条排版稿。"""
        self._validate_layout(layout)
        with self.database_manager.connection() as conn:
            conn.execute(
                """
                INSERT INTO article_layouts (
                    content_id,
                    title,
                    digest,
                    article_html,
                    cover_asset_id,
                    payload_json,
                    status,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(content_id) DO UPDATE SET
                    title = excluded.title,
                    digest = excluded.digest,
                    article_html = excluded.article_html,
                    cover_asset_id = excluded.cover_asset_id,
                    payload_json = excluded.payload_json,
                    status = excluded.status,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    layout.content_id,
                    layout.title,
                    layout.digest,
                    layout.article_html,
                    layout.cover_asset_id,
                    dumps_json_or_none(layout.payload),
                    layout.status,
                ),
            )
            row = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    title,
                    digest,
                    article_html,
                    cover_asset_id,
                    payload_json,
                    status,
                    created_at,
                    updated_at
                FROM article_layouts
                WHERE content_id = ?
                """,
                (layout.content_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError(f"写入 article_layouts 后无法读取记录：content_id={layout.content_id}")
        return self._row_to_record(row)

    def get_by_content_id(self, content_id: int) -> ArticleLayoutRecord | None:
        """按内容 ID 读取一条排版稿。"""
        if content_id <= 0:
            raise ValueError("content_id 必须大于 0")

        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    title,
                    digest,
                    article_html,
                    cover_asset_id,
                    payload_json,
                    status,
                    created_at,
                    updated_at
                FROM article_layouts
                WHERE content_id = ?
                """,
                (content_id,),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_record(row)

    def latest_ready_for_delivery(self) -> ArticleLayoutRecord | None:
        """读取最近一条可进入公众号草稿创建阶段的排版稿。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    title,
                    digest,
                    article_html,
                    cover_asset_id,
                    payload_json,
                    status,
                    created_at,
                    updated_at
                FROM article_layouts
                WHERE status = 'ready'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None
        return self._row_to_record(row)

    def _validate_layout(self, layout: ArticleLayoutInput) -> None:
        """在进入数据库前做边界检查，避免无效排版稿污染后续发布链路。"""
        if layout.content_id <= 0:
            raise ValueError("content_id 必须大于 0")
        if not layout.title.strip():
            raise ValueError("排版稿标题不能为空")
        if not layout.article_html.strip():
            raise ValueError("排版稿 HTML 不能为空")
        if layout.cover_asset_id is not None and layout.cover_asset_id <= 0:
            raise ValueError("cover_asset_id 必须为空或大于 0")
        if layout.status.strip() not in {"created", "ready", "failed"}:
            raise ValueError(f"不支持的排版稿状态：{layout.status}")

    def _row_to_record(self, row: Any) -> ArticleLayoutRecord:
        """把 sqlite3.Row 转成业务层使用的只读对象。"""
        return ArticleLayoutRecord(
            id=int(row["id"]),
            content_id=int(row["content_id"]),
            title=str(row["title"]),
            digest=str(row["digest"] or ""),
            article_html=str(row["article_html"]),
            cover_asset_id=None if row["cover_asset_id"] is None else int(row["cover_asset_id"]),
            payload=loads_json_or_empty(row["payload_json"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
