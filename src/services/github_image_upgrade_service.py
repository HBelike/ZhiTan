from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config.config_manager import AppConfig
from src.database.database_manager import DatabaseManager
from src.providers.github_repository_asset_provider import GitHubRepositoryAssetProvider
from src.repositories.generated_content_repository import GeneratedContentForImage, GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository


@dataclass(frozen=True)
class GitHubImageUpgradeResult:
    """手动升级 GitHub 仓库图后的汇总结果。"""

    content_id: int
    week_end: str
    prompt_count: int
    created_count: int
    skipped_existing_count: int
    not_found_count: int
    failed_count: int
    replaced_count: int
    created_assets: list[dict[str, Any]]
    skipped_repositories: list[str]
    not_found_repositories: list[str]
    failed_repositories: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        """转换成可写入 task_runs 和 API 响应的字典。"""

        return {
            "content_id": self.content_id,
            "week_end": self.week_end,
            "prompt_count": self.prompt_count,
            "created_count": self.created_count,
            "skipped_existing_count": self.skipped_existing_count,
            "not_found_count": self.not_found_count,
            "failed_count": self.failed_count,
            "replaced_count": self.replaced_count,
            "created_assets": self.created_assets,
            "skipped_repositories": self.skipped_repositories,
            "not_found_repositories": self.not_found_repositories,
            "failed_repositories": self.failed_repositories,
            "provider": "github_repository_asset",
            "paid_generation_called": False,
            "old_assets_deleted": False,
        }


