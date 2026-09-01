from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.application import Application
from src.tasks.image_task import ImageTask


def main() -> int:
    """只重新生成当前最新内容的无文字 Seedream 原始插图。"""

    app = Application(project_root=Path.cwd())
    app.initialize()
    result = app._run_task(ImageTask)
    print(result.__dict__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
