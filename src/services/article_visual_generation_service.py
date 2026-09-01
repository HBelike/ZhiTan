from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.providers.gotenberg_screenshot_provider import (
    GotenbergScreenshotProvider,
    GotenbergScreenshotResult,
)
from src.repositories.generated_content_repository import GeneratedContentForImage
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord
from src.services.article_visual_planning_service import ArticleVisualPlanningService
from src.services.article_visual_spec import (
    ArticleVisualSpecError,
    ValidatedArticleVisualSpec,
    build_render_key,
)
from src.services.article_visual_template_service import ArticleVisualTemplateService


class ArticleVisualGenerationConfig(Protocol):
    project_root: Path
    image_output_dir: Path
    image_template_version: str
    image_renderer_version: str
    image_font_version: str
    image_canvas_width: int
    image_canvas_height: int


@dataclass(frozen=True)
class PreparedArticleVisualAsset:
    """已完成截图、校验，等待统一写入数据库的图片资产。"""

    input: MediaAssetInput
    render_key: str
    repository_full_name: str
    output_path: Path


@dataclass(frozen=True)
class ArticleVisualGenerationBatch:
    """一次内容图片任务的完整准备结果。"""

    new_assets: tuple[PreparedArticleVisualAsset, ...]
    reused_assets: tuple[MediaAssetRecord, ...]
    replace_asset_ids: tuple[int, ...]
    figure_role_counts: dict[str, int]
    cleanup_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _PlannedVisual:
    index: int
    item: dict[str, Any]
    spec: ValidatedArticleVisualSpec
    render_key: str
    output_path: Path


