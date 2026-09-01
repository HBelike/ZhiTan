from __future__ import annotations

from typing import Any

from src.providers.seedance_video_provider import SeedanceVideoApiError, SeedanceVideoProvider
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.repositories.video_clip_plan_repository import VideoClipPlanRecord, VideoClipPlanRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class SeedanceClipTask(BaseTask):
    """按 clip 计划逐段提交火山方舟 Seedance 视频生成任务。"""

    task_name = "SeedanceClipTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取 planned/failed clip 计划，提交远程 Seedance 任务并记录 task_id。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)

        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可提交 Seedance clip 的 generated_contents，请先运行 SummaryTask")

        clip_plans = clip_plan_repository.list_by_content_id(content.id)
        if not clip_plans:
            return {
                "content_id": content.id,
                "skipped": True,
                "skip_reason": "no_video_clip_plans",
                "network_called": False,
            }

        if not context.config.video_submit_enabled:
            self.logger.info(
                "视频远程提交已关闭，SeedanceClipTask 跳过：content_id=%s clip_plans=%s",
                content.id,
                len(clip_plans),
            )
            return {
                "content_id": content.id,
                "clip_plan_count": len(clip_plans),
                "submitted_clip_count": 0,
                "skipped_existing_count": 0,
                "provider": context.config.video_provider,
                "model": context.config.video_model,
                "skipped": True,
                "skip_reason": "video.submit_enabled=false",
                "network_called": False,
                "paid_generation_called": False,
            }

        provider = SeedanceVideoProvider(config=context.config)
        if not provider.has_api_key():
            if context.config.video_skip_when_api_key_missing:
                return {
                    "content_id": content.id,
                    "clip_plan_count": len(clip_plans),
                    "skipped": True,
                    "skip_reason": f"{context.config.video_api_key_env} 未配置",
                    "missing_requirements": [f"{context.config.video_api_key_env} 未配置"],
                    "network_called": False,
                }
            raise RuntimeError(f"{context.config.video_api_key_env} 未配置，无法提交 Seedance clip")

        existing_task_assets = media_asset_repository.list_by_content_id(content.id, "video_clip_task")
        existing_task_assets_by_plan_id = self._existing_task_assets_by_plan_id(existing_task_assets)

        submitted_assets: list[dict[str, Any]] = []
        skipped_existing_count = 0
        network_called = False
        for clip_plan in clip_plans:
            existing_task_asset = existing_task_assets_by_plan_id.get(clip_plan.id)
            if existing_task_asset is not None:
                skipped_existing_count += 1
                self._sync_plan_with_existing_task(
                    clip_plan_repository=clip_plan_repository,
                    clip_plan=clip_plan,
                    existing_task_asset=existing_task_asset,
                )
                continue

            if clip_plan.status not in {"planned", "failed"}:
                skipped_existing_count += 1
                continue

            image_urls: list[str] = []
            if context.config.video_reference_images_enabled:
                image_urls = self._reference_image_urls(
                    media_asset_repository=media_asset_repository,
                    reference_image_asset_ids=clip_plan.reference_image_asset_ids,
                )
            if context.config.video_reference_images_enabled and len(image_urls) < len(clip_plan.reference_image_asset_ids):
                missing_count = len(clip_plan.reference_image_asset_ids) - len(image_urls)
                return {
                    "content_id": content.id,
                    "clip_plan_count": len(clip_plans),
                    "submitted_clip_count": len(submitted_assets),
                    "skipped_existing_count": skipped_existing_count,
                    "skipped": True,
                    "skip_reason": "video_clip_reference_image_url_missing",
                    "missing_requirements": [
                        f"clip_plan_id={clip_plan.id} 缺少 {missing_count} 个可供 Seedance 读取的参考图公网 URL"
                    ],
                    "network_called": network_called,
                }

            try:
                network_called = True
                task_result = provider.create_video_task(
                    prompt=clip_plan.seedance_prompt,
                    image_urls=image_urls,
                    duration_seconds=clip_plan.planned_duration_seconds,
                )
            except SeedanceVideoApiError as exc:
                if self._is_model_not_open_error(exc):
                    return {
                        "content_id": content.id,
                        "clip_plan_count": len(clip_plans),
                        "submitted_clip_count": len(submitted_assets),
                        "skipped_existing_count": skipped_existing_count,
                        "blocked_clip_plan_id": clip_plan.id,
                        "blocked_clip_index": clip_plan.clip_index,
                        "skipped": True,
                        "skip_reason": "video_model_not_open",
                        "missing_requirements": [
                            f"火山方舟未开通视频模型：{context.config.video_model}"
                        ],
                        "network_called": True,
                    }

                clip_plan_repository.merge_metadata_and_status(
                    clip_plan_id=clip_plan.id,
                    metadata_patch={"last_submit_error": str(exc)},
                    status="failed",
                )
                raise

            task_asset = media_asset_repository.create(
                MediaAssetInput(
                    content_id=content.id,
                    repository_id=None,
                    asset_type="video_clip_task",
                    provider=context.config.video_provider,
                    path=f"{context.config.video_provider}://clip-tasks/{task_result.task_id}",
                    mime_type="application/vnd.seedance.video-clip-task",
                    status="submitted",
                    metadata={
                        "task_id": task_result.task_id,
                        "clip_plan_id": clip_plan.id,
                        "clip_index": clip_plan.clip_index,
                        "storyboard_id": clip_plan.storyboard_id,
                        "source_scene_index": clip_plan.source_scene_index,
                        "clip_title": clip_plan.clip_title,
                        "repository_full_name": clip_plan.repository_full_name,
                        "prompt": clip_plan.seedance_prompt,
                        "reference_image_asset_ids": clip_plan.reference_image_asset_ids,
                        "reference_image_urls": image_urls,
                        "generation_type": context.config.video_generation_type,
                        "reference_images_enabled": context.config.video_reference_images_enabled,
                        "planned_duration_seconds": clip_plan.planned_duration_seconds,
                        "output_start_second": clip_plan.output_start_second,
                        "output_end_second": clip_plan.output_end_second,
                        "model": context.config.video_model,
                        "resolution": context.config.video_resolution,
                        "aspect_ratio": context.config.video_aspect_ratio,
                        "watermark": context.config.video_watermark,
                        "generate_audio": context.config.video_generate_audio,
                        "raw_response": task_result.raw_response,
                    },
                )
            )
            clip_plan_repository.merge_metadata_and_status(
                clip_plan_id=clip_plan.id,
                metadata_patch={
                    "task_asset_id": task_asset.id,
                    "task_id": task_result.task_id,
                    "submitted_provider": context.config.video_provider,
                },
                status="submitted",
            )
            submitted_assets.append(
                {
                    "clip_plan_id": clip_plan.id,
                    "clip_index": clip_plan.clip_index,
                    "asset_id": task_asset.id,
                    "task_id": task_result.task_id,
                }
            )
            self.logger.info(
                "Seedance clip 任务已提交：content_id=%s clip_plan_id=%s clip_index=%s asset_id=%s task_id=%s",
                content.id,
                clip_plan.id,
                clip_plan.clip_index,
                task_asset.id,
                task_result.task_id,
            )

        return {
            "content_id": content.id,
            "clip_plan_count": len(clip_plans),
            "submitted_clip_count": len(submitted_assets),
            "skipped_existing_count": skipped_existing_count,
            "video_clip_task_asset_ids": [asset["asset_id"] for asset in submitted_assets],
            "provider": context.config.video_provider,
            "model": context.config.video_model,
            "skipped": len(submitted_assets) == 0,
            "skip_reason": "all_video_clip_tasks_already_submitted" if not submitted_assets else None,
            "network_called": network_called,
            "assets": submitted_assets,
        }

    def _existing_task_assets_by_plan_id(
        self,
        existing_task_assets: list[MediaAssetRecord],
    ) -> dict[int, MediaAssetRecord]:
        """按 clip_plan_id 索引仍然有效的远程视频任务资产。"""

        indexed_assets: dict[int, MediaAssetRecord] = {}
        for asset in sorted(existing_task_assets, key=lambda item: item.id, reverse=True):
            if asset.status in {"failed", "replaced"}:
                continue
            raw_clip_plan_id = asset.metadata.get("clip_plan_id")
            try:
                clip_plan_id = int(raw_clip_plan_id)
            except (TypeError, ValueError):
                continue
            if clip_plan_id <= 0:
                continue
            indexed_assets.setdefault(clip_plan_id, asset)
        return indexed_assets

    def _sync_plan_with_existing_task(
        self,
        clip_plan_repository: VideoClipPlanRepository,
        clip_plan: VideoClipPlanRecord,
        existing_task_asset: MediaAssetRecord,
    ) -> None:
        """已有远程任务时，把 plan 状态同步到 submitted，保证重复运行幂等。"""

        if clip_plan.status in {"submitted", "processing", "completed"}:
            return
        task_id = str(existing_task_asset.metadata.get("task_id", "")).strip()
        clip_plan_repository.merge_metadata_and_status(
            clip_plan_id=clip_plan.id,
            metadata_patch={
                "task_asset_id": existing_task_asset.id,
                "task_id": task_id,
                "synced_from_existing_task": True,
            },
            status="submitted",
        )

    def _reference_image_urls(
        self,
        media_asset_repository: MediaAssetRepository,
        reference_image_asset_ids: list[int],
    ) -> list[str]:
        """把参考图 asset_id 转成 Seedance 可读取的公网 URL。"""

        image_urls: list[str] = []
        for asset_id in reference_image_asset_ids:
            asset = media_asset_repository.get_by_id(asset_id)
            if asset is None:
                continue
            image_url = self._public_url_from_asset(asset)
            if image_url:
                image_urls.append(image_url)
        return image_urls

    def _public_url_from_asset(self, asset: MediaAssetRecord) -> str | None:
        """从媒体资产 metadata 中提取公网 URL。"""

        candidates = [
            asset.metadata.get("remote_url"),
            asset.metadata.get("source_url"),
            asset.metadata.get("url"),
            asset.path,
        ]
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            normalized = candidate.strip()
            if normalized.startswith("http://") or normalized.startswith("https://"):
                return normalized
        return None

    def _is_model_not_open_error(self, exc: SeedanceVideoApiError) -> bool:
        """识别火山方舟模型未开通错误。"""

        normalized_message = str(exc).lower()
        return "modelnotopen" in normalized_message or "has not activated the model" in normalized_message
