from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from PIL import Image, UnidentifiedImageError
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.application import Application
from src.repositories.media_asset_repository import MediaAssetRecord, MediaAssetRepository
from src.tasks.image_task import ImageTask


FORBIDDEN_VISIBLE_TEXT = (
    "16:9",
    "16.9",
    "工程架构",
    "架构信息图",
    "技术信息图",
    "流程图",
    "对比图",
    "示意图",
    "无标题",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验收确定性技术文章配图")
    parser.add_argument("--content-id", type=int, default=20)
    parser.add_argument("--expected-count", type=int, default=6)
    parser.add_argument(
        "--gotenberg-base-url",
        default="http://127.0.0.1:3000",
    )
    return parser.parse_args()


def check_gotenberg_health(base_url: str) -> dict[str, Any]:
    """使用不继承系统代理的会话检查本地 Gotenberg。"""

    session = requests.Session()
    session.trust_env = False
    response = session.get(f"{base_url.rstrip('/')}/health", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "up":
        raise RuntimeError(f"Gotenberg health 非 up：{payload}")
    return {"status": "up"}


def assert_no_forbidden_text(value: Any, *, location: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    matched = next((text for text in FORBIDDEN_VISIBLE_TEXT if text in serialized), None)
    if matched:
        raise AssertionError(f"{location} 包含禁用元文字：{matched}")


def resolve_asset_path(asset: MediaAssetRecord) -> Path:
    path = Path(asset.path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def validate_asset(asset: MediaAssetRecord) -> dict[str, Any]:
    """校验数据库 metadata 与真实 PNG，只输出非敏感验收摘要。"""

    metadata = asset.metadata
    required_text_fields = (
        "render_key",
        "template_version",
        "renderer_version",
        "figure_role",
    )
    for field in required_text_fields:
        if not str(metadata.get(field, "")).strip():
            raise AssertionError(f"asset_id={asset.id} metadata 缺少 {field}")
    validation = metadata.get("validation_result")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        raise AssertionError(f"asset_id={asset.id} validation_result 未通过")
    visual_spec = metadata.get("visual_spec")
    if not isinstance(visual_spec, dict):
        raise AssertionError(f"asset_id={asset.id} visual_spec 缺失")
    assert_no_forbidden_text(visual_spec, location=f"asset_id={asset.id}.visual_spec")

    output_path = resolve_asset_path(asset).resolve()
    try:
        with Image.open(output_path) as image:
            image.load()
            if image.format != "PNG":
                raise AssertionError(f"{output_path} 不是 PNG")
            if image.size != (2048, 1152):
                raise AssertionError(
                    f"{output_path} 尺寸错误：actual={image.width}x{image.height}"
                )
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise AssertionError(f"图片不可读取：{output_path}：{exc}") from exc

    return {
        "path": str(output_path),
        "width": 2048,
        "height": 1152,
        "role": str(metadata["figure_role"]),
        "render_key_prefix": str(metadata["render_key"])[:12],
        "validation": "passed",
        "visual_review": "pending",
    }


def main() -> None:
    args = parse_args()
    health = check_gotenberg_health(args.gotenberg_base_url)

    application = Application(PROJECT_ROOT)
    application.initialize()
    if application.config is None:
        raise RuntimeError("Application config 初始化失败")
    # 参数用于本地验收环境切换，不改变配置文件。
    application.config.raw["image"]["renderer"]["gotenberg_base_url"] = (
        args.gotenberg_base_url
    )
    context = application._build_task_context()
    task = ImageTask(
        task_run_repository=application.task_run_repository,
        error_event_repository=application.error_event_repository,
    )

    first_run = task.execute_for_content(context, args.content_id)
    second_run = task.execute_for_content(context, args.content_id)
    if int(second_run.get("rendered_image_count", -1)) != 0:
        raise AssertionError(f"第二次执行仍发生渲染：{second_run}")
    if int(second_run.get("reused_image_count", -1)) != args.expected_count:
        raise AssertionError(f"第二次执行未复用全部图片：{second_run}")

    repository = MediaAssetRepository(context.database_manager)
    active_assets = repository.list_by_content_id(args.content_id, "image")
    if len(active_assets) != args.expected_count:
        raise AssertionError(
            "有效图片数量不符："
            f"expected={args.expected_count} actual={len(active_assets)}"
        )
    if any(asset.provider != "gotenberg_html" for asset in active_assets):
        raise AssertionError("有效图片中仍存在非 gotenberg_html 资产")

    images = [validate_asset(asset) for asset in active_assets]
    content_dirs = {Path(item["path"]).parent for item in images}
    part_files = sorted(
        str(path.resolve())
        for directory in content_dirs
        for path in directory.glob("*.part")
    )
    if part_files:
        raise AssertionError(f"输出目录残留 .part：{part_files}")

    roles = {item["role"] for item in images}
    if roles != {"summary_card"}:
        raise AssertionError(f"legacy content 应全部降级 summary_card：actual={roles}")

    report = {
        "status": "pending_visual_review",
        "content_id": args.content_id,
        "expected_count": args.expected_count,
        "gotenberg_health": health,
        "active_image_count": len(images),
        "first_run": {
            "rendered_image_count": int(first_run.get("rendered_image_count", 0)),
            "reused_image_count": int(first_run.get("reused_image_count", 0)),
        },
        "second_run": {
            "rendered_image_count": int(second_run["rendered_image_count"]),
            "reused_image_count": int(second_run["reused_image_count"]),
        },
        "part_files": [],
        "legacy_summary_card_count": sum(
            item["role"] == "summary_card" for item in images
        ),
        "images": images,
    }
    report_path = (
        PROJECT_ROOT
        / "outputs"
        / "visual-verification"
        / f"content-{args.content_id}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"视觉验收报告（待逐图人工复核）：{report_path.resolve()}")


if __name__ == "__main__":
    main()
