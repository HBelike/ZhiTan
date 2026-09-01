from __future__ import annotations

from src.repositories.media_asset_repository import MediaAssetRecord
from src.services.media_preview_service import MediaPreviewService


def _asset(
    asset_id: int,
    *,
    provider: str,
    prompt_index: int,
    repository_full_name: str = "owner/project",
    content_id: int = 20,
    metadata: dict | None = None,
) -> MediaAssetRecord:
    asset_metadata = {
        "repository_full_name": repository_full_name,
        "prompt_index": prompt_index,
        **(metadata or {}),
    }
    return MediaAssetRecord(
        id=asset_id,
        content_id=content_id,
        asset_type="image",
        provider=provider,
        path=f"outputs/images/{asset_id}.png",
        mime_type="image/png",
        status="created",
        metadata=asset_metadata,
    )


def _visual_spec(repository_full_name: str = "owner/project") -> dict:
    return {
        "version": "article_visual_spec_v1",
        "repository_full_name": repository_full_name,
        "figure_role": "summary_card",
        "purpose": "解释项目定位和工程价值",
        "headline": "可验证的任务执行",
        "evidence_refs": [
            {
                "kind": "weekly_ranking",
                "path": "weekly_ranking",
                "claim": "项目进入本周榜单",
            }
        ],
        "positioning": "把任务规划、执行与校验收敛到同一条链路。",
        "capabilities": [
            {"label": "任务规划", "description": "把目标拆成可执行步骤。"}
        ],
        "takeaways": ["先明确约束，再执行任务。"],
        "art_direction": {
            "style": "notion",
            "palette": "editorial_blue",
            "density": "medium",
        },
    }


def _legacy_prompt(repository_full_name: str = "owner/project") -> dict:
    return {
        "repository_full_name": repository_full_name,
        "summary_text": "旧摘要",
        "prompt": "旧 Seedream prompt",
        "prompt_stage": "summary",
    }


def _service() -> MediaPreviewService:
    return object.__new__(MediaPreviewService)


def test_deterministic_asset_overrides_legacy_and_seedream_prompt_display() -> None:
    spec = _visual_spec()
    deterministic = _asset(
        40,
        provider="gotenberg_html",
        prompt_index=1,
        metadata={
            "prompt": spec["purpose"],
            "render_key": "a" * 64,
            "visual_spec": spec,
            "figure_role": "summary_card",
            "template_version": "article_visual_v1",
            "renderer_version": "gotenberg_html_v1",
            "validation_result": {"status": "passed"},
        },
    )
    seedream = _asset(
        99,
        provider="seedream",
        prompt_index=1,
        metadata={
            "prompt": "真实提交给 Seedream 的旧 prompt",
            "prompt_designed_by": "ImagePromptDesignService",
        },
    )

    prompts = _service()._build_effective_image_prompts(
        content_id=20,
        image_prompts=[_legacy_prompt()],
        media_assets=[deterministic, seedream],
    )

    assert len(prompts) == 1
    item = prompts[0]
    assert item["prompt_stage"] == "deterministic_rendered"
    assert item["prompt_designed_by"] == "ArticleVisualTemplateService"
    assert item["prompt"] == spec["purpose"]
    assert item["effective_prompt"] == spec["purpose"]
    assert item["visual_spec"] == spec
    assert item["figure_role"] == "summary_card"
    assert item["template_version"] == "article_visual_v1"
    assert item["renderer_version"] == "gotenberg_html_v1"
    assert item["render_key"] == "a" * 64
    assert item["validation_result"] == {"status": "passed"}
    assert item["asset_id"] == 40
    assert item["asset_provider"] == "gotenberg_html"


def test_deterministic_asset_requires_matching_repository_and_prompt_index() -> None:
    first_spec = _visual_spec("owner/project")
    wrong_index = _asset(
        51,
        provider="gotenberg_html",
        prompt_index=2,
        metadata={
            "render_key": "b" * 64,
            "visual_spec": first_spec,
            "figure_role": "summary_card",
        },
    )
    wrong_repository = _asset(
        52,
        provider="gotenberg_html",
        prompt_index=1,
        repository_full_name="owner/other",
        metadata={
            "render_key": "c" * 64,
            "visual_spec": _visual_spec("owner/other"),
            "figure_role": "summary_card",
        },
    )

    prompts = _service()._build_effective_image_prompts(
        content_id=20,
        image_prompts=[_legacy_prompt()],
        media_assets=[wrong_index, wrong_repository],
    )

    assert prompts[0]["prompt_stage"] == "summary"
    assert prompts[0]["asset_id"] is None
    assert "visual_spec" not in prompts[0]


def test_gotenberg_without_render_key_stays_on_legacy_fallback_path() -> None:
    asset = _asset(
        53,
        provider="gotenberg_html",
        prompt_index=1,
        metadata={"visual_spec": _visual_spec(), "render_key": ""},
    )

    prompts = _service()._build_effective_image_prompts(
        content_id=20,
        image_prompts=[_legacy_prompt()],
        media_assets=[asset],
    )

    assert prompts[0]["prompt_stage"] == "summary"
    assert prompts[0]["asset_id"] == 53
    assert prompts[0]["asset_provider"] == "gotenberg_html"


def test_deterministic_asset_without_prompt_index_uses_unique_repository_match() -> None:
    spec = _visual_spec()
    asset = _asset(
        54,
        provider="gotenberg_html",
        prompt_index=1,
        metadata={
            "render_key": "d" * 64,
            "visual_spec": spec,
            "figure_role": "summary_card",
            "validation_result": {"status": "passed"},
        },
    )
    asset.metadata.pop("prompt_index")

    prompts = _service()._build_effective_image_prompts(
        content_id=20,
        image_prompts=[_legacy_prompt()],
        media_assets=[asset],
    )

    assert prompts[0]["prompt_stage"] == "deterministic_rendered"
    assert prompts[0]["asset_id"] == 54
