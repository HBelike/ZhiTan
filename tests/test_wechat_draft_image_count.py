from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config.config_manager import AppConfig
from src.repositories.article_layout_repository import ArticleLayoutRecord
from src.repositories.generated_content_repository import GeneratedContentForLayout
from src.repositories.media_asset_repository import MediaAssetRecord
from src.services.article_layout_service import (
    ArticleLayoutBuildResult,
    ArticleLayoutService,
    resolve_expected_project_image_count,
)
from src.services.wechat_draft_preflight_service import WechatDraftPreflightService


def _config(project_root: Path) -> AppConfig:
    return AppConfig(
        project_root=project_root,
        config_path=project_root / "config" / "app.yaml",
        raw={
            "wechat": {
                "enabled": True,
                "require_cover_asset": True,
                "require_public_article_images": False,
                "require_local_uploadable_images": True,
                "require_video_asset": False,
                "require_local_uploadable_video": False,
            }
        },
    )


def _layout(expected_image_count: int | None) -> ArticleLayoutRecord:
    layout_stats: dict[str, int] = {
        "embedded_image_count": 0,
        "missing_image_count": 1,
    }
    if expected_image_count is not None:
        layout_stats["expected_image_count"] = expected_image_count
    return ArticleLayoutRecord(
        id=1,
        content_id=17,
        title="Top1",
        digest="digest",
        article_html="<section>content</section>",
        cover_asset_id=101,
        payload={"layout_stats": layout_stats},
        status="ready",
        created_at="2026-08-20T00:00:00Z",
        updated_at="2026-08-20T00:00:00Z",
    )


def _image_asset(
    project_root: Path,
    *,
    index: int = 1,
    repository_full_name: str = "owner/repo",
) -> MediaAssetRecord:
    image_path = project_root / "outputs" / "images" / f"top{index}.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image")
    return MediaAssetRecord(
        id=100 + index,
        content_id=17,
        asset_type="image",
        provider="seedream",
        path=str(image_path),
        mime_type="image/png",
        status="created",
        metadata={"repository_full_name": repository_full_name},
    )


class WechatDraftImageCountTest(unittest.TestCase):
    def test_layout_places_project_image_after_overview_before_teaching_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project_root = Path(temporary_dir)
            content = GeneratedContentForLayout(
                id=17,
                week_end="2026-08-14",
                title="Top 1",
                digest="digest",
                article_markdown=(
                    "### Top 1 项目拆解\n\n"
                    "#### 项目 1：owner/repo\n\n"
                    "本周新增 10 stars，总 stars 为 100。它用于验证排版，适合单项目流程。\n\n"
                    "**技术特点**\n\n模块职责清楚。\n\n"
                    "**机制拆解**\n\n数据从入口流向结果。\n\n"
                    "**工程启发**\n\n先验证边界。"
                ),
                video_script="",
                voiceover_text="",
                image_prompts=[
                    {
                        "repository_full_name": "owner/repo",
                        "rank": 1,
                        "summary_text": "项目架构与主数据流。",
                    }
                ],
                status="approved",
                created_at="2026-08-14T00:00:00Z",
                updated_at="2026-08-14T00:00:00Z",
            )

            result = ArticleLayoutService().build(
                content=content,
                media_assets=[_image_asset(project_root)],
            )

        overview_index = result.article_html.index("本周新增 10 stars")
        image_index = result.article_html.index('data-asset-id="101"')
        feature_index = result.article_html.index("技术特点")
        self.assertLess(overview_index, image_index)
        self.assertLess(image_index, feature_index)

    def test_layout_embeds_all_six_configured_project_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project_root = Path(temporary_dir)
            repositories = [f"owner/repo-{index}" for index in range(1, 7)]
            article_blocks = ["### Top 6 项目拆解"]
            image_prompts = []
            image_assets = []
            for index, repository_full_name in enumerate(repositories, start=1):
                article_blocks.extend(
                    [
                        f"#### 项目 {index}：{repository_full_name}",
                        f"项目 {index} 本周新增 {index} stars，总 stars 为 {index * 100}。",
                        "**技术特点**",
                        "模块职责清楚。",
                        "**机制拆解**",
                        "数据从入口流向结果。",
                        "**工程启发**",
                        "先验证边界。",
                    ]
                )
                image_prompts.append(
                    {
                        "repository_full_name": repository_full_name,
                        "rank": index,
                        "summary_text": f"项目 {index} 的架构与主数据流。",
                    }
                )
                image_assets.append(
                    _image_asset(
                        project_root,
                        index=index,
                        repository_full_name=repository_full_name,
                    )
                )

            content = GeneratedContentForLayout(
                id=17,
                week_end="2026-08-14",
                title="Top 6",
                digest="digest",
                article_markdown="\n\n".join(article_blocks),
                video_script="",
                voiceover_text="",
                image_prompts=image_prompts,
                status="approved",
                created_at="2026-08-14T00:00:00Z",
                updated_at="2026-08-14T00:00:00Z",
            )

            result = ArticleLayoutService().build(content=content, media_assets=image_assets)

        self.assertEqual(result.expected_image_count, 6)
        for index in range(1, 7):
            overview_index = result.article_html.index(f"项目 {index} 本周新增 {index} stars")
            image_index = result.article_html.index(f'data-asset-id="{100 + index}"')
            feature_index = result.article_html.index("技术特点", overview_index)
            self.assertLess(overview_index, image_index)
            self.assertLess(image_index, feature_index)

    def test_top1_layout_accepts_one_uploadable_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir, patch.dict(
            os.environ,
            {"WECHAT_APP_ID": "configured", "WECHAT_APP_SECRET": "configured"},
        ):
            project_root = Path(temporary_dir)
            result = WechatDraftPreflightService().build(
                config=_config(project_root),
                layout=_layout(expected_image_count=1),
                media_assets=[_image_asset(project_root)],
            )

        self.assertTrue(result.can_call_wechat_api)
        self.assertEqual(result.missing_requirements, [])
        self.assertEqual(result.payload["assets"]["expected_image_count"], 1)

    def test_preflight_reports_actual_expected_image_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir, patch.dict(
            os.environ,
            {"WECHAT_APP_ID": "configured", "WECHAT_APP_SECRET": "configured"},
        ):
            project_root = Path(temporary_dir)
            result = WechatDraftPreflightService().build(
                config=_config(project_root),
                layout=_layout(expected_image_count=2),
                media_assets=[_image_asset(project_root)],
            )

        self.assertFalse(result.can_call_wechat_api)
        self.assertIn("可上传到微信的本地项目图不足 2 张：当前 1 张", result.missing_requirements)

    def test_legacy_layout_infers_image_count_from_layout_stats(self) -> None:
        self.assertEqual(resolve_expected_project_image_count(_layout(expected_image_count=None).payload), 1)

    def test_layout_payload_persists_expected_image_count(self) -> None:
        result = ArticleLayoutBuildResult(
            article_html="<section />",
            cover_asset_id=101,
            expected_image_count=1,
            embedded_image_count=0,
            missing_image_count=1,
            block_count=2,
            style_version="test",
        )
        payload = ArticleLayoutService().build_payload(
            content=type(
                "Content",
                (),
                {
                    "id": 17,
                    "week_end": "2026-08-14",
                    "status": "approved",
                    "updated_at": "2026-08-20T00:00:00Z",
                },
            )(),
            result=result,
            media_assets=[],
        )

        self.assertEqual(payload["layout_stats"]["expected_image_count"], 1)


if __name__ == "__main__":
    unittest.main()
