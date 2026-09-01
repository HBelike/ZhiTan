from __future__ import annotations

from typing import Any

from src.repositories.article_layout_repository import ArticleLayoutInput, ArticleLayoutRepository
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetRepository
from src.services.article_layout_service import ArticleLayoutService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class ArticleLayoutTask(BaseTask):
    """负责把人工审核通过的内容转换成微信公众号排版稿。"""

    task_name = "ArticleLayoutTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取已批准内容，生成 HTML，并写入 article_layouts 表。"""
        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        layout_repository = ArticleLayoutRepository(database_manager=context.database_manager)
        layout_service = ArticleLayoutService()

        content = content_repository.latest_approved_for_layout()
        if content is None:
            self.logger.info("没有已通过人工审核的内容，ArticleLayoutTask 跳过")
            return {
                "skipped": True,
                "skip_reason": "no_approved_content",
                "layout_id": None,
                "content_id": None,
            }

        media_assets = media_asset_repository.list_for_content(content.id)
        build_result = layout_service.build(content=content, media_assets=media_assets)
        payload = layout_service.build_payload(
            content=content,
            result=build_result,
            media_assets=media_assets,
        )

        layout = layout_repository.upsert(
            ArticleLayoutInput(
                content_id=content.id,
                title=content.title,
                digest=content.digest,
                article_html=build_result.article_html,
                cover_asset_id=build_result.cover_asset_id,
                payload=payload,
                status="ready",
            )
        )

        self.logger.info(
            "公众号排版稿已生成：content_id=%s layout_id=%s embedded_images=%s missing_images=%s",
            content.id,
            layout.id,
            build_result.embedded_image_count,
            build_result.missing_image_count,
        )
        return {
            "skipped": False,
            "content_id": content.id,
            "layout_id": layout.id,
            "status": layout.status,
            "cover_asset_id": layout.cover_asset_id,
            "html_length": len(layout.article_html),
            "expected_image_count": build_result.expected_image_count,
            "embedded_image_count": build_result.embedded_image_count,
            "missing_image_count": build_result.missing_image_count,
            "style_version": build_result.style_version,
        }
