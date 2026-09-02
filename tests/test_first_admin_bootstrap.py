from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.platform_access.bootstrap import (
    FirstAdminBootstrapError,
    bootstrap_first_admin,
    reset_configured_admin_password,
)
from src.platform_access.contracts import PlatformRole, PlatformUser
from src.platform_access.security import verify_password


class _Repository:
    def __init__(self) -> None:
        self.created_email: str | None = None

    def has_active_admin(self) -> bool:
        return False

    def find_user_by_email(self, _email: str):
        return None

    def create_first_admin(self, **values) -> PlatformUser:
        self.created_email = values["email"]
        return PlatformUser(
            id=uuid4(),
            organization_id=uuid4(),
            username=values["username"],
            display_name=values["display_name"],
            email=values["email"],
            email_verified_at=values["email_verified_at"],
            role=PlatformRole.ADMIN,
            is_active=True,
            created_at=datetime.now(UTC),
        )


def test_bootstrap_reads_admin_email_after_modules_are_imported(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "owner@example.test")
    repository = _Repository()

    user = bootstrap_first_admin(
        repository,
        email="OWNER@example.test",
        display_name="Owner",
        password="secure-password",
    )

    assert user.email == "owner@example.test"
    assert repository.created_email == "owner@example.test"


class _ResetRepository:
    def __init__(self, user: PlatformUser | None) -> None:
        self.user = user
        self.changed_user_id = None
        self.changed_password_hash = ""

    def find_user_by_email(self, _email: str):
        if self.user is None:
            return None
        return self.user, "old-password-hash"

    def change_password_and_revoke_sessions(self, user_id, password_hash: str) -> None:
        self.changed_user_id = user_id
        self.changed_password_hash = password_hash


def _platform_user(*, role: PlatformRole = PlatformRole.ADMIN, active: bool = True) -> PlatformUser:
    return PlatformUser(
        id=uuid4(),
        organization_id=uuid4(),
        username="admin-test",
        display_name="Owner",
        email="owner@example.test",
        email_verified_at=datetime.now(UTC),
        role=role,
        is_active=active,
        created_at=datetime.now(UTC),
    )


def test_reset_configured_admin_password_hashes_password_and_revokes_sessions(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "owner@example.test")
    user = _platform_user()
    repository = _ResetRepository(user)

    result = reset_configured_admin_password(
        repository,
        email="OWNER@example.test",
        password="replacement-password",
    )

    assert result == user
    assert repository.changed_user_id == user.id
    assert repository.changed_password_hash != "replacement-password"
    assert verify_password("replacement-password", repository.changed_password_hash)


@pytest.mark.parametrize(
    "user",
    [None, _platform_user(role=PlatformRole.USER), _platform_user(active=False)],
)
def test_reset_configured_admin_password_rejects_missing_or_ineligible_admin(monkeypatch, user) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "owner@example.test")

    with pytest.raises(FirstAdminBootstrapError, match="管理员"):
        reset_configured_admin_password(
            _ResetRepository(user),
            email="owner@example.test",
            password="replacement-password",
        )
