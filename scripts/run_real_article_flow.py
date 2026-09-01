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
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.tasks.article_layout_task import ArticleLayoutTask
from src.tasks.deliver_task import DeliverTask
from src.tasks.image_task import ImageTask
from src.tasks.summary_task import SummaryTask


def _utc_now() -> str:
    """返回日志使用的 UTC 时间字符串。"""

    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _json_default(value: Any) -> str:
    """避免日志序列化失败；不输出不可控对象细节。"""

    return str(value)


def _write_event(log_path: Path, event: dict[str, Any]) -> None:
    """追加一行 JSON 日志，方便主进程轮询真实流程进度。"""

    payload = {"time": _utc_now(), **event}
    with log_path.open("a", encoding="utf-8") as file:
        line = json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n"
        file.write(line)
    latest_log_path = log_path.parent / "latest_real_article_flow.jsonl"
    with latest_log_path.open("a", encoding="utf-8") as file:
        file.write(line)


def _run_task(app: Application, task_class: type, log_path: Path) -> dict[str, Any]:
    """运行单个 Task，并把关键结果写入进度日志。"""

    _write_event(log_path, {"event": "task_start", "task": task_class.__name__})
    result = app._run_task(task_class)
    result_payload = {
        "task_name": result.task_name,
        "run_id": result.run_id,
        "metadata": result.metadata,
    }
    _write_event(
        log_path,
        {
            "event": "task_finish",
            "task": task_class.__name__,
            "run_id": result.run_id,
            "metadata": result.metadata,
        },
    )
    return result_payload


def main() -> int:
    """按用户确认的新规则真实执行：总结、原始生图、排版、推送草稿。"""

    project_root = Path.cwd()
    log_dir = project_root / "outputs" / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"real_article_flow_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    latest_log_path = log_dir / "latest_real_article_flow.jsonl"
    if latest_log_path.exists():
        latest_log_path.unlink()

    _write_event(log_path, {"event": "flow_start", "project_root": str(project_root)})

    try:
        app = Application(project_root=project_root)
        app.initialize()

        summary_result = _run_task(app, SummaryTask, log_path)
        content_id = int(summary_result["metadata"]["content_id"])
        assert app.database_manager is not None
        GeneratedContentRepository(database_manager=app.database_manager).update_status(
            content_id=content_id,
            status="approved",
        )
        _write_event(log_path, {"event": "content_approved", "content_id": content_id})

        image_result = _run_task(app, ImageTask, log_path)
        layout_result = _run_task(app, ArticleLayoutTask, log_path)
        deliver_result = _run_task(app, DeliverTask, log_path)

        final_payload = {
            "event": "flow_finish",
            "content_id": content_id,
            "title": summary_result["metadata"].get("title"),
            "image_created_count": image_result["metadata"].get("created_image_count"),
            "seedream_image_count": image_result["metadata"].get("seedream_image_count"),
            "layout_id": layout_result["metadata"].get("layout_id"),
            "draft_record_id": deliver_result["metadata"].get("draft_record_id"),
            "wechat_draft_id": deliver_result["metadata"].get("wechat_draft_id"),
        }
        _write_event(log_path, final_payload)
        print(json.dumps(final_payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        _write_event(
            log_path,
            {
                "event": "flow_error",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        print(f"真实流程执行失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
