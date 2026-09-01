from __future__ import annotations

from dataclasses import dataclass

from src.database.database_manager import DatabaseManager


VALID_CONTENT_APPROVAL_DECISIONS = {"approved", "rejected", "regenerate_requested"}


@dataclass(frozen=True)
class ContentApprovalInput:
    """准备写入 content_approval_records 的人工审核决策。"""

    content_id: int
    decision: str
    operator: str | None
    comment: str | None


@dataclass(frozen=True)
class ContentApprovalRecord:
    """content_approval_records 表的一条记录。"""

    id: int
    content_id: int
    decision: str
    operator: str | None
    comment: str | None
    created_at: str


class ContentApprovalRepository:
    """负责保存和读取内容级人工审核记录。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def create(self, approval: ContentApprovalInput) -> ContentApprovalRecord:
        """插入一条内容审核记录。"""
        normalized_decision = approval.decision.strip()
        if normalized_decision not in VALID_CONTENT_APPROVAL_DECISIONS:
            supported = ", ".join(sorted(VALID_CONTENT_APPROVAL_DECISIONS))
            raise ValueError(f"不支持的内容审核决策：{normalized_decision}，支持：{supported}")

        operator = self._normalize_optional_text(approval.operator)
        comment = self._normalize_optional_text(approval.comment)

        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO content_approval_records (
                    content_id,
                    decision,
                    operator,
                    comment
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    approval.content_id,
                    normalized_decision,
                    operator,
                    comment,
                ),
            )
            row = conn.execute(
                """
                SELECT id, content_id, decision, operator, comment, created_at
                FROM content_approval_records
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        if row is None:
            raise RuntimeError("创建 content_approval_records 记录后无法读取该记录")

        return self._row_to_record(row)

    def latest_for_content(self, content_id: int) -> ContentApprovalRecord | None:
        """读取指定内容的最新审核记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, content_id, decision, operator, comment, created_at
                FROM content_approval_records
                WHERE content_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (content_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    def latest_regeneration_feedback_for_content(self, content_id: int) -> ContentApprovalRecord | None:
        """读取指定内容最近一次带备注的重生成反馈。

        SummaryTask 会把这段反馈追加到下一次生成 prompt 中，让人工审核意见
        真正影响再生成结果，而不是只停留在审核记录里。
        """
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, content_id, decision, operator, comment, created_at
                FROM content_approval_records
                WHERE content_id = ?
                  AND decision IN ('rejected', 'regenerate_requested')
                  AND comment IS NOT NULL
                  AND TRIM(comment) != ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (content_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    def _row_to_record(self, row: object) -> ContentApprovalRecord:
        """把 sqlite3.Row 转成审核记录对象。"""
        return ContentApprovalRecord(
            id=int(row["id"]),
            content_id=int(row["content_id"]),
            decision=str(row["decision"]),
            operator=None if row["operator"] is None else str(row["operator"]),
            comment=None if row["comment"] is None else str(row["comment"]),
            created_at=str(row["created_at"]),
        )

    def _normalize_optional_text(self, value: str | None) -> str | None:
        """规范化可选文本字段。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
