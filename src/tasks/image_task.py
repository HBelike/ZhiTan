from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.providers.gotenberg_screenshot_provider import GotenbergScreenshotProvider
from src.providers.github_repository_asset_provider import GitHubRepositoryAssetProvider
from src.providers.local_image_provider import LocalTechCardImageProvider
from src.providers.seedream_provider import SeedreamProvider
from src.repositories.generated_content_repository import GeneratedContentForImage, GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.services.article_visual_generation_service import ArticleVisualGenerationService
from src.services.image_prompt_design_service import ImagePromptDesignService
from src.repositories.video_storyboard_repository import VideoStoryboardRecord, VideoStoryboardRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


@dataclass(frozen=True)
class ImagePromptSelection:
    """ImageTask 本轮实际采用的图片 prompt 来源。"""

    content: GeneratedContentForImage
    prompt_source: str
    storyboard_id: int | None
    github_asset_fallback_allowed: bool


class ImageTask(BaseTask):
    """根据 SummaryTask 产出的项目 prompt 生成项目配图。"""

    task_name = "ImageTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """按配置执行默认图片渲染器。"""

        return self.execute_for_content(context)

    def execute_for_content(
        self,
        context: TaskContext,
        content_id: int | None = None,
    ) -> dict[str, Any]:
        """为最新或指定内容生成图片，显式 renderer 决定调用链。"""

        renderer = str(
            getattr(context.config, "image_renderer", "gotenberg_html")
        ).strip()
        if renderer == "gotenberg_html":
            return self._execute_deterministic(context, content_id=content_id)
        if renderer == "seedream":
            return self._execute_seedream(context, content_id=content_id)
        raise RuntimeError(f"不支持的图片 renderer：{renderer}")

    def _execute_deterministic(
        self,
        context: TaskContext,
        *,
        content_id: int | None,
    ) -> dict[str, Any]:
        """使用 ArticleVisualSpec 和 Gotenberg 完成整批关闭式渲染。"""

        content_repository = GeneratedContentRepository(
            database_manager=context.database_manager
        )
        media_asset_repository = MediaAssetRepository(
            database_manager=context.database_manager
        )
        content = (
            content_repository.get_for_image_generation(content_id)
            if content_id is not None
            else content_repository.latest_for_image_generation()
        )
        if content is None:
            raise RuntimeError(f"没有可生成图片的内容：content_id={content_id}")
        if not content.image_prompts:
            raise RuntimeError(f"content_id={content.id} 没有可用 image_prompts")

        existing_assets = media_asset_repository.list_by_content_id(
            content.id, "image"
        )
        provider = GotenbergScreenshotProvider(config=context.config)
        generation_service = ArticleVisualGenerationService(
            context.config,
            provider=provider,
        )
        batch = generation_service.prepare(
            content=content,
            existing_assets=existing_assets,
        )

        created_records: list[MediaAssetRecord] = []
        if batch.new_assets:
            try:
                created_records = media_asset_repository.create_and_replace_images(
                    assets=[item.input for item in batch.new_assets],
                    replace_asset_ids=list(batch.replace_asset_ids),
                )
            except Exception:
                generation_service.cleanup_new_files(batch)
                raise

        assets = [
            {
                "asset_id": asset.id,
                "repository_full_name": str(
                    asset.metadata.get("repository_full_name", "")
                ),
                "path": asset.path,
                "provider": asset.provider,
                "reused": False,
            }
            for asset in created_records
        ]
        assets.extend(
            {
                "asset_id": asset.id,
                "repository_full_name": str(
                    asset.metadata.get("repository_full_name", "")
                ),
                "path": asset.path,
                "provider": asset.provider,
                "reused": True,
            }
            for asset in batch.reused_assets
        )
        return {
            "content_id": content.id,
            "week_end": content.week_end,
            "prompt_count": len(content.image_prompts),
            "image_asset_count": len(batch.new_assets) + len(batch.reused_assets),
            "renderer": "gotenberg_html",
            "provider": "gotenberg_html",
            "rendered_image_count": len(batch.new_assets),
            "reused_image_count": len(batch.reused_assets),
            # 兼容旧工作台字段，含义保持为本轮复用数量。
            "reusable_image_count": len(batch.reused_assets),
            "created_image_count": len(created_records),
            "replaced_image_count": len(batch.replace_asset_ids),
            "validation_failed_count": 0,
            "figure_role_counts": batch.figure_role_counts,
            "prompt_source": "summary_image_prompt",
            "storyboard_id": None,
            "github_asset_fallback_allowed": False,
            "skipped": not bool(batch.new_assets),
            "skip_reason": "all_render_keys_reused"
            if not batch.new_assets
            else None,
            "network_called": bool(batch.new_assets),
            "paid_generation_called": False,
            "assets": assets,
        }

    def _execute_seedream(
        self,
        context: TaskContext,
        *,
        content_id: int | None = None,
    ) -> dict[str, Any]:
        """读取最新内容，补齐缺失的项目配图，并写入 media_assets。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        storyboard_repository = VideoStoryboardRepository(database_manager=context.database_manager)

        base_content = (
            content_repository.get_for_image_generation(content_id)
            if content_id is not None
            else content_repository.latest_for_image_generation()
        )
        if base_content is None:
            raise RuntimeError("没有可生成图片的 generated_contents，请先运行 SummaryTask")
        prompt_selection = self._select_image_prompts(
            content=base_content,
            storyboard_repository=storyboard_repository,
        )
        content = prompt_selection.content
        if not content.image_prompts:
            raise RuntimeError(f"content_id={content.id} 没有可用 image_prompts")

        existing_assets = media_asset_repository.list_by_content_id(content.id, "image")
        if context.config.image_refresh_existing_assets_on_run:
            reusable_assets: dict[str, MediaAssetRecord] = {}
            if existing_assets:
                self.logger.info(
                    "图片刷新模式已开启，本轮将为当前 %s 个项目重新创建图片：content_id=%s existing=%s",
                    len(content.image_prompts),
                    content.id,
                    len(existing_assets),
                )
        else:
            reusable_assets = self._find_reusable_assets(
                project_root=context.config.project_root,
                prompts=content.image_prompts,
                existing_assets=existing_assets,
            )
        missing_prompts = [
            item
            for item in content.image_prompts
            if str(item.get("repository_full_name", "")).strip() not in reusable_assets
        ]

        if not missing_prompts:
            self.logger.info("项目配图已经全部存在，ImageTask 跳过重复生成：content_id=%s", content.id)
            return {
                "content_id": content.id,
                "week_end": content.week_end,
                "prompt_count": len(content.image_prompts),
                "image_asset_count": len(existing_assets),
                "reusable_image_count": len(reusable_assets),
                "created_image_count": 0,
                "provider": context.config.image_provider,
                "model": context.config.image_model,
                "prompt_source": prompt_selection.prompt_source,
                "storyboard_id": prompt_selection.storyboard_id,
                "github_asset_fallback_allowed": prompt_selection.github_asset_fallback_allowed,
                "skipped": True,
                "skip_reason": "all_images_already_exist",
                "network_called": False,
            }

        created_assets: list[dict[str, Any]] = []
        github_created_assets: list[dict[str, Any]] = []
        remaining_prompts = missing_prompts
        if context.config.image_github_asset_fallback_enabled and prompt_selection.github_asset_fallback_allowed:
            github_created_assets, remaining_prompts = self._generate_github_repository_images(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                missing_prompts=missing_prompts,
            )
            created_assets.extend(github_created_assets)
        elif context.config.image_github_asset_fallback_enabled and not prompt_selection.github_asset_fallback_allowed:
            self.logger.info(
                "检测到短视频架构图 prompt，本轮跳过 GitHub 仓库图复用，直接进入 Seedream 生图：content_id=%s storyboard_id=%s",
                content.id,
                prompt_selection.storyboard_id,
            )

        if not remaining_prompts:
            replaced_count = self._mark_replaced_for_created_repositories(
                media_asset_repository=media_asset_repository,
                existing_assets=existing_assets,
                created_assets=created_assets,
            )
            return {
                "content_id": content.id,
                "week_end": content.week_end,
                "prompt_count": len(content.image_prompts),
                "image_asset_count": len(existing_assets) + len(created_assets) - replaced_count,
                "reusable_image_count": len(reusable_assets),
                "missing_prompt_count": len(missing_prompts),
                "created_image_count": len(created_assets),
                "replaced_image_count": replaced_count,
                "github_asset_count": len(github_created_assets),
                "provider": "github_repository_asset",
                "providers_used": self._providers_used(created_assets),
                "prompt_source": prompt_selection.prompt_source,
                "storyboard_id": prompt_selection.storyboard_id,
                "github_asset_fallback_allowed": prompt_selection.github_asset_fallback_allowed,
                "skipped": False,
                "network_called": bool(github_created_assets),
                "paid_generation_called": False,
                "assets": created_assets,
            }

        if not context.config.image_paid_generation_enabled:
            return self._handle_paid_generation_disabled(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                existing_assets=existing_assets,
                reusable_assets=reusable_assets,
                missing_prompts=remaining_prompts,
                precreated_assets=created_assets,
                prompt_source=prompt_selection.prompt_source,
                storyboard_id=prompt_selection.storyboard_id,
                github_asset_fallback_allowed=prompt_selection.github_asset_fallback_allowed,
            )

        provider = SeedreamProvider(config=context.config)
        if not provider.has_api_key():
            return self._handle_missing_seedream_key(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                existing_assets=existing_assets,
                reusable_assets=reusable_assets,
                missing_prompts=remaining_prompts,
                precreated_assets=created_assets,
                prompt_source=prompt_selection.prompt_source,
                storyboard_id=prompt_selection.storyboard_id,
                github_asset_fallback_allowed=prompt_selection.github_asset_fallback_allowed,
            )

        seedream_created_assets: list[dict[str, Any]] = []
        seedream_failed_assets: list[dict[str, Any]] = []
        seedream_local_fallback_assets: list[dict[str, Any]] = []
        prompt_design_service = ImagePromptDesignService(config=context.config)
        for item in remaining_prompts:
            index = self._prompt_index(content.image_prompts, item)
            repository_full_name = str(item["repository_full_name"])
            raw_prompt = str(item["prompt"])
            generation_prompt = prompt_design_service.build_project_architecture_prompt(
                repository_full_name=repository_full_name,
                focus_prompt=raw_prompt,
                project_summary_text=str(item.get("project_summary_text") or item.get("summary_text") or "").strip(),
                visual_brief=item.get("visual_brief") if isinstance(item.get("visual_brief"), dict) else None,
                project_index=index,
            )
            generation_prompt = self._apply_runtime_image_instruction(
                generation_prompt=generation_prompt,
                runtime_instruction=context.config.runtime_prompt("image"),
                max_length=context.config.image_prompt_max_length,
            )
            output_path = self._build_output_path(
                output_dir=context.config.image_output_dir,
                content=content,
                index=index,
                repository_full_name=repository_full_name,
            )

            try:
                image_result = provider.generate_image(prompt=generation_prompt, output_path=output_path)
            except Exception as exc:
                seedream_failed_assets.append(
                    {
                        "repository_full_name": repository_full_name,
                        "error": str(exc),
                    }
                )
                self.logger.warning(
                    "Seedream 单张项目图生成失败，将尝试本地兜底：content_id=%s repository=%s error=%s",
                    content.id,
                    repository_full_name,
                    exc,
                )
                if not context.config.image_local_fallback_enabled:
                    raise

                fallback_asset = self._generate_one_local_fallback_image(
                    context=context,
                    content=content,
                    media_asset_repository=media_asset_repository,
                    repository_full_name=repository_full_name,
                    prompt=raw_prompt,
                    index=index,
                )
                seedream_local_fallback_assets.append(fallback_asset)
                continue

            asset = media_asset_repository.create(
                MediaAssetInput(
                    content_id=content.id,
                    repository_id=None,
                    asset_type="image",
                    provider=context.config.image_provider,
                    path=str(image_result.output_path),
                    mime_type="image/png",
                    status="created",
                    metadata={
                        **self._build_prompt_metadata(
                            item=item,
                            repository_full_name=repository_full_name,
                            prompt=generation_prompt,
                            index=index,
                            raw_prompt=raw_prompt,
                            prompt_stage="ark_final_v3",
                        ),
                        "prompt_designed_by": "ImagePromptDesignService",
                        "image_post_processing": "none",
                        "source_url": image_result.source_url,
                        "model": context.config.image_model,
                        "size": context.config.image_size,
                    },
                )
            )
            seedream_created_assets.append(
                {
                    "asset_id": asset.id,
                    "repository_full_name": repository_full_name,
                    "path": asset.path,
                    "provider": context.config.image_provider,
                    "prompt_source": str(item.get("prompt_source", "summary_image_prompt")),
                }
            )
            self.logger.info(
                "Seedream 项目配图生成完成：content_id=%s asset_id=%s repository=%s path=%s",
                content.id,
                asset.id,
                repository_full_name,
                asset.path,
            )

        created_assets.extend(seedream_created_assets)
        created_assets.extend(seedream_local_fallback_assets)
        replaced_count = self._mark_replaced_for_created_repositories(
            media_asset_repository=media_asset_repository,
            existing_assets=existing_assets,
            created_assets=created_assets,
        )
        return {
            "content_id": content.id,
            "week_end": content.week_end,
            "prompt_count": len(content.image_prompts),
            "image_asset_count": len(existing_assets) + len(created_assets) - replaced_count,
            "reusable_image_count": len(reusable_assets),
            "missing_prompt_count": len(missing_prompts),
            "remaining_seedream_prompt_count": len(remaining_prompts),
            "created_image_count": len(created_assets),
            "replaced_image_count": replaced_count,
            "github_asset_count": len(github_created_assets),
            "seedream_image_count": len(seedream_created_assets),
            "seedream_failed_count": len(seedream_failed_assets),
            "seedream_local_fallback_count": len(seedream_local_fallback_assets),
            "seedream_errors": seedream_failed_assets,
            "provider": context.config.image_provider,
            "providers_used": self._providers_used(created_assets),
            "model": context.config.image_model,
            "prompt_source": prompt_selection.prompt_source,
            "storyboard_id": prompt_selection.storyboard_id,
            "github_asset_fallback_allowed": prompt_selection.github_asset_fallback_allowed,
            "skipped": False,
            "network_called": True,
            "paid_generation_called": bool(seedream_created_assets or seedream_failed_assets),
            "assets": created_assets,
        }

    def _select_image_prompts(
        self,
        content: GeneratedContentForImage,
        storyboard_repository: VideoStoryboardRepository,
    ) -> ImagePromptSelection:
        """优先选择 ShortVideoPromptTask 产出的技术架构图 prompt。"""

        storyboard = storyboard_repository.latest_for_content(content.id)
        if storyboard is None:
            self.logger.info("未找到短视频蓝图，ImageTask 使用 SummaryTask 原始图片 prompt：content_id=%s", content.id)
            return ImagePromptSelection(
                content=content,
                prompt_source="summary_image_prompt",
                storyboard_id=None,
                github_asset_fallback_allowed=True,
            )

        storyboard_prompts = self._normalize_storyboard_image_prompts(storyboard=storyboard)
        if len(storyboard_prompts) != len(content.image_prompts):
            self.logger.warning(
                "短视频蓝图中的架构图 prompt 不完整，ImageTask 回退到 SummaryTask 原始图片 prompt：content_id=%s storyboard_id=%s count=%s",
                content.id,
                storyboard.id,
                len(storyboard_prompts),
            )
            return ImagePromptSelection(
                content=content,
                prompt_source="summary_image_prompt",
                storyboard_id=storyboard.id,
                github_asset_fallback_allowed=True,
            )

        self.logger.info(
            "ImageTask 使用 ShortVideoPromptTask 技术架构图 prompt：content_id=%s storyboard_id=%s prompt_count=%s",
            content.id,
            storyboard.id,
            len(storyboard_prompts),
        )
        return ImagePromptSelection(
            content=GeneratedContentForImage(
                id=content.id,
                week_end=content.week_end,
                title=content.title,
                image_prompts=storyboard_prompts,
            ),
            prompt_source="video_storyboard_architecture_prompt",
            storyboard_id=storyboard.id,
            github_asset_fallback_allowed=False,
        )

    def _normalize_storyboard_image_prompts(self, storyboard: VideoStoryboardRecord) -> list[dict[str, Any]]:
        """把 video_storyboards 里的架构图 prompt 转换成 ImageTask 可消费的结构。"""

        normalized_prompts: list[dict[str, Any]] = []
        for fallback_index, item in enumerate(storyboard.architecture_image_prompts, start=1):
            repository_full_name = str(item.get("repository_full_name", "")).strip()
            prompt = str(item.get("architecture_prompt", "")).strip()
            if not repository_full_name or not prompt:
                continue

            raw_project_index = item.get("project_index", fallback_index)
            try:
                project_index = int(raw_project_index)
            except (TypeError, ValueError):
                project_index = fallback_index

            normalized_prompts.append(
                {
                    "repository_full_name": repository_full_name,
                    "prompt": prompt,
                    "prompt_source": "video_storyboard_architecture_prompt",
                    "storyboard_id": storyboard.id,
                    "project_index": project_index,
                    "project_summary_text": str(item.get("project_summary_text") or item.get("summary_text") or "").strip(),
                    "project_analysis_markdown": str(item.get("project_analysis_markdown") or "").strip(),
                    "visual_brief": item.get("visual_brief") if isinstance(item.get("visual_brief"), dict) else None,
                    "video_brief": item.get("video_brief") if isinstance(item.get("video_brief"), dict) else None,
                }
            )

        return sorted(normalized_prompts, key=lambda item: int(item.get("project_index", 0) or 0))

    def _apply_runtime_image_instruction(
        self,
        generation_prompt: str,
        runtime_instruction: str,
        max_length: int,
    ) -> str:
        """在不突破最终请求长度上限的前提下追加运行时生图偏好。

        运行时策略不能覆盖结构、中文可读性和禁止本地叠字等基础约束。先收紧
        运行时文本并为它预留容量，避免旧实现把已编译的 Ark prompt 又扩展到 5000 字。
        """

        bounded_length = max(256, int(max_length))
        # 先完整保住已编译的视觉合同。它包含结构、中文短标签和“禁止本地叠字”等
        # 不能被运行时偏好替换的硬约束；当合同已经占满上限时，宁可忽略补充策略。
        base = generation_prompt[:bounded_length].rstrip(" ，,；;。")
        if len(base) >= bounded_length:
            return base

        instruction = " ".join(runtime_instruction.split()).strip()[:420]
        if not instruction:
            return base

        available_length = bounded_length - len(base)
        suffix_prefix = " 运行时补充风格偏好（仅补充，不覆盖前述约束）："
        if available_length <= len(suffix_prefix) + 8:
            return base

        suffix = f"{suffix_prefix}{instruction}"
        return f"{base}{suffix[:available_length].rstrip(' ，,；;。')}"

    def _generate_github_repository_images(
        self,
        context: TaskContext,
        content: GeneratedContentForImage,
        media_asset_repository: MediaAssetRepository,
        missing_prompts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """在调用生图 API 前，优先从 GitHub 仓库中寻找项目相关图片。"""

        provider = GitHubRepositoryAssetProvider(config=context.config, logger=self.logger)
        created_assets: list[dict[str, Any]] = []
        remaining_prompts: list[dict[str, Any]] = []

        for item in missing_prompts:
            index = self._prompt_index(content.image_prompts, item)
            repository_full_name = str(item["repository_full_name"]).strip()
            prompt = str(item["prompt"])
            output_path = self._build_output_path(
                output_dir=context.config.image_output_dir,
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
                    "GitHub 仓库图检索失败，后续将进入生图或本地兜底：content_id=%s repository=%s error=%s",
                    content.id,
                    repository_full_name,
                    exc,
                )
                remaining_prompts.append(item)
                continue

            if github_result is None:
                remaining_prompts.append(item)
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
                        **self._build_prompt_metadata(
                            item=item,
                            repository_full_name=repository_full_name,
                            prompt=prompt,
                            index=index,
                        ),
                        "source_url": github_result.source_url,
                        "source_kind": github_result.source_kind,
                        "width": github_result.width,
                        "height": github_result.height,
                        **github_result.metadata,
                    },
                )
            )
            created_assets.append(
                {
                    "asset_id": asset.id,
                    "repository_full_name": repository_full_name,
                    "path": asset.path,
                    "provider": "github_repository_asset",
                    "source_url": github_result.source_url,
                    "source_kind": github_result.source_kind,
                }
            )
            self.logger.info(
                "GitHub 仓库图复用完成：content_id=%s asset_id=%s repository=%s source=%s path=%s",
                content.id,
                asset.id,
                repository_full_name,
                github_result.source_url,
                asset.path,
            )

        return created_assets, remaining_prompts

    def _handle_paid_generation_disabled(
        self,
        context: TaskContext,
        content: GeneratedContentForImage,
        media_asset_repository: MediaAssetRepository,
        existing_assets: list[MediaAssetRecord],
        reusable_assets: dict[str, MediaAssetRecord],
        missing_prompts: list[dict[str, Any]],
        precreated_assets: list[dict[str, Any]],
        prompt_source: str,
        storyboard_id: int | None,
        github_asset_fallback_allowed: bool,
    ) -> dict[str, Any]:
        """付费生图关闭时，不调用 Seedream，改走本地免费卡片图或直接跳过。"""

        self.logger.info(
            "付费生图已关闭，ImageTask 不会调用 Seedream：content_id=%s missing=%s",
            content.id,
            len(missing_prompts),
        )
        created_assets = list(precreated_assets)
        if context.config.image_local_fallback_enabled:
            local_created_assets = self._generate_local_fallback_images(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                missing_prompts=missing_prompts,
            )
            created_assets.extend(local_created_assets)
            replaced_count = self._mark_replaced_for_created_repositories(
                media_asset_repository=media_asset_repository,
                existing_assets=existing_assets,
                created_assets=created_assets,
            )
            return {
                "content_id": content.id,
                "week_end": content.week_end,
                "prompt_count": len(content.image_prompts),
                "image_asset_count": len(existing_assets) + len(created_assets) - replaced_count,
                "reusable_image_count": len(reusable_assets),
                "missing_prompt_count": len(missing_prompts),
                "created_image_count": len(created_assets),
                "replaced_image_count": replaced_count,
                "github_asset_count": self._count_provider(created_assets, "github_repository_asset"),
                "local_image_count": len(local_created_assets),
                "provider": "local_tech_card",
                "providers_used": self._providers_used(created_assets),
                "model": "pillow-generated-card",
                "prompt_source": prompt_source,
                "storyboard_id": storyboard_id,
                "github_asset_fallback_allowed": github_asset_fallback_allowed,
                "image_paid_generation_enabled": False,
                "local_fallback_used": True,
                "skipped": False,
                "network_called": bool(precreated_assets),
                "paid_generation_called": False,
                "assets": created_assets,
            }

        replaced_count = self._mark_replaced_for_created_repositories(
            media_asset_repository=media_asset_repository,
            existing_assets=existing_assets,
            created_assets=precreated_assets,
        )
        return {
            "content_id": content.id,
            "week_end": content.week_end,
            "prompt_count": len(content.image_prompts),
            "image_asset_count": len(existing_assets) + len(precreated_assets) - replaced_count,
            "reusable_image_count": len(reusable_assets),
            "missing_prompt_count": len(missing_prompts),
            "created_image_count": len(precreated_assets),
            "replaced_image_count": replaced_count,
            "github_asset_count": self._count_provider(precreated_assets, "github_repository_asset"),
            "provider": context.config.image_provider,
            "providers_used": self._providers_used(precreated_assets),
            "model": context.config.image_model,
            "prompt_source": prompt_source,
            "storyboard_id": storyboard_id,
            "github_asset_fallback_allowed": github_asset_fallback_allowed,
            "image_paid_generation_enabled": False,
            "skipped": not bool(precreated_assets),
            "partial": bool(precreated_assets),
            "skip_reason": "image.paid_generation_enabled=false",
            "network_called": bool(precreated_assets),
            "paid_generation_called": False,
            "assets": precreated_assets,
        }

    def _handle_missing_seedream_key(
        self,
        context: TaskContext,
        content: GeneratedContentForImage,
        media_asset_repository: MediaAssetRepository,
        existing_assets: list[MediaAssetRecord],
        reusable_assets: dict[str, MediaAssetRecord],
        missing_prompts: list[dict[str, Any]],
        precreated_assets: list[dict[str, Any]],
        prompt_source: str,
        storyboard_id: int | None,
        github_asset_fallback_allowed: bool,
    ) -> dict[str, Any]:
        """Seedream Key 缺失时，优先尝试本地免费卡片图兜底。"""

        created_assets = list(precreated_assets)
        if context.config.image_local_fallback_enabled:
            try:
                local_created_assets = self._generate_local_fallback_images(
                    context=context,
                    content=content,
                    media_asset_repository=media_asset_repository,
                    missing_prompts=missing_prompts,
                )
                created_assets.extend(local_created_assets)
                replaced_count = self._mark_replaced_for_created_repositories(
                    media_asset_repository=media_asset_repository,
                    existing_assets=existing_assets,
                    created_assets=created_assets,
                )
                return {
                    "content_id": content.id,
                    "week_end": content.week_end,
                    "prompt_count": len(content.image_prompts),
                    "image_asset_count": len(existing_assets) + len(created_assets) - replaced_count,
                    "reusable_image_count": len(reusable_assets),
                    "missing_prompt_count": len(missing_prompts),
                    "created_image_count": len(created_assets),
                    "replaced_image_count": replaced_count,
                    "github_asset_count": self._count_provider(created_assets, "github_repository_asset"),
                    "local_image_count": len(local_created_assets),
                    "provider": "local_tech_card",
                    "providers_used": self._providers_used(created_assets),
                    "model": "pillow-generated-card",
                    "prompt_source": prompt_source,
                    "storyboard_id": storyboard_id,
                    "github_asset_fallback_allowed": github_asset_fallback_allowed,
                    "local_fallback_used": True,
                    "skipped": False,
                    "network_called": bool(precreated_assets),
                    "paid_generation_called": False,
                    "assets": created_assets,
                }
            except Exception as exc:
                self.logger.exception("本地科技风卡片图兜底生成失败：content_id=%s", content.id)
                if not context.config.image_skip_when_api_key_missing:
                    raise
                replaced_count = self._mark_replaced_for_created_repositories(
                    media_asset_repository=media_asset_repository,
                    existing_assets=existing_assets,
                    created_assets=precreated_assets,
                )
                return {
                    "content_id": content.id,
                    "week_end": content.week_end,
                    "prompt_count": len(content.image_prompts),
                    "image_asset_count": len(existing_assets) + len(precreated_assets) - replaced_count,
                    "reusable_image_count": len(reusable_assets),
                    "missing_prompt_count": len(missing_prompts),
                    "created_image_count": len(precreated_assets),
                    "replaced_image_count": replaced_count,
                    "github_asset_count": self._count_provider(precreated_assets, "github_repository_asset"),
                    "provider": context.config.image_provider,
                    "providers_used": self._providers_used(precreated_assets),
                    "model": context.config.image_model,
                    "prompt_source": prompt_source,
                    "storyboard_id": storyboard_id,
                    "github_asset_fallback_allowed": github_asset_fallback_allowed,
                    "skipped": not bool(precreated_assets),
                    "partial": bool(precreated_assets),
                    "skip_reason": "local_image_fallback_failed",
                    "local_fallback_error": str(exc),
                    "network_called": bool(precreated_assets),
                    "paid_generation_called": False,
                    "assets": precreated_assets,
                }

        if context.config.image_skip_when_api_key_missing:
            replaced_count = self._mark_replaced_for_created_repositories(
                media_asset_repository=media_asset_repository,
                existing_assets=existing_assets,
                created_assets=precreated_assets,
            )
            self.logger.warning(
                "%s 未配置，ImageTask 跳过真实图片生成：content_id=%s missing=%s",
                context.config.image_api_key_env,
                content.id,
                len(missing_prompts),
            )
            return {
                "content_id": content.id,
                "week_end": content.week_end,
                "prompt_count": len(content.image_prompts),
                "image_asset_count": len(existing_assets) + len(precreated_assets) - replaced_count,
                "reusable_image_count": len(reusable_assets),
                "missing_prompt_count": len(missing_prompts),
                "created_image_count": len(precreated_assets),
                "replaced_image_count": replaced_count,
                "github_asset_count": self._count_provider(precreated_assets, "github_repository_asset"),
                "provider": context.config.image_provider,
                "providers_used": self._providers_used(precreated_assets),
                "model": context.config.image_model,
                "prompt_source": prompt_source,
                "storyboard_id": storyboard_id,
                "github_asset_fallback_allowed": github_asset_fallback_allowed,
                "skipped": not bool(precreated_assets),
                "partial": bool(precreated_assets),
                "skip_reason": f"{context.config.image_api_key_env} 未配置",
                "network_called": bool(precreated_assets),
                "paid_generation_called": False,
                "assets": precreated_assets,
            }

        raise RuntimeError(f"{context.config.image_api_key_env} 未配置，无法生成图片")

    def _generate_local_fallback_images(
        self,
        context: TaskContext,
        content: GeneratedContentForImage,
        media_asset_repository: MediaAssetRepository,
        missing_prompts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """生成本地科技风卡片图，并保存为 image 类型资产。"""

        created_assets: list[dict[str, Any]] = []

        for item in missing_prompts:
            index = self._prompt_index(content.image_prompts, item)
            repository_full_name = str(item["repository_full_name"])
            prompt = str(item["prompt"])
            asset_payload = self._generate_one_local_fallback_image(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                repository_full_name=repository_full_name,
                prompt=prompt,
                index=index,
                prompt_item=item,
            )
            created_assets.append(asset_payload)

        return created_assets

    def _generate_one_local_fallback_image(
        self,
        context: TaskContext,
        content: GeneratedContentForImage,
        media_asset_repository: MediaAssetRepository,
        repository_full_name: str,
        prompt: str,
        index: int,
        prompt_item: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为单个项目生成本地科技风兜底图。"""

        local_provider = LocalTechCardImageProvider()
        output_path = self._build_output_path(
            output_dir=context.config.image_output_dir,
            content=content,
            index=index,
            repository_full_name=repository_full_name,
        )
        local_result = local_provider.generate_card(
            repository_full_name=repository_full_name,
            prompt=prompt,
            output_path=output_path,
            index=index,
            total=len(content.image_prompts),
        )
        asset = media_asset_repository.create(
            MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="image",
                provider="local_tech_card",
                path=str(local_result.output_path),
                mime_type="image/png",
                status="created",
                metadata={
                    **self._build_prompt_metadata(
                        item=prompt_item or {},
                        repository_full_name=repository_full_name,
                        prompt=prompt,
                        index=index,
                    ),
                    **local_result.metadata,
                },
            )
        )
        self.logger.info(
            "本地科技风卡片图生成完成：content_id=%s asset_id=%s repository=%s path=%s",
            content.id,
            asset.id,
            repository_full_name,
            asset.path,
        )
        return {
            "asset_id": asset.id,
            "repository_full_name": repository_full_name,
            "path": asset.path,
            "provider": "local_tech_card",
            "prompt_source": str((prompt_item or {}).get("prompt_source", "summary_image_prompt")),
        }

    def _build_prompt_metadata(
        self,
        item: dict[str, Any],
        repository_full_name: str,
        prompt: str,
        index: int,
        raw_prompt: str | None = None,
        prompt_stage: str = "brief",
    ) -> dict[str, Any]:
        """构造图片资产 metadata，保留简报与最终 Ark prompt 的对应关系。

        ``prompt`` 是实际提交给 provider 的最终提示词；``raw_prompt`` 是上游摘要或
        分镜的原始意图。二者都保存，便于审核台区分“内容规划”与“生图执行”，同时
        为后续 Seedance / HyperFrames 链路复用 visual_brief、video_brief。
        """

        metadata: dict[str, Any] = {
            "repository_full_name": repository_full_name,
            "prompt": prompt,
            "raw_prompt": str(raw_prompt if raw_prompt is not None else item.get("prompt", "")).strip(),
            "prompt_stage": prompt_stage,
            "prompt_index": index,
            "prompt_source": str(item.get("prompt_source", "summary_image_prompt")).strip() or "summary_image_prompt",
        }
        storyboard_id = item.get("storyboard_id")
        if storyboard_id is not None:
            metadata["storyboard_id"] = storyboard_id
        project_index = item.get("project_index")
        if project_index is not None:
            metadata["project_index"] = project_index
        project_summary_text = str(item.get("project_summary_text") or item.get("summary_text") or "").strip()
        if project_summary_text:
            metadata["project_summary_text"] = project_summary_text
        visual_brief = item.get("visual_brief")
        if isinstance(visual_brief, dict):
            metadata["visual_brief"] = visual_brief
            metadata["creative_brief_version"] = str(visual_brief.get("version", "creative_brief_v2"))
        video_brief = item.get("video_brief")
        if isinstance(video_brief, dict):
            metadata["video_brief"] = video_brief
        return metadata

    def _providers_used(self, assets: list[dict[str, Any]]) -> list[str]:
        """统计本步骤实际创建图片使用过的 provider，方便任务结果监控。"""

        providers: list[str] = []
        for asset in assets:
            provider = str(asset.get("provider", "")).strip()
            if provider and provider not in providers:
                providers.append(provider)
        return providers

    def _count_provider(self, assets: list[dict[str, Any]], provider_name: str) -> int:
        """统计某个 provider 创建的图片数量。"""

        return sum(1 for asset in assets if str(asset.get("provider", "")).strip() == provider_name)

    def _mark_replaced_for_created_repositories(
        self,
        media_asset_repository: MediaAssetRepository,
        existing_assets: list[MediaAssetRecord],
        created_assets: list[dict[str, Any]],
    ) -> int:
        """把本轮已成功刷新项目对应的旧图片标记为 replaced。"""

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
        replaced_count = media_asset_repository.mark_replaced_by_ids(old_asset_ids)
        if replaced_count:
            self.logger.info("旧图片已标记为 replaced：count=%s repositories=%s", replaced_count, sorted(refreshed_repositories))
        return replaced_count

    def _find_reusable_assets(
        self,
        project_root: Path,
        prompts: list[dict[str, Any]],
        existing_assets: list[MediaAssetRecord],
    ) -> dict[str, MediaAssetRecord]:
        """按 repository_full_name 找到仍然可复用的图片资产。"""

        prompt_repositories = {
            str(item.get("repository_full_name", "")).strip()
            for item in prompts
            if str(item.get("repository_full_name", "")).strip()
        }
        reusable_assets: dict[str, MediaAssetRecord] = {}
        for asset in sorted(existing_assets, key=lambda item: item.id):
            repository_full_name = str(asset.metadata.get("repository_full_name", "")).strip()
            if repository_full_name not in prompt_repositories:
                continue
            if repository_full_name in reusable_assets:
                continue
            if self._asset_is_reusable(project_root=project_root, asset=asset):
                reusable_assets[repository_full_name] = asset
        return reusable_assets

    def _asset_is_reusable(self, project_root: Path, asset: MediaAssetRecord) -> bool:
        """判断图片资产是否还可用，避免重复调用生图 API 或重复本地绘图。"""

        remote_url = str(asset.metadata.get("remote_url", "")).strip()
        if remote_url.startswith("http://") or remote_url.startswith("https://"):
            return True
        if asset.path.startswith("http://") or asset.path.startswith("https://"):
            return True

        raw_path = Path(asset.path)
        candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
        try:
            return candidate.exists() and candidate.is_file()
        except OSError:
            return False

    def _prompt_index(self, prompts: list[dict[str, Any]], target: dict[str, Any]) -> int:
        """返回 prompt 在当前项目集合中的 1-based 序号，用于稳定文件命名。"""

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
        """为单张项目配图生成稳定、可读的本地路径。"""

        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", repository_full_name).strip("_")
        if not safe_name:
            safe_name = "repository"
        return output_dir / content.week_end / f"{index:02d}_{safe_name}.png"
