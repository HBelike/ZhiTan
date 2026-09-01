from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL_FIXTURE = "tests/fixtures/skills/minimal-public-skill/SKILL.md"


def _tracked_paths() -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return {
        item.replace("\\", "/")
        for item in completed.stdout.decode("utf-8").split("\0")
        if item
    }


def test_public_skill_fixture_is_part_of_the_checkout() -> None:
    assert PUBLIC_SKILL_FIXTURE in _tracked_paths()


def test_public_skill_manifest_matches_tracked_seed_files() -> None:
    manifest_path = PROJECT_ROOT / "deploy/skill-seeds/catalog.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tracked_seed_files = {
        path
        for path in _tracked_paths()
        if path.startswith("deploy/skill-seeds/") and path.endswith("/SKILL.md")
    }

    assert manifest["total"] == len(manifest["skills"])
    assert manifest["total"] == len(tracked_seed_files)
    assert manifest["skills"] == []