class ArticleVisualGenerationService:
    """先编译整批规格，再渲染，确保失败时不会留下半批数据库记录。"""

    def __init__(
        self,
        config: ArticleVisualGenerationConfig,
        *,
        provider: GotenbergScreenshotProvider,
        planning_service: ArticleVisualPlanningService | None = None,
        template_service: ArticleVisualTemplateService | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.planning_service = planning_service or ArticleVisualPlanningService()
        self.template_service = template_service or ArticleVisualTemplateService()

    def prepare(
        self,
        *,
        content: GeneratedContentForImage,
        existing_assets: list[MediaAssetRecord],
    ) -> ArticleVisualGenerationBatch:
        """准备一批确定性图片；所有规格通过后才允许发起第一次截图。"""

        plans = self._plan_all(content)
        existing_by_repository = self._existing_assets_by_repository(existing_assets)
        reused_assets: list[MediaAssetRecord] = []
        render_plans: list[_PlannedVisual] = []
        replace_asset_ids: set[int] = set()

        for plan in plans:
            repository_assets = existing_by_repository.get(
                plan.spec.repository_full_name, []
            )
            reusable = self._find_reusable_asset(repository_assets, plan.render_key)
            if reusable is not None:
                reused_assets.append(reusable)
                continue
            render_plans.append(plan)
            replace_asset_ids.update(asset.id for asset in repository_assets)

        conflicting_paths = [
            plan.output_path for plan in render_plans if plan.output_path.exists()
        ]
        if conflicting_paths:
            joined_paths = ", ".join(str(path) for path in conflicting_paths)
            raise RuntimeError(
                "确定性图片目标文件已存在，但当前内容没有可复用的精确资产；"
                f"为避免覆盖现有文件，本批次已关闭式失败：{joined_paths}"
            )

        new_assets: list[PreparedArticleVisualAsset] = []
        cleanup_paths: list[Path] = []
        try:
            for plan in render_plans:
                existed_before = plan.output_path.is_file()
                if not existed_before:
                    # 先登记清理目标，覆盖 provider 已落盘后再抛错的边界情况。
                    cleanup_paths.append(plan.output_path)
                document = self.template_service.render(plan.spec)
                result = self.provider.render(document, plan.output_path)
                prepared = self._to_prepared_asset(content, plan, result)
                if not prepared.output_path.is_file():
                    raise RuntimeError(
                        "Gotenberg 返回成功但图片文件不存在："
                        f"{prepared.output_path}"
                    )
                new_assets.append(prepared)
        except Exception:
            self._remove_files(cleanup_paths)
            raise

        figure_role_counts = dict(Counter(plan.spec.figure_role for plan in plans))
        return ArticleVisualGenerationBatch(
            new_assets=tuple(new_assets),
            reused_assets=tuple(reused_assets),
            replace_asset_ids=tuple(sorted(replace_asset_ids)),
            figure_role_counts=figure_role_counts,
            cleanup_paths=tuple(cleanup_paths),
        )

    def cleanup_new_files(self, batch: ArticleVisualGenerationBatch) -> None:
        """数据库事务失败时，只删除本批新创建、未被复用的文件。"""

        self._remove_files(batch.cleanup_paths)

    def _plan_all(self, content: GeneratedContentForImage) -> list[_PlannedVisual]:
        if not content.image_prompts:
            raise ArticleVisualSpecError(
                f"content_id={content.id} 没有可用 image_prompts"
            )

        plans: list[_PlannedVisual] = []
        repositories: set[str] = set()
        for index, item in enumerate(content.image_prompts, start=1):
            if not isinstance(item, dict):
                raise ArticleVisualSpecError(f"image_prompts[{index - 1}] 必须是对象")
            repository_full_name = str(item.get("repository_full_name", "")).strip()
            if not repository_full_name:
                raise ArticleVisualSpecError(
                    f"image_prompts[{index - 1}].repository_full_name 不能为空"
                )
            if repository_full_name in repositories:
                raise ArticleVisualSpecError(
                    f"同一内容不能重复生成项目图片：{repository_full_name}"
                )
            repositories.add(repository_full_name)

            raw_spec = item.get("visual_spec")
            if isinstance(raw_spec, dict):
                spec = self.planning_service.plan(
                    raw_spec=raw_spec,
                    repository_full_name=repository_full_name,
                    allowed_evidence_paths=None,
                )
            else:
                # 历史内容只读取摘要和固定文章章节，绝不消费旧 visual_brief 的截断拓扑。
                spec = self.planning_service.plan_legacy_content_brief(item)
            render_key = build_render_key(
                spec,
                template_version=self.config.image_template_version,
                renderer_version=self.config.image_renderer_version,
                font_version=self.config.image_font_version,
            )
            output_path = self._build_output_path(
                content=content,
                index=index,
                repository_full_name=repository_full_name,
                render_key=render_key,
            )
            plans.append(
                _PlannedVisual(
                    index=index,
                    item=item,
                    spec=spec,
                    render_key=render_key,
                    output_path=output_path,
                )
            )
        return plans

    def _to_prepared_asset(
        self,
        content: GeneratedContentForImage,
        plan: _PlannedVisual,
        result: GotenbergScreenshotResult,
    ) -> PreparedArticleVisualAsset:
        spec_value = plan.spec.value
        output_path = Path(result.output_path)
        metadata: dict[str, Any] = {
            "repository_full_name": plan.spec.repository_full_name,
            "prompt": spec_value["purpose"],
            "raw_prompt": str(plan.item.get("prompt", "")).strip(),
            "prompt_stage": "deterministic_rendered",
            "prompt_index": plan.index,
            "prompt_source": str(
                plan.item.get("prompt_source", "summary_image_prompt")
            ).strip()
            or "summary_image_prompt",
            "visual_spec": spec_value,
            "visual_spec_version": str(spec_value["version"]),
            "figure_role": plan.spec.figure_role,
            "evidence_refs": spec_value["evidence_refs"],
            "template_version": self.config.image_template_version,
            "renderer_version": self.config.image_renderer_version,
            "font_version": self.config.image_font_version,
            "render_key": plan.render_key,
            "width": int(self.config.image_canvas_width),
            "height": int(self.config.image_canvas_height),
            "validation_result": {"status": "passed"},
            "trace_id": result.trace_id,
            "attempts": int(result.attempts),
        }
        return PreparedArticleVisualAsset(
            input=MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="image",
                provider="gotenberg_html",
                path=str(output_path),
                mime_type="image/png",
                status="created",
                metadata=metadata,
            ),
            render_key=plan.render_key,
            repository_full_name=plan.spec.repository_full_name,
            output_path=output_path,
        )

    def _find_reusable_asset(
        self,
        assets: list[MediaAssetRecord],
        render_key: str,
    ) -> MediaAssetRecord | None:
        for asset in sorted(assets, key=lambda candidate: candidate.id, reverse=True):
            if asset.provider != "gotenberg_html":
                continue
            if asset.status in {"failed", "replaced"}:
                continue
            if asset.metadata.get("render_key") != render_key:
                continue
            if self._resolve_asset_path(asset.path).is_file():
                return asset
        return None

    @staticmethod
    def _existing_assets_by_repository(
        existing_assets: list[MediaAssetRecord],
    ) -> dict[str, list[MediaAssetRecord]]:
        indexed: dict[str, list[MediaAssetRecord]] = {}
        for asset in existing_assets:
            repository_full_name = str(
                asset.metadata.get("repository_full_name", "")
            ).strip()
            if repository_full_name:
                indexed.setdefault(repository_full_name, []).append(asset)
        return indexed

    def _resolve_asset_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        return path if path.is_absolute() else Path(self.config.project_root) / path

    def _build_output_path(
        self,
        *,
        content: GeneratedContentForImage,
        index: int,
        repository_full_name: str,
        render_key: str,
    ) -> Path:
        safe_repository = re.sub(
            r"[^A-Za-z0-9_.-]+", "_", repository_full_name
        ).strip("_")
        if not safe_repository:
            safe_repository = "repository"
        filename = f"{index:02d}_{safe_repository}_{render_key[:12]}.png"
        return (
            Path(self.config.image_output_dir)
            / content.week_end
            / f"content-{content.id}"
            / filename
        )

    @staticmethod
    def _remove_files(paths: list[Path] | tuple[Path, ...]) -> None:
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "ArticleVisualGenerationBatch",
    "ArticleVisualGenerationService",
    "PreparedArticleVisualAsset",
]
