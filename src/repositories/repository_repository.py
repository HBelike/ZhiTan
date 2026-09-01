from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.providers.github_client import GitHubRepositoryItem


@dataclass(frozen=True)
class RepositoryRecord:
    """repositories 表的一条仓库记录。"""

    id: int
    github_id: int
    full_name: str
    stars: int


@dataclass(frozen=True)
class RepositoryUpsertResult:
    """仓库批量 upsert 的结果摘要。"""

    inserted_count: int
    updated_count: int
    total_count: int


class RepositoryRepository:
    """负责 repositories 表的新增和更新。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def upsert_many(self, items: list[GitHubRepositoryItem]) -> RepositoryUpsertResult:
        """批量写入 GitHub 仓库基础信息。"""
        if not items:
            return RepositoryUpsertResult(
                inserted_count=0,
                updated_count=0,
                total_count=0,
            )

        github_ids = [item.github_id for item in items]
        with self.database_manager.connection() as conn:
            existing_ids = self._load_existing_github_ids(conn, github_ids)
            inserted_count = 0
            updated_count = 0

            for item in items:
                if item.github_id in existing_ids:
                    updated_count += 1
                else:
                    inserted_count += 1
                self._upsert_one(conn, item)

        return RepositoryUpsertResult(
            inserted_count=inserted_count,
            updated_count=updated_count,
            total_count=len(items),
        )

    def list_recent(self, limit: int = 10) -> list[RepositoryRecord]:
        """读取最近看到的仓库记录，用于验证和未来 Web UI 展示。"""
        if limit <= 0:
            raise ValueError("limit 必须大于 0")

        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, github_id, full_name, stars
                FROM repositories
                ORDER BY last_seen_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def list_by_github_ids(self, github_ids: list[int]) -> dict[int, RepositoryRecord]:
        """按 GitHub ID 批量读取仓库记录。"""
        if not github_ids:
            return {}

        placeholders = ",".join("?" for _ in github_ids)
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, github_id, full_name, stars
                FROM repositories
                WHERE github_id IN ({placeholders})
                """,
                github_ids,
            ).fetchall()

        records = [self._row_to_record(row) for row in rows]
        return {record.github_id: record for record in records}

    def count_all(self) -> int:
        """统计 repositories 表记录数。"""
        with self.database_manager.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM repositories").fetchone()
        return int(row["total"])

    def _load_existing_github_ids(self, conn: Any, github_ids: list[int]) -> set[int]:
        """读取已存在的 GitHub ID，用于统计 insert/update 数量。"""
        placeholders = ",".join("?" for _ in github_ids)
        rows = conn.execute(
            f"""
            SELECT github_id
            FROM repositories
            WHERE github_id IN ({placeholders})
            """,
            github_ids,
        ).fetchall()
        return {int(row["github_id"]) for row in rows}

    def _upsert_one(self, conn: Any, item: GitHubRepositoryItem) -> None:
        """写入或更新单个仓库。"""
        conn.execute(
            """
            INSERT INTO repositories (
                github_id,
                owner,
                name,
                full_name,
                html_url,
                description,
                language,
                stars,
                forks,
                open_issues,
                default_branch,
                pushed_at,
                github_updated_at,
                github_created_at,
                is_archived,
                is_fork,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(github_id) DO UPDATE SET
                owner = excluded.owner,
                name = excluded.name,
                full_name = excluded.full_name,
                html_url = excluded.html_url,
                description = excluded.description,
                language = excluded.language,
                stars = excluded.stars,
                forks = excluded.forks,
                open_issues = excluded.open_issues,
                default_branch = excluded.default_branch,
                pushed_at = excluded.pushed_at,
                github_updated_at = excluded.github_updated_at,
                github_created_at = excluded.github_created_at,
                is_archived = excluded.is_archived,
                is_fork = excluded.is_fork,
                last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                item.github_id,
                item.owner,
                item.name,
                item.full_name,
                item.html_url,
                item.description,
                item.language,
                item.stars,
                item.forks,
                item.open_issues,
                item.default_branch,
                item.pushed_at,
                item.github_updated_at,
                item.github_created_at,
                int(item.is_archived),
                int(item.is_fork),
            ),
        )

    def _row_to_record(self, row: Any) -> RepositoryRecord:
        """把 sqlite3.Row 转成只读仓库对象。"""
        return RepositoryRecord(
            id=int(row["id"]),
            github_id=int(row["github_id"]),
            full_name=str(row["full_name"]),
            stars=int(row["stars"]),
        )
