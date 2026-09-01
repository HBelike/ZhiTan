from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DECLARATION_FILES = (
    "requirements.txt",
    "requirements-career-assistant.txt",
    "requirements-development.txt",
)


def _requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if match:
            names.add(match.group(1).casefold().replace("_", "-"))
    return names


def test_lock_contains_every_direct_dependency_without_local_paths() -> None:
    lock_path = PROJECT_ROOT / "requirements.lock.txt"
    locked = _requirement_names(lock_path)
    declared = set().union(
        *(_requirement_names(PROJECT_ROOT / name) for name in DECLARATION_FILES),
    )
    content = lock_path.read_text(encoding="utf-8")

    assert declared <= locked
    assert "file://" not in content
    assert "-e " not in content
    assert "C:\\" not in content
    assert "/Users/" not in content


def test_api_image_installs_only_the_lock_file() -> None:
    dockerfile = (PROJECT_ROOT / "docker/Dockerfile.api").read_text(encoding="utf-8")

    assert "COPY requirements.lock.txt ./" in dockerfile
    assert "pip install -r requirements.lock.txt" in dockerfile
    assert "pip install -r requirements.txt" not in dockerfile
