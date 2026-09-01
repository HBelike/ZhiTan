from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.application import Application
from src.tasks.article_layout_task import ArticleLayoutTask
from src.tasks.deliver_task import DeliverTask
from src.tasks.image_task import ImageTask


def _write_event(log_path: Path, event: dict[str, Any]) -> None:
    """写入一行流程进度日志。"""

    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        **event,
    }
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    with log_path.open("a", encoding="utf-8") as file:
        file.write(line)


def _run_task(app: Application, task_class: type, log_path: Path) -> None:
    """运行一个任务并记录关键 metadata。"""

    _write_event(log_path, {"event": "task_start", "task": task_class.__name__})
    result = app._run_task(task_class)
    _write_event(
        log_path,
        {
            "event": "task_finish",
            "task": task_class.__name__,
            "run_id": result.run_id,
            "metadata": result.metadata,
        },
    )


def main() -> int:
    """重新生成教学风格插图，并推送一版新公众号草稿。"""

    project_root = Path.cwd()
    log_dir = project_root / "outputs" / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "latest_teaching_style_flow.jsonl"
    if log_path.exists():
        log_path.unlink()

    _write_event(log_path, {"event": "flow_start"})
    app = Application(project_root=project_root)
    app.initialize()

    for task_class in (ImageTask, ArticleLayoutTask, DeliverTask):
        _run_task(app=app, task_class=task_class, log_path=log_path)

    _write_event(log_path, {"event": "flow_finish"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
