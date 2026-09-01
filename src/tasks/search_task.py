from __future__ import annotations

from typing import Any

from src.providers.github_client import GitHubClient
from src.repositories.repository_repository import RepositoryRepository
from src.repositories.star_snapshot_repository import StarSnapshotInput, StarSnapshotRepository
from src.repositories.weekly_ranking_repository import WeeklyRankingRepository
from src.services.weekly_ranking_service import WeeklyRankingService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class SearchTask(BaseTask):
    """搜索任务，负责拉取 GitHub 候选仓库并写入数据库。"""

    task_name = "SearchTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """调用 GitHub Search API，并 upsert 候选仓库。"""
        client = GitHubClient(config=context.config)
        repository_repository = RepositoryRepository(database_manager=context.database_manager)
        snapshot_repository = StarSnapshotRepository(database_manager=context.database_manager)
        ranking_repository = WeeklyRankingRepository(database_manager=context.database_manager)
        ranking_service = WeeklyRankingService(config=context.config)

        response = client.search_weekly_candidates()
        request = response.request

        if response.auth_fallback_used:
            self.logger.warning(
                "%s 已配置但 GitHub 返回 Bad credentials，已降级为未认证请求；请更换有效 GitHub token",
                context.config.github_token_env,
            )
        elif not response.token_configured:
            self.logger.warning(
                "%s 未配置；当前使用 GitHub 未认证请求，可能触发较低限流",
                context.config.github_token_env,
            )

        upsert_result = repository_repository.upsert_many(response.items)
        self.logger.info(
            "GitHub 候选仓库写入完成：fetched=%s inserted=%s updated=%s",
            len(response.items),
            upsert_result.inserted_count,
            upsert_result.updated_count,
        )

        github_ids = [item.github_id for item in response.items]
        repository_map = repository_repository.list_by_github_ids(github_ids)
        repositories = [repository_map[item.github_id] for item in response.items if item.github_id in repository_map]
        window = ranking_service.build_window()
        previous_snapshots = snapshot_repository.load_latest_before(
            repository_ids=[repository.id for repository in repositories],
            snapshot_date=window.snapshot_date,
        )
        snapshot_inputs = [
            StarSnapshotInput(
                repository_id=repository.id,
                snapshot_date=window.snapshot_date,
                stars=repository.stars,
            )
            for repository in repositories
        ]
        snapshot_result = snapshot_repository.upsert_many(snapshot_inputs)
        rankings = ranking_service.build_rankings(
            repositories=repositories,
            previous_snapshots=previous_snapshots,
            window=window,
        )
        ranking_count = ranking_repository.replace_for_week(
            week_end=window.week_end,
            rankings=rankings,
        )
        top_repositories = ranking_repository.list_for_week(window.week_end)
        self.logger.info(
            "周榜评分完成：week_end=%s snapshots=%s rankings=%s previous_snapshots=%s",
            window.week_end,
            snapshot_result.total_count,
            ranking_count,
            len(previous_snapshots),
        )

        return {
            "dry_run": False,
            "network_called": True,
            "token_env": context.config.github_token_env,
            "token_configured": response.token_configured,
            "token_accepted": response.token_accepted,
            "auth_fallback_used": response.auth_fallback_used,
            "method": request.method,
            "url": request.url,
            "params": request.params,
            "safe_headers": request.safe_headers(),
            "candidate_limit": context.config.github_candidate_limit,
            "timeout_seconds": request.timeout_seconds,
            "github_total_count": response.total_count,
            "github_incomplete_results": response.incomplete_results,
            "fetched_count": len(response.items),
            "inserted_count": upsert_result.inserted_count,
            "updated_count": upsert_result.updated_count,
            "repository_total_count": repository_repository.count_all(),
            "snapshot_date": window.snapshot_date,
            "week_start": window.week_start,
            "week_end": window.week_end,
            "snapshot_inserted_count": snapshot_result.inserted_count,
            "snapshot_updated_count": snapshot_result.updated_count,
            "snapshot_total_count": snapshot_repository.count_all(),
            "previous_snapshot_count": len(previous_snapshots),
            "ranking_count": ranking_count,
            "weekly_ranking_total_count": ranking_repository.count_all(),
            "top_repositories": [
                {
                    "rank": item.rank,
                    "full_name": item.full_name,
                    "current_stars": item.current_stars,
                    "star_growth": item.star_growth,
                    "growth_rate": round(item.growth_rate, 6),
                    "score": round(item.score, 4),
                    "reason": item.reason,
                }
                for item in top_repositories
            ],
            "rate_limit_remaining": response.rate_limit_remaining,
            "rate_limit_reset": response.rate_limit_reset,
        }
