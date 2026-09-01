from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3

import pytest
from PIL import Image

from src.providers.gotenberg_screenshot_provider import GotenbergScreenshotResult
from src.database.database_manager import DatabaseManager
from src.repositories.generated_content_repository import GeneratedContentForImage
from src.repositories.media_asset_repository import (
    MediaAssetInput,
    MediaAssetRecord,
    MediaAssetRepository,
)
from src.services.article_visual_generation_service import ArticleVisualGenerationService
from src.services.article_visual_spec import ArticleVisualSpecError


def _visual_spec(repository_full_name: str, index: int) -> dict[str, object]:
    return {
        "version": "article_visual_spec_v1",
        "repository_full_name": repository_full_name,
        "figure_role": "summary_card",
        "purpose": "总结项目定位、能力和工程价值",
        "headline": f"项目 {index}",
        "evidence_refs": [
            {
                "kind": "weekly_ranking",
                "path": "weekly_ranking",
                "claim": f"项目 {index} 进入本周榜单",
            }
        ],
        "positioning": f"项目 {index} 提供可验证的开发工作流能力。",
        "capabilities": [
            {"label": "任务规划", "description": "把目标拆分成可执行步骤。"},
            {"label": "结果校验", "description": "对关键产物执行一致性检查。"},
        ],
        "takeaways": ["先明确约束，再执行任务。"],
        "art_direction": {
            "style": "notion",
            "palette": "editorial_blue",
            "density": "medium",
        },
    }


def _content(count: int = 6, *, content_id: int = 20) -> GeneratedContentForImage:
    return GeneratedContentForImage(
        id=content_id,
        week_end="2026-08-14",
        title=f"Top {count}",
        image_prompts=[
            {
                "repository_full_name": f"owner/repo-{index}",
                "prompt": f"项目 {index} 图片总结方案",
                "visual_spec": _visual_spec(f"owner/repo-{index}", index),
            }
            for index in range(1, count + 1)
        ],
    )


def _config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=tmp_path,
        image_output_dir=tmp_path / "images",
        image_template_version="article_visual_v1",
        image_renderer_version="gotenberg_html_v1",
        image_font_version="noto-cjk-test",
        image_canvas_width=2048,
        image_canvas_height=1152,
    )


def _render_result(output_path: Path, trace_id: str = "trace-1") -> GotenbergScreenshotResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2048, 1152), "white").save(output_path, format="PNG")
    return GotenbergScreenshotResult(
        output_path=output_path,
        trace_id=trace_id,
        attempts=1,
    )


def _provider_that_renders() -> Mock:
    provider = Mock()
    provider.render.side_effect = lambda _document, output_path: _render_result(
        output_path,
        trace_id=f"trace-{Path(output_path).stem}",
    )
    return provider


def test_prepares_one_deterministic_image_for_each_of_six_projects(
    tmp_path: Path,
) -> None:
    provider = _provider_that_renders()
    service = ArticleVisualGenerationService(_config(tmp_path), provider=provider)

    batch = service.prepare(content=_content(), existing_assets=[])

    assert len(batch.new_assets) == 6
    assert batch.reused_assets == ()
    assert provider.render.call_count == 6
    assert {item.input.provider for item in batch.new_assets} == {"gotenberg_html"}
    assert batch.figure_role_counts == {"summary_card": 6}
    for index, item in enumerate(batch.new_assets, start=1):
        assert item.output_path.parent.name == "content-20"
        assert item.input.path.endswith(
            f"{index:02d}_owner_repo-{index}_{item.render_key[:12]}.png"
        )
        assert item.input.metadata["render_key"] == item.render_key
        assert item.input.metadata["figure_role"] == "summary_card"
        assert item.input.metadata["validation_result"] == {"status": "passed"}
        assert item.input.metadata["trace_id"].startswith("trace-")
        assert item.input.metadata["attempts"] == 1
        assert item.output_path.is_file()


