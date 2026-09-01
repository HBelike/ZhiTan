from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts.verify_quickstart import verify_post_bootstrap, verify_pre_bootstrap


@dataclass
class _Response:
    status_code: int
    payload: dict
    text: str = ""

    def json(self) -> dict:
        return self.payload


class _Session:
    def __init__(self, responses: dict[tuple[str, str], _Response]) -> None:
        self.responses = responses

    def get(self, url: str, **_kwargs) -> _Response:
        return self.responses[("GET", url)]

    def post(self, url: str, **_kwargs) -> _Response:
        return self.responses[("POST", url)]


def test_pre_bootstrap_verifies_health_readiness_pending_and_401() -> None:
    base = "http://127.0.0.1:18081"
    session = _Session(
        {
            ("GET", f"{base}/api/health"): _Response(200, {"status": "ok"}),
            ("GET", f"{base}/api/ready"): _Response(
                200,
                {"ready": True, "checks": [{"name": "database", "ok": True}]},
            ),
            ("GET", f"{base}/api/auth/bootstrap-status"): _Response(
                200,
                {"requires_bootstrap": True},
            ),
            ("GET", f"{base}/api/navigation/modules"): _Response(401, {"detail": "请先登录"}),
        },
    )

    verify_pre_bootstrap(base, session=session)


def test_post_bootstrap_performs_real_password_login_and_me_request() -> None:
    base = "http://127.0.0.1:18081"
    session = _Session(
        {
            ("GET", f"{base}/api/auth/bootstrap-status"): _Response(
                200,
                {"requires_bootstrap": False},
            ),
            ("POST", f"{base}/api/auth/login"): _Response(
                200,
                {"user": {"role": "admin"}},
            ),
            ("GET", f"{base}/api/auth/me"): _Response(
                200,
                {"user": {"role": "admin"}},
            ),
        },
    )

    verify_post_bootstrap(
        base,
        admin_email="owner@example.test",
        password="random-password",
        session=session,
    )


def test_pre_bootstrap_rejects_any_failed_readiness_check() -> None:
    base = "http://127.0.0.1:18081"
    session = _Session(
        {
            ("GET", f"{base}/api/health"): _Response(200, {"status": "ok"}),
            ("GET", f"{base}/api/ready"): _Response(
                503,
                {"ready": False, "checks": [{"name": "database", "ok": False}]},
            ),
        },
    )

    with pytest.raises(RuntimeError, match="ready"):
        verify_pre_bootstrap(base, session=session)
