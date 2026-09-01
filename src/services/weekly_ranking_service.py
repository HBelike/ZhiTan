from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.config.config_manager import AppConfig
from src.repositories.repository_repository import RepositoryRecord
from src.repositories.star_snapshot_repository import StarSnapshotRecord
from src.repositories.weekly_ranking_repository import WeeklyRankingInput
from src.scheduler.weekly_schedule import parse_weekday


@dataclass(frozen=True)
class WeekWindow:
    """一次周榜计算使用的日期窗口。"""

    snapshot_date: str
    week_start: str
    week_end: str


class WeeklyRankingService:
    """负责计算周榜窗口和候选仓库评分。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.timezone = ZoneInfo(config.timezone_name)

    def build_window(self, now: datetime | None = None) -> WeekWindow:
        """根据当前时间计算快照日期和周榜日期窗口。"""
        current = now if now is not None else datetime.now(self.timezone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self.timezone)
        else:
            current = current.astimezone(self.timezone)

        snapshot_date = current.date()
        week_end = self._resolve_week_end(snapshot_date)
        week_start = week_end - timedelta(days=6)
        return WeekWindow(
            snapshot_date=snapshot_date.isoformat(),
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
        )

    def build_rankings(
        self,
        repositories: list[RepositoryRecord],
        previous_snapshots: dict[int, StarSnapshotRecord],
        window: WeekWindow,
    ) -> list[WeeklyRankingInput]:
        """根据当前 stars 和历史快照计算 Top N 周榜。"""
        scored = [
            self._score_repository(repository, previous_snapshots.get(repository.id), window)
            for repository in repositories
        ]
        scored.sort(key=lambda item: (item.score, item.star_growth, item.current_stars), reverse=True)

        top_rankings: list[WeeklyRankingInput] = []
        for index, ranking in enumerate(scored[: self.config.ranking_top_n], start=1):
            top_rankings.append(
                WeeklyRankingInput(
                    week_start=ranking.week_start,
                    week_end=ranking.week_end,
                    repository_id=ranking.repository_id,
                    rank=index,
                    current_stars=ranking.current_stars,
                    star_growth=ranking.star_growth,
                    growth_rate=ranking.growth_rate,
                    score=ranking.score,
                    reason=ranking.reason,
                )
            )
        return top_rankings

    def _resolve_week_end(self, snapshot_date: date) -> date:
        """把任意运行日映射到当前周榜的周五日期。"""
        target_weekday = parse_weekday(self.config.production_day)
        current_weekday = snapshot_date.weekday()

        if current_weekday <= target_weekday:
            return snapshot_date + timedelta(days=target_weekday - current_weekday)

        return snapshot_date - timedelta(days=current_weekday - target_weekday)

    def _score_repository(
        self,
        repository: RepositoryRecord,
        previous_snapshot: StarSnapshotRecord | None,
        window: WeekWindow,
    ) -> WeeklyRankingInput:
        """计算单个仓库的综合得分。"""
        if previous_snapshot is None:
            star_growth = 0
            growth_rate = 0.0
            reason = "缺少历史快照，当前 stars 作为基线，等待下一周期计算真实增长"
        else:
            star_growth = max(repository.stars - previous_snapshot.stars, 0)
            growth_rate = star_growth / max(previous_snapshot.stars, 1)
            reason = (
                f"基于历史快照 {previous_snapshot.snapshot_date} "
                f"stars={previous_snapshot.stars} 计算增长"
            )

        star_score = math.log10(repository.stars + 1) * 100
        score = (
            self.config.ranking_growth_weight * star_growth
            + self.config.ranking_growth_rate_weight * growth_rate * 10000
            + self.config.ranking_star_weight * star_score
        )

        return WeeklyRankingInput(
            week_start=window.week_start,
            week_end=window.week_end,
            repository_id=repository.id,
            rank=0,
            current_stars=repository.stars,
            star_growth=star_growth,
            growth_rate=growth_rate,
            score=score,
            reason=reason,
        )
