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


def test_readme_product_screenshots_are_repository_assets() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    references = set(
        re.findall(
            r'(?:src="|\]\()(docs/assets/product/[a-z0-9-]+\.png)',
            readme,
        )
    )

    assert len(references) == 7
    for reference in references:
        assert (PROJECT_ROOT / reference).is_file(), reference


def test_auth_recovery_and_registration_configuration_are_documented() -> None:
    text = _active_text()

    assert "bootstrap_first_admin.py --reset-password" in text
    assert "PLATFORM_PUBLIC_REGISTRATION_ENABLED=true" in text
    assert "RESEND_API_KEY" in text
    assert "RESEND_FROM_ADDRESS" in text


def test_email_onboarding_uses_official_links_and_never_a_maintainer_key() -> None:
    text = _active_text()

    assert "https://resend.com/signup" in text
    assert "https://resend.com/docs/create-an-api-key" in text
    assert "https://resend.com/docs/add-a-domain" in text
    assert "re_your_own_key" in text
    assert "never use a maintainer's key" in text.lower()
    assert not re.search(r"\bre_[A-Za-z0-9_-]{20,}\b", text)


def test_email_registration_docs_keep_a_provider_free_login_path() -> None:
    text = _active_text()

    assert "Email delivery is optional" in text
    assert "Password login remains available" in text
    assert "bootstrap_first_admin.py --reset-password" in text
