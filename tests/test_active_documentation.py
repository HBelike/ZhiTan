from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTIVE_DOCUMENTS = (
    "README.md",
    "CONTRIBUTING.md",
    "docs/getting-started.md",
    "docs/local-development.md",
    "docs/production-deployment.md",
    "docs/troubleshooting.md",
)


def _active_text() -> str:
    return "\n".join(
        (PROJECT_ROOT / path).read_text(encoding="utf-8")
        for path in ACTIVE_DOCUMENTS
    )


def test_active_documentation_uses_the_current_quickstart_contract() -> None:
    text = _active_text()

    assert "ZHITAN_HTTP_PORT=18081" in text
    assert "docker compose --env-file .env.quickstart up -d --build --wait" in text
    assert "docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py" in text
    assert "http://127.0.0.1:18081/api/ready" in text
    assert "docker compose --env-file .env.quickstart down" in text
    assert "docker compose --env-file .env.quickstart down -v" in text


def test_active_documentation_does_not_present_historical_state_as_current() -> None:
    text = _active_text()

    assert "docker-compose.production.yml" not in text
    assert "57 Skill" not in text
    assert "git pull --ff-only origin main" not in text
    addresses = set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text))
    assert addresses <= {"127.0.0.1", "0.0.0.0"}


def test_source_installation_uses_the_project_virtual_environment() -> None:
    local_development = (PROJECT_ROOT / "docs/local-development.md").read_text(encoding="utf-8")

    assert ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.lock.txt" in local_development
    assert ".venv/bin/python -m pip install -r requirements.lock.txt" in local_development
    assert "python -m pip install -r requirements.txt" not in local_development


def test_readme_links_to_each_active_guide() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for path in ACTIVE_DOCUMENTS[2:]:
        assert path in readme
