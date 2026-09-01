from __future__ import annotations

import unittest

from src.repositories.media_asset_repository import MediaAssetRecord
from src.services.media_preview_service import MediaPreviewService


def build_asset(asset_id: int, asset_type: str) -> MediaAssetRecord:
    return MediaAssetRecord(
        id=asset_id,
        content_id=18,
        asset_type=asset_type,
        provider="test",
        path=f"outputs/{asset_id}",
        mime_type="application/octet-stream",
        status="created",
        metadata={},
    )


class MediaLibraryBoundaryTests(unittest.TestCase):
    def test_only_user_consumable_media_enters_the_media_grid(self) -> None:
        assets = [
            build_asset(1, "image"),
            build_asset(2, "audio"),
            build_asset(3, "video"),
            build_asset(4, "video_clip"),
            build_asset(5, "video_clip_task"),
            build_asset(6, "video_quality_report"),
            build_asset(7, "video_narration_timeline"),
        ]

        visible_assets = MediaPreviewService._media_library_assets(assets)

        self.assertEqual(
            ["image", "audio", "video", "video_clip"],
            [asset.asset_type for asset in visible_assets],
        )


if __name__ == "__main__":
    unittest.main()
