from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager


@dataclass(frozen=True)
class StarSnapshotInput:
    """准备写入 star_snapshots 的快照数据。"""

    repository_id: int
    snapshot_date: str
    stars: int


@dataclass(frozen=True)
class StarSnapshotRecord:
    """star_snapshots 表的一条快照记录。"""

    id: int
    repository_id: int
    snapshot_date: str
    stars: int


@dataclass(frozen=True)
class StarSnapshotWriteResult:
    """star_snapshots 批量 upsert 的结果摘要。"""

    inserted_count: int
    updated_count: int
    total_count: int


class StarSnapshotRepository:
    """负责 star_snapshots 表的快照写入和历史读取。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def upsert_many(self, snapshots: list[StarSnapshotInput]) -> StarSnapshotWriteResult:
        """批量写入某一天的 star 快照。"""
        if not snapshots:
            return StarSnapshotWriteResult(
                inserted_count=0,
                updated_count=0,
                total_count=0,
            )

        existing_keys = [(snapshot.repository_id, snapshot.snapshot_date) for snapshot in snapshots]
        with self.database_manager.connection() as conn:
            existing = self._load_existing_keys(conn, existing_keys)
            inserted_count = 0
            updated_count = 0

            for snapshot in snapshots:
                key = (snapshot.repository_id, snapshot.snapshot_date)
                if key in existing:
                    updated_count += 1
                else:
                    inserted_count += 1
                self._upsert_one(conn, snapshot)

        return StarSnapshotWriteResult(
            inserted_count=inserted_count,
            updated_count=updated_count,
            total_count=len(snapshots),
        )

    def load_latest_before(
        self,
        repository_ids: list[int],
        snapshot_date: str,
    ) -> dict[int, StarSnapshotRecord]:
        """读取每个仓库在 snapshot_date 之前的最近一次快照。"""
        if not repository_ids:
            return {}

        placeholders = ",".join("?" for _ in repository_ids)
        params: list[Any] = [*repository_ids, snapshot_date]
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT s.id, s.repository_id, s.snapshot_date, s.stars
                FROM star_snapshots s
                INNER JOIN (
                    SELECT repository_id, MAX(snapshot_date) AS snapshot_date
                    FROM star_snapshots
                    WHERE repository_id IN ({placeholders})
                      AND snapshot_date < ?
                    GROUP BY repository_id
                ) latest
                  ON latest.repository_id = s.repository_id
                 AND latest.snapshot_date = s.snapshot_date
                """,
                params,
            ).fetchall()

        records = [self._row_to_record(row) for row in rows]
        return {record.repository_id: record for record in records}

    def count_all(self) -> int:
        """统计 star_snapshots 表记录数。"""
        with self.database_manager.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM star_snapshots").fetchone()
        return int(row["total"])

    def _load_existing_keys(
        self,
        conn: Any,
        keys: list[tuple[int, str]],
    ) -> set[tuple[int, str]]:
        """读取已存在的快照键，用于统计 insert/update 数量。"""
        existing: set[tuple[int, str]] = set()
        for repository_id, snapshot_date in keys:
            row = conn.execute(
                """
                SELECT repository_id, snapshot_date
                FROM star_snapshots
                WHERE repository_id = ?
                  AND snapshot_date = ?
                """,
                (repository_id, snapshot_date),
            ).fetchone()
            if row is not None:
                existing.add((int(row["repository_id"]), str(row["snapshot_date"])))
        return existing

    def _upsert_one(self, conn: Any, snapshot: StarSnapshotInput) -> None:
        """写入或更新单个 star 快照。"""
        conn.execute(
            """
            INSERT INTO star_snapshots (
                repository_id,
                snapshot_date,
                stars,
                captured_at
            )
            VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(repository_id, snapshot_date) DO UPDATE SET
                stars = excluded.stars,
                captured_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                snapshot.repository_id,
                snapshot.snapshot_date,
                snapshot.stars,
            ),
        )

    def _row_to_record(self, row: Any) -> StarSnapshotRecord:
        """把 sqlite3.Row 转成只读快照对象。"""
        return StarSnapshotRecord(
            id=int(row["id"]),
            repository_id=int(row["repository_id"]),
            snapshot_date=str(row["snapshot_date"]),
            stars=int(row["stars"]),
        )
