from __future__ import annotations

from pathlib import Path
from typing import Any

from src.providers.local_slideshow_video_provider import LocalSlideshowVideoProvider
from src.providers.seedance_video_provider import SeedanceVideoProvider
from src.repositories.generated_content_repository import GeneratedContentForVideo, GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.repositories.video_storyboard_repository import VideoStoryboardRecord, VideoStoryboardRepository
from src.services.video_material_service import VideoMaterialReadiness, VideoMaterialService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class VideoTask(BaseTask):
    """检查视频素材，并提交 Seedance 或生成本地审核版视频。"""

    task_name = "VideoTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取最新内容和媒体资产，决定走真实视频 API 还是本地免费兜底。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        storyboard_repository = VideoStoryboardRepository(database_manager=context.database_manager)
        material_service = VideoMaterialService()

        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可生成视频的 generated_contents，请先运行 SummaryTask")

        media_assets = media_asset_repository.list_for_content(content.id)
        storyboard = storyboard_repository.latest_for_content(content.id)
        video_provider = SeedanceVideoProvider(config=context.config)
        readiness = material_service.build_readiness(
            config=context.config,
            content=content,
            media_assets=media_assets,
            video_credentials_ready=video_provider.has_api_key(),
        )

        metadata: dict[str, Any] = {
            **readiness.manifest,
            "ready_for_generation": readiness.ready_for_submission,
            "network_called": False,
        }
        stale_local_video_assets = self._find_stale_local_slideshow_videos(
            existing_video_assets=readiness.existing_video_assets,
            storyboard=storyboard,
        )
        active_remote_video_assets = [
            asset for asset in readiness.existing_video_assets if asset.id not in {item.id for item in stale_local_video_assets}
        ]

        if active_remote_video_assets:
            self.logger.info("视频资产已经存在，VideoTask 跳过重复生成：content_id=%s", content.id)
            return {
                **metadata,
                "skipped": True,
                "skip_reason": "video_asset_already_exists",
                "existing_video_asset_ids": [asset.id for asset in active_remote_video_assets],
            }

        if readiness.existing_video_task_assets:
            self.logger.info("视频生成任务已经存在，VideoTask 跳过重复提交：content_id=%s", content.id)
            return {
                **metadata,
                "skipped": True,
                "skip_reason": "video_task_already_submitted",
            }

        if not readiness.ready_for_submission:
            local_result = self._try_local_fallback_video(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                readiness=readiness,
                metadata=metadata,
            )
            if local_result is not None:
                return local_result

            if context.config.video_skip_when_material_missing or context.config.video_skip_when_api_key_missing:
                self.logger.warning(
                    "视频生成前置检查未通过：content_id=%s missing=%s",
                    content.id,
                    readiness.missing_requirements,
                )
                return {
                    **metadata,
                    "skipped": True,
                    "skip_reason": "video_preflight_blocked",
                    "missing_requirements": readiness.missing_requirements,
                    "warning_count": len(readiness.warnings),
                }
            raise RuntimeError("视频生成前置检查未通过：" + "；".join(readiness.missing_requirements))

        prompt = self._build_seedance_prompt(
            context=context,
            content=content,
            storyboard=storyboard,
            material_service=material_service,
        )
        task_result = video_provider.create_reference_video_task(
            prompt=prompt,
            image_urls=readiness.public_image_urls[: context.config.video_required_image_count],
        )
        task_asset = media_asset_repository.create(
            MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="video_task",
                provider=context.config.video_provider,
                path=f"{context.config.video_provider}://tasks/{task_result.task_id}",
                mime_type="application/vnd.seedance.video-task",
                status="submitted",
                metadata={
                    "task_id": task_result.task_id,
                    "prompt": prompt,
                    "prompt_source": "video_storyboard_seedance_prompt" if storyboard is not None else "summary_video_script",
                    "storyboard_id": None if storyboard is None else storyboard.id,
                    "seedance_clip_duration_seconds": context.config.video_clip_duration_seconds,
                    "material_manifest": readiness.manifest,
                    "image_urls": readiness.public_image_urls[: context.config.video_required_image_count],
                    "reference_image_asset_ids": [asset.id for asset in readiness.selected_image_assets],
                    "audio_asset_id": None if readiness.selected_audio_asset is None else readiness.selected_audio_asset.id,
                    "model": context.config.video_model,
                    "clip_duration_seconds": context.config.video_clip_duration_seconds,
                    "target_duration_seconds": context.config.video_duration_seconds,
                    "resolution": context.config.video_resolution,
                    "aspect_ratio": context.config.video_aspect_ratio,
                    "raw_response": task_result.raw_response,
                },
            )
        )
        replaced_stale_video_count = media_asset_repository.mark_replaced_by_ids([asset.id for asset in stale_local_video_assets])
        self.logger.info(
            "Seedance 视频生成任务已提交：content_id=%s asset_id=%s task_id=%s",
            content.id,
            task_asset.id,
            task_result.task_id,
        )
        return {
            **metadata,
            "skipped": False,
            "skip_reason": None,
            "ready_for_generation": True,
            "network_called": True,
            "video_task_asset_id": task_asset.id,
            "seedance_task_id": task_result.task_id,
            "prompt_source": "video_storyboard_seedance_prompt" if storyboard is not None else "summary_video_script",
            "storyboard_id": None if storyboard is None else storyboard.id,
            "replaced_stale_video_count": replaced_stale_video_count,
        }

    def _find_stale_local_slideshow_videos(
        self,
        existing_video_assets: list[MediaAssetRecord],
        storyboard: VideoStoryboardRecord | None,
    ) -> list[MediaAssetRecord]:
        """找出旧 slideshow 审核视频；新 Seedance 任务成功提交后才会替换它。"""

        if storyboard is None:
            return []
        stale_assets: list[MediaAssetRecord] = []
        for asset in existing_video_assets:
            if asset.provider != "local_slideshow_video":
                continue
            if bool(asset.metadata.get("fallback", False)):
                stale_assets.append(asset)
        return stale_assets

    def _build_seedance_prompt(
        self,
        context: TaskContext,
        content: GeneratedContentForVideo,
        storyboard: VideoStoryboardRecord | None,
        material_service: VideoMaterialService,
    ) -> str:
        """优先基于短视频蓝图生成 Seedance prompt。"""

        if storyboard is None:
            return material_service.build_video_prompt(
                title=content.title,
                video_script=content.video_script,
            )

        scenes = self._select_storyboard_scenes_for_clip(
            storyboard=storyboard,
            clip_duration_seconds=context.config.video_clip_duration_seconds,
        )
        scene_text = "\n".join(
            (
                f"{scene.get('time_range', '')}：{scene.get('purpose', '')}；"
                f"画面：{scene.get('visual_design', '')}；"
                f"运动：{scene.get('motion_design', '')}；"
                f"旁白：{scene.get('narration', '')}"
            )
            for scene in scenes
        )
        reference_hint = "参考输入图片的蓝绿色科技架构图风格，保持深色背景、清晰流程线、模块高亮和 PPT 动画感。"
        if context.config.video_generate_audio:
            audio_hint = "生成自然中文旁白，语速稳定，旁白内容与字幕和画面同步。"
        else:
            audio_hint = "不需要生成旁白音频，但画面节奏要适合后期配音。"

        if context.config.video_clip_duration_seconds < context.config.video_duration_seconds:
            duration_hint = (
                f"这是完整 {context.config.video_duration_seconds} 秒教学视频的前 "
                f"{context.config.video_clip_duration_seconds} 秒样片，只覆盖开场和第一个项目，不要压缩完整五个项目。"
            )
        else:
            duration_hint = f"生成完整 {context.config.video_duration_seconds} 秒教学视频。"

        return (
            f"{duration_hint}"
            f"标题：{storyboard.title}。"
            f"{reference_hint}"
            f"{audio_hint}"
            "整体风格是高质量科技教学短视频，不是图片硬切 slideshow。"
            "画面要像技术课件动画：节点展开、流程线推进、模块依次高亮、镜头平滑移动。"
            "避免乱码文字、错误 UI、随机抽象素材。"
            f"本段分镜：\n{scene_text}\n"
            f"完整口播参考：{storyboard.progressive_script}"
        )[:1800]

    def _select_storyboard_scenes_for_clip(
        self,
        storyboard: VideoStoryboardRecord,
        clip_duration_seconds: int,
    ) -> list[dict[str, Any]]:
        """从完整 60 秒分镜中取出当前单次 Seedance 任务要覆盖的片段。"""

        raw_scenes = storyboard.storyboard.get("scenes", [])
        if not isinstance(raw_scenes, list):
            return []

        selected_scenes: list[dict[str, Any]] = []
        elapsed = 0
        for item in raw_scenes:
            if not isinstance(item, dict):
                continue
            duration = int(item.get("duration_seconds", 0) or 0)
            if duration <= 0:
                continue
            selected_scenes.append(item)
            elapsed += duration
            if elapsed >= clip_duration_seconds:
                break

        return selected_scenes or [item for item in raw_scenes if isinstance(item, dict)][:1]

    def _try_local_fallback_video(
        self,
        context: TaskContext,
        content: GeneratedContentForVideo,
        media_asset_repository: MediaAssetRepository,
        readiness: VideoMaterialReadiness,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """当远程 Seedance 前置条件不足时，尝试生成本地免费审核版视频。"""

        if not context.config.video_local_fallback_enabled:
            return None

        local_missing_requirements = self._local_video_missing_requirements(
            context=context,
            content=content,
            readiness=readiness,
        )
        if local_missing_requirements:
            return None

        try:
            image_paths = [
                self._resolve_local_asset_path(context.config.project_root, asset)
                for asset in readiness.selected_image_assets[: context.config.video_required_image_count]
            ]
            audio_path = self._resolve_local_asset_path(context.config.project_root, readiness.selected_audio_asset)
            output_path = context.config.video_output_dir / content.week_end / f"{content.id}_local_slideshow.mp4"
            provider = LocalSlideshowVideoProvider()
            video_result = provider.create_video(
                image_paths=image_paths,
                audio_path=audio_path,
                output_path=output_path,
                clip_duration_seconds=context.config.video_clip_duration_seconds,
                target_duration_seconds=context.config.video_duration_seconds,
                resolution=context.config.video_resolution,
            )
            video_asset = media_asset_repository.create(
                MediaAssetInput(
                    content_id=content.id,
                    repository_id=None,
                    asset_type="video",
                    provider="local_slideshow_video",
                    path=str(video_result.output_path),
                    mime_type="video/mp4",
                    status="created",
                    metadata={
                        "fallback": True,
                        "source": "local_images_and_local_audio",
                        "image_asset_ids": [asset.id for asset in readiness.selected_image_assets],
                        "audio_asset_id": None if readiness.selected_audio_asset is None else readiness.selected_audio_asset.id,
                        "size_bytes": video_result.size_bytes,
                        "ffmpeg_path": video_result.ffmpeg_path,
                        "remote_seedance_missing_requirements": readiness.missing_requirements,
                    },
                )
            )
            local_success_metadata = {
                **metadata,
                "checks": {
                    "missing_requirements": [],
                    "warnings": [
                        *metadata.get("checks", {}).get("warnings", []),
                        "已使用本地免费兜底生成审核版视频，未提交 Seedance 远程任务",
                    ],
                },
                "ready_for_generation": True,
            }
            self.logger.info(
                "本地审核版视频生成完成：content_id=%s video_asset_id=%s path=%s",
                content.id,
                video_asset.id,
                video_asset.path,
            )
            return {
                **local_success_metadata,
                "skipped": False,
                "skip_reason": None,
                "local_fallback_used": True,
                "network_called": False,
                "video_asset_id": video_asset.id,
                "video_path": video_asset.path,
                "size_bytes": video_result.size_bytes,
                "remote_seedance_missing_requirements": readiness.missing_requirements,
            }
        except Exception as exc:
            self.logger.exception("本地审核版视频生成失败：content_id=%s", content.id)
            if not (context.config.video_skip_when_material_missing or context.config.video_skip_when_api_key_missing):
                raise
            return {
                **metadata,
                "skipped": True,
                "skip_reason": "local_video_fallback_failed",
                "missing_requirements": readiness.missing_requirements,
                "local_fallback_error": str(exc),
                "network_called": False,
            }

    def _local_video_missing_requirements(
        self,
        context: TaskContext,
        content: GeneratedContentForVideo,
        readiness: VideoMaterialReadiness,
    ) -> list[str]:
        """检查本地视频兜底所需的最小素材。"""

        missing: list[str] = []
        if not content.video_script.strip():
            missing.append("video_script 为空")
        if len(readiness.selected_image_assets) < context.config.video_required_image_count:
            missing.append(
                f"本地图片素材不足：需要 {context.config.video_required_image_count} 张，当前 {len(readiness.selected_image_assets)} 张"
            )
        else:
            for asset in readiness.selected_image_assets[: context.config.video_required_image_count]:
                try:
                    self._resolve_local_asset_path(context.config.project_root, asset)
                except RuntimeError:
                    missing.append(f"图片资产缺少本地文件：asset_id={asset.id}")

        if readiness.selected_audio_asset is None:
            missing.append("缺少本地旁白音频")
        else:
            try:
                self._resolve_local_asset_path(context.config.project_root, readiness.selected_audio_asset)
            except RuntimeError:
                missing.append(f"音频资产缺少本地文件：asset_id={readiness.selected_audio_asset.id}")

        return missing

    def _resolve_local_asset_path(self, project_root: Path, asset: MediaAssetRecord | None) -> Path:
        """把 media_assets 中的本地路径解析成绝对路径。"""

        if asset is None:
            raise RuntimeError("媒体资产为空")
        if asset.path.startswith("http://") or asset.path.startswith("https://"):
            raise RuntimeError(f"媒体资产不是本地文件：asset_id={asset.id}")

        raw_path = Path(asset.path)
        candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise RuntimeError(f"媒体资产本地文件不存在：asset_id={asset.id} path={resolved}")
        return resolved
