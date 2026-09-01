from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ci_uses_locks_and_gates_master_on_quickstart_smoke() -> None:
    workflow_text = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]

    assert "quickstart-smoke" in jobs
    assert "requirements.lock.txt" in workflow_text
    assert "npm --prefix web-ui ci" in workflow_text
    assert "scripts/ci_quickstart_smoke.sh" in workflow_text
    assert "docker compose --env-file .env.quickstart down -v --remove-orphans" in workflow_text
    assert "COMPOSE_PROJECT_NAME: zhitan-ci-${{ github.run_id }}-${{ github.run_attempt }}" in workflow_text


def test_provider_smoke_is_manual_and_not_a_pull_request_gate() -> None:
    provider_text = (PROJECT_ROOT / ".github/workflows/provider-smoke.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in provider_text
    assert "pull_request:" not in provider_text
    assert "push:" not in provider_text


def test_ci_smoke_passes_password_only_through_stdin() -> None:
    script = (PROJECT_ROOT / "scripts/ci_quickstart_smoke.sh").read_text(encoding="utf-8")

    assert "create_ephemeral_test_admin.py" in script
    assert "verify_quickstart.py" in script
    assert "printf '%s\\n' \"$TEST_ADMIN_PASSWORD\" |" in script
    assert "--password" not in script
