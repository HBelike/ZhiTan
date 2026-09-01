from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.providers.deepseek_provider import DeepSeekMessage, DeepSeekProvider, parse_json_object_from_text
from src.providers.doubao_tts_provider import DoubaoTtsApiError, DoubaoTtsProvider
from src.providers.narration_audio_timeline_provider import NarrationAudioTimelineProvider
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.services.media_probe_service import MediaProbeService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class SegmentedAudioTask(BaseTask):
    """按真实视频片段时长逐段生成并拟合旁白，再串成完整音轨。"""

    task_name = "SegmentedAudioTask"
    timing_mode = "video_first_spec_bound"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """使用豆包 TTS 生成分段旁白；任何段落超长都不会被硬裁切。"""

        if not context.config.audio_enabled:
            self.logger.info("audio.enabled=false，SegmentedAudioTask 跳过全部音频生成")
            return {
                "skipped": True,
                "skip_reason": "audio.enabled=false",
                "disabled_by_config": True,
                "network_called": False,
            }

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可生成分段旁白的内容，请先执行 SummaryTask")

        timeline_asset = self._find_latest_timeline_asset(
            project_root=context.config.project_root,
            assets=asset_repository.list_by_content_id(content.id, "video_narration_timeline"),
        )
        if timeline_asset is None:
            return self._skipped(content.id, "narration_timeline_missing")
        timeline = self._load_timeline(context.config.project_root, timeline_asset)
        clips = timeline.get("clips", [])
        if not isinstance(clips, list) or not clips:
            return self._skipped(content.id, "narration_timeline_invalid")

        existing_audio = self._find_reusable_audio_asset(
            project_root=context.config.project_root,
            assets=asset_repository.list_by_content_id(content.id, "audio"),
            timeline_asset_id=timeline_asset.id,
        )
        if existing_audio is not None:
            return self._skipped(
                content.id,
                "segmented_audio_already_exists",
                audio_asset_id=existing_audio.id,
                audio_path=existing_audio.path,
            )

        tts_provider = DoubaoTtsProvider(config=context.config)
        if not tts_provider.has_credentials():
            return self._skipped(
                content.id,
                "doubao_tts_credentials_missing",
                missing_requirements=[context.config.audio_api_key_env, context.config.audio_voice_type_env],
            )

        output_directory = context.config.audio_output_dir / content.week_end / "video_timeline" / str(content.id)
        audio_fitter = NarrationAudioTimelineProvider()
        media_probe = MediaProbeService()
        fitted_paths: list[Path] = []
        segment_metadata: list[dict[str, Any]] = []
        timeline_refit_count = 0

        for item in clips:
            try:
                clip_index = int(item["clip_index"])
                duration = float(item["visual_duration_seconds"])
                narration = str(item["narration"]).strip()
            except (KeyError, TypeError, ValueError) as exc:
                return self._skipped(content.id, "narration_timeline_invalid", detail=str(exc))
            if not narration:
                return self._skipped(content.id, "narration_text_missing", clip_index=clip_index)

            raw_path = output_directory / "raw" / f"{clip_index:02d}.mp3"
            fitted_path = output_directory / "segments" / f"{clip_index:02d}.m4a"
            try:
                tts_result = self._synthesize_with_retry(tts_provider, narration, raw_path)
                raw_probe = media_probe.probe_media(raw_path, context.config.audio_timeout_seconds)
                fit_result = audio_fitter.fit_to_visual_duration(
                    input_path=raw_path,
                    output_path=fitted_path,
                    source_duration_seconds=raw_probe.duration_seconds,
                    visual_duration_seconds=duration,
                    tail_padding_seconds=context.config.video_narration_tail_padding_seconds,
                    max_tts_speed_ratio=context.config.video_narration_max_tts_speed_ratio,
                    timeout_seconds=context.config.audio_timeout_seconds,
                )
            except ValueError as exc:
                if context.config.video_narration_max_refit_attempts <= 0:
                    return self._skipped(
                        content.id,
                        "segmented_audio_generation_failed",
                        clip_index=clip_index,
                        detail=self._safe_error(exc),
                        action="该段旁白超过允许的轻微加速范围，当前配置禁止自动改写；不会裁切人声。",
                    )
                refitted = self._rewrite_for_audio_fit(context, item, narration)
                if refitted is None:
                    return self._skipped(
                        content.id,
                        "segmented_audio_generation_failed",
                        clip_index=clip_index,
                        detail=self._safe_error(exc),
                        action="该段旁白超过允许的轻微加速范围，自动改写也未通过约束；不会裁切人声。",
                    )
                narration, subtitle = refitted
                item["narration"] = narration
                item["subtitle"] = subtitle
                item["fit_status"] = "tts_refitted_for_actual_visual_duration"
                raw_path = output_directory / "raw" / f"{clip_index:02d}_refit.mp3"
                try:
                    tts_result = self._synthesize_with_retry(tts_provider, narration, raw_path)
                    raw_probe = media_probe.probe_media(raw_path, context.config.audio_timeout_seconds)
                    fit_result = audio_fitter.fit_to_visual_duration(
                        input_path=raw_path,
                        output_path=fitted_path,
                        source_duration_seconds=raw_probe.duration_seconds,
                        visual_duration_seconds=duration,
                        tail_padding_seconds=context.config.video_narration_tail_padding_seconds,
                        max_tts_speed_ratio=context.config.video_narration_max_tts_speed_ratio,
                        timeout_seconds=context.config.audio_timeout_seconds,
                    )
                except (DoubaoTtsApiError, FileNotFoundError, RuntimeError, ValueError) as refit_exc:
                    return self._skipped(
                        content.id,
                        "segmented_audio_refit_failed",
                        clip_index=clip_index,
                        detail=self._safe_error(refit_exc),
                        action="该段不会被硬裁切；请在审核台修改旁白后重新生成。",
                    )
                timeline_refit_count += 1
            except (DoubaoTtsApiError, FileNotFoundError, RuntimeError, ValueError) as exc:
                return self._skipped(
                    content.id,
                    "segmented_audio_generation_failed",
                    clip_index=clip_index,
                    detail=self._safe_error(exc),
                    action="该段不会被静默裁剪；请缩短旁白后重新生成时间线。",
                )

            fitted_paths.append(fit_result.output_path)
            segment_metadata.append(
                {
                    "clip_index": clip_index,
                    "visual_duration_seconds": duration,
                    "source_audio_duration_seconds": raw_probe.duration_seconds,
                    "speed_ratio": fit_result.speed_ratio,
                    "reqid": tts_result.reqid,
                    "voice_type": tts_result.voice_type,
                    "raw_path": str(raw_path),
                    "fitted_path": str(fit_result.output_path),
                }
            )

        if timeline_refit_count:
            self._persist_refitted_timeline(context, timeline_asset, timeline)
            asset_repository.merge_metadata_and_status(
                timeline_asset.id,
                {"tts_refit_count": timeline_refit_count, "updated_for_audio_fit": True},
                timeline_asset.status,
            )

        output_path = output_directory / "voiceover_timeline.m4a"
        try:
            ffmpeg_path = audio_fitter.concat_audio(
                audio_paths=fitted_paths,
                output_path=output_path,
                timeout_seconds=context.config.audio_timeout_seconds,
            )
            combined_probe = media_probe.probe_media(output_path, context.config.audio_timeout_seconds)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return self._skipped(content.id, "segmented_audio_concat_failed", detail=self._safe_error(exc))

        expected_duration = float(timeline.get("total_visual_duration_seconds", 0))
        timing_delta = round(combined_probe.duration_seconds - expected_duration, 3)
        if abs(timing_delta) > 0.35:
            return self._skipped(
                content.id,
                "segmented_audio_duration_mismatch",
                expected_duration_seconds=expected_duration,
                actual_duration_seconds=combined_probe.duration_seconds,
                delta_seconds=timing_delta,
            )

        stale_audio_assets = asset_repository.list_by_content_id(content.id, "audio")
        audio_asset = asset_repository.create(
            MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="audio",
                provider=context.config.audio_provider,
                path=str(output_path),
                mime_type="audio/mp4",
                status="created",
                metadata={
                    "timing_mode": self.timing_mode,
                    "source_timeline_asset_id": timeline_asset.id,
                    "timeline_version": timeline.get("timeline_version"),
                    "clip_count": len(segment_metadata),
                    "expected_duration_seconds": expected_duration,
                    "actual_duration_seconds": combined_probe.duration_seconds,
                    "duration_delta_seconds": timing_delta,
                    "segments": segment_metadata,
                    "ffmpeg_path": ffmpeg_path,
                    "voice_type": context.config.audio_default_voice_type,
                },
            )
        )
        asset_repository.mark_replaced_by_ids(
            [
                asset.id
                for asset in stale_audio_assets
                if asset.id != audio_asset.id and asset.metadata.get("timing_mode") == self.timing_mode
            ]
        )

        self.logger.info(
            "分段旁白已按真实视频时长对齐：content_id=%s audio_asset_id=%s clips=%s duration=%.3fs",
            content.id,
            audio_asset.id,
            len(segment_metadata),
            combined_probe.duration_seconds,
        )
        return {
            "content_id": content.id,
            "audio_asset_id": audio_asset.id,
            "audio_path": str(output_path),
            "clip_count": len(segment_metadata),
            "expected_duration_seconds": expected_duration,
            "actual_duration_seconds": combined_probe.duration_seconds,
            "duration_delta_seconds": timing_delta,
            "timeline_refit_count": timeline_refit_count,
            "skipped": False,
            "network_called": True,
        }

    def _synthesize_with_retry(self, provider: DoubaoTtsProvider, text: str, output_path: Path):
        """仅针对暂态网络或服务错误进行有限重试，避免重复收费失控。"""

        last_error: DoubaoTtsApiError | None = None
        for attempt in range(1, 3):
            try:
                return provider.synthesize(text=text, output_path=output_path)
            except DoubaoTtsApiError as exc:
                last_error = exc
                if attempt == 2:
                    raise
                time.sleep(1.0)
        assert last_error is not None
        raise last_error

    def _rewrite_for_audio_fit(
        self,
        context: TaskContext,
        item: dict[str, Any],
        narration: str,
    ) -> tuple[str, str] | None:
        """仅在 TTS 确认超时长后，受限地压缩该段旁白并保留项目标识。"""

        required_tokens = [str(token) for token in item.get("required_tokens", []) if str(token).strip()]
        try:
            character_budget = int(item.get("character_budget", len(narration)))
        except (TypeError, ValueError):
            character_budget = len(narration)
        target_budget = max(8, min(len(narration) - 1, int(character_budget * 0.82)))
        if target_budget <= 0 or any(len(token) > target_budget for token in required_tokens):
            return None
        prompt = (
            "你是技术视频旁白编辑。只能压缩原始旁白，不得添加任何新事实、数据、功能或结论。"
            "必须保留 required_tokens 中的全部字符串。返回严格 JSON："
            '{"narration":"...","subtitle":"..."}。\n'
            + json.dumps(
                {
                    "original_narration": narration,
                    "original_subtitle": str(item.get("subtitle", "")).strip(),
                    "max_characters": target_budget,
                    "required_tokens": required_tokens,
                },
                ensure_ascii=False,
            )
        )
        try:
            response = DeepSeekProvider(
                config=context.config,
                run_name="wechat.tts.script_compaction",
            ).chat([DeepSeekMessage(role="user", content=prompt)])
            payload = parse_json_object_from_text(response.content)
            rewritten = self._normalize_compact_text(payload.get("narration"))
            subtitle = self._normalize_compact_text(payload.get("subtitle")) or rewritten
        except Exception as exc:
            self.logger.warning("TTS 超时长旁白自动改写失败：%s", self._safe_error(exc))
            return None
        if not rewritten or len(rewritten) > target_budget:
            return None
        if not all(token in rewritten for token in required_tokens):
            return None
        return rewritten, subtitle[: min(28, target_budget)] or rewritten

    @staticmethod
    def _normalize_compact_text(value: Any) -> str:
        return "".join(str(value or "").strip().split())

    def _persist_refitted_timeline(
        self,
        context: TaskContext,
        timeline_asset: MediaAssetRecord,
        timeline: dict[str, Any],
    ) -> None:
        """TTS 改写后同步更新 JSON 与 SRT，确保字幕、音频、审核台使用同一旁白版本。"""

        timeline_path = self._resolve_asset_path(context.config.project_root, timeline_asset)
        subtitle_path = Path(str(timeline_asset.metadata.get("subtitle_path", "")).strip())
        if not subtitle_path.is_absolute():
            subtitle_path = context.config.project_root / subtitle_path
        clips = timeline.get("clips", [])
        if not isinstance(clips, list):
            raise RuntimeError("旁白时间线 clips 无效，无法更新 TTS 改写结果")
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        subtitle_path.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path.write_text(self._build_srt(clips), encoding="utf-8")

    @staticmethod
    def _resolve_asset_path(project_root: Path, asset: MediaAssetRecord) -> Path:
        path = Path(asset.path)
        candidate = path if path.is_absolute() else project_root / path
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"旁白时间线文件不存在：{candidate}")
        return candidate

    def _build_srt(self, clips: list[dict[str, Any]]) -> str:
        blocks: list[str] = []
        for index, item in enumerate(clips, start=1):
            try:
                start = float(item["start_seconds"]) + 0.05
                end = max(start + 0.4, float(item["end_seconds"]) - 0.12)
            except (KeyError, TypeError, ValueError):
                continue
            blocks.extend(
                [str(index), f"{self._format_srt_time(start)} --> {self._format_srt_time(end)}", str(item.get("subtitle", "")).strip(), ""]
            )
        return "\n".join(blocks)

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        total_milliseconds = max(0, round(seconds * 1000))
        hours, remainder = divmod(total_milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds_part, milliseconds = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds_part:02d},{milliseconds:03d}"

    @staticmethod
    def _find_latest_timeline_asset(project_root: Path, assets: list[MediaAssetRecord]) -> MediaAssetRecord | None:
        """选取仍存在于本地的最新旁白时间线。"""

        for asset in reversed(assets):
            if asset.status in {"failed", "replaced"}:
                continue
            path = Path(asset.path)
            candidate = path if path.is_absolute() else project_root / path
            if candidate.exists() and candidate.is_file():
                return asset
        return None

    @staticmethod
    def _load_timeline(project_root: Path, asset: MediaAssetRecord) -> dict[str, Any]:
        """读取经过旁白任务落盘的 JSON，并拒绝非法结构。"""

        path = Path(asset.path)
        candidate = path if path.is_absolute() else project_root / path
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"旁白时间线读取失败：{candidate}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("旁白时间线 JSON 顶层必须是对象")
        return payload

    @staticmethod
    def _find_reusable_audio_asset(
        project_root: Path,
        assets: list[MediaAssetRecord],
        timeline_asset_id: int,
    ) -> MediaAssetRecord | None:
        """仅复用来源于当前旁白时间线的完整音轨，避免会话或内容串音。"""

        for asset in reversed(assets):
            if asset.status in {"failed", "replaced"}:
                continue
            if asset.metadata.get("timing_mode") != SegmentedAudioTask.timing_mode:
                continue
            if asset.metadata.get("source_timeline_asset_id") != timeline_asset_id:
                continue
            path = Path(asset.path)
            candidate = path if path.is_absolute() else project_root / path
            if candidate.exists() and candidate.is_file():
                return asset
        return None

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """截断第三方错误文本，避免响应正文或凭证写入任务结果。"""

        return (str(exc).strip() or exc.__class__.__name__)[:600]

    @staticmethod
    def _skipped(content_id: int, reason: str, **extra: Any) -> dict[str, Any]:
        """以可观测的结构返回前置条件不满足或需重新生成的状态。"""

        return {"content_id": content_id, "skipped": True, "skip_reason": reason, "network_called": False, **extra}
