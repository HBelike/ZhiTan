from __future__ import annotations

from typing import Any

from src.repositories.article_layout_repository import ArticleLayoutRepository
from src.repositories.draft_record_repository import DraftRecordInput, DraftRecordRepository
from src.repositories.media_asset_repository import MediaAssetRepository
from src.services.wechat_draft_creation_service import WechatDraftCreationService
from src.services.wechat_draft_preflight_service import WechatDraftPreflightService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class DeliverTask(BaseTask):
    """负责把已排版内容推进到微信公众号草稿创建阶段。"""

    task_name = "DeliverTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """执行微信草稿创建前置检查，并把检查结果写入 draft_records。"""
        layout_repository = ArticleLayoutRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        draft_repository = DraftRecordRepository(database_manager=context.database_manager)
        draft_creation_service = WechatDraftCreationService()
        preflight_service = WechatDraftPreflightService()

        layout = layout_repository.latest_ready_for_delivery()
        if layout is None:
            self.logger.info("没有 ready 状态的公众号排版稿，DeliverTask 跳过")
            return {
                "skipped": True,
                "skip_reason": "no_ready_article_layout",
                "layout_id": None,
                "draft_record_id": None,
                "network_called": False,
            }

        media_assets = media_asset_repository.list_for_content(layout.content_id)
        preflight = preflight_service.build(
            config=context.config,
            layout=layout,
            media_assets=media_assets,
        )
        draft = draft_repository.upsert(
            DraftRecordInput(
                layout_id=layout.id,
                status=preflight.status,
                response=preflight.payload,
                error_message=preflight.error_message,
            )
        )

        if preflight.can_call_wechat_api:
            self.logger.info("微信公众号草稿前置检查通过：layout_id=%s draft_record_id=%s", layout.id, draft.id)
        else:
            self.logger.warning(
                "微信公众号草稿前置检查未通过：layout_id=%s draft_record_id=%s missing=%s",
                layout.id,
                draft.id,
                preflight.missing_requirements,
            )

            return {
                "skipped": True,
                "skip_reason": "preflight_blocked",
                "layout_id": layout.id,
                "content_id": layout.content_id,
                "draft_record_id": draft.id,
                "draft_status": draft.status,
                "missing_requirements": preflight.missing_requirements,
                "warning_count": len(preflight.warnings),
                "can_call_wechat_api": False,
                "network_called": False,
            }

        try:
            creation_result = draft_creation_service.create_draft(
                config=context.config,
                layout=layout,
                media_assets=media_assets,
            )
        except Exception as exc:
            failed_draft = draft_repository.upsert(
                DraftRecordInput(
                    layout_id=layout.id,
                    status="failed",
                    response={
                        "preflight": preflight.payload,
                        "error_type": exc.__class__.__name__,
                    },
                    error_message=str(exc) or exc.__class__.__name__,
                )
            )
            self.logger.exception("微信公众号草稿创建失败：layout_id=%s draft_record_id=%s", layout.id, failed_draft.id)
            raise

        created_draft = draft_repository.upsert(
            DraftRecordInput(
                layout_id=layout.id,
                status="draft_created",
                response={
                    "preflight": preflight.payload,
                    "creation": creation_result.response,
                },
                error_message=None,
                wechat_draft_id=creation_result.draft_media_id,
                wechat_media_id=creation_result.cover_media_id,
            )
        )
        self.logger.info(
            "微信公众号草稿创建成功：layout_id=%s draft_record_id=%s draft_media_id=%s",
            layout.id,
            created_draft.id,
            creation_result.draft_media_id,
        )
        return {
            "skipped": False,
            "skip_reason": None,
            "layout_id": layout.id,
            "content_id": layout.content_id,
            "draft_record_id": created_draft.id,
            "draft_status": created_draft.status,
            "wechat_draft_id": creation_result.draft_media_id,
            "cover_media_id": creation_result.cover_media_id,
            "video_media_id": creation_result.video_media_id,
            "uploaded_image_count": creation_result.uploaded_image_count,
            "final_article_html_length": creation_result.final_article_html_length,
            "missing_requirements": [],
            "warning_count": len(preflight.warnings),
            "can_call_wechat_api": True,
            "network_called": True,
        }