def test_matching_render_keys_reuse_all_six_images(tmp_path: Path) -> None:
    provider = _provider_that_renders()
    service = ArticleVisualGenerationService(_config(tmp_path), provider=provider)
    first = service.prepare(content=_content(), existing_assets=[])
    existing = [
        MediaAssetRecord(
            id=100 + index,
            content_id=20,
            asset_type="image",
            provider="gotenberg_html",
            path=str(item.output_path),
            mime_type="image/png",
            status="created",
            metadata=item.input.metadata,
        )
        for index, item in enumerate(first.new_assets)
    ]
    provider.reset_mock()

    second = service.prepare(content=_content(), existing_assets=existing)

    assert second.new_assets == ()
    assert len(second.reused_assets) == 6
    assert second.replace_asset_ids == ()
    provider.render.assert_not_called()


def test_reuse_requires_provider_file_and_exact_render_key(tmp_path: Path) -> None:
    service = ArticleVisualGenerationService(
        _config(tmp_path), provider=_provider_that_renders()
    )
    first = service.prepare(content=_content(count=1), existing_assets=[])
    generated = first.new_assets[0]
    wrong_provider = MediaAssetRecord(
        id=1,
        content_id=20,
        asset_type="image",
        provider="seedream",
        path=str(generated.output_path),
        mime_type="image/png",
        status="created",
        metadata=generated.input.metadata,
    )
    wrong_key = MediaAssetRecord(
        id=2,
        content_id=20,
        asset_type="image",
        provider="gotenberg_html",
        path=str(generated.output_path),
        mime_type="image/png",
        status="created",
        metadata={**generated.input.metadata, "render_key": "different"},
    )
    generated.output_path.unlink()
    provider = _provider_that_renders()
    service = ArticleVisualGenerationService(_config(tmp_path), provider=provider)

    batch = service.prepare(
        content=_content(count=1),
        existing_assets=[wrong_provider, wrong_key],
    )

    assert len(batch.new_assets) == 1
    assert batch.reused_assets == ()
    assert batch.replace_asset_ids == (1, 2)
    provider.render.assert_called_once()


def test_all_specs_are_validated_before_first_render(tmp_path: Path) -> None:
    content = _content()
    content.image_prompts[-1]["visual_spec"] = {
        **content.image_prompts[-1]["visual_spec"],
        "headline": "无标题",
    }
    provider = _provider_that_renders()
    service = ArticleVisualGenerationService(_config(tmp_path), provider=provider)

    with pytest.raises(ArticleVisualSpecError):
        service.prepare(content=content, existing_assets=[])

    provider.render.assert_not_called()
    assert not (_config(tmp_path).image_output_dir).exists()


