from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.platform_access.bootstrap import bootstrap_first_admin
from src.platform_access.contracts import PlatformRole, PlatformUser


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