class GitHubImageUpgradeService:
    """为指定内容手动补充 GitHub 仓库图。

    这个服务只新增更优图片，不删除、不覆盖旧媒体资产。
    ArticleLayoutService 会在同一项目多张图时优先选择 provider=github_repository_asset 的图片。
    """

    def __init__(
        self,
        config: AppConfig,
        database_manager: DatabaseManager,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.database_manager = database_manager
        self.logger = logger or logging.getLogger(__name__)

    def upgrade_content_images(self, content_id: int, force: bool = False) -> GitHubImageUpgradeResult:
        """对指定内容的动态 N 个项目尝试新增 GitHub 仓库图。

        `force=False` 时，如果某个项目已经有可用的 GitHub 仓库图，会跳过这个项目；
        `force=True` 时仍然会再尝试新增一张，但依旧不会删除旧图。
        """

        if content_id <= 0:
            raise ValueError("content_id 必须大于 0")

        content_repository = GeneratedContentRepository(database_manager=self.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=self.database_manager)
        content = content_repository.get_for_image_generation(content_id=content_id)
        if content is None:
            raise RuntimeError(f"内容不存在或没有图片 prompt：content_id={content_id}")
        if not content.image_prompts:
            raise RuntimeError(f"内容没有可用 image_prompts：content_id={content_id}")

        existing_assets = media_asset_repository.list_by_content_id(content.id, "image")
        existing_github_assets = self._existing_github_assets_by_repository(existing_assets=existing_assets)
        provider = GitHubRepositoryAssetProvider(config=self.config, logger=self.logger)

        created_assets: list[dict[str, Any]] = []
        skipped_repositories: list[str] = []
        not_found_repositories: list[str] = []
        failed_repositories: list[dict[str, str]] = []

        for item in content.image_prompts:
            repository_full_name = str(item.get("repository_full_name", "")).strip()
            prompt = str(item.get("prompt", "")).strip()
            if not repository_full_name:
                continue

            if not force and repository_full_name in existing_github_assets:
                skipped_repositories.append(repository_full_name)
                continue

            index = self._prompt_index(content.image_prompts, item)
            output_path = self._build_output_path(
                output_dir=self.config.image_output_dir,
                content=content,
                index=index,
                repository_full_name=repository_full_name,
            )

            try:
                github_result = provider.find_and_download_image(
                    repository_full_name=repository_full_name,
                    output_path=output_path,
                )
            except Exception as exc:
                self.logger.warning(
                    "手动升级 GitHub 仓库图失败：content_id=%s repository=%s error=%s",
                    content.id,
                    repository_full_name,
                    exc,
                )
                failed_repositories.append(
                    {
                        "repository_full_name": repository_full_name,
                        "error": str(exc),
                    }
                )
                continue

            if github_result is None:
                not_found_repositories.append(repository_full_name)
                continue

            asset = media_asset_repository.create(
                MediaAssetInput(
                    content_id=content.id,
                    repository_id=None,
                    asset_type="image",
                    provider="github_repository_asset",
                    path=str(github_result.output_path),
                    mime_type="image/png",
                    status="created",
                    metadata={
                        "repository_full_name": repository_full_name,
                        "prompt": prompt,
                        "prompt_index": index,
                        "source_url": github_result.source_url,
                        "source_kind": github_result.source_kind,
                        "width": github_result.width,
                        "height": github_result.height,
                        "manual_upgrade": True,
                        **github_result.metadata,
                    },
                )
            )
            created_assets.append(
                {
                    "asset_id": asset.id,
                    "repository_full_name": repository_full_name,
                    "path": asset.path,
                    "provider": asset.provider,
                    "source_url": github_result.source_url,
                    "source_kind": github_result.source_kind,
                    "width": github_result.width,
                    "height": github_result.height,
                }
            )

        replaced_count = self._mark_replaced_for_created_repositories(
            media_asset_repository=media_asset_repository,
            existing_assets=existing_assets,
            created_assets=created_assets,
        )
        return GitHubImageUpgradeResult(
            content_id=content.id,
            week_end=content.week_end,
            prompt_count=len(content.image_prompts),
            created_count=len(created_assets),
            skipped_existing_count=len(skipped_repositories),
            not_found_count=len(not_found_repositories),
            failed_count=len(failed_repositories),
            replaced_count=replaced_count,
            created_assets=created_assets,
            skipped_repositories=skipped_repositories,
            not_found_repositories=not_found_repositories,
            failed_repositories=failed_repositories,
        )

    def _mark_replaced_for_created_repositories(
        self,
        media_asset_repository: MediaAssetRepository,
        existing_assets: list[MediaAssetRecord],
        created_assets: list[dict[str, Any]],
    ) -> int:
        """手动升级成功后，把同项目旧图片标记为 replaced。"""

        refreshed_repositories = {
            str(asset.get("repository_full_name", "")).strip()
            for asset in created_assets
            if str(asset.get("repository_full_name", "")).strip()
        }
        if not refreshed_repositories:
            return 0

        old_asset_ids = [
            asset.id
            for asset in existing_assets
            if str(asset.metadata.get("repository_full_name", "")).strip() in refreshed_repositories
        ]
        return media_asset_repository.mark_replaced_by_ids(old_asset_ids)

    def _existing_github_assets_by_repository(
        self,
        existing_assets: list[MediaAssetRecord],
    ) -> dict[str, MediaAssetRecord]:
        """找到已经存在且仍可读取的 GitHub 仓库图。"""

        reusable_assets: dict[str, MediaAssetRecord] = {}
        for asset in existing_assets:
            if asset.provider != "github_repository_asset":
                continue
            repository_full_name = str(asset.metadata.get("repository_full_name", "")).strip()
            if not repository_full_name:
                continue
            if not self._asset_is_reusable(asset):
                continue
            reusable_assets[repository_full_name] = asset
        return reusable_assets

    def _asset_is_reusable(self, asset: MediaAssetRecord) -> bool:
        """判断已有 GitHub 仓库图是否仍可用。"""

        if asset.path.startswith("http://") or asset.path.startswith("https://"):
            return True

        raw_path = Path(asset.path)
        candidate = raw_path if raw_path.is_absolute() else self.config.project_root / raw_path
        try:
            return candidate.exists() and candidate.is_file()
        except OSError:
            return False

    def _prompt_index(self, prompts: list[dict[str, Any]], target: dict[str, Any]) -> int:
        """返回项目在图片 prompt 列表中的 1-based 序号。"""

        target_repository = str(target.get("repository_full_name", "")).strip()
        for index, item in enumerate(prompts, start=1):
            if str(item.get("repository_full_name", "")).strip() == target_repository:
                return index
        return 1

    def _build_output_path(
        self,
        output_dir: Path,
        content: GeneratedContentForImage,
        index: int,
        repository_full_name: str,
    ) -> Path:
        """为手动升级图片生成独立路径，避免覆盖旧图片。"""

        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", repository_full_name).strip("_")
        if not safe_name:
            safe_name = "repository"
        return output_dir / content.week_end / "github_repository_asset" / f"{index:02d}_{safe_name}.png"
