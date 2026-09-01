from __future__ import annotations

from typing import Any

from src.providers.storage_provider import StorageProviderError, create_storage_provider
from src.repositories.media_asset_repository import MediaAssetRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class StorageTask(BaseTask):
    """负责把本地媒体资产上传到可被外部服务访问的位置。"""

    task_name = "StorageTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """扫描待上传媒体资产，上传后把 remote_url 合并回 metadata。"""
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        enabled_asset_types = self._enabled_asset_types(context.config)
        candidates = media_asset_repository.list_upload_candidates(enabled_asset_types)

        if not candidates:
            self.logger.info("没有待上传媒体资产，StorageTask 跳过")
            return {
                "provider": context.config.storage_provider,
                "candidate_count": 0,
                "uploaded_count": 0,
                "failed_count": 0,
                "skipped": True,
                "skip_reason": "没有待上传媒体资产",
                "network_called": False,
            }

        try:
            provider = create_storage_provider(config=context.config)
        except StorageProviderError as exc:
            if context.config.storage_skip_when_unconfigured:
                self.logger.warning("Storage Provider 不可用，StorageTask 跳过：%s", exc)
                return {
                    "provider": context.config.storage_provider,
                    "candidate_count": len(candidates),
                    "uploaded_count": 0,
                    "failed_count": 0,
                    "skipped": True,
                    "skip_reason": str(exc),
                    "network_called": False,
                }
            raise

        if not provider.can_upload():
            if context.config.storage_skip_when_unconfigured:
                reason = provider.unavailable_reason()
                self.logger.warning("Storage Provider 未配置完整，StorageTask 跳过：%s", reason)
                return {
                    "provider": context.config.storage_provider,
                    "candidate_count": len(candidates),
                    "uploaded_count": 0,
                    "failed_count": 0,
                    "skipped": True,
                    "skip_reason": reason,
                    "network_called": False,
                }
            raise RuntimeError(provider.unavailable_reason())

        uploaded_assets = []
        failed_assets = []
        for asset in candidates:
            try:
                upload_result = provider.upload(asset)
                updated_asset = media_asset_repository.merge_metadata_and_status(
                    asset_id=asset.id,
                    status="uploaded",
                    metadata_patch={
                        "remote_url": upload_result.remote_url,
                        "object_key": upload_result.object_key,
                        "storage_provider": upload_result.storage_provider,
                        **upload_result.metadata,
                    },
                )
                uploaded_assets.append(
                    {
                        "asset_id": updated_asset.id,
                        "asset_type": updated_asset.asset_type,
                        "remote_url": updated_asset.metadata.get("remote_url"),
                    }
                )
                self.logger.info("媒体资产上传完成：asset_id=%s remote_url=%s", updated_asset.id, updated_asset.metadata.get("remote_url"))
            except Exception as exc:
                failed_assets.append(
                    {
                        "asset_id": asset.id,
                        "asset_type": asset.asset_type,
                        "error": str(exc),
                    }
                )
                self.logger.exception("媒体资产上传失败：asset_id=%s", asset.id)

        return {
            "provider": context.config.storage_provider,
            "candidate_count": len(candidates),
            "uploaded_count": len(uploaded_assets),
            "failed_count": len(failed_assets),
            "uploaded_assets": uploaded_assets,
            "failed_assets": failed_assets,
            "skipped": False,
            "network_called": context.config.storage_provider != "local",
        }

    @staticmethod
    def _enabled_asset_types(config: Any) -> list[str]:
        """关闭媒体通道后仅保留仍允许上传的素材类型。"""

        enabled_types: list[str] = []
        for asset_type in config.storage_asset_types:
            if asset_type == "audio" and not config.audio_enabled:
                continue
            if asset_type == "video" and not config.video_submit_enabled:
                continue
            enabled_types.append(asset_type)
        return enabled_types
