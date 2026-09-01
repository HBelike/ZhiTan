from __future__ import annotations

import sys
from pathlib import Path

from src.app.application import Application


PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> int:
    """程序主入口，负责把控制权交给 Application。"""
    app = Application(project_root=PROJECT_ROOT)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
