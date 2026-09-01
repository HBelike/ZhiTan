from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.config.config_manager import AppConfig
from src.database.database_manager import DatabaseManager
from src.repositories.article_layout_repository import ArticleLayoutRepository
from src.repositories.content_approval_repository import ContentApprovalRepository
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetRecord, MediaAssetRepository
from src.services.article_layout_service import ArticleLayoutService, LOCAL_WECHAT_IMAGE_SCHEME
from src.services.wechat_title_service import normalize_wechat_title
from src.repositories.video_clip_plan_repository import VideoClipPlanRepository
from src.repositories.video_storyboard_repository import VideoStoryboardRepository


class MediaPreviewError(RuntimeError):
    """媒体预览解析失败。"""


class MediaPreviewService:
    """负责构建审核预览数据，并安全解析本地媒体文件。"""

    media_library_asset_types = frozenset({"image", "audio", "video", "video_clip"})

    def __init__(self, config: AppConfig, database_manager: DatabaseManager) -> None:
        self.config = config
        self.database_manager = database_manager

    def build_latest_preview(self) -> dict[str, Any]:
        """构建最新内容的审核预览数据。"""
        content_repository = GeneratedContentRepository(database_manager=self.database_manager)
        content = content_repository.latest_for_preview()
        if content is None:
            return {
                "content": None,
                "media_assets": [],
            }

        return self._build_content_preview_payload(content=content)

    def build_content_preview(self, content_id: int) -> dict[str, Any] | None:
        """构建指定 ``content_id`` 的推文和素材预览。

        历史页面与媒体素材页必须以内容批次为边界；不能通过“最新一条”
        间接读取到其他任务的文章或资源。
        """

        content_repository = GeneratedContentRepository(database_manager=self.database_manager)
        content = content_repository.get_for_preview(content_id=content_id)
        if content is None:
            return None

        return self._build_content_preview_payload(content=content)

    def _build_content_preview_payload(self, content: Any) -> dict[str, Any]:
        """将一条内容快照扩展为完整的只读预览负载。"""

        media_asset_repository = MediaAssetRepository(database_manager=self.database_manager)
        approval_repository = ContentApprovalRepository(database_manager=self.database_manager)
        article_layout_repository = ArticleLayoutRepository(database_manager=self.database_manager)
        storyboard_repository = VideoStoryboardRepository(database_manager=self.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=self.database_manager)

        media_assets = media_asset_repository.list_for_content(content.id)
        latest_approval = approval_repository.latest_for_content(content.id)
        article_layout = article_layout_repository.get_by_content_id(content.id)
        preview_layout = None
        if article_layout is None:
            preview_layout = self._build_transient_article_layout(content=content, media_assets=media_assets)
        storyboard = storyboard_repository.latest_for_content(content.id)
        clip_plans = clip_plan_repository.list_by_content_id(content.id)
        effective_image_prompts = self._build_effective_image_prompts(
            content_id=content.id,
            image_prompts=content.image_prompts,
            media_assets=media_assets,
        )
        return {
            "content": {
                "id": content.id,
                "week_end": content.week_end,
                "title": content.title,
                "wechat_title": normalize_wechat_title(content.title),
                "digest": content.digest,
                "article_markdown": content.article_markdown,
                "video_script": content.video_script,
                "voiceover_text": content.voiceover_text,
                # SummaryTask 保存的是创作意图；审核台和提示词页面优先展示
                # ImageTask 实际提交给 Seedream 的最终 prompt。两者均由下面的
                # 组装函数保留，避免用户误把简短意图当作模型实际输入。
                "image_prompts": effective_image_prompts,
                "status": content.status,
                "created_at": content.created_at,
                "updated_at": content.updated_at,
            },
            "article_layout": preview_layout
            if article_layout is None
            else {
                "id": article_layout.id,
                "content_id": article_layout.content_id,
                "title": article_layout.title,
                "wechat_title": normalize_wechat_title(article_layout.title),
                "digest": article_layout.digest,
                # 已落库的正式排版同样含有仅供 DeliverTask 处理的占位符；
                # 返回给浏览器前转换，但绝不回写数据库或影响公众号发布链路。
                "article_html": self._replace_wechat_image_placeholders(article_layout.article_html),
                "cover_asset_id": article_layout.cover_asset_id,
                "payload": article_layout.payload,
                "status": article_layout.status,
                "created_at": article_layout.created_at,
                "updated_at": article_layout.updated_at,
            },
            "video_storyboard": None
            if storyboard is None
            else {
                "id": storyboard.id,
                "content_id": storyboard.content_id,
                "title": storyboard.title,
                "progressive_script": storyboard.progressive_script,
                "seedance_prompt": storyboard.seedance_prompt,
                "architecture_image_prompts": storyboard.architecture_image_prompts,
                "storyboard": storyboard.storyboard,
                "status": storyboard.status,
                "created_at": storyboard.created_at,
                "updated_at": storyboard.updated_at,
            },
            "video_clip_plans": [
                {
                    "id": clip_plan.id,
                    "content_id": clip_plan.content_id,
                    "storyboard_id": clip_plan.storyboard_id,
                    "clip_index": clip_plan.clip_index,
                    "source_scene_index": clip_plan.source_scene_index,
                    "clip_title": clip_plan.clip_title,
                    "repository_full_name": clip_plan.repository_full_name,
                    "planned_duration_seconds": clip_plan.planned_duration_seconds,
                    "output_start_second": clip_plan.output_start_second,
                    "output_end_second": clip_plan.output_end_second,
                    "narration": clip_plan.narration,
                    "subtitle": clip_plan.subtitle,
                    "visual_design": clip_plan.visual_design,
                    "motion_design": clip_plan.motion_design,
                    "transition_to_next": clip_plan.transition_to_next,
                    "seedance_prompt": clip_plan.seedance_prompt,
                    "reference_image_asset_ids": clip_plan.reference_image_asset_ids,
                    "provider": clip_plan.provider,
                    "status": clip_plan.status,
                    "metadata": clip_plan.metadata,
                    "created_at": clip_plan.created_at,
                    "updated_at": clip_plan.updated_at,
                }
                for clip_plan in clip_plans
            ],
            "approval": None
            if latest_approval is None
            else {
                "id": latest_approval.id,
                "content_id": latest_approval.content_id,
                "decision": latest_approval.decision,
                "operator": latest_approval.operator,
                "comment": latest_approval.comment,
                "created_at": latest_approval.created_at,
            },
            "media_assets": [self._asset_to_preview_payload(asset) for asset in media_assets],
        }

    def _build_effective_image_prompts(
        self,
        content_id: int,
        image_prompts: Any,
        media_assets: list[MediaAssetRecord],
    ) -> list[dict[str, Any]]:
        """构建审核页可直接阅读的项目图片 prompt。

        SummaryTask 的 ``image_prompts`` 是文章创作阶段的简短视觉意图，而
        ImageTask 会使用 ``ImagePromptDesignService`` 将其扩写为真实提交给
        Seedream 的完整 prompt。此处只在同一个 ``content_id`` 中按仓库名称
        关联最终图片资产，绝不向前一个或后一个内容批次借用素材。

        如果没有找到经过 ``ImagePromptDesignService`` 处理的 Seedream 图片，
        则仍返回原始意图，并把 GitHub/本地兜底的来源明确标为 fallback，避免
        前端错误显示为 ``ark_final``。
        """

        if not isinstance(image_prompts, list):
            return []

        deterministic_assets: dict[tuple[str, int], MediaAssetRecord] = {}
        unindexed_deterministic_assets: dict[str, list[MediaAssetRecord]] = {}
        seedream_assets: dict[str, MediaAssetRecord] = {}
        fallback_assets: dict[str, MediaAssetRecord] = {}
        # 后创建的资产优先。刷新图片时旧图会被标记为 replaced，因此这里也
        # 显式排除 replaced/failed，避免审核台回填到历史失败提示词。
        for asset in sorted(media_assets, key=lambda item: item.id, reverse=True):
            if asset.content_id != content_id or asset.asset_type != "image":
                continue
            if asset.status in {"failed", "replaced"}:
                continue

            metadata = self._metadata_dict(asset.metadata)
            repository_full_name = self._repository_key(metadata.get("repository_full_name"))
            if not repository_full_name:
                continue

            if self._is_deterministic_rendered_asset(asset=asset, metadata=metadata):
                prompt_index = metadata.get("prompt_index")
                if (
                    isinstance(prompt_index, int)
                    and not isinstance(prompt_index, bool)
                    and prompt_index > 0
                ):
                    deterministic_assets.setdefault(
                        (repository_full_name, prompt_index), asset
                    )
                else:
                    unindexed_deterministic_assets.setdefault(
                        repository_full_name, []
                    ).append(asset)
                continue

            if self._is_seedream_designed_asset(asset=asset, metadata=metadata):
                seedream_assets.setdefault(repository_full_name, asset)
                continue

            fallback_assets.setdefault(repository_full_name, asset)

        preview_prompts: list[dict[str, Any]] = []
        for prompt_index, raw_item in enumerate(image_prompts, start=1):
            if not isinstance(raw_item, dict):
                continue

            item = dict(raw_item)
            repository_full_name = str(item.get("repository_full_name", "")).strip()
            repository_key = self._repository_key(repository_full_name)
            original_prompt = str(item.get("prompt", "")).strip()
            raw_prompt = str(item.get("raw_prompt", "")).strip() or original_prompt
            deterministic_asset = deterministic_assets.get((repository_key, prompt_index))
            if deterministic_asset is None:
                unindexed_assets = unindexed_deterministic_assets.get(
                    repository_key, []
                )
                if len(unindexed_assets) == 1:
                    deterministic_asset = unindexed_assets[0]
            seedream_asset = seedream_assets.get(repository_key)
            fallback_asset = fallback_assets.get(repository_key)

            # 即使历史旧数据尚未保存 visual/video brief，也将字段稳定地返回；
            # 这样前端不需要因为一次历史任务缺少新字段而走异常分支。
            item["raw_prompt"] = raw_prompt
            item["visual_brief"] = item.get("visual_brief")
            item["video_brief"] = item.get("video_brief")
            item["asset_id"] = item.get("asset_id")

            if deterministic_asset is not None:
                metadata = self._metadata_dict(deterministic_asset.metadata)
                visual_spec = self._metadata_dict(metadata.get("visual_spec"))
                effective_prompt = (
                    str(metadata.get("prompt", "")).strip()
                    or str(visual_spec.get("purpose", "")).strip()
                    or original_prompt
                )
                item["prompt"] = effective_prompt
                item["effective_prompt"] = effective_prompt
                item["prompt_stage"] = "deterministic_rendered"
                item["prompt_designed_by"] = "ArticleVisualTemplateService"
                item["visual_spec"] = visual_spec
                item["figure_role"] = str(
                    metadata.get("figure_role") or visual_spec.get("figure_role") or ""
                ).strip()
                item["template_version"] = metadata.get("template_version")
                item["renderer_version"] = metadata.get("renderer_version")
                item["render_key"] = metadata.get("render_key")
                item["validation_result"] = self._metadata_dict(
                    metadata.get("validation_result")
                )
                item["asset_id"] = deterministic_asset.id
                item["asset_provider"] = deterministic_asset.provider
                item["asset_status"] = deterministic_asset.status
            elif seedream_asset is not None:
                metadata = self._metadata_dict(seedream_asset.metadata)
                effective_prompt = str(metadata.get("prompt", "")).strip()
                # marker 已由 _is_seedream_designed_asset 校验；此处仍保留回退，
                # 防御旧资产 metadata 结构不完整导致的空白展示。
                if effective_prompt:
                    item["prompt"] = effective_prompt
                    item["effective_prompt"] = effective_prompt
                else:
                    item["effective_prompt"] = original_prompt
                item["prompt_stage"] = "ark_final"
                item["asset_id"] = seedream_asset.id
                item["asset_provider"] = seedream_asset.provider
                item["asset_status"] = seedream_asset.status
                item["prompt_designed_by"] = "ImagePromptDesignService"
            elif fallback_asset is not None:
                metadata = self._metadata_dict(fallback_asset.metadata)
                item["effective_prompt"] = original_prompt
                item["prompt_stage"] = self._fallback_prompt_stage(
                    asset=fallback_asset,
                    metadata=metadata,
                    existing_stage=item.get("prompt_stage"),
                )
                item["asset_id"] = fallback_asset.id
                item["asset_provider"] = fallback_asset.provider
                item["asset_status"] = fallback_asset.status
            else:
                item["effective_prompt"] = original_prompt
                item["prompt_stage"] = self._default_prompt_stage(item)

            preview_prompts.append(item)

        return preview_prompts

    @staticmethod
    def _metadata_dict(value: Any) -> dict[str, Any]:
        """把历史异常 metadata 安全收敛为字典。"""

        return value if isinstance(value, dict) else {}

    @staticmethod
    def _repository_key(value: Any) -> str:
        """生成不区分大小写的仓库关联键。"""

        return str(value or "").strip().casefold()

    def _is_seedream_designed_asset(self, asset: MediaAssetRecord, metadata: dict[str, Any]) -> bool:
        """判断资产是否是本系统实际提交给 Seedream 的最终图片。"""

        provider = str(asset.provider or "").strip().casefold()
        designed_by = str(metadata.get("prompt_designed_by", "")).strip()
        prompt = str(metadata.get("prompt", "")).strip()
        return (
            designed_by == "ImagePromptDesignService"
            and bool(prompt)
            and "seedream" in provider
        )

    @staticmethod
    def _is_deterministic_rendered_asset(
        asset: MediaAssetRecord, metadata: dict[str, Any]
    ) -> bool:
        """识别已通过确定性模板渲染的图片资产。"""

        provider = str(asset.provider or "").strip().casefold()
        render_key = str(metadata.get("render_key", "")).strip()
        return provider == "gotenberg_html" and bool(render_key)

    def _fallback_prompt_stage(
        self,
        asset: MediaAssetRecord,
        metadata: dict[str, Any],
        existing_stage: Any,
    ) -> str:
        """标记非 Seedream 素材的真实来源，禁止伪装成最终 Ark Prompt。"""

        provider = str(asset.provider or "").strip().casefold()
        prompt_source = str(metadata.get("prompt_source", "")).strip().casefold()
        if "github" in provider or "github" in prompt_source:
            return "github_fallback"
        if "local" in provider or "local" in prompt_source:
            return "local_fallback"
        normalized_stage = str(existing_stage or "").strip()
        if normalized_stage and normalized_stage != "ark_final":
            return normalized_stage
        return "asset_fallback"

    @staticmethod
    def _default_prompt_stage(item: dict[str, Any]) -> str:
        """为没有实际图片资产的历史内容提供兼容的来源阶段。"""

        existing_stage = str(item.get("prompt_stage", "")).strip()
        if existing_stage:
            return existing_stage
        prompt_source = str(item.get("prompt_source", "")).strip().casefold()
        if "storyboard" in prompt_source:
            return "storyboard"
        return "summary"

    def build_execution_history(self, limit: int = 50) -> dict[str, Any]:
        """构建以 ``content_id`` 为边界的执行历史索引。

        每一项只保留用户要求长期查看的两类内容：推文快照摘要和同一
        ``content_id`` 下的素材统计。完整正文与资源文件按需通过详情接口
        读取，避免历史列表把全部文章和媒体一次性塞回前端。
        """

        normalized_limit = min(max(int(limit), 1), 100)
        content_repository = GeneratedContentRepository(database_manager=self.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=self.database_manager)
        content_items = content_repository.list_recent_for_history(limit=normalized_limit)
        summaries = media_asset_repository.summarize_by_content_ids([item.id for item in content_items])

        default_summary = {
            "total_asset_count": 0,
            "image_count": 0,
            "audio_count": 0,
            "video_count": 0,
            "failed_asset_count": 0,
        }
        return {
            "items": [
                {
                    "content_id": item.id,
                    "week_end": item.week_end,
                    "title": item.title,
                    "digest": item.digest,
                    "status": item.status,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "article": {
                        "available": True,
                        "source": "generated_content",
                    },
                    "assets": summaries.get(item.id, default_summary),
                }
                for item in content_items
            ],
            "summary": {
                "total_content_count": len(content_items),
            },
        }

    def build_media_library(self, limit: int = 300, content_id: int | None = None) -> dict[str, Any]:
        """构建工作台媒体资源库，可按 ``content_id`` 缩小到一个执行批次。

        未传 ``content_id`` 时保留原有跨内容资源库能力；传入时只读取该内容
        的图片、音频、视频和待生成的视频分片，避免把不同任务的素材混在一起。
        """

        normalized_limit = min(max(int(limit), 1), 500)
        media_asset_repository = MediaAssetRepository(database_manager=self.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=self.database_manager)

        normalized_content_id = None if content_id is None else int(content_id)
        if normalized_content_id is not None and normalized_content_id <= 0:
            raise ValueError("content_id 必须大于 0")

        if normalized_content_id is None:
            media_assets = media_asset_repository.list_recent(limit=normalized_limit)
            clip_plans = clip_plan_repository.list_recent(limit=normalized_limit)
        else:
            media_assets = media_asset_repository.list_for_content(normalized_content_id)
            clip_plans = clip_plan_repository.list_by_content_id(normalized_content_id)

        clip_plan_ids_with_asset = self._clip_plan_ids_with_asset(media_assets)
        pending_video_clips = [
            self._video_clip_plan_to_library_payload(clip_plan)
            for clip_plan in clip_plans
            if clip_plan.id not in clip_plan_ids_with_asset
        ]

        library_assets = self._media_library_assets(media_assets)
        playable_video_types = {"video", "video_clip"}
        return {
            "content_id": normalized_content_id,
            "scope": "content" if normalized_content_id is not None else "all",
            "items": [self._asset_to_preview_payload(asset) for asset in library_assets],
            "pending_video_clips": pending_video_clips,
            "summary": {
                "total_asset_count": len(library_assets),
                "image_count": sum(asset.asset_type == "image" for asset in library_assets),
                "audio_count": sum(asset.asset_type == "audio" for asset in library_assets),
                "video_count": sum(asset.asset_type in playable_video_types for asset in library_assets),
                "pending_video_count": len(pending_video_clips),
                "failed_asset_count": sum(asset.status == "failed" for asset in library_assets),
                "failed_video_plan_count": sum(item["status"] == "failed" for item in pending_video_clips),
            },
        }

    @classmethod
    def _media_library_assets(cls, media_assets: list[MediaAssetRecord]) -> list[MediaAssetRecord]:
        """只向素材库暴露用户能够直接消费的图片、音频和视频文件。"""

        return [asset for asset in media_assets if asset.asset_type in cls.media_library_asset_types]

    def _clip_plan_ids_with_asset(self, media_assets: list[MediaAssetRecord]) -> set[int]:
        """找出已经创建任务或已下载文件的视频分片计划。"""

        clip_plan_ids: set[int] = set()
        for asset in media_assets:
            raw_clip_plan_id = asset.metadata.get("clip_plan_id")
            try:
                clip_plan_id = int(raw_clip_plan_id)
            except (TypeError, ValueError):
                continue
            if clip_plan_id > 0:
                clip_plan_ids.add(clip_plan_id)
        return clip_plan_ids

    def _video_clip_plan_to_library_payload(self, clip_plan: Any) -> dict[str, Any]:
        """将没有对应媒体文件的视频计划转换为资源库状态项。"""

        return {
            "id": clip_plan.id,
            "content_id": clip_plan.content_id,
            "clip_index": clip_plan.clip_index,
            "clip_title": clip_plan.clip_title,
            "planned_duration_seconds": clip_plan.planned_duration_seconds,
            "provider": clip_plan.provider,
            "status": clip_plan.status,
            "metadata": self._safe_metadata(clip_plan.metadata),
        }

    def _build_transient_article_layout(
        self,
        content: Any,
        media_assets: list[MediaAssetRecord],
    ) -> dict[str, Any] | None:
        """为审核台生成不落库的只读排版预览。

        ArticleLayoutTask 只处理审核通过后的正式排版稿；但审核台需要在通过前看到图文
        一对一的版式效果。这里复用同一个 ArticleLayoutService，只返回临时 payload，
        不修改 generated_contents、article_layouts 或 media_assets。
        """
        try:
            layout_service = ArticleLayoutService()
            build_result = layout_service.build(content=content, media_assets=media_assets)
            payload = layout_service.build_payload(
                content=content,
                result=build_result,
                media_assets=media_assets,
            )
        except (TypeError, ValueError, RuntimeError):
            return None

        return {
            "id": None,
            "content_id": content.id,
            "title": content.title,
            "wechat_title": normalize_wechat_title(content.title),
            "digest": content.digest,
            # 正式公众号排版保留 wechat-image-asset:// 占位符，后续由 DeliverTask 上传并替换；
            # 审核台则必须使用本地媒体接口，避免浏览器尝试加载一个非 HTTP 协议而显示破图。
            "article_html": self._replace_wechat_image_placeholders(build_result.article_html),
            "cover_asset_id": build_result.cover_asset_id,
            "payload": {
                **payload,
                "preview_only": True,
            },
            "status": "preview",
            "created_at": content.created_at,
            "updated_at": content.updated_at,
        }

    def _replace_wechat_image_placeholders(self, article_html: str) -> str:
        """把仅供正式发布阶段使用的图片占位符替换为审核台可访问的本地 URL。"""

        prefix = self.config.preview_media_route_prefix.rstrip("/")
        pattern = re.compile(
            rf'(?P<attribute>src=["\']){re.escape(LOCAL_WECHAT_IMAGE_SCHEME)}://(?P<asset_id>\d+)(?P<quote>["\'])'
        )

        def replace(match: re.Match[str]) -> str:
            return f'{match.group("attribute")}{prefix}/{match.group("asset_id")}/file{match.group("quote")}'

        return pattern.sub(replace, article_html)

    def resolve_local_media_path(self, asset_id: int) -> tuple[Path, str | None]:
        """安全解析本地媒体路径，供 Web API 返回文件。"""
        media_asset_repository = MediaAssetRepository(database_manager=self.database_manager)
        asset = media_asset_repository.get_by_id(asset_id)
        if asset is None:
            raise MediaPreviewError(f"媒体资产不存在：asset_id={asset_id}")

        resolved_path = self._safe_resolve_local_path(asset)
        if not resolved_path.exists():
            raise MediaPreviewError(f"媒体文件不存在：asset_id={asset_id}")
        if not resolved_path.is_file():
            raise MediaPreviewError(f"媒体路径不是文件：asset_id={asset_id}")
        return resolved_path, asset.mime_type

    def _asset_to_preview_payload(self, asset: MediaAssetRecord) -> dict[str, Any]:
        """把媒体资产转换为不会泄露本地绝对路径的预览数据。"""
        remote_url = self._public_remote_url(asset)
        local_url = self._local_preview_url(asset)
        return {
            "id": asset.id,
            "content_id": asset.content_id,
            "asset_type": asset.asset_type,
            "provider": asset.provider,
            "mime_type": asset.mime_type,
            "status": asset.status,
            "remote_url": remote_url,
            "local_url": local_url,
            # 已保存到 outputs 的文件优先走本站预览接口，避免模型供应商的临时 URL
            # 过期后让工作台误以为媒体文件不可查看。
            "preview_url": local_url or remote_url,
            "metadata": self._safe_metadata(asset.metadata),
        }

    def _public_remote_url(self, asset: MediaAssetRecord) -> str | None:
        """读取公网 URL。"""
        remote_url = str(asset.metadata.get("remote_url", "")).strip()
        if remote_url.startswith("http://") or remote_url.startswith("https://"):
            return remote_url
        return None

    def _local_preview_url(self, asset: MediaAssetRecord) -> str | None:
        """生成本地预览 URL。"""
        if not self.config.preview_expose_local_media:
            return None

        try:
            resolved_path = self._safe_resolve_local_path(asset)
        except MediaPreviewError:
            return None

        if not resolved_path.exists() or not resolved_path.is_file():
            return None

        prefix = self.config.preview_media_route_prefix
        return f"{prefix}/{asset.id}/file"

    def _safe_resolve_local_path(self, asset: MediaAssetRecord) -> Path:
        """只允许解析项目 outputs 范围内的本地媒体文件。"""
        raw_path = Path(asset.path)
        if str(asset.path).startswith("http://") or str(asset.path).startswith("https://"):
            raise MediaPreviewError("远程媒体不需要本地路径解析")

        candidate = raw_path if raw_path.is_absolute() else self.config.project_root / raw_path
        resolved_candidate = candidate.resolve()
        allowed_roots = [
            (self.config.project_root / "outputs").resolve(),
            self.config.storage_local_upload_dir.resolve(),
        ]

        for allowed_root in allowed_roots:
            try:
                resolved_candidate.relative_to(allowed_root)
                return resolved_candidate
            except ValueError:
                continue

        raise MediaPreviewError("媒体路径不在允许的 outputs 目录内")

    def _safe_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """过滤不适合给前端展示的大字段。"""
        blocked_keys = {"raw_response", "data", "b64_json"}
        # 审核/提示词页面需要查看真正提交给图像模型的完整文案；其余 metadata
        # 仍维持较短上限，避免把供应商响应或无关大字段塞入预览接口。
        prompt_keys = {"prompt", "raw_prompt", "effective_prompt"}
        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            if key in blocked_keys:
                continue
            max_length = 3200 if key in prompt_keys else 500
            if isinstance(value, str) and len(value) > max_length:
                safe[key] = value[:max_length] + "..."
                continue
            safe[key] = value
        return safe
