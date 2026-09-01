"""平台账号验证码的邮件投递适配器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html import escape
from typing import Any

import requests


logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """邮件服务不可用时给 Web 层的可读错误。

    ``reason`` 只用于服务端日志和后续观测，不会携带验证码、API Key 或收件人完整地址。
    """

    def __init__(self, message: str, *, reason: str = "unknown", status_code: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class ResendEmailSettings:
    """Resend 所需的服务端配置，不向浏览器暴露 API Key。"""

    api_key: str
    from_address: str
    timeout_seconds: int = 12


class ResendEmailDelivery:
    """通过 Resend REST API 投递事务邮件。"""

    endpoint = "https://api.resend.com/emails"

    def __init__(self, settings: ResendEmailSettings) -> None:
        if not settings.api_key.strip():
            raise ValueError("尚未配置 RESEND_API_KEY")
        if not settings.from_address.strip():
            raise ValueError("尚未配置 RESEND_FROM_ADDRESS")
        self._settings = settings

    def send_verification_code(self, *, recipient: str, code: str, purpose: str) -> None:
        """发送十分钟有效的一次性验证码，失败时不隐藏服务端根因。"""

        subject = "职业智能工作台验证码"
        purpose_label = {
            "login": "登录平台",
            "register": "完成注册",
            "bootstrap": "初始化管理员账号",
            "bind_email": "绑定登录邮箱",
            "reset_password": "重置密码",
        }.get(purpose, "完成身份验证")
        safe_code = escape(code)
        payload = {
            "from": self._settings.from_address,
            "to": [recipient],
            "subject": subject,
            "text": f"你的职业智能工作台验证码是 {code}，用于{purpose_label}，10 分钟内有效。若非本人操作，请忽略此邮件。",
            "html": (
                "<div style=\"font-family:Arial,'Microsoft YaHei',sans-serif;color:#20301d\">"
                "<h2>职业智能工作台</h2>"
                f"<p>请使用以下验证码{escape(purpose_label)}：</p>"
                f"<p style=\"font-size:28px;font-weight:700;letter-spacing:6px\">{safe_code}</p>"
                "<p>验证码 10 分钟内有效。若非本人操作，请忽略此邮件。</p>"
                "</div>"
            ),
        }
        try:
            response = requests.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "career-orbit-platform/1.0",
                },
                json=payload,
                timeout=self._settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning(
                "验证码邮件投递无法连接 Resend：purpose=%s recipient_domain=%s error=%s",
                purpose,
                _email_domain(recipient),
                exc.__class__.__name__,
            )
            raise EmailDeliveryError(
                "验证码邮件发送失败：无法连接 Resend，请检查网络后重试",
                reason="network_unavailable",
            ) from exc

        if response.ok:
            message_id = _response_message_id(response)
            logger.info(
                "验证码邮件投递成功：purpose=%s recipient_domain=%s resend_message_id=%s",
                purpose,
                _email_domain(recipient),
                message_id or "unknown",
            )
            return

        detail = _response_detail(response)
        normalized_detail = detail.lower()
        logger.warning(
            "验证码邮件投递被 Resend 拒绝：status=%s purpose=%s recipient_domain=%s reason=%s",
            response.status_code,
            purpose,
            _email_domain(recipient),
            _classify_resend_failure(response.status_code, normalized_detail),
        )

        if response.status_code in {401, 403} and ("api key" in normalized_detail or "invalid_api_key" in normalized_detail):
            raise EmailDeliveryError(
                "验证码邮件发送失败：Resend API Key 无效或已失效，请在 Resend 后台重新创建具有发送邮件权限的 Key。",
                reason="api_key_invalid",
                status_code=response.status_code,
            )
        if _is_resend_test_domain_restriction(response.status_code, normalized_detail, self._settings.from_address):
            raise EmailDeliveryError(
                "验证码邮件无法投递：当前发件地址使用 Resend 的 resend.dev 测试域名，"
                "它只能发送到 Resend 账户绑定的邮箱。请先在 Resend 验证自己的域名，"
                "再将 RESEND_FROM_ADDRESS 改为该域名下的地址后重试。",
                reason="resend_test_domain_restriction",
                status_code=response.status_code,
            )
        if "domain" in normalized_detail and ("not verified" in normalized_detail or "verify" in normalized_detail):
            raise EmailDeliveryError(
                "验证码邮件无法投递：发件域名尚未在 Resend 验证完成。请补齐 Resend 提供的 SPF 和 DKIM DNS 记录，"
                "等待域名状态变为 Verified 后重试。",
                reason="sender_domain_unverified",
                status_code=response.status_code,
            )
        if response.status_code in {401, 403}:
            raise EmailDeliveryError(
                "验证码邮件发送失败：Resend 拒绝了本次投递。请检查 API Key 发送权限和发件域名验证状态。",
                reason="resend_authorization_rejected",
                status_code=response.status_code,
            )
        if response.status_code == 429:
            raise EmailDeliveryError(
                "验证码邮件发送过于频繁，请稍后再试",
                reason="rate_limited",
                status_code=response.status_code,
            )
        raise EmailDeliveryError(
            f"验证码邮件发送失败：Resend 返回 {response.status_code}{f'（{detail}）' if detail else ''}",
            reason="resend_request_rejected",
            status_code=response.status_code,
        )


def _response_detail(response: requests.Response) -> str:
    """读取 Resend 的可展示错误摘要，不把完整响应或敏感请求信息写到日志。"""

    try:
        payload: Any = response.json()
    except ValueError:
        return response.text.strip()[:500]
    if not isinstance(payload, dict):
        return ""
    value = payload.get("message") or payload.get("name") or ""
    return str(value).strip()[:500]


def _response_message_id(response: requests.Response) -> str | None:
    """提取成功投递的 Resend 消息 ID，仅用于追查投递日志。"""

    try:
        payload: Any = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    message_id = payload.get("id")
    return str(message_id) if message_id else None


def _email_domain(value: str) -> str:
    """仅记录邮箱域名，避免在服务日志中落入完整个人邮箱。"""

    normalized = value.strip().lower().rstrip(">")
    if "@" not in normalized:
        return "unknown"
    return normalized.rsplit("@", 1)[-1]


def _is_resend_test_domain_restriction(status_code: int, detail: str, from_address: str) -> bool:
    """识别 Resend 测试发件域名只能投递账户邮箱的专用拒绝原因。"""

    return status_code == 403 and (
        "only send testing emails" in detail
        or "resend.dev" in _email_domain(from_address)
        or "testing emails to your own email" in detail
    )


def _classify_resend_failure(status_code: int, detail: str) -> str:
    """为日志归类投递失败，不向调用方暴露供应商原始响应全文。"""

    if status_code == 403 and (
        "only send testing emails" in detail
        or "testing emails to your own email" in detail
        or "resend.dev" in detail
    ):
        return "resend_test_domain_restriction"
    if "domain" in detail and ("not verified" in detail or "verify" in detail):
        return "sender_domain_unverified"
    if "api key" in detail or "invalid_api_key" in detail:
        return "api_key_invalid"
    if status_code == 429:
        return "rate_limited"
    return "resend_request_rejected"
