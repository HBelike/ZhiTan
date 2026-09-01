from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.providers.seedance_video_provider import SeedanceVideoProvider
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


COMPLETED_STATUSES = {"completed", "succeeded", "success", "done", "finished"}
FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}


class VideoStatusTask(BaseTask):
    """负责轮询 Seedance 视频任务，并在完成后下载视频资产。"""

    task_name = "VideoStatusTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """查询最新 video_task 资产状态，必要时创建 video 资产。"""
        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        provider = SeedanceVideoProvider(config=context.config)

        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可查询视频状态的 generated_contents，请先运行 SummaryTask")

        existing_video_assets = media_asset_repository.list_by_content_id(content.id, "video")
        if existing_video_assets:
            self.logger.info("视频资产已存在，VideoStatusTask 跳过：content_id=%s", content.id)
            return {
                "skipped": True,
                "skip_reason": "video_asset_already_exists",
                "content_id": content.id,
                "video_asset_count": len(existing_video_assets),
                "network_called": False,
            }

        video_task_assets = media_asset_repository.list_by_content_id(content.id, "video_task")
        if not video_task_assets:
            self.logger.info("没有待查询的视频生成任务，VideoStatusTask 跳过：content_id=%s", content.id)
            return {
                "skipped": True,
                "skip_reason": "no_video_task_asset",
                "content_id": content.id,
                "video_task_asset_count": 0,
                "network_called": False,
            }

        task_asset = self._select_latest_video_task(video_task_assets)
        task_id = str(task_asset.metadata.get("task_id", "")).strip()
        if not task_id:
            raise RuntimeError(f"video_task 资产缺少 task_id：asset_id={task_asset.id}")

        if not provider.has_api_key():
            if context.config.video_skip_when_api_key_missing:
                self.logger.warning("%s 未配置，VideoStatusTask 跳过任务查询", context.config.video_api_key_env)
                return {
                    "skipped": True,
                    "skip_reason": f"{context.config.video_api_key_env} 未配置",
                    "content_id": content.id,
                    "video_task_asset_id": task_asset.id,
                    "seedance_task_id": task_id,
                    "network_called": False,
                }
            raise RuntimeError(f"{context.config.video_api_key_env} 未配置，无法查询视频任务")

        task_status = provider.get_task_status(task_id)
        normalized_status = task_status.status.strip().lower()
        metadata_patch = {
            "last_status": task_status.status,
            "last_status_raw_response": task_status.raw_response,
            "video_url": task_status.video_url,
        }

        if normalized_status in FAILED_STATUSES:
            media_asset_repository.merge_metadata_and_status(
                asset_id=task_asset.id,
                metadata_patch=metadata_patch,
                status="failed",
            )
            raise RuntimeError(f"Seedance 视频任务失败：task_id={task_id} status={task_status.status}")

        if normalized_status not in COMPLETED_STATUSES:
            updated_task = media_asset_repository.merge_metadata_and_status(
                asset_id=task_asset.id,
                metadata_patch=metadata_patch,
                status="processing",
            )
            self.logger.info(
                "Seedance 视频任务仍在处理中：content_id=%s asset_id=%s status=%s",
                content.id,
                updated_task.id,
                task_status.status,
            )
            return {
                "skipped": True,
                "skip_reason": "video_task_processing",
                "content_id": content.id,
                "video_task_asset_id": task_asset.id,
                "seedance_task_id": task_id,
                "task_status": task_status.status,
                "network_called": True,
            }

        if not task_status.video_url:
            media_asset_repository.merge_metadata_and_status(
                asset_id=task_asset.id,
                metadata_patch=metadata_patch,
                status="completed_without_url",
            )
            raise RuntimeError(f"Seedance 视频任务已完成但缺少 video_url：task_id={task_id}")

        output_path = self._build_output_path(
            output_dir=context.config.video_output_dir,
            week_end=content.week_end,
            content_id=content.id,
            task_id=task_id,
        )
        downloaded = provider.download_video(video_url=task_status.video_url, output_path=output_path)
        media_asset_repository.merge_metadata_and_status(
            asset_id=task_asset.id,
            metadata_patch=metadata_patch,
            status="completed",
        )
        video_asset = media_asset_repository.create(
            MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="video",
                provider=context.config.video_provider,
                path=str(downloaded.output_path),
                mime_type=downloaded.content_type or "video/mp4",
                status="created",
                metadata={
                    "source_task_asset_id": task_asset.id,
                    "task_id": task_id,
                    "remote_url": downloaded.source_url,
                    "size_bytes": downloaded.size_bytes,
                    "model": context.config.video_model,
                },
            )
        )
        self.logger.info(
            "Seedance 视频已下载入库：content_id=%s video_asset_id=%s path=%s",
            content.id,
            video_asset.id,
            video_asset.path,
        )
        return {
            "skipped": False,
            "skip_reason": None,
            "content_id": content.id,
            "video_task_asset_id": task_asset.id,
            "video_asset_id": video_asset.id,
            "seedance_task_id": task_id,
            "task_status": task_status.status,
            "video_path": video_asset.path,
            "size_bytes": downloaded.size_bytes,
            "network_called": True,
        }

    def _select_latest_video_task(self, video_task_assets: list[MediaAssetRecord]) -> MediaAssetRecord:
        """选择最新一条未失败的视频任务资产。"""
        sorted_assets = sorted(video_task_assets, key=lambda asset: asset.id, reverse=True)
        for asset in sorted_assets:
            if asset.status != "failed":
                return asset
        return sorted_assets[0]

    def _build_output_path(self, output_dir: Path, week_end: str, content_id: int, task_id: str) -> Path:
        """构建下载后视频文件的本地保存路径。"""
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id).strip("_") or "seedance_task"
        return output_dir / week_end / f"{content_id}_{safe_task_id}.mp4"
