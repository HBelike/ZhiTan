from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.providers.deepseek_provider import DeepSeekMessage, DeepSeekProvider, parse_json_object_from_text
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.repositories.video_clip_plan_repository import VideoClipPlanRecord, VideoClipPlanRepository
from src.services.media_probe_service import MediaProbeService
from src.services.narration_timeline_service import NarrationTimelineClip, NarrationTimelineService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class VideoNarrationTimelineTask(BaseTask):
    """在 Seedance 片段完成后，依据真实视频时长生成可追溯的旁白与字幕时间线。"""

    task_name = "VideoNarrationTimelineTask"
    timeline_version = "video_first_spec_bound_v1"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """将视频时长、分镜事实合同和受限旁白保存为 JSON/SRT，不直接生成音频。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=context.database_manager)
        asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可生成旁白时间线的内容，请先执行 SummaryTask")

        plans = clip_plan_repository.list_by_content_id(content.id)
        if not plans:
            return self._skipped(content.id, "no_video_clip_plans")
        if any(plan.status != "completed" for plan in plans):
            return self._skipped(
                content.id,
                "video_clips_not_completed",
                incomplete_clip_indexes=[plan.clip_index for plan in plans if plan.status != "completed"],
            )

        clip_assets = asset_repository.list_by_content_id(content.id, "video_clip")
        asset_by_plan_id = self._index_clip_assets(clip_assets)
        if len(asset_by_plan_id) != len(plans):
            return self._skipped(
                content.id,
                "video_clip_assets_missing",
                expected_clip_count=len(plans),
                available_clip_count=len(asset_by_plan_id),
            )

        durations_by_plan_id = self._resolve_actual_durations(
            context=context,
            plans=plans,
            asset_by_plan_id=asset_by_plan_id,
            asset_repository=asset_repository,
            plan_repository=clip_plan_repository,
        )
        if durations_by_plan_id is None:
            return self._skipped(content.id, "video_media_probe_failed")

        source_fingerprint = self._source_fingerprint(plans, durations_by_plan_id)
        existing = self._find_reusable_timeline_asset(
            project_root=context.config.project_root,
            assets=asset_repository.list_by_content_id(content.id, "video_narration_timeline"),
            source_fingerprint=source_fingerprint,
        )
        if existing is not None:
            return self._skipped(
                content.id,
                "narration_timeline_already_exists",
                timeline_asset_id=existing.id,
                timeline_path=existing.path,
            )

        timeline_service = NarrationTimelineService(context.config.video_narration_characters_per_second)
        source_specs = timeline_service.build_source_specs(plans, durations_by_plan_id)
        fallback_used = False
        model_name = context.config.llm_model
        try:
            response = DeepSeekProvider(
                config=context.config,
                run_name="wechat.narration.timeline",
            ).chat(self._build_messages(source_specs))
            model_name = response.model
            generated_payload = parse_json_object_from_text(response.content)
            clips = timeline_service.normalize_generated_clips(source_specs, generated_payload)
        except Exception as exc:
            fallback_used = True
            clips = timeline_service.fallback_clips(source_specs)
            self.logger.warning(
                "旁白时间线模型改写失败，已使用受限本地兜底并要求人工复核：content_id=%s error=%s",
                content.id,
                str(exc)[:500],
            )

        output_directory = context.config.video_output_dir / content.week_end / "narration"
        timeline_path = output_directory / f"{content.id}_timeline.json"
        subtitle_path = output_directory / f"{content.id}_subtitles.srt"
        payload = self._build_timeline_payload(
            content_id=content.id,
            model_name=model_name,
            source_fingerprint=source_fingerprint,
            clips=clips,
            fallback_used=fallback_used,
        )
        self._write_timeline_files(timeline_path, subtitle_path, payload, clips)

        stale_assets = asset_repository.list_by_content_id(content.id, "video_narration_timeline")
        timeline_asset = asset_repository.create(
            MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="video_narration_timeline",
                provider="deepseek_narration_timeline",
                path=str(timeline_path),
                mime_type="application/json",
                status="created",
                metadata={
                    "timeline_version": self.timeline_version,
                    "source_fingerprint": source_fingerprint,
                    "subtitle_path": str(subtitle_path),
                    "total_visual_duration_seconds": payload["total_visual_duration_seconds"],
                    "fallback_used": fallback_used,
                    "model": model_name,
                    "requires_manual_review": fallback_used,
                    "clip_count": len(clips),
                },
            )
        )
        asset_repository.mark_replaced_by_ids([asset.id for asset in stale_assets])

        self.logger.info(
            "视频优先旁白时间线已生成：content_id=%s timeline_asset_id=%s clips=%s fallback=%s",
            content.id,
            timeline_asset.id,
            len(clips),
            fallback_used,
        )
        return {
            "content_id": content.id,
            "timeline_asset_id": timeline_asset.id,
            "timeline_path": str(timeline_path),
            "subtitle_path": str(subtitle_path),
            "clip_count": len(clips),
            "total_visual_duration_seconds": payload["total_visual_duration_seconds"],
            "fallback_used": fallback_used,
            "requires_manual_review": fallback_used,
            "model": model_name,
            "skipped": False,
            "network_called": not fallback_used,
        }

    def _resolve_actual_durations(
        self,
        context: TaskContext,
        plans: list[VideoClipPlanRecord],
        asset_by_plan_id: dict[int, MediaAssetRecord],
        asset_repository: MediaAssetRepository,
        plan_repository: VideoClipPlanRepository,
    ) -> dict[int, float] | None:
        """读取或补齐片段实测时长，兼容改造前已经下载到本地的历史片段。"""

        probe_service = MediaProbeService()
        durations: dict[int, float] = {}
        for plan in plans:
            asset = asset_by_plan_id.get(plan.id)
            if asset is None:
                return None
            raw_duration = asset.metadata.get("actual_duration_seconds")
            try:
                duration = float(raw_duration)
            except (TypeError, ValueError):
                duration = 0.0
            if duration > 0:
                durations[plan.id] = duration
                continue

            try:
                path = self._resolve_local_path(context.config.project_root, asset)
                probe = probe_service.probe_video(path, context.config.video_assembly_timeout_seconds)
            except (FileNotFoundError, RuntimeError, ValueError) as exc:
                self.logger.error(
                    "历史视频片段无法补齐真实时长：content_id=%s clip_index=%s error=%s",
                    plan.content_id,
                    plan.clip_index,
                    str(exc)[:500],
                )
                return None

            metadata_patch = {
                "actual_duration_seconds": probe.duration_seconds,
                "duration_delta_seconds": round(probe.duration_seconds - plan.planned_duration_seconds, 3),
                "video_width": probe.width,
                "video_height": probe.height,
                "ffmpeg_path": probe.ffmpeg_path,
                "timing_backfilled": True,
            }
            asset_repository.merge_metadata_and_status(asset.id, metadata_patch, asset.status)
            plan_repository.merge_metadata_and_status(plan.id, metadata_patch, plan.status)
            durations[plan.id] = probe.duration_seconds
        return durations

    def _build_messages(self, source_specs: list[dict[str, Any]]) -> list[DeepSeekMessage]:
        """构造只允许压缩和改写、禁止补造事实的旁白改写请求。"""

        system_prompt = (
            "你是技术科普视频的旁白编辑。只可改写输入的原始旁白，不得添加原文没有的功能、数据、结论或项目。"
            "每段必须满足字符预算，并保留 required_tokens 中的全部字符串。"
            "不解释过程，只输出合法 JSON：{\"clips\":[{\"clip_index\":1,\"narration\":\"...\",\"subtitle\":\"...\"}]}。"
        )
        user_prompt = (
            "视频已经生成，下面是每段的真实时长和事实合同。请写与镜头时长相匹配的自然中文口播；"
            "subtitle 要更短，且不要额外事实。\n"
            + json.dumps(source_specs, ensure_ascii=False)
        )
        return [
            DeepSeekMessage(role="system", content=system_prompt),
            DeepSeekMessage(role="user", content=user_prompt),
        ]

    def _build_timeline_payload(
        self,
        content_id: int,
        model_name: str,
        source_fingerprint: list[dict[str, Any]],
        clips: list[NarrationTimelineClip],
        fallback_used: bool,
    ) -> dict[str, Any]:
        """生成可用于 TTS、字幕和审核台的统一时间线文件内容。"""

        return {
            "timeline_version": self.timeline_version,
            "content_id": content_id,
            "strategy": "video_first_spec_bound",
            "model": model_name,
            "fallback_used": fallback_used,
            "source_fingerprint": source_fingerprint,
            "total_visual_duration_seconds": round(sum(item.visual_duration_seconds for item in clips), 3),
            "clips": [
                {
                    "clip_plan_id": item.clip_plan_id,
                    "clip_index": item.clip_index,
                    "repository_full_name": item.repository_full_name,
                    "start_seconds": item.start_seconds,
                    "end_seconds": item.end_seconds,
                    "visual_duration_seconds": item.visual_duration_seconds,
                    "character_budget": item.character_budget,
                    "source_narration": item.source_narration,
                    "narration": item.narration,
                    "subtitle": item.subtitle,
                    "required_tokens": item.required_tokens,
                    "fit_status": item.fit_status,
                }
                for item in clips
            ],
        }

    def _write_timeline_files(
        self,
        timeline_path: Path,
        subtitle_path: Path,
        payload: dict[str, Any],
        clips: list[NarrationTimelineClip],
    ) -> None:
        """原子性落盘旁白 JSON 与 SRT，供后续 TTS 和最终装配共享使用。"""

        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        subtitle_path.write_text(self._build_srt(clips), encoding="utf-8")

    def _build_srt(self, clips: list[NarrationTimelineClip]) -> str:
        """按视频真实时间线生成字幕；尾部空隙留给镜头转场。"""

        blocks: list[str] = []
        for index, clip in enumerate(clips, start=1):
            start = clip.start_seconds + 0.05
            end = max(start + 0.4, clip.end_seconds - 0.12)
            blocks.extend(
                [
                    str(index),
                    f"{self._format_srt_time(start)} --> {self._format_srt_time(end)}",
                    clip.subtitle,
                    "",
                ]
            )
        return "\n".join(blocks)

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """将浮点秒转换为 SRT 标准时间戳。"""

        total_milliseconds = max(0, round(seconds * 1000))
        hours, remainder = divmod(total_milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds_part, milliseconds = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds_part:02d},{milliseconds:03d}"

    def _find_reusable_timeline_asset(
        self,
        project_root: Path,
        assets: list[MediaAssetRecord],
        source_fingerprint: list[dict[str, Any]],
    ) -> MediaAssetRecord | None:
        """仅当片段集合和实测时长未变时复用时间线，防止视频重试后沿用旧旁白。"""

        for asset in reversed(assets):
            if asset.status in {"failed", "replaced"}:
                continue
            if asset.metadata.get("timeline_version") != self.timeline_version:
                continue
            if asset.metadata.get("source_fingerprint") != source_fingerprint:
                continue
            try:
                path = self._resolve_local_path(project_root, asset)
            except RuntimeError:
                continue
            if path.exists() and path.is_file():
                return asset
        return None

    @staticmethod
    def _source_fingerprint(
        plans: list[VideoClipPlanRecord],
        durations_by_plan_id: dict[int, float],
    ) -> list[dict[str, Any]]:
        """以计划 ID、原始旁白和实测时长作为时间线复用条件。"""

        return [
            {
                "clip_plan_id": plan.id,
                "clip_index": plan.clip_index,
                "actual_duration_seconds": round(durations_by_plan_id[plan.id], 3),
                "source_narration": plan.narration,
            }
            for plan in sorted(plans, key=lambda item: item.clip_index)
        ]

    @staticmethod
    def _index_clip_assets(assets: list[MediaAssetRecord]) -> dict[int, MediaAssetRecord]:
        """按 clip_plan_id 选择最新可用的视频片段资产。"""

        indexed: dict[int, MediaAssetRecord] = {}
        for asset in assets:
            if asset.status in {"failed", "replaced"}:
                continue
            try:
                plan_id = int(asset.metadata.get("clip_plan_id"))
            except (TypeError, ValueError):
                continue
            indexed[plan_id] = asset
        return indexed

    @staticmethod
    def _resolve_local_path(project_root: Path, asset: MediaAssetRecord) -> Path:
        """解析资产路径并拒绝未下载的远程 URL。"""

        if asset.path.startswith(("http://", "https://")):
            raise RuntimeError(f"视频片段尚未下载到本地：asset_id={asset.id}")
        path = Path(asset.path)
        candidate = path if path.is_absolute() else project_root / path
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise RuntimeError(f"视频片段文件不存在：asset_id={asset.id} path={resolved}")
        return resolved

    @staticmethod
    def _skipped(content_id: int, reason: str, **extra: Any) -> dict[str, Any]:
        """返回任务尚未满足前置条件时的可观测结果。"""

        return {"content_id": content_id, "skipped": True, "skip_reason": reason, "network_called": False, **extra}
