from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from src.providers.gotenberg_screenshot_provider import GotenbergScreenshotResult
from src.providers.seedream_provider import SeedreamImageResult
from src.repositories.generated_content_repository import GeneratedContentForImage
from src.repositories.media_asset_repository import MediaAssetRecord
from src.services.article_visual_generation_service import ArticleVisualGenerationService
from src.services.github_image_upgrade_service import GitHubImageUpgradeService
from src.tasks.image_task import ImageTask


def test_manual_github_image_upgrade_honors_dynamic_project_count(tmp_path: Path) -> None:
    repositories = [f"owner/repo-{index}" for index in range(1, 7)]
    content = GeneratedContentForImage(
        id=17,
        week_end="2026-08-14",
        title="Top 6",
        image_prompts=[
            {
                "repository_full_name": repository_full_name,
                "prompt": f"{repository_full_name} 架构图",
            }
            for repository_full_name in repositories
        ],
    )
    content_repository = Mock()
    content_repository.get_for_image_generation.return_value = content
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = []
    media_asset_repository.mark_replaced_by_ids.return_value = 0
    provider = Mock()
    provider.find_and_download_image.return_value = None

    with (
        patch(
            "src.services.github_image_upgrade_service.GeneratedContentRepository",
            return_value=content_repository,
        ),
        patch(
            "src.services.github_image_upgrade_service.MediaAssetRepository",
            return_value=media_asset_repository,
        ),
        patch(
            "src.services.github_image_upgrade_service.GitHubRepositoryAssetProvider",
            return_value=provider,
        ),
    ):
        result = GitHubImageUpgradeService(
            config=SimpleNamespace(image_output_dir=tmp_path),
            database_manager=SimpleNamespace(),
        ).upgrade_content_images(content_id=17)

    assert result.prompt_count == 6
    assert result.not_found_repositories == repositories
    assert provider.find_and_download_image.call_count == 6


def test_image_task_generates_one_image_for_each_dynamic_project(tmp_path: Path) -> None:
    repositories = [f"owner/repo-{index}" for index in range(1, 7)]
    content = GeneratedContentForImage(
        id=17,
        week_end="2026-08-14",
        title="Top 6",
        image_prompts=[
            {
                "repository_full_name": repository_full_name,
                "prompt": f"{repository_full_name} 架构图",
                "summary_text": f"{repository_full_name} 的架构与主数据流。",
            }
            for repository_full_name in repositories
        ],
    )
    content_repository = Mock()
    content_repository.latest_for_image_generation.return_value = content
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = []
    media_asset_repository.mark_replaced_by_ids.return_value = 0
    media_asset_repository.create.side_effect = [
        SimpleNamespace(
            id=100 + index,
            path=str(tmp_path / f"top{index}.png"),
            provider="seedream",
        )
        for index in range(1, 7)
    ]
    storyboard_repository = Mock()
    storyboard_repository.latest_for_content.return_value = None
    provider = Mock()
    provider.has_api_key.return_value = True
    provider.generate_image.side_effect = lambda prompt, output_path: SeedreamImageResult(
        output_path=output_path,
        source_url=None,
        raw_response={},
    )
    prompt_design_service = Mock()
    prompt_design_service.build_project_architecture_prompt.return_value = "项目架构与主数据流"
    config = SimpleNamespace(
        project_root=tmp_path,
        image_output_dir=tmp_path,
        image_refresh_existing_assets_on_run=False,
        image_github_asset_fallback_enabled=False,
        image_paid_generation_enabled=True,
        image_local_fallback_enabled=False,
        image_renderer="seedream",
        image_provider="seedream",
        image_model="stub-seedream",
        image_size="2048x2048",
        image_prompt_max_length=5000,
        runtime_prompt=lambda _name: "",
    )
    context = SimpleNamespace(config=config, database_manager=SimpleNamespace())
    task = object.__new__(ImageTask)
    task.logger = logging.getLogger("test.image.dynamic-project-count")

    with (
        patch("src.tasks.image_task.GeneratedContentRepository", return_value=content_repository),
        patch("src.tasks.image_task.MediaAssetRepository", return_value=media_asset_repository),
        patch("src.tasks.image_task.VideoStoryboardRepository", return_value=storyboard_repository),
        patch("src.tasks.image_task.SeedreamProvider", return_value=provider),
        patch("src.tasks.image_task.ImagePromptDesignService", return_value=prompt_design_service),
    ):
        result = task.execute(context)

    assert result["prompt_count"] == 6
    assert result["created_image_count"] == 6
    assert provider.generate_image.call_count == 6
    assert media_asset_repository.create.call_count == 6


