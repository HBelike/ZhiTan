"""把生产镜像携带的 Skill 种子初始化到 Docker 持久卷。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.skill_portability import seed_skill_tree


def parse_args() -> argparse.Namespace:
    """解析生产初始化所需参数。"""

    parser = argparse.ArgumentParser(description="初始化项目本地 Skill 持久目录。")
    parser.add_argument(
        "--seed-root",
        type=Path,
        default=PROJECT_ROOT / "deploy" / "skill-seeds",
        help="镜像内 Skill 种子目录。",
    )
    parser.add_argument(
        "--destination-root",
        type=Path,
        default=PROJECT_ROOT / ".agents" / "skills",
        help="持久 Skill 卷在容器中的挂载目录。",
    )
    parser.add_argument("--overwrite", action="store_true", help="明确允许用种子覆盖已有本地 Skill。")
    parser.add_argument(
        "--owner",
        default=None,
        help="可选 user:group，仅 root 初始化容器使用，例如 career:career。",
    )
    return parser.parse_args()


def main() -> int:
    """执行种子初始化并输出不含敏感路径的 JSON 摘要。"""

    args = parse_args()
    result = seed_skill_tree(
        seed_root=args.seed_root,
        destination_root=args.destination_root,
        overwrite=args.overwrite,
        owner=args.owner,
    )
    print(
        json.dumps(
            {
                "event": "project_skill_seed_completed",
                "copied": result.copied_count,
                "skipped_existing": result.skipped_count,
                "copied_paths": [str(item).replace("\\", "/") for item in result.copied],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