def test_sixth_render_failure_cleans_only_files_created_by_batch(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    preserved = config.image_output_dir / "2026-08-14" / "unrelated.png"
    preserved.parent.mkdir(parents=True, exist_ok=True)
    preserved.write_bytes(b"user-file")
    provider = Mock()
    call_count = 0

    def render(_document: object, output_path: Path) -> GotenbergScreenshotResult:
        nonlocal call_count
        call_count += 1
        if call_count == 6:
            raise RuntimeError("校验失败")
        return _render_result(output_path, trace_id=f"trace-{call_count}")

    provider.render.side_effect = render
    service = ArticleVisualGenerationService(config, provider=provider)

    with pytest.raises(RuntimeError, match="校验失败"):
        service.prepare(content=_content(), existing_assets=[])

    assert preserved.read_bytes() == b"user-file"
    assert list(config.image_output_dir.rglob("*.png")) == [preserved]


def test_same_week_and_repository_are_isolated_by_content_id(tmp_path: Path) -> None:
    service = ArticleVisualGenerationService(
        _config(tmp_path), provider=_provider_that_renders()
    )

    first = service.prepare(
        content=_content(count=1, content_id=20), existing_assets=[]
    )
    second = service.prepare(
        content=_content(count=1, content_id=21), existing_assets=[]
    )

    first_path = first.new_assets[0].output_path
    second_path = second.new_assets[0].output_path
    assert first_path != second_path
    assert first_path.parent.name == "content-20"
    assert second_path.parent.name == "content-21"
    assert first_path.name == second_path.name
    assert first_path.is_file()
    assert second_path.is_file()


def test_orphan_conflict_on_sixth_target_fails_before_any_render_and_is_preserved(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    warmup = ArticleVisualGenerationService(
        config, provider=_provider_that_renders()
    ).prepare(content=_content(), existing_assets=[])
    for item in warmup.new_assets[:5]:
        item.output_path.unlink()
    orphan = warmup.new_assets[5].output_path
    orphan.write_bytes(b"orphan-owned-by-another-run")
    provider = _provider_that_renders()
    service = ArticleVisualGenerationService(config, provider=provider)

    with pytest.raises(RuntimeError, match="目标文件已存在"):
        service.prepare(content=_content(), existing_assets=[])

    provider.render.assert_not_called()
    assert orphan.read_bytes() == b"orphan-owned-by-another-run"
    assert list(config.image_output_dir.rglob("*.png")) == [orphan]


def test_legacy_item_uses_summary_card_and_ignores_old_visual_brief(
    tmp_path: Path,
) -> None:
    content = GeneratedContentForImage(
        id=20,
        week_end="2026-08-14",
        title="旧文章",
        image_prompts=[
            {
                "repository_full_name": "owner/legacy",
                "prompt": "旧提示词",
                "summary_text": "这是一个面向工程团队的自动化开发工具。",
                "project_analysis_markdown": """
## 技术特点
通过声明式配置管理任务输入。
## 机制拆解
先规划，再执行并校验结果。
## 工程启发
把失败条件前置可以减少无效调用。
""",
                "visual_brief": {
                    "nodes": [{"id": "broken", "label": "Agent运"}],
                    "relationships": [{"from": "broken", "to": "missing"}],
                },
            }
        ],
    )
    service = ArticleVisualGenerationService(
        _config(tmp_path), provider=_provider_that_renders()
    )

    batch = service.prepare(content=content, existing_assets=[])

    metadata = batch.new_assets[0].input.metadata
    assert metadata["figure_role"] == "summary_card"
    assert "Agent运" not in str(metadata["visual_spec"])
    assert "relationships" not in metadata["visual_spec"]


def _database(tmp_path: Path) -> DatabaseManager:
    database = DatabaseManager(
        SimpleNamespace(
            database_path=tmp_path / "app.db",
            database_timeout_seconds=5,
        )
    )
    database.initialize()
    with database.connection() as conn:
        conn.execute(
            """
            INSERT INTO generated_contents (week_end, title, article_markdown)
            VALUES ('2026-08-14', '测试文章', '正文')
            """
        )
    return database


def _asset_input(path: Path, *, repository_id: int | None = None) -> MediaAssetInput:
    return MediaAssetInput(
        content_id=1,
        repository_id=repository_id,
        asset_type="image",
        provider="gotenberg_html",
        path=str(path),
        mime_type="image/png",
        status="created",
        metadata={"render_key": path.stem},
    )


def test_repository_creates_batch_then_replaces_old_images_in_one_transaction(
    tmp_path: Path,
) -> None:
    repository = MediaAssetRepository(_database(tmp_path))
    old = repository.create(_asset_input(tmp_path / "old.png"))

    created = repository.create_and_replace_images(
        [_asset_input(tmp_path / "new-1.png"), _asset_input(tmp_path / "new-2.png")],
        [old.id],
    )

    assert [item.path for item in created] == [
        str(tmp_path / "new-1.png"),
        str(tmp_path / "new-2.png"),
    ]
    assert repository.get_by_id(old.id).status == "replaced"
    assert len(repository.list_by_content_id(1, "image")) == 2


def test_repository_does_not_replace_anything_when_new_batch_is_empty(
    tmp_path: Path,
) -> None:
    repository = MediaAssetRepository(_database(tmp_path))
    old = repository.create(_asset_input(tmp_path / "old.png"))

    assert repository.create_and_replace_images([], [old.id]) == []

    assert repository.get_by_id(old.id).status == "created"


def test_repository_rolls_back_first_insert_and_old_replacement_on_batch_failure(
    tmp_path: Path,
) -> None:
    repository = MediaAssetRepository(_database(tmp_path))
    old = repository.create(_asset_input(tmp_path / "old.png"))

    with pytest.raises(sqlite3.IntegrityError):
        repository.create_and_replace_images(
            [
                _asset_input(tmp_path / "new-valid.png"),
                _asset_input(tmp_path / "new-invalid.png", repository_id=999),
            ],
            [old.id],
        )

    active = repository.list_by_content_id(1, "image")
    assert [asset.id for asset in active] == [old.id]
    assert active[0].status == "created"
