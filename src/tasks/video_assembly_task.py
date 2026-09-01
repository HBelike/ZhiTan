from __future__ import annotations

from pathlib import Path
from typing import Any

from src.providers.video_assembly_provider import VideoAssemblyClip, VideoAssemblyProvider
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.repositories.video_clip_plan_repository import VideoClipPlanRecord, VideoClipPlanRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class VideoAssemblyTask(BaseTask):
    """将已下载的 Seedance 分片和统一旁白装配为公众号可上传的最终视频。"""

    task_name = "VideoAssemblyTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """仅在所有分片完成且旁白就绪后启动本地 ffmpeg 装配。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=context.database_manager)
        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可装配视频的内容，请先执行 SummaryTask")

        if not context.config.video_assembly_enabled:
            return self._skipped(content.id, "video.assembly.enabled=false")

        existing_video_assets = media_asset_repository.list_by_content_id(content.id, "video")
        existing_final_video = self._reusable_final_video(
            project_root=context.config.project_root,
            assets=existing_video_assets,
            timing_mode=context.config.video_assembly_timing_mode,
        )
        if existing_final_video is not None:
            return self._skipped(
                content.id,
                "final_video_already_exists",
                video_asset_id=existing_final_video.id,
                video_path=existing_final_video.path,
            )

        plans = clip_plan_repository.list_by_content_id(content.id)
        if not plans:
            return self._skipped(content.id, "no_video_clip_plans")
        if any(plan.status != "completed" for plan in plans):
            incomplete = [plan.clip_index for plan in plans if plan.status != "completed"]
            return self._skipped(
                content.id,
                "video_clips_not_completed",
                incomplete_clip_indexes=incomplete,
                clip_plan_count=len(plans),
            )

        clips = self._build_clip_inputs(
            project_root=context.config.project_root,
            plans=plans,
            clip_assets=media_asset_repository.list_by_content_id(content.id, "video_clip"),
        )
        if len(clips) != len(plans):
            return self._skipped(
                content.id,
                "downloaded_video_clips_missing",
                expected_clip_count=len(plans),
                available_clip_count=len(clips),
            )

        audio_asset = self._reusable_audio_asset(
            project_root=context.config.project_root,
            assets=media_asset_repository.list_by_content_id(content.id, "audio"),
            timing_mode=context.config.video_assembly_timing_mode,
        )
        if audio_asset is None and context.config.video_assembly_require_voiceover:
            return self._skipped(content.id, "voiceover_audio_missing", expected_provider=context.config.audio_provider)

        timeline_asset = self._reusable_timeline_asset(
            project_root=context.config.project_root,
            assets=media_asset_repository.list_by_content_id(content.id, "video_narration_timeline"),
        )
        if timeline_asset is None:
            return self._skipped(content.id, "narration_timeline_missing")
        subtitle_path = self._resolve_subtitle_path(
            project_root=context.config.project_root,
            timeline_asset=timeline_asset,
        )
        if subtitle_path is None and context.config.video_assembly_burn_subtitles:
            return self._skipped(content.id, "subtitle_timeline_missing")

        output_path = context.config.video_output_dir / content.week_end / f"{content.id}_seedance_final.mp4"
        provider = VideoAssemblyProvider()
        result = provider.assemble(
            clips=clips,
            audio_path=None if audio_asset is None else self._resolve_local_path(context.config.project_root, audio_asset),
            subtitle_path=subtitle_path,
            output_path=output_path,
            resolution=context.config.video_resolution,
            timeout_seconds=context.config.video_assembly_timeout_seconds,
            require_audio=context.config.video_assembly_require_voiceover,
            burn_subtitles=context.config.video_assembly_burn_subtitles,
        )
        video_asset = media_asset_repository.create(
            MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="video",
                provider="ffmpeg_seedance_assembly",
                path=str(result.output_path),
                mime_type="video/mp4",
                status="created",
                metadata={
                    "source": "seedance_clips_plus_video_first_segmented_voiceover",
                    "clip_asset_ids": self._clip_asset_ids_for_plans(
                        plans=plans,
                        clip_assets=media_asset_repository.list_by_content_id(content.id, "video_clip"),
                    ),
                    "clip_plan_ids": [plan.id for plan in plans],
                    "audio_asset_id": None if audio_asset is None else audio_asset.id,
                    "narration_timeline_asset_id": timeline_asset.id,
                    "subtitle_path": None if subtitle_path is None else str(subtitle_path),
                    "subtitles_burned": context.config.video_assembly_burn_subtitles,
                    "actual_duration_seconds": result.duration_seconds,
                    "timing_mode": context.config.video_assembly_timing_mode,
                    "clip_count": result.clip_count,
                    "ffmpeg_path": result.ffmpeg_path,
                    "size_bytes": result.size_bytes,
                    "model": context.config.video_model,
                    "resolution": context.config.video_resolution,
                    "aspect_ratio": context.config.video_aspect_ratio,
                },
            )
        )
        replaced_fallback_count = media_asset_repository.mark_replaced_by_ids(
            [
                asset.id
                for asset in existing_video_assets
                if asset.provider == "local_slideshow_video" or bool(asset.metadata.get("fallback", False))
            ]
        )
        self.logger.info(
            "Seedance 视频装配完成：content_id=%s video_asset_id=%s clips=%s path=%s",
            content.id,
            video_asset.id,
            result.clip_count,
            result.output_path,
        )
        return {
            "content_id": content.id,
            "video_asset_id": video_asset.id,
            "video_path": video_asset.path,
            "clip_count": result.clip_count,
            "duration_seconds": result.duration_seconds,
            "timing_mode": context.config.video_assembly_timing_mode,
            "audio_asset_id": None if audio_asset is None else audio_asset.id,
            "narration_timeline_asset_id": timeline_asset.id,
            "subtitle_path": None if subtitle_path is None else str(subtitle_path),
            "subtitles_burned": context.config.video_assembly_burn_subtitles,
            "size_bytes": result.size_bytes,
            "replaced_fallback_count": replaced_fallback_count,
            "skipped": False,
            "network_called": False,
        }

    def _skipped(self, content_id: int, reason: str, **extra: Any) -> dict[str, Any]:
        """以统一结构描述可恢复的装配前置条件不足。"""

        return {"content_id": content_id, "skipped": True, "skip_reason": reason, "network_called": False, **extra}

    def _build_clip_inputs(
        self,
        project_root: Path,
        plans: list[VideoClipPlanRecord],
        clip_assets: list[MediaAssetRecord],
    ) -> list[VideoAssemblyClip]:
        """按计划顺序选择可用本地片段，拒绝跨内容或缺少计划映射的资产。"""

        assets_by_plan_id: dict[int, MediaAssetRecord] = {}
        for asset in clip_assets:
            if asset.status in {"failed", "replaced"}:
                continue
            try:
                plan_id = int(asset.metadata.get("clip_plan_id"))
            except (TypeError, ValueError):
                continue
            if plan_id not in assets_by_plan_id:
                assets_by_plan_id[plan_id] = asset

        clips: list[VideoAssemblyClip] = []
        for plan in plans:
            asset = assets_by_plan_id.get(plan.id)
            if asset is None:
                continue
            try:
                input_path = self._resolve_local_path(project_root, asset)
            except RuntimeError:
                continue
            try:
                actual_duration = float(asset.metadata.get("actual_duration_seconds"))
            except (TypeError, ValueError):
                actual_duration = 0.0
            if actual_duration <= 0:
                continue
            clips.append(
                VideoAssemblyClip(
                    clip_index=plan.clip_index,
                    input_path=input_path,
                    duration_seconds=actual_duration,
                )
            )
        return clips

    def _clip_asset_ids_for_plans(
        self,
        plans: list[VideoClipPlanRecord],
        clip_assets: list[MediaAssetRecord],
    ) -> list[int]:
        """为最终资产记录可追溯的中间素材 ID。"""

        asset_id_by_plan_id: dict[int, int] = {}
        for asset in clip_assets:
            try:
                plan_id = int(asset.metadata.get("clip_plan_id"))
            except (TypeError, ValueError):
                continue
            asset_id_by_plan_id.setdefault(plan_id, asset.id)
        return [asset_id_by_plan_id[plan.id] for plan in plans if plan.id in asset_id_by_plan_id]

    def _reusable_audio_asset(
        self,
        project_root: Path,
        assets: list[MediaAssetRecord],
        timing_mode: str,
    ) -> MediaAssetRecord | None:
        """选择一条仍可在本地读取的旁白，禁止把临时远程 URL 当成装配输入。"""

        for asset in sorted(assets, key=lambda item: item.id, reverse=True):
            if asset.status in {"failed", "replaced"}:
                continue
            if timing_mode == "video_first_spec_bound" and asset.metadata.get("timing_mode") != timing_mode:
                continue
            # 本地幻灯片仅是无云端视频时的审核兜底，不能阻止真实 Seedance 分片装配。
            if asset.provider == "local_slideshow_video" or bool(asset.metadata.get("fallback", False)):
                continue
            try:
                self._resolve_local_path(project_root, asset)
            except RuntimeError:
                continue
            return asset
        return None

    def _reusable_final_video(
        self,
        project_root: Path,
        assets: list[MediaAssetRecord],
        timing_mode: str,
    ) -> MediaAssetRecord | None:
        """幂等重跑时复用已生成的最终视频，而非重复消耗本地 CPU。"""

        for asset in sorted(assets, key=lambda item: item.id, reverse=True):
            if asset.status in {"failed", "replaced"}:
                continue
            if asset.metadata.get("timing_mode") != timing_mode:
                continue
            try:
                self._resolve_local_path(project_root, asset)
            except RuntimeError:
                continue
            return asset
        return None

    def _reusable_timeline_asset(
        self,
        project_root: Path,
        assets: list[MediaAssetRecord],
    ) -> MediaAssetRecord | None:
        """获取与本次视频先行链路对应的最新旁白/字幕时间线。"""

        for asset in sorted(assets, key=lambda item: item.id, reverse=True):
            if asset.status in {"failed", "replaced"}:
                continue
            if asset.metadata.get("timeline_version") != "video_first_spec_bound_v1":
                continue
            try:
                self._resolve_local_path(project_root, asset)
            except RuntimeError:
                continue
            return asset
        return None

    def _resolve_subtitle_path(self, project_root: Path, timeline_asset: MediaAssetRecord) -> Path | None:
        """从时间线资产元数据读取 SRT 路径；文件缺失时让调用方明确跳过而非静默无字幕。"""

        raw_path = timeline_asset.metadata.get("subtitle_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        path = Path(raw_path)
        candidate = path if path.is_absolute() else project_root / path
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_file():
            return None
        return resolved

    def _resolve_local_path(self, project_root: Path, asset: MediaAssetRecord) -> Path:
        """解析并验证媒体资产指向的本地文件。"""

        if asset.path.startswith(("http://", "https://")):
            raise RuntimeError(f"媒体资产不是本地文件：asset_id={asset.id}")
        raw_path = Path(asset.path)
        candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise RuntimeError(f"媒体资产文件不存在：asset_id={asset.id} path={resolved}")
        return resolved