def _deterministic_content(count: int = 6) -> GeneratedContentForImage:
    prompts = []
    for index in range(1, count + 1):
        repository_full_name = f"owner/repo-{index}"
        prompts.append(
            {
                "repository_full_name": repository_full_name,
                "prompt": f"项目 {index} 图片总结方案",
                "visual_spec": {
                    "version": "article_visual_spec_v1",
                    "repository_full_name": repository_full_name,
                    "figure_role": "summary_card",
                    "purpose": "总结项目定位、能力和工程价值",
                    "headline": f"项目 {index}",
                    "evidence_refs": [
                        {
                            "kind": "weekly_ranking",
                            "path": "weekly_ranking",
                            "claim": f"项目 {index} 进入榜单",
                        }
                    ],
                    "positioning": f"项目 {index} 提供可验证的自动化能力。",
                    "capabilities": [
                        {"label": "任务规划", "description": "把目标拆分为执行步骤。"},
                        {"label": "结果校验", "description": "检查关键产物是否一致。"},
                    ],
                    "takeaways": ["先明确约束，再执行任务。"],
                    "art_direction": {
                        "style": "notion",
                        "palette": "editorial_blue",
                        "density": "medium",
                    },
                },
            }
        )
    return GeneratedContentForImage(
        id=20,
        week_end="2026-08-14",
        title=f"Top {count}",
        image_prompts=prompts,
    )


def _deterministic_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=tmp_path,
        image_output_dir=tmp_path / "images",
        image_renderer="gotenberg_html",
        image_template_version="article_visual_v1",
        image_renderer_version="gotenberg_html_v1",
        image_font_version="noto-cjk-test",
        image_canvas_width=2048,
        image_canvas_height=1152,
    )


def _created_records(
    assets: list[object], replace_asset_ids: list[int]
) -> list[MediaAssetRecord]:
    del replace_asset_ids
    return [
        MediaAssetRecord(
            id=500 + index,
            content_id=asset.content_id,
            asset_type=asset.asset_type,
            provider=asset.provider,
            path=asset.path,
            mime_type=asset.mime_type,
            status=asset.status,
            metadata=asset.metadata,
        )
        for index, asset in enumerate(assets, start=1)
    ]


def test_default_gotenberg_branch_renders_six_and_never_touches_seedream(
    tmp_path: Path,
) -> None:
    content_repository = Mock()
    content_repository.latest_for_image_generation.return_value = _deterministic_content()
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = []
    media_asset_repository.create_and_replace_images.side_effect = _created_records
    provider = Mock()

    def render(_document: object, output_path: Path) -> GotenbergScreenshotResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2048, 1152), "white").save(output_path, format="PNG")
        return GotenbergScreenshotResult(output_path, "trace", 1)

    provider.render.side_effect = render
    context = SimpleNamespace(
        config=_deterministic_config(tmp_path), database_manager=SimpleNamespace()
    )
    task = object.__new__(ImageTask)
    task.logger = logging.getLogger("test.image.gotenberg")

    with (
        patch("src.tasks.image_task.GeneratedContentRepository", return_value=content_repository),
        patch("src.tasks.image_task.MediaAssetRepository", return_value=media_asset_repository),
        patch("src.tasks.image_task.GotenbergScreenshotProvider", return_value=provider),
        patch("src.tasks.image_task.SeedreamProvider") as seedream,
        patch("src.tasks.image_task.GitHubRepositoryAssetProvider") as github,
        patch("src.tasks.image_task.LocalTechCardImageProvider") as local,
    ):
        result = task.execute(context)

    assert result["renderer"] == "gotenberg_html"
    assert result["rendered_image_count"] == 6
    assert result["reused_image_count"] == 0
    assert result["figure_role_counts"] == {"summary_card": 6}
    assert provider.render.call_count == 6
    media_asset_repository.create_and_replace_images.assert_called_once()
    seedream.assert_not_called()
    github.assert_not_called()
    local.assert_not_called()


def test_execute_for_content_uses_explicit_content_id(tmp_path: Path) -> None:
    content_repository = Mock()
    content_repository.get_for_image_generation.return_value = _deterministic_content(1)
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = []
    media_asset_repository.create_and_replace_images.side_effect = _created_records
    provider = Mock()

    def render(_document: object, output_path: Path) -> GotenbergScreenshotResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2048, 1152), "white").save(output_path, format="PNG")
        return GotenbergScreenshotResult(output_path, "trace", 1)

    provider.render.side_effect = render
    task = object.__new__(ImageTask)
    task.logger = logging.getLogger("test.image.explicit-content")
    context = SimpleNamespace(
        config=_deterministic_config(tmp_path), database_manager=SimpleNamespace()
    )

    with (
        patch("src.tasks.image_task.GeneratedContentRepository", return_value=content_repository),
        patch("src.tasks.image_task.MediaAssetRepository", return_value=media_asset_repository),
        patch("src.tasks.image_task.GotenbergScreenshotProvider", return_value=provider),
    ):
        result = task.execute_for_content(context, content_id=20)

    assert result["content_id"] == 20
    content_repository.get_for_image_generation.assert_called_once_with(20)
    content_repository.latest_for_image_generation.assert_not_called()


