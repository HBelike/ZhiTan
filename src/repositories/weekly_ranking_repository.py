from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager


@dataclass(frozen=True)
class WeeklyRankingInput:
    """准备写入 weekly_rankings 的排名数据。"""

    week_start: str
    week_end: str
    repository_id: int
    rank: int
    current_stars: int
    star_growth: int
    growth_rate: float
    score: float
    reason: str


@dataclass(frozen=True)
class WeeklyRankingRecord:
    """weekly_rankings 表的一条排名记录。"""

    repository_id: int
    rank: int
    full_name: str
    html_url: str
    description: str | None
    language: str | None
    current_stars: int
    star_growth: int
    growth_rate: float
    score: float
    reason: str


class WeeklyRankingRepository:
    """负责 weekly_rankings 表的整周排名替换和读取。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def replace_for_week(self, week_end: str, rankings: list[WeeklyRankingInput]) -> int:
        """替换某个 week_end 对应的整批周榜记录。"""
        with self.database_manager.connection() as conn:
            conn.execute(
                """
                DELETE FROM weekly_rankings
                WHERE week_end = ?
                """,
                (week_end,),
            )
            for ranking in rankings:
                conn.execute(
                    """
                    INSERT INTO weekly_rankings (
                        week_start,
                        week_end,
                        repository_id,
                        rank,
                        current_stars,
                        star_growth,
                        growth_rate,
                        score,
                        reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ranking.week_start,
                        ranking.week_end,
                        ranking.repository_id,
                        ranking.rank,
                        ranking.current_stars,
                        ranking.star_growth,
                        ranking.growth_rate,
                        ranking.score,
                        ranking.reason,
                    ),
                )
        return len(rankings)

    def list_for_week(self, week_end: str) -> list[WeeklyRankingRecord]:
        """读取某个 week_end 的周榜记录。"""
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.repository_id,
                    r.rank,
                    repo.full_name,
                    repo.html_url,
                    repo.description,
                    repo.language,
                    r.current_stars,
                    r.star_growth,
                    r.growth_rate,
                    r.score,
                    r.reason
                FROM weekly_rankings r
                INNER JOIN repositories repo
                  ON repo.id = r.repository_id
                WHERE r.week_end = ?
                ORDER BY r.rank ASC
                """,
                (week_end,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def latest_week_end(self) -> str | None:
        """读取最近一次周榜的 week_end。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT week_end
                FROM weekly_rankings
                ORDER BY week_end DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None
        return str(row["week_end"])

    def latest_snapshot_status(self) -> dict[str, Any] | None:
        """返回最近周榜快照的时间、所属周和项目数，供页面说明数据来源。"""

        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    week_start,
                    week_end,
                    MAX(created_at) AS updated_at,
                    COUNT(*) AS project_count
                FROM weekly_rankings
                WHERE week_end = (
                    SELECT MAX(week_end)
                    FROM weekly_rankings
                )
                GROUP BY week_start, week_end
                """
            ).fetchone()

        if row is None:
            return None
        return {
            "week_start": str(row["week_start"]),
            "week_end": str(row["week_end"]),
            "updated_at": str(row["updated_at"]),
            "project_count": int(row["project_count"]),
        }

    def count_all(self) -> int:
        """统计 weekly_rankings 表记录数。"""
        with self.database_manager.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM weekly_rankings").fetchone()
        return int(row["total"])

    def _row_to_record(self, row: Any) -> WeeklyRankingRecord:
        """把 sqlite3.Row 转成只读周榜对象。"""
        return WeeklyRankingRecord(
            repository_id=int(row["repository_id"]),
            rank=int(row["rank"]),
            full_name=str(row["full_name"]),
            html_url=str(row["html_url"]),
            description=row["description"],
            language=row["language"],
            current_stars=int(row["current_stars"]),
            star_growth=int(row["star_growth"]),
            growth_rate=float(row["growth_rate"]),
            score=float(row["score"]),
            reason=str(row["reason"]),
        )
