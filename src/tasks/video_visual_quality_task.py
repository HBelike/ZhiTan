from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.providers.qwen_video_quality_provider import QwenVideoQualityApiError, QwenVideoQualityProvider
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.repositories.video_clip_plan_repository import VideoClipPlanRecord, VideoClipPlanRepository
from src.services.video_frame_sampler import VideoFrameSampler
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class VideoVisualQualityTask(BaseTask):
    """对已完成的视频片段抽帧质检，并仅重生成不合格片段。"""

    task_name = "VideoVisualQualityTask"
    report_version = "qwen_vl_clip_quality_v1"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """在旁白生成前完成视觉质检，避免把视觉错误带入最终音画合成。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        plan_repository = VideoClipPlanRepository(database_manager=context.database_manager)
        asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可进行视频视觉质检的内容，请先执行 SummaryTask")
        if not context.config.video_quality_assurance_enabled:
            return self._skipped(content.id, "video.quality_assurance.enabled=false")

        plans = plan_repository.list_by_content_id(content.id)
        if not plans:
            return self._skipped(content.id, "no_video_clip_plans")
        if any(plan.status != "completed" for plan in plans):
            return self._skipped(
                content.id,
                "video_clips_not_completed",
                incomplete_clip_indexes=[plan.clip_index for plan in plans if plan.status != "completed"],
            )

        clip_assets = self._index_current_clip_assets(asset_repository.list_by_content_id(content.id, "video_clip"))
        if len(clip_assets) != len(plans):
            return self._skipped(
                content.id,
                "video_clip_assets_missing",
                expected_clip_count=len(plans),
                available_clip_count=len(clip_assets),
            )

        provider = QwenVideoQualityProvider(config=context.config)
        if not provider.has_api_key():
            return {
                "content_id": content.id,
                "skipped": True,
                "skip_reason": "video_quality_api_key_missing_requires_manual_review",
                "requires_manual_review": True,
                "missing_requirements": [context.config.video_quality_assurance_api_key_env],
                "network_called": False,
            }

        existing_reports = self._index_reusable_reports(
            asset_repository.list_by_content_id(content.id, "video_quality_report")
        )
        sampler = VideoFrameSampler()
        assessed_count = 0
        passed_count = 0
        retry_submitted_count = 0
        manual_review_count = 0
        reports: list[dict[str, Any]] = []
        warnings: list[str] = []

        for plan in plans:
            clip_asset = clip_assets[plan.id]
            existing_report = existing_reports.get(clip_asset.id)
            if existing_report is not None:
                outcome = str(existing_report.metadata.get("outcome", "manual_review"))
                if outcome == "pass":
                    passed_count += 1
                elif outcome == "retry_submitted":
                    retry_submitted_count += 1
                else:
                    manual_review_count += 1
                reports.append(
                    {
                        "clip_index": plan.clip_index,
                        "clip_asset_id": clip_asset.id,
                        "report_asset_id": existing_report.id,
                        "outcome": outcome,
                        "reused": True,
                    }
                )
                continue

            try:
                duration = self._read_duration(clip_asset)
                clip_path = self._resolve_local_path(context.config.project_root, clip_asset)
                frame_directory = (
                    context.config.video_output_dir
                    / content.week_end
                    / "quality"
                    / str(content.id)
                    / f"clip_{plan.clip_index:02d}_asset_{clip_asset.id}"
                )
                frame_paths = sampler.sample(
                    input_path=clip_path,
                    duration_seconds=duration,
                    output_directory=frame_directory,
                    frame_count=context.config.video_quality_assurance_sample_frame_count,
                    timeout_seconds=context.config.video_quality_assurance_timeout_seconds,
                )
                assessment = provider.assess(
                    frame_paths=frame_paths,
                    expected_contract=self._build_expected_contract(plan),
                )
                assessed_count += 1
            except (FileNotFoundError, RuntimeError, ValueError, QwenVideoQualityApiError) as exc:
                manual_review_count += 1
                warnings.append(f"clip_index={plan.clip_index} 视觉质检未完成：{self._safe_error(exc)}")
                report = self._create_report(
                    asset_repository=asset_repository,
                    content_id=content.id,
                    clip_asset=clip_asset,
                    plan=plan,
                    report_path=self._report_path(context, content.week_end, content.id, plan.clip_index, clip_asset.id),
                    payload={
                        "report_version": self.report_version,
                        "outcome": "manual_review",
                        "reason": "quality_assessment_unavailable",
                        "error": self._safe_error(exc),
                    },
                    status="manual_review",
                )
                reports.append(
                    {
                        "clip_index": plan.clip_index,
                        "clip_asset_id": clip_asset.id,
                        "report_asset_id": report.id,
                        "outcome": "manual_review",
                    }
                )
                continue

            outcome = self._handle_assessment(
                context=context,
                content_id=content.id,
                week_end=content.week_end,
                plan=plan,
                clip_asset=clip_asset,
                assessment=assessment,
                asset_repository=asset_repository,
                plan_repository=plan_repository,
            )
            if outcome["outcome"] == "pass":
                passed_count += 1
            elif outcome["outcome"] == "retry_submitted":
                retry_submitted_count += 1
            else:
                manual_review_count += 1
            reports.append({"clip_index": plan.clip_index, "clip_asset_id": clip_asset.id, **outcome})

        return {
            "content_id": content.id,
            "assessed_count": assessed_count,
            "passed_count": passed_count,
            "retry_submitted_count": retry_submitted_count,
            "manual_review_count": manual_review_count,
            "reports": reports,
            "warnings": warnings,
            "requires_manual_review": manual_review_count > 0,
            "regeneration_required": retry_submitted_count > 0,
            "skipped": False,
            "network_called": assessed_count > 0,
        }

    def _handle_assessment(
        self,
        context: TaskContext,
        content_id: int,
        week_end: str,
        plan: VideoClipPlanRecord,
        clip_asset: MediaAssetRecord,
        assessment: Any,
        asset_repository: MediaAssetRepository,
        plan_repository: VideoClipPlanRepository,
    ) -> dict[str, Any]:
        """保存质检结论；低分片段最多重生成一次，避免无界付费重试。"""

        is_passed = assessment.verdict == "pass" and assessment.score >= context.config.video_quality_assurance_minimum_score
        previous_attempts = self._read_regeneration_attempts(plan)
        can_retry = previous_attempts < context.config.video_quality_assurance_max_regeneration_attempts
        if is_passed:
            outcome = "pass"
        elif can_retry:
            outcome = "retry_submitted"
        else:
            outcome = "manual_review"

        report_path = self._report_path(context, week_end, content_id, plan.clip_index, clip_asset.id)
        payload = {
            "report_version": self.report_version,
            "outcome": outcome,
            "clip_plan_id": plan.id,
            "clip_asset_id": clip_asset.id,
            "actual_duration_seconds": self._read_duration(clip_asset),
            "score": assessment.score,
            "verdict": assessment.verdict,
            "issues": assessment.issues,
            "regeneration_instruction": assessment.regeneration_instruction,
            "summary": assessment.summary,
            "raw_response": assessment.raw_response,
            "regeneration_attempt": previous_attempts + (1 if outcome == "retry_submitted" else 0),
        }
        report = self._create_report(
            asset_repository=asset_repository,
            content_id=content_id,
            clip_asset=clip_asset,
            plan=plan,
            report_path=report_path,
            payload=payload,
            status=outcome,
        )

        if outcome == "retry_submitted":
            source_task_asset_id = self._read_positive_int(clip_asset.metadata.get("source_task_asset_id"))
            replacement_ids = [clip_asset.id]
            if source_task_asset_id is not None:
                replacement_ids.append(source_task_asset_id)
            asset_repository.mark_replaced_by_ids(replacement_ids)
            plan_repository.merge_metadata_and_status(
                clip_plan_id=plan.id,
                metadata_patch={
                    "qa_report_asset_id": report.id,
                    "qa_regeneration_attempts": previous_attempts + 1,
                    "qa_last_score": assessment.score,
                    "qa_last_verdict": assessment.verdict,
                    "qa_last_summary": assessment.summary,
                    "qa_regeneration_instruction": assessment.regeneration_instruction,
                    "qa_replaced_clip_asset_id": clip_asset.id,
                    "qa_requires_manual_review": False,
                },
                status="failed",
            )
        elif outcome == "manual_review":
            plan_repository.merge_metadata_and_status(
                clip_plan_id=plan.id,
                metadata_patch={
                    "qa_report_asset_id": report.id,
                    "qa_last_score": assessment.score,
                    "qa_last_verdict": assessment.verdict,
                    "qa_last_summary": assessment.summary,
                    "qa_requires_manual_review": True,
                },
                status="completed",
            )
        else:
            plan_repository.merge_metadata_and_status(
                clip_plan_id=plan.id,
                metadata_patch={
                    "qa_report_asset_id": report.id,
                    "qa_last_score": assessment.score,
                    "qa_last_verdict": assessment.verdict,
                    "qa_last_summary": assessment.summary,
                    "qa_requires_manual_review": False,
                },
                status="completed",
            )
        return {"report_asset_id": report.id, "outcome": outcome, "score": assessment.score, "verdict": assessment.verdict}

    def _create_report(
        self,
        asset_repository: MediaAssetRepository,
        content_id: int,
        clip_asset: MediaAssetRecord,
        plan: VideoClipPlanRecord,
        report_path: Path,
        payload: dict[str, Any],
        status: str,
    ) -> MediaAssetRecord:
        """把质检 JSON 同时落盘和持久化，供审核台与 CatTask 回溯。"""

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return asset_repository.create(
            MediaAssetInput(
                content_id=content_id,
                repository_id=None,
                asset_type="video_quality_report",
                provider="qwen_vl_video_quality",
                path=str(report_path),
                mime_type="application/json",
                status=status,
                metadata={
                    "clip_plan_id": plan.id,
                    "clip_index": plan.clip_index,
                    "clip_asset_id": clip_asset.id,
                    "actual_duration_seconds": payload.get("actual_duration_seconds"),
                    "outcome": payload.get("outcome"),
                    "score": payload.get("score"),
                    "verdict": payload.get("verdict"),
                    "summary": payload.get("summary", ""),
                },
            )
        )

    @staticmethod
    def _index_current_clip_assets(assets: list[MediaAssetRecord]) -> dict[int, MediaAssetRecord]:
        indexed: dict[int, MediaAssetRecord] = {}
        for asset in sorted(assets, key=lambda item: item.id, reverse=True):
            if asset.status in {"failed", "replaced"}:
                continue
            plan_id = VideoVisualQualityTask._read_positive_int(asset.metadata.get("clip_plan_id"))
            if plan_id is not None:
                indexed.setdefault(plan_id, asset)
        return indexed

    @staticmethod
    def _index_reusable_reports(assets: list[MediaAssetRecord]) -> dict[int, MediaAssetRecord]:
        indexed: dict[int, MediaAssetRecord] = {}
        for asset in sorted(assets, key=lambda item: item.id, reverse=True):
            clip_asset_id = VideoVisualQualityTask._read_positive_int(asset.metadata.get("clip_asset_id"))
            if clip_asset_id is not None:
                indexed.setdefault(clip_asset_id, asset)
        return indexed

    @staticmethod
    def _build_expected_contract(plan: VideoClipPlanRecord) -> dict[str, Any]:
        return {
            "clip_index": plan.clip_index,
            "clip_title": plan.clip_title,
            "repository_full_name": plan.repository_full_name,
            "teaching_visual_design": plan.visual_design,
            "motion_design": plan.motion_design,
            "source_narration": plan.narration,
            "source_subtitle": plan.subtitle,
            "must_not_contain": ["无关人物", "营销口播", "乱码文字", "水印", "与项目无关的品牌标识"],
        }

    @staticmethod
    def _read_duration(asset: MediaAssetRecord) -> float:
        try:
            duration = float(asset.metadata.get("actual_duration_seconds"))
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            raise ValueError(f"video_clip asset_id={asset.id} 缺少真实时长")
        return duration

    @staticmethod
    def _read_regeneration_attempts(plan: VideoClipPlanRecord) -> int:
        try:
            return max(0, int(plan.metadata.get("qa_regeneration_attempts", 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _read_positive_int(value: Any) -> int | None:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None
        return normalized if normalized > 0 else None

    @staticmethod
    def _resolve_local_path(project_root: Path, asset: MediaAssetRecord) -> Path:
        path = Path(asset.path)
        candidate = path if path.is_absolute() else project_root / path
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"视频片段不存在：asset_id={asset.id} path={candidate}")
        return candidate

    def _report_path(self, context: TaskContext, week_end: Any, content_id: int, clip_index: int, clip_asset_id: int) -> Path:
        safe_week_end = str(week_end).strip() or "unknown"
        return context.config.video_output_dir / safe_week_end / "quality" / str(content_id) / f"clip_{clip_index:02d}_asset_{clip_asset_id}_report.json"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return (str(exc).strip() or exc.__class__.__name__)[:600]

    @staticmethod
    def _skipped(content_id: int, reason: str, **extra: Any) -> dict[str, Any]:
        return {"content_id": content_id, "skipped": True, "skip_reason": reason, "network_called": False, **extra}
