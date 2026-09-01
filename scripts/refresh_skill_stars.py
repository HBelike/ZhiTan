"""按周刷新项目可见 Skill 的 GitHub Star 快照。

该脚本不参与 Web 请求。生产环境可由 crontab 或已有 scheduler 每天运行一次；服务会
自行跳过七天内未过期的仓库，因此不会重复消耗 GitHub API 配额。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.config_manager import ConfigManager
from src.services.skill_library_service import SkillLibraryService


def main() -> int:
    """执行一次过期 Star 快照刷新，并输出不含凭据的摘要。"""

    config = ConfigManager(project_root=PROJECT_ROOT).load()
    service = SkillLibraryService(config=config)
    summary = service.refresh_stale_star_snapshots()
    print(json.dumps({"event": "skill_star_refresh_completed", **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
