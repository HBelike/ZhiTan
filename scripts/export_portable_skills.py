"""将经过用户选择的本地 Skill 导出为可随项目部署的种子。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.skill_portability import export_skill_trees


def parse_args() -> argparse.Namespace:
    """解析导出参数；支持显式目录或本机可见 Skill 全量目录。"""

    parser = argparse.ArgumentParser(description="导出本地 SKILL.md 到项目部署种子目录。")
    parser.add_argument(
        "--source-root",
        type=Path,
        action="append",
        default=[],
        help="需要导出的 Skill 根目录。可重复指定，按传入顺序按 Skill name 去重。",
    )
    parser.add_argument(
        "--local-catalog",
        action="store_true",
        help="导出本机 ~/.agents、~/.codex 和已安装插件缓存中的全部 SKILL.md。",
    )
    parser.add_argument(
        "--destination-root",
        type=Path,
        default=PROJECT_ROOT / "deploy" / "skill-seeds",
        help="项目内种子目录。",
    )
    parser.add_argument("--overwrite", action="store_true", help="明确允许覆盖已审查的种子文件。")
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=None,
        help="可选目录清单路径；默认写入 <destination-root>/catalog.manifest.json。",
    )
    return parser.parse_args()


def main() -> int:
    """执行导出并输出相对文件清单。"""

    args = parse_args()
    source_roots = list(args.source_root)
    if args.local_catalog:
        source_roots.extend(
            [
                Path.home() / ".agents" / "skills",
                Path.home() / ".codex" / "skills",
                Path.home() / ".codex" / "plugins" / "cache",
            ]
        )
    if not source_roots:
        raise SystemExit("请至少提供一个 --source-root，或使用 --local-catalog。")

    result = export_skill_trees(
        source_roots=source_roots,
        destination_root=args.destination_root,
        overwrite=args.overwrite,
    )
    manifest_path = args.manifest_path or args.destination_root / "catalog.manifest.json"
    manifest = build_manifest(args.destination_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "event": "portable_skill_export_completed",
                "copied": result.copied_count,
                "skipped_existing": result.skipped_count,
                "copied_paths": [str(item).replace("\\", "/") for item in result.copied],
                "catalog_total": manifest["total"],
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_manifest(destination_root: Path) -> dict[str, object]:
    """从导出的种子目录生成仅含名称和校验值的可审查清单。"""

    root = destination_root.resolve()
    skills: list[dict[str, str]] = []
    for skill_file in sorted(root.rglob("SKILL.md"), key=lambda item: str(item).casefold()):
        relative_path = skill_file.relative_to(root).as_posix()
        digest = hashlib.sha256(skill_file.read_bytes()).hexdigest()
        skills.append(
            {
                "name": skill_file.parent.name,
                "path": relative_path,
                "sha256": digest,
            }
        )
    return {
        "format": 1,
        "description": "仅包含可部署 SKILL.md 的名称、相对路径与 SHA-256；不包含本机绝对路径、缓存或凭据。",
        "total": len(skills),
        "skills": skills,
    }


if __name__ == "__main__":
    raise SystemExit(main())
