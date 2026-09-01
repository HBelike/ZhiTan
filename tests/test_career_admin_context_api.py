"""管理员模型上下文策略 API 测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.persistence import (
    ModelContextPolicy,
    ModelCostTier,
    ModelProfileRecord,
)
from src.career_assistant.web import admin_context_router
from src.platform_access.contracts import PlatformRole, PlatformUser
from src.platform_access.web import get_current_platform_user


def user(role: PlatformRole) -> PlatformUser:
    now = datetime.now(UTC)
    return PlatformUser(
        id=uuid4(),
        organization_id=uuid4(),
        username="tester",
        display_name="测试用户",
        email=None,
        email_verified_at=None,
        role=role,
        is_active=True,
        created_at=now,
    )


def profile(policy: ModelContextPolicy | None = None) -> ModelProfileRecord:
    now = datetime.now(UTC)
    return ModelProfileRecord(
        id=uuid4(),
        organization_id=uuid4(),
        profile_key="deepseek-v4",
        display_name="DeepSeek V4",
        provider_key="deepseek",
        model_id="deepseek-v4",
        capabilities=frozenset({ModelCapability.TEXT}),
        cost_tier=ModelCostTier.PAID,
        priority=100,
        enabled=True,
        api_base_url=None,
        created_at=now,
        updated_at=now,
        context_policy=policy or ModelContextPolicy(),
    )


def make_client(role: PlatformRole, repository: Mock) -> TestClient:
    app = FastAPI()
    app.include_router(admin_context_router.router)
    app.dependency_overrides[get_current_platform_user] = lambda: user(role)
    services = SimpleNamespace(model_profile_repository=repository)
    patcher = patch.object(admin_context_router, "get_career_read_services", return_value=services)
    patcher.start()
    client = TestClient(app)
    client._career_patcher = patcher  # type: ignore[attr-defined]
    return client


def close_client(client: TestClient) -> None:
    client.close()
    client._career_patcher.stop()  # type: ignore[attr-defined]


def test_regular_user_cannot_read_exact_context_policy() -> None:
    repository = Mock()
    client = make_client(PlatformRole.USER, repository)
    try:
        response = client.get(
            "/api/admin/career/model-profiles/deepseek-v4/context-policy",
        )
    finally:
        close_client(client)

    assert response.status_code == 403
    repository.get_profile_by_key.assert_not_called()


def test_admin_reads_exact_context_policy() -> None:
    repository = Mock()
    repository.get_profile_by_key.return_value = profile(
        ModelContextPolicy(
            context_window_tokens=64_000,
            reserved_output_tokens=4_096,
            context_window_source="admin",
        ),
    )
    client = make_client(PlatformRole.ADMIN, repository)
    try:
        response = client.get(
            "/api/admin/career/model-profiles/deepseek-v4/context-policy",
        )
    finally:
        close_client(client)

    assert response.status_code == 200
    assert response.json()["item"]["context_window_tokens"] == 64_000
    assert response.json()["item"]["compression_trigger_percent"] == 80


def test_admin_rejects_target_above_trigger() -> None:
    repository = Mock()
    client = make_client(PlatformRole.ADMIN, repository)
    try:
        response = client.put(
            "/api/admin/career/model-profiles/deepseek-v4/context-policy",
            json={
                "context_window_tokens": 64_000,
                "reserved_output_tokens": 4_096,
                "compression_trigger_percent": 70,
                "compression_target_percent": 75,
                "context_window_source": "admin",
            },
        )
    finally:
        close_client(client)

    assert response.status_code == 422
    repository.update_context_policy.assert_not_called()


def test_admin_updates_valid_policy() -> None:
    repository = Mock()
    repository.update_context_policy.return_value = profile(
        ModelContextPolicy(
            context_window_tokens=128_000,
            reserved_output_tokens=8_192,
            context_window_source="admin",
        ),
    )
    client = make_client(PlatformRole.ADMIN, repository)
    try:
        response = client.put(
            "/api/admin/career/model-profiles/deepseek-v4/context-policy",
            json={
                "context_window_tokens": 128_000,
                "reserved_output_tokens": 8_192,
                "compression_trigger_percent": 80,
                "compression_target_percent": 60,
                "context_window_source": "admin",
            },
        )
    finally:
        close_client(client)

    assert response.status_code == 200
    written_policy = repository.update_context_policy.call_args.args[2]
    assert written_policy.context_window_tokens == 128_000


def test_admin_accepts_verified_large_model_output_capability() -> None:
    repository = Mock()
    repository.update_context_policy.return_value = profile(
        ModelContextPolicy(
            context_window_tokens=1_048_576,
            reserved_output_tokens=384_000,
            context_window_source="admin",
        ),
    )
    client = make_client(PlatformRole.ADMIN, repository)
    try:
        response = client.put(
            "/api/admin/career/model-profiles/deepseek-v4/context-policy",
            json={
                "context_window_tokens": 1_048_576,
                "reserved_output_tokens": 384_000,
                "compression_trigger_percent": 80,
                "compression_target_percent": 60,
                "context_window_source": "admin",
            },
        )
    finally:
        close_client(client)

    assert response.status_code == 200
    written_policy = repository.update_context_policy.call_args.args[2]
    assert written_policy.reserved_output_tokens == 384_000