def test_sixth_render_failure_produces_no_database_mutation(tmp_path: Path) -> None:
    content_repository = Mock()
    content_repository.get_for_image_generation.return_value = _deterministic_content()
    old_assets = [
        MediaAssetRecord(
            id=index,
            content_id=20,
            asset_type="image",
            provider="seedream",
            path=str(tmp_path / f"old-{index}.png"),
            mime_type="image/png",
            status="created",
            metadata={"repository_full_name": f"owner/repo-{index}"},
        )
        for index in range(1, 7)
    ]
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = old_assets
    provider = Mock()
    calls = 0

    def render(_document: object, output_path: Path) -> GotenbergScreenshotResult:
        nonlocal calls
        calls += 1
        if calls == 6:
            raise RuntimeError("校验失败")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2048, 1152), "white").save(output_path, format="PNG")
        return GotenbergScreenshotResult(output_path, f"trace-{calls}", 1)

    provider.render.side_effect = render
    task = object.__new__(ImageTask)
    task.logger = logging.getLogger("test.image.fail-closed")
    context = SimpleNamespace(
        config=_deterministic_config(tmp_path), database_manager=SimpleNamespace()
    )

    with (
        patch("src.tasks.image_task.GeneratedContentRepository", return_value=content_repository),
        patch("src.tasks.image_task.MediaAssetRepository", return_value=media_asset_repository),
        patch("src.tasks.image_task.GotenbergScreenshotProvider", return_value=provider),
        pytest.raises(RuntimeError, match="校验失败"),
    ):
        task.execute_for_content(context, content_id=20)

    media_asset_repository.create_and_replace_images.assert_not_called()
    assert [asset.status for asset in old_assets] == ["created"] * 6
    assert list((_deterministic_config(tmp_path).image_output_dir).rglob("*.png")) == []


def test_database_failure_cleans_only_new_batch_files(tmp_path: Path) -> None:
    content_repository = Mock()
    content_repository.get_for_image_generation.return_value = _deterministic_content(1)
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = []
    media_asset_repository.create_and_replace_images.side_effect = RuntimeError(
        "数据库提交失败"
    )
    unrelated = tmp_path / "images" / "unrelated.png"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"keep")
    provider = Mock()

    def render(_document: object, output_path: Path) -> GotenbergScreenshotResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2048, 1152), "white").save(output_path, format="PNG")
        return GotenbergScreenshotResult(output_path, "trace", 1)

    provider.render.side_effect = render
    task = object.__new__(ImageTask)
    task.logger = logging.getLogger("test.image.database-failure")
    context = SimpleNamespace(
        config=_deterministic_config(tmp_path), database_manager=SimpleNamespace()
    )

    with (
        patch("src.tasks.image_task.GeneratedContentRepository", return_value=content_repository),
        patch("src.tasks.image_task.MediaAssetRepository", return_value=media_asset_repository),
        patch("src.tasks.image_task.GotenbergScreenshotProvider", return_value=provider),
        pytest.raises(RuntimeError, match="数据库提交失败"),
    ):
        task.execute_for_content(context, content_id=20)

    assert unrelated.read_bytes() == b"keep"
    assert list((tmp_path / "images" / "2026-08-14").rglob("*.png")) == []


def test_second_run_reuses_all_six_render_keys_without_database_write(
    tmp_path: Path,
) -> None:
    config = _deterministic_config(tmp_path)
    content = _deterministic_content()
    warmup_provider = Mock()

    def render(_document: object, output_path: Path) -> GotenbergScreenshotResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2048, 1152), "white").save(output_path, format="PNG")
        return GotenbergScreenshotResult(output_path, "trace", 1)

    warmup_provider.render.side_effect = render
    warmup = ArticleVisualGenerationService(
        config, provider=warmup_provider
    ).prepare(content=content, existing_assets=[])
    existing = [
        MediaAssetRecord(
            id=700 + index,
            content_id=20,
            asset_type="image",
            provider="gotenberg_html",
            path=item.input.path,
            mime_type="image/png",
            status="created",
            metadata=item.input.metadata,
        )
        for index, item in enumerate(warmup.new_assets, start=1)
    ]
    content_repository = Mock()
    content_repository.get_for_image_generation.return_value = content
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = existing
    second_provider = Mock()
    task = object.__new__(ImageTask)
    task.logger = logging.getLogger("test.image.reuse")
    context = SimpleNamespace(config=config, database_manager=SimpleNamespace())

    with (
        patch("src.tasks.image_task.GeneratedContentRepository", return_value=content_repository),
        patch("src.tasks.image_task.MediaAssetRepository", return_value=media_asset_repository),
        patch("src.tasks.image_task.GotenbergScreenshotProvider", return_value=second_provider),
    ):
        result = task.execute_for_content(context, content_id=20)

    assert result["rendered_image_count"] == 0
    assert result["reused_image_count"] == 6
    second_provider.render.assert_not_called()
    media_asset_repository.create_and_replace_images.assert_not_called()
