"""验证 Resend 邮件适配器的错误分类与失败重试语义。

脚本不读取真实 Key、不调用 Resend，也不会发送邮件。它使用 HTTP 响应替身覆盖：
1. 测试域名投递限制；2. 未验证发件域名；3. 成功投递日志路径；4. 投递失败后挑战清理。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from requests import Response


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.platform_access.email_delivery import EmailDeliveryError, ResendEmailDelivery, ResendEmailSettings
from src.platform_access.service import PlatformAccessService


class FakeEmailRepository:
    """用于验证失败清理语义的最小仓储替身，不连接真实 PostgreSQL。"""

    def __init__(self) -> None:
        self.discarded: list[str] = []

    def create_email_challenge(self, **_: object) -> dict[str, object]:
        return {"id": "11111111-1111-1111-1111-111111111111"}

    def discard_email_challenge(self, challenge_id: str) -> None:
        self.discarded.append(challenge_id)


class RejectingDelivery:
    """模拟 Resend 拒绝投递。"""

    def send_verification_code(self, **_: object) -> None:
        raise EmailDeliveryError("模拟投递失败", reason="simulated")


def _response(status_code: int, body: str) -> Response:
    """构造 requests 兼容的 JSON 响应。"""

    item = Response()
    item.status_code = status_code
    item._content = body.encode("utf-8")
    item.headers["Content-Type"] = "application/json"
    return item


def verify_test_domain_error() -> None:
    """Resend 默认测试域名必须给出可执行的中文修复说明。"""

    delivery = ResendEmailDelivery(
        ResendEmailSettings(api_key="test-key", from_address="职业智能工作台 <onboarding@resend.dev>"),
    )
    with patch("src.platform_access.email_delivery.requests.post", return_value=_response(403, '{"message":"You can only send testing emails to your own email address"}')):
        try:
            delivery.send_verification_code(recipient="someone@qq.com", code="123456", purpose="register")
        except EmailDeliveryError as exc:
            assert exc.reason == "resend_test_domain_restriction"
            assert "验证自己的域名" in str(exc)
        else:
            raise AssertionError("应识别 Resend 测试域名限制")


def verify_unverified_domain_error() -> None:
    """未验证域名响应应提示 SPF/DKIM，而不是笼统归为 Key 错误。"""

    delivery = ResendEmailDelivery(
        ResendEmailSettings(api_key="test-key", from_address="职业智能工作台 <no-reply@example.com>"),
    )
    with patch("src.platform_access.email_delivery.requests.post", return_value=_response(403, '{"message":"The example.com domain is not verified"}')):
        try:
            delivery.send_verification_code(recipient="someone@qq.com", code="123456", purpose="reset_password")
        except EmailDeliveryError as exc:
            assert exc.reason == "sender_domain_unverified"
            assert "SPF" in str(exc) and "DKIM" in str(exc)
        else:
            raise AssertionError("应识别未验证发件域名")


def verify_api_key_error_has_priority() -> None:
    """即使仍在测试发件域名下，明确的 Key 错误也不能被误报为发件域名限制。"""

    delivery = ResendEmailDelivery(
        ResendEmailSettings(api_key="test-key", from_address="职业智能工作台 <onboarding@resend.dev>"),
    )
    with patch("src.platform_access.email_delivery.requests.post", return_value=_response(403, '{"message":"Invalid API key"}')):
        try:
            delivery.send_verification_code(recipient="someone@qq.com", code="123456", purpose="register")
        except EmailDeliveryError as exc:
            assert exc.reason == "api_key_invalid"
            assert "API Key" in str(exc)
        else:
            raise AssertionError("应优先识别 API Key 错误")


def verify_failed_delivery_discards_challenge() -> None:
    """邮件失败时不保留挑战，修复发件配置后可立即重新申请验证码。"""

    repository = FakeEmailRepository()
    service = PlatformAccessService(
        repository,  # type: ignore[arg-type]
        email_delivery=RejectingDelivery(),  # type: ignore[arg-type]
        verification_secret="x" * 32,
    )
    try:
        service._send_challenge(
            email="someone@example.com",
            purpose="register",
            payload={},
        )
    except EmailDeliveryError:
        pass
    else:
        raise AssertionError("模拟投递失败应向上返回")
    assert repository.discarded == ["11111111-1111-1111-1111-111111111111"]


def main() -> None:
    """执行全部离线验证。"""

    verify_test_domain_error()
    verify_unverified_domain_error()
    verify_api_key_error_has_priority()
    verify_failed_delivery_discards_challenge()
    print("platform_email_delivery_ok")


if __name__ == "__main__":
    main()
