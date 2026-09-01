"""验证 Skill 种子导入不会覆盖 WebUI 已保存的版本。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.skill_portability import export_skill_trees, seed_skill_tree


def write_skill(path: Path, name: str, description: str) -> None:
    """创建最小有效 SKILL.md 测试样本。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n", encoding="utf-8")


def main() -> int:
    """运行可重复、无网络依赖的便携性验证。"""

    with tempfile.TemporaryDirectory(prefix="skill-portability-") as temporary_dir:
        root = Path(temporary_dir)
        source_root = root / "source"
        seed_root = root / "seed"
        volume_root = root / "volume"

        write_skill(source_root / "find-skills" / "SKILL.md", "find-skills", "发现开放 Skill。")
        secondary_source_root = root / "secondary-source"
        write_skill(secondary_source_root / "duplicate" / "SKILL.md", "find-skills", "后续来源的同名版本。")
        export_result = export_skill_trees(
            source_roots=[source_root, secondary_source_root],
            destination_root=seed_root,
        )
        assert export_result.copied_count == 1
        assert export_result.skipped_count == 1
        assert (seed_root / "find-skills" / "SKILL.md").is_file()
        assert "发现开放 Skill" in (seed_root / "find-skills" / "SKILL.md").read_text(encoding="utf-8")

        first_seed_result = seed_skill_tree(seed_root=seed_root, destination_root=volume_root)
        assert first_seed_result.copied_count == 1
        destination_file = volume_root / "find-skills" / "SKILL.md"
        assert "发现开放 Skill" in destination_file.read_text(encoding="utf-8")

        # 模拟用户在 WebUI 修改后的项目本地版本；再次部署不能覆盖它。
        write_skill(destination_file, "find-skills", "用户在 WebUI 保存的版本。")
        second_seed_result = seed_skill_tree(seed_root=seed_root, destination_root=volume_root)
        assert second_seed_result.copied_count == 0
        assert second_seed_result.skipped_count == 1
        assert "用户在 WebUI 保存的版本" in destination_file.read_text(encoding="utf-8")

        # 新版本镜像加入新的种子时，已有卷仍应增量获得它。
        write_skill(seed_root / "image-layout" / "SKILL.md", "image-layout", "公众号插图排版。")
        third_seed_result = seed_skill_tree(seed_root=seed_root, destination_root=volume_root)
        assert third_seed_result.copied_count == 1
        assert (volume_root / "image-layout" / "SKILL.md").is_file()

    print("skill_portability_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
