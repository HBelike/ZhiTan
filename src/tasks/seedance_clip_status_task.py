from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.providers.seedance_video_provider import SeedanceVideoApiError, SeedanceVideoProvider
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.repositories.video_clip_plan_repository import VideoClipPlanRecord, VideoClipPlanRepository
from src.services.media_probe_service import MediaProbeService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext
from src.tasks.video_status_task import COMPLETED_STATUSES, FAILED_STATUSES


class SeedanceClipStatusTask(BaseTask):
    """轮询每个 Seedance 分片任务，并将已完成片段下载为本地素材。"""

    task_name = "SeedanceClipStatusTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """同步七段生成任务状态；单段失败不会阻断其他已完成片段的回收。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可查询 Seedance 分片状态的内容，请先执行 SummaryTask")

        clip_plans = clip_plan_repository.list_by_content_id(content.id)
        if not clip_plans:
            return self._skipped(content.id, "no_video_clip_plans")

        task_assets = media_asset_repository.list_by_content_id(content.id, "video_clip_task")
        if not task_assets:
            return self._skipped(content.id, "no_video_clip_task_assets", clip_plan_count=len(clip_plans))

        provider = SeedanceVideoProvider(config=context.config)
        media_probe_service = MediaProbeService()
        if not provider.has_api_key():
            if context.config.video_skip_when_api_key_missing:
                return self._skipped(
                    content.id,
                    "video_api_key_missing",
                    clip_plan_count=len(clip_plans),
                    missing_requirements=[f"{context.config.video_api_key_env} 未配置"],
                )
            raise RuntimeError(f"{context.config.video_api_key_env} 未配置，无法轮询 Seedance 分片")

        plans_by_id = {plan.id: plan for plan in clip_plans}
        existing_clip_assets_by_plan_id = self._existing_clip_assets_by_plan_id(
            project_root=context.config.project_root,
            assets=media_asset_repository.list_by_content_id(content.id, "video_clip"),
        )
        processed_count = 0
        completed_count = 0
        processing_count = 0
        failed_count = 0
        downloaded_assets: list[dict[str, Any]] = []
        warnings: list[str] = []

        for task_asset in task_assets:
            if task_asset.status in {"failed", "replaced"}:
                continue
            plan = self._plan_for_task_asset(task_asset, plans_by_id)
            if plan is None:
                warnings.append(f"task_asset_id={task_asset.id} 缺少有效 clip_plan_id")
                continue
            if plan.id in existing_clip_assets_by_plan_id:
                completed_count += 1
                self._mark_plan_completed_if_needed(clip_plan_repository, plan, existing_clip_assets_by_plan_id[plan.id])
                continue

            task_id = str(task_asset.metadata.get("task_id", "")).strip()
            if not task_id:
                failed_count += 1
                self._mark_failed(
                    media_asset_repository,
                    clip_plan_repository,
                    task_asset,
                    plan,
                    "video_clip_task 缺少 task_id",
                )
                continue

            processed_count += 1
            try:
                task_status = provider.get_task_status(task_id)
            except SeedanceVideoApiError as exc:
                processing_count += 1
                self._mark_processing(
                    media_asset_repository,
                    clip_plan_repository,
                    task_asset,
                    plan,
                    {"last_status_query_error": self._safe_error(exc)},
                )
                warnings.append(f"clip_index={plan.clip_index} 状态查询暂时失败，将在下次任务重试")
                continue

            normalized_status = task_status.status.strip().lower()
            status_metadata = {
                "last_status": task_status.status,
                "last_status_raw_response": task_status.raw_response,
                "video_url": task_status.video_url,
            }
            if normalized_status in FAILED_STATUSES:
                failed_count += 1
                self._mark_failed(
                    media_asset_repository,
                    clip_plan_repository,
                    task_asset,
                    plan,
                    f"Seedance 任务失败：task_id={task_id} status={task_status.status}",
                    status_metadata,
                )
                continue

            if normalized_status not in COMPLETED_STATUSES or not task_status.video_url:
                processing_count += 1
                self._mark_processing(media_asset_repository, clip_plan_repository, task_asset, plan, status_metadata)
                continue

            output_path = self._build_output_path(
                output_dir=context.config.video_output_dir,
                week_end=content.week_end,
                content_id=content.id,
                clip_index=plan.clip_index,
                task_id=task_id,
            )
            try:
                downloaded = provider.download_video(task_status.video_url, output_path)
            except SeedanceVideoApiError as exc:
                processing_count += 1
                self._mark_processing(
                    media_asset_repository,
                    clip_plan_repository,
                    task_asset,
                    plan,
                    {**status_metadata, "last_download_error": self._safe_error(exc)},
                )
                warnings.append(f"clip_index={plan.clip_index} 下载失败，将在下次任务重试")
                continue

            try:
                probe_result = media_probe_service.probe_video(
                    downloaded.output_path,
                    timeout_seconds=context.config.video_assembly_timeout_seconds,
                )
            except (FileNotFoundError, RuntimeError, ValueError) as exc:
                failed_count += 1
                self._mark_failed(
                    media_asset_repository,
                    clip_plan_repository,
                    task_asset,
                    plan,
                    f"视频片段下载完成但媒体探测失败：{self._safe_error(exc)}",
                    {
                        **status_metadata,
                        "downloaded_path": str(downloaded.output_path),
                        "media_probe_error": self._safe_error(exc),
                    },
                )
                warnings.append(f"clip_index={plan.clip_index} 媒体探测失败，已阻止进入旁白与合成阶段")
                continue

            media_timing_metadata = {
                "actual_duration_seconds": probe_result.duration_seconds,
                "duration_delta_seconds": round(probe_result.duration_seconds - plan.planned_duration_seconds, 3),
                "video_width": probe_result.width,
                "video_height": probe_result.height,
                "ffmpeg_path": probe_result.ffmpeg_path,
            }

            media_asset_repository.merge_metadata_and_status(
                asset_id=task_asset.id,
                status="completed",
                metadata_patch={
                    **status_metadata,
                    "downloaded_path": str(downloaded.output_path),
                    **media_timing_metadata,
                },
            )
            clip_asset = media_asset_repository.create(
                MediaAssetInput(
                    content_id=content.id,
                    repository_id=None,
                    asset_type="video_clip",
                    provider=context.config.video_provider,
                    path=str(downloaded.output_path),
                    mime_type=downloaded.content_type or "video/mp4",
                    status="created",
                    metadata={
                        "source_task_asset_id": task_asset.id,
                        "task_id": task_id,
                        "clip_plan_id": plan.id,
                        "clip_index": plan.clip_index,
                        "planned_duration_seconds": plan.planned_duration_seconds,
                        "remote_url": downloaded.source_url,
                        "size_bytes": downloaded.size_bytes,
                        "model": context.config.video_model,
                        "resolution": context.config.video_resolution,
                        "aspect_ratio": context.config.video_aspect_ratio,
                        **media_timing_metadata,
                    },
                )
            )
            clip_plan_repository.merge_metadata_and_status(
                clip_plan_id=plan.id,
                status="completed",
                metadata_patch={
                    "task_asset_id": task_asset.id,
                    "task_id": task_id,
                    "video_clip_asset_id": clip_asset.id,
                    "downloaded_path": str(downloaded.output_path),
                    **media_timing_metadata,
                },
            )
            completed_count += 1
            downloaded_assets.append(
                {
                    "clip_index": plan.clip_index,
                    "asset_id": clip_asset.id,
                    "path": clip_asset.path,
                    "actual_duration_seconds": probe_result.duration_seconds,
                }
            )
            self.logger.info(
                "Seedance 分片已下载：content_id=%s clip_index=%s asset_id=%s",
                content.id,
                plan.clip_index,
                clip_asset.id,
            )

        all_completed = len(existing_clip_assets_by_plan_id) + len(downloaded_assets) >= len(clip_plans)
        return {
            "content_id": content.id,
            "clip_plan_count": len(clip_plans),
            "task_asset_count": len(task_assets),
            "processed_count": processed_count,
            "completed_count": completed_count,
            "processing_count": processing_count,
            "failed_count": failed_count,
            "downloaded_assets": downloaded_assets,
            "all_completed": all_completed,
            "skipped": processed_count == 0 and not downloaded_assets,
            "skip_reason": None if all_completed else "video_clips_processing",
            "warnings": warnings,
            "network_called": processed_count > 0,
        }

    def _skipped(self, content_id: int, reason: str, **extra: Any) -> dict[str, Any]:
        """构造统一的可观测跳过结果。"""

        return {"content_id": content_id, "skipped": True, "skip_reason": reason, "network_called": False, **extra}

    def _existing_clip_assets_by_plan_id(
        self,
        project_root: Path,
        assets: list[MediaAssetRecord],
    ) -> dict[int, MediaAssetRecord]:
        """索引本地仍可使用的已下载分片，保证任务可幂等重跑。"""

        indexed: dict[int, MediaAssetRecord] = {}
        for asset in assets:
            if asset.status in {"failed", "replaced"}:
                continue
            try:
                plan_id = int(asset.metadata.get("clip_plan_id"))
            except (TypeError, ValueError):
                continue
            raw_path = Path(asset.path)
            candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
            if candidate.exists() and candidate.is_file():
                indexed[plan_id] = asset
        return indexed

    def _plan_for_task_asset(
        self,
        task_asset: MediaAssetRecord,
        plans_by_id: dict[int, VideoClipPlanRecord],
    ) -> VideoClipPlanRecord | None:
        """从任务资产元数据恢复其对应的分片计划。"""

        try:
            plan_id = int(task_asset.metadata.get("clip_plan_id"))
        except (TypeError, ValueError):
            return None
        return plans_by_id.get(plan_id)

    def _mark_plan_completed_if_needed(
        self,
        repository: VideoClipPlanRepository,
        plan: VideoClipPlanRecord,
        asset: MediaAssetRecord,
    ) -> None:
        """历史运行已下载片段时，修复计划状态而不重新访问外部 API。"""

        if plan.status == "completed":
            return
        repository.merge_metadata_and_status(
            clip_plan_id=plan.id,
            status="completed",
            metadata_patch={"video_clip_asset_id": asset.id, "recovered_from_asset": True},
        )

    def _mark_processing(
        self,
        asset_repository: MediaAssetRepository,
        plan_repository: VideoClipPlanRepository,
        task_asset: MediaAssetRecord,
        plan: VideoClipPlanRecord,
        metadata: dict[str, Any],
    ) -> None:
        """更新可重试的处理中状态。"""

        asset_repository.merge_metadata_and_status(task_asset.id, metadata, "processing")
        plan_repository.merge_metadata_and_status(plan.id, metadata, "processing")

    def _mark_failed(
        self,
        asset_repository: MediaAssetRepository,
        plan_repository: VideoClipPlanRepository,
        task_asset: MediaAssetRecord,
        plan: VideoClipPlanRecord,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """记录单段永久失败，留给 CatTask 与人工审核做重新生成决策。"""

        patch = {"last_error": error, **(metadata or {})}
        asset_repository.merge_metadata_and_status(task_asset.id, patch, "failed")
        plan_repository.merge_metadata_and_status(plan.id, patch, "failed")
        self.logger.error("Seedance 分片失败：content_id=%s clip_index=%s error=%s", plan.content_id, plan.clip_index, error)

    def _build_output_path(
        self,
        output_dir: Path,
        week_end: str,
        content_id: int,
        clip_index: int,
        task_id: str,
    ) -> Path:
        """用内容、片段和任务 ID 生成稳定且安全的下载位置。"""

        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id).strip("_") or "seedance_task"
        return output_dir / week_end / "clips" / f"{content_id}_{clip_index:02d}_{safe_task_id}.mp4"

    def _safe_error(self, exc: Exception) -> str:
        """限制错误文本长度，避免把异常响应或凭证片段写入数据库。"""

        message = str(exc).strip() or exc.__class__.__name__
        return message[:600]
