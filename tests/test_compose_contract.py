from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_SERVICES = {
    "career-postgres",
    "career-migrate",
    "career-api",
    "career-agent-worker",
    "career-web",
}


def _load(name: str) -> dict:
    return yaml.safe_load((PROJECT_ROOT / name).read_text(encoding="utf-8"))


def test_quickstart_contains_only_the_isolated_core_stack() -> None:
    compose = _load("compose.yaml")

    assert "name" not in compose
    assert set(compose["services"]) == CORE_SERVICES
    assert all("container_name" not in service for service in compose["services"].values())
    assert all("name" not in volume for volume in compose["volumes"].values())


def test_registration_policy_can_be_enabled_by_the_operator() -> None:
    compose = _load("compose.yaml")

    assert compose["x-career-environment"]["PLATFORM_PUBLIC_REGISTRATION_ENABLED"] == (
        "${PLATFORM_PUBLIC_REGISTRATION_ENABLED:-false}"
    )


def test_quickstart_exposes_only_the_local_web_port() -> None:
    compose = _load("compose.yaml")
    services_with_ports = {
        name: service["ports"]
        for name, service in compose["services"].items()
        if "ports" in service
    }

    assert services_with_ports == {
        "career-web": ["127.0.0.1:${ZHITAN_HTTP_PORT:-18081}:80"],
    }


def test_quickstart_waits_for_database_migration_and_runtime_readiness() -> None:
    services = _load("compose.yaml")["services"]

    assert services["career-migrate"]["depends_on"]["career-postgres"]["condition"] == "service_healthy"
    assert services["career-api"]["depends_on"]["career-migrate"]["condition"] == "service_completed_successfully"
    assert services["career-agent-worker"]["depends_on"]["career-migrate"]["condition"] == "service_completed_successfully"
    assert services["career-migrate"]["build"]["dockerfile"] == "docker/Dockerfile.api"
    assert services["career-api"]["pull_policy"] == "never"
    assert services["career-agent-worker"]["pull_policy"] == "never"
    assert "/api/ready" in " ".join(services["career-api"]["healthcheck"]["test"])
    assert "check_career_worker_health.py" in " ".join(
        services["career-agent-worker"]["healthcheck"]["test"],
    )
    assert "/api/ready" in " ".join(services["career-web"]["healthcheck"]["test"])


def test_heavy_and_public_services_live_only_in_overlays() -> None:
    production_services = set(_load("compose.production.yaml")["services"])
    document_services = set(_load("compose.document.yaml")["services"])

    assert {"caddy", "pipeline-scheduler"} <= production_services
    assert {"career-gotenberg", "career-docling"} <= document_services
    assert not ({"caddy", "pipeline-scheduler", "career-gotenberg", "career-docling"} & CORE_SERVICES)
