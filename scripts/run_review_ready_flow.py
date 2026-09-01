from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.application import Application
from src.tasks.audio_task import AudioTask
from src.tasks.cat_task import CatTask
from src.tasks.image_task import ImageTask
from src.tasks.preview_task import PreviewTask
from src.tasks.search_task import SearchTask
from src.tasks.short_video_prompt_task import ShortVideoPromptTask
from src.tasks.storage_task import StorageTask
from src.tasks.task_result import TaskResult
from src.tasks.summary_task import SummaryTask


def utc_now() -> str:
    """返回用于运行日志的 UTC 时间字符串。"""

    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def write_event(log_path: Path, event: dict[str, Any]) -> None:
    """向本次和最新运行日志追加同一条结构化事件。"""

    payload = {"time": utc_now(), **event}
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    with log_path.open("a", encoding="utf-8") as file:
        file.write(line)
    with (log_path.parent / "latest_review_ready_flow.jsonl").open("a", encoding="utf-8") as file:
        file.write(line)


def run_task(app: Application, task_class: type, log_path: Path) -> TaskResult:
    """运行一个 Task 并把开始、结束状态写入审计日志。"""

    write_event(log_path, {"event": "task_start", "task": task_class.__name__})
    result = app._run_task(task_class)
    write_event(
        log_path,
        {
            "event": "task_finish",
            "task": task_class.__name__,
            "run_id": result.run_id,
            "metadata": result.metadata,
        },
    )
    return result


def main() -> int:
    """真实生成可人工审核的周榜内容，但绝不提交视频或公众号草稿。"""

    project_root = Path.cwd()
    log_dir = project_root / "outputs" / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"review_ready_flow_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    latest_log_path = log_dir / "latest_review_ready_flow.jsonl"
    if latest_log_path.exists():
        latest_log_path.unlink()

    write_event(log_path, {"event": "flow_start", "project_root": str(project_root)})
    try:
        app = Application(project_root=project_root)
        app.initialize()
        assert app.config is not None
        if app.config.video_submit_enabled:
            raise RuntimeError("安全中止：video.submit_enabled 必须为 false，才可运行审核前流程")

        search_result = run_task(app, SearchTask, log_path)
        summary_result = run_task(app, SummaryTask, log_path)
        storyboard_result = run_task(app, ShortVideoPromptTask, log_path)
        image_result = run_task(app, ImageTask, log_path)
        audio_result = run_task(app, AudioTask, log_path)
        storage_result = run_task(app, StorageTask, log_path)
        preview_result = run_task(app, PreviewTask, log_path)
        cat_result = run_task(app, CatTask, log_path)

        final_payload = {
            "event": "flow_finish",
            "content_id": summary_result.metadata.get("content_id"),
            "content_status": summary_result.metadata.get("status"),
            "title": summary_result.metadata.get("title"),
            "ranking_count": search_result.metadata.get("ranking_count"),
            "image_created_count": image_result.metadata.get("created_image_count"),
            "providers_used": image_result.metadata.get("providers_used"),
            "audio_asset_id": audio_result.metadata.get("asset_id") or audio_result.metadata.get("reusable_audio_asset_id"),
            "audio_provider": audio_result.metadata.get("provider"),
            "audio_skipped": audio_result.metadata.get("skipped", False),
            "storyboard_id": storyboard_result.metadata.get("storyboard_id"),
            "preview_path": preview_result.metadata.get("output_path"),
            "storage_uploaded_count": storage_result.metadata.get("uploaded_count"),
            "cat_run_id": cat_result.run_id,
            "video_submission": "disabled",
            "wechat_draft": "not_created_pending_manual_approval",
        }
        write_event(log_path, final_payload)
        print(json.dumps(final_payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        write_event(
            log_path,
            {
                "event": "flow_error",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        print(f"审核前真实生产流程失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
