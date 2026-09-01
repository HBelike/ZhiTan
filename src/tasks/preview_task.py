from __future__ import annotations

import json
from typing import Any

from src.services.media_preview_service import MediaPreviewService
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class PreviewTask(BaseTask):
    """负责生成最新内容的审核预览 JSON。"""

    task_name = "PreviewTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """构建预览数据并写入 outputs/preview/latest.json。"""
        if not context.config.preview_enabled:
            self.logger.info("preview.enabled=false，PreviewTask 跳过")
            return {
                "enabled": False,
                "skipped": True,
                "skip_reason": "preview.enabled=false",
            }

        service = MediaPreviewService(
            config=context.config,
            database_manager=context.database_manager,
        )
        preview_payload = service.build_latest_preview()
        content = preview_payload.get("content")
        media_assets = preview_payload.get("media_assets", [])

        context.config.preview_output_dir.mkdir(parents=True, exist_ok=True)
        output_path = context.config.preview_output_dir / "latest.json"
        output_path.write_text(
            json.dumps(preview_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.logger.info("审核预览 JSON 已生成：%s", output_path)
        return {
            "enabled": True,
            "output_path": str(output_path),
            "content_id": None if content is None else content.get("id"),
            "media_asset_count": len(media_assets) if isinstance(media_assets, list) else 0,
            "skipped": False,
        }
