"""平台账号、邮箱验证与会话策略的业务服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.platform_access.contracts import PLATFORM_ADMIN_EMAIL, PlatformRole, PlatformUser, SessionResolution
from src.platform_access.email_delivery import EmailDeliveryError, ResendEmailDelivery
from src.platform_access.navigation_config import normalize_route_module_settings, route_modules_for_ui
from src.platform_access.repository import PlatformAccessRepository
from src.platform_access.runtime_config import pipeline_config_for_ui
from src.platform_access.security import (
    create_session_token,
    create_verification_code,
    digest_verification_code,
    hash_password,
    normalize_email,
    verify_password,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthenticatedSession:
    """登录成功后交给 Web 层写入 Cookie 的数据。"""

    user: PlatformUser
    raw_token: str
    expires_at: datetime
    absolute_expires_at: datetime


class PlatformAccessService:
    """协调账号注册、邮件验证、会话续期与管理员配置。"""

    def __init__(
        self,
        repository: PlatformAccessRepository,
        *,
        email_delivery: ResendEmailDelivery | None = None,
        verification_secret: str = "",
        idle_session_ttl_hours: int = 24 * 7,
        absolute_session_ttl_hours: int = 24 * 30,
    ) -> None:
        if idle_session_ttl_hours <= 0 or absolute_session_ttl_hours < idle_session_ttl_hours:
            raise ValueError("会话有效期配置不正确")
        self._repository = repository
        self._email_delivery = email_delivery
        self._verification_secret = verification_secret.strip()
        self._idle_session_ttl_hours = idle_session_ttl_hours
        self._absolute_session_ttl_hours = absolute_session_ttl_hours

    def requires_bootstrap(self) -> bool:
        """返回系统是否尚未存在固定管理员。"""

        return not self._repository.has_active_admin()

    def send_registration_code(self, *, email: str, display_name: str, password: str, bootstrap: bool = False) -> dict[str, object]:
        """保存密码散列至一次性挑战并投递验证码，注册前不创建可登录账号。"""

        normalized_email = normalize_email(email)
        if bootstrap and normalized_email != PLATFORM_ADMIN_EMAIL:
            raise ValueError(f"管理员邮箱必须是 {PLATFORM_ADMIN_EMAIL}")
        if not bootstrap and normalized_email == PLATFORM_ADMIN_EMAIL:
            raise ValueError("管理员专用邮箱不能注册为普通用户")
        if bootstrap and not self.requires_bootstrap():
            raise PermissionError("管理员已初始化，请使用普通注册或登录")
        if not bootstrap and self.requires_bootstrap():
            raise PermissionError("请先完成首个管理员初始化")
        if self._repository.find_user_by_email(normalized_email) is not None:
            raise ValueError("该邮箱已注册，请直接登录或重置密码")
        purpose = "bootstrap" if bootstrap else "register"
        return self._send_challenge(
            email=normalized_email,
            purpose=purpose,
            payload={"display_name": display_name.strip(), "password_hash": hash_password(password)},
        )

    def verify_registration_code(self, *, challenge_id: str, code: str, bootstrap: bool = False) -> AuthenticatedSession:
        """消费注册挑战后创建账户；管理员 bootstrap 与普通注册共享验证链路。"""

        purpose = "bootstrap" if bootstrap else "register"
        challenge = self._consume_challenge(challenge_id=challenge_id, purpose=purpose, code=code)
        payload = challenge["payload"]
        password_hash = str(payload.get("password_hash", ""))
        if not password_hash:
            raise ValueError("注册会话无效，请重新获取验证码")
        if bootstrap:
            if normalize_email(str(challenge["email"])) != PLATFORM_ADMIN_EMAIL:
                raise ValueError(f"管理员邮箱必须是 {PLATFORM_ADMIN_EMAIL}")
            user = self._repository.create_first_admin(
                username=f"admin-{UUID(challenge_id).hex[:20]}",
                display_name=str(payload.get("display_name", "")),
                password_hash=password_hash,
                email=challenge["email"],
                email_verified_at=datetime.now(UTC),
            )
        else:
            if normalize_email(str(challenge["email"])) == PLATFORM_ADMIN_EMAIL:
                raise ValueError("管理员专用邮箱不能注册为普通用户")
            user = self._repository.create_registered_user(
                email=challenge["email"],
                display_name=str(payload.get("display_name", "")),
                password_hash=password_hash,
            )
        return self._create_session(user)

    def authenticate(self, identity: str, password: str) -> AuthenticatedSession:
        """验证邮箱账号；为历史管理员保留用户名兼容入口。"""

        candidate = self._repository.find_user_for_login(identity)
        if candidate is None:
            raise PermissionError("邮箱或密码不正确")
        user, password_hash = candidate
        if not user.is_active or not verify_password(password, password_hash):
            raise PermissionError("邮箱或密码不正确")
        return self._create_session(user)

    def send_login_code(self, *, email: str) -> dict[str, object]:
        """向已注册且可用的邮箱账号发送一次性登录验证码。"""

        normalized_email = normalize_email(email)
        candidate = self._repository.find_user_by_email(normalized_email)
        if candidate is None or not candidate[0].is_active or candidate[0].email_verified_at is None:
            raise PermissionError("该邮箱尚未注册或未完成验证")
        return self._send_challenge(
            email=normalized_email,
            purpose="login",
            payload={"user_id": str(candidate[0].id)},
        )

    def authenticate_with_code(self, *, challenge_id: str, code: str) -> AuthenticatedSession:
        """消费邮箱登录验证码并创建与密码登录相同的平台会话。"""

        challenge = self._consume_challenge(challenge_id=challenge_id, purpose="login", code=code)
        user_id = str(challenge["payload"].get("user_id", ""))
        candidate = self._repository.find_user_by_email(str(challenge["email"]))
        if candidate is None or str(candidate[0].id) != user_id or not candidate[0].is_active:
            raise PermissionError("登录验证码对应的账号不可用")
        return self._create_session(candidate[0])

    def send_bind_email_code(self, *, user: PlatformUser, email: str) -> dict[str, object]:
        """为旧用户名账号补充邮箱，方便后续使用统一邮箱登录。"""

        normalized_email = normalize_email(email)
        if normalized_email == PLATFORM_ADMIN_EMAIL and user.role is not PlatformRole.ADMIN:
            raise ValueError("管理员专用邮箱不能绑定到普通用户")
        if user.role is PlatformRole.ADMIN and normalized_email != PLATFORM_ADMIN_EMAIL:
            raise ValueError(f"管理员邮箱必须是 {PLATFORM_ADMIN_EMAIL}")
        existing = self._repository.find_user_by_email(normalized_email)
        if existing is not None and existing[0].id != user.id:
            raise ValueError("该邮箱已绑定其他账号")
        return self._send_challenge(email=normalized_email, purpose="bind_email", payload={"user_id": str(user.id)})

    def verify_bind_email_code(self, *, user: PlatformUser, challenge_id: str, code: str) -> PlatformUser:
        """确认验证码和当前用户匹配后写入邮箱身份。"""

        challenge = self._consume_challenge(challenge_id=challenge_id, purpose="bind_email", code=code)
        if str(challenge["payload"].get("user_id", "")) != str(user.id):
            raise PermissionError("验证码不属于当前账号")
        return self._repository.bind_verified_email(user.id, challenge["email"])

    def send_password_reset_code(self, *, email: str) -> dict[str, object]:
        """为存在账号投递重置码；不存在时仍返回泛化结果以避免枚举邮箱。"""

        normalized_email = normalize_email(email)
        candidate = self._repository.find_user_by_email(normalized_email)
        if candidate is None:
            return {"accepted": True, "challenge_id": None, "expires_at": None}
        return self._send_challenge(
            email=normalized_email,
            purpose="reset_password",
            payload={"user_id": str(candidate[0].id)},
        )

    def reset_password(self, *, challenge_id: str, code: str, new_password: str) -> None:
        """验证邮件挑战后更新密码并撤销全部旧会话。"""

        challenge = self._consume_challenge(challenge_id=challenge_id, purpose="reset_password", code=code)
        user_id = challenge["payload"].get("user_id")
        if not user_id:
            raise ValueError("密码重置会话无效")
        self._repository.change_password_and_revoke_sessions(UUID(str(user_id)), hash_password(new_password))

    def resolve_session(self, raw_token: str) -> SessionResolution | None:
        """解析当前请求会话，并执行空闲续期。"""

        return self._repository.resolve_session(raw_token, idle_ttl_hours=self._idle_session_ttl_hours)

    def logout(self, raw_token: str) -> None:
        """撤销当前会话。"""

        self._repository.revoke_session(raw_token)

    def get_pipeline_config(self, user: PlatformUser) -> dict[str, object]:
        self._require_role(user, PlatformRole.ADMIN)
        item = self._repository.get_active_pipeline_config(user.organization_id)
        if item is None:
            return {"id": None, "version": 0, "config": pipeline_config_for_ui(None), "created_at": None}
        item["config"] = pipeline_config_for_ui(item.get("config"))
        return item

    def save_pipeline_config(self, user: PlatformUser, config_value: dict[str, object]) -> dict[str, object]:
        self._require_role(user, PlatformRole.ADMIN)
        return self._repository.save_pipeline_config(user.organization_id, user.id, pipeline_config_for_ui(config_value))

    def get_route_modules(self, user: PlatformUser) -> list[dict[str, object]]:
        """读取管理员配置，并按当前登录角色计算最终可访问状态。"""

        settings = self._repository.get_route_module_settings(user.organization_id)
        return route_modules_for_ui(settings, user.role)

    def save_route_modules(self, user: PlatformUser, settings: dict[str, object]) -> list[dict[str, object]]:
        """仅管理员可以更新模块目录；管理台入口始终保持开启。"""

        self._require_role(user, PlatformRole.ADMIN)
        normalized = normalize_route_module_settings(settings)
        saved = self._repository.save_route_module_settings(user.organization_id, user.id, normalized)
        return route_modules_for_ui(saved, user.role)

    def create_manual_pipeline_request(self, user: PlatformUser, *, idempotency_key: str) -> tuple[dict[str, object], dict[str, object]]:
        self._require_role(user, PlatformRole.ADMIN)
        config_item = self.get_pipeline_config(user)
        config_id = config_item.get("id")
        request = self._repository.create_pipeline_execution_request(
            organization_id=user.organization_id,
            requested_by=user.id,
            config_version_id=None if not config_id else UUID(str(config_id)),
            idempotency_key=idempotency_key,
            metadata={"config_version": config_item["version"], "config": config_item["config"]},
        )
        return request, config_item

    def update_manual_pipeline_request(self, user: PlatformUser, request_id: str, *, status: str, error_message: str | None = None, metadata: dict[str, object] | None = None) -> dict[str, object]:
        self._require_role(user, PlatformRole.ADMIN)
        return self._repository.update_pipeline_execution_request(UUID(request_id), status=status, error_message=error_message, metadata=metadata)

    def list_manual_pipeline_requests(self, user: PlatformUser) -> list[dict[str, object]]:
        self._require_role(user, PlatformRole.ADMIN)
        return self._repository.list_pipeline_execution_requests(user.organization_id)

    def get_manual_pipeline_request(self, user: PlatformUser, request_id: str) -> dict[str, object] | None:
        """读取当前管理员 organization 内的一次运行。"""

        self._require_role(user, PlatformRole.ADMIN)
        return self._repository.get_pipeline_execution_request(user.organization_id, UUID(request_id))

    def append_manual_pipeline_event(
        self,
        user: PlatformUser,
        request_id: str,
        *,
        event_type: str,
        level: str,
        message: str,
        task_name: str | None = None,
        task_run_id: str | None = None,
    ) -> dict[str, object]:
        """为后台执行线程追加一条可实时查看的事件。"""

        self._require_role(user, PlatformRole.ADMIN)
        return self._repository.append_pipeline_execution_event(
            UUID(request_id),
            event_type=event_type,
            level=level,
            message=message,
            task_name=task_name,
            task_run_id=task_run_id,
        )

    def list_manual_pipeline_events(
        self,
        user: PlatformUser,
        request_id: str,
        *,
        after_id: int = 0,
        limit: int = 500,
    ) -> dict[str, object]:
        """返回运行状态及 after_id 之后的有序事件。"""

        self._require_role(user, PlatformRole.ADMIN)
        normalized_id = UUID(request_id)
        item = self._repository.get_pipeline_execution_request(user.organization_id, normalized_id)
        if item is None:
            raise ValueError("未找到流水线执行请求")
        events = self._repository.list_pipeline_execution_events(
            user.organization_id,
            normalized_id,
            after_id=after_id,
            limit=limit,
        )
        return {"item": item, "events": events}

    def _send_challenge(self, *, email: str, purpose: str, payload: dict[str, object]) -> dict[str, object]:
        if not self._verification_secret:
            raise RuntimeError("账号邮件服务尚未配置 PLATFORM_EMAIL_CODE_SECRET")
        if self._email_delivery is None:
            raise RuntimeError("账号邮件服务尚未配置 RESEND_API_KEY 和 RESEND_FROM_ADDRESS")
        code = create_verification_code()
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        # 先生成挑战 ID，再用其参与 HMAC，随后在仓储中持久化摘要。
        # 为保证 ID 一致，仓储允许服务层提供挑战 ID 的摘要通过回写构造。
        # 这里先创建一个占位 challenge，随后使用真实 ID 的摘要更新由仓储完成。
        # 为避免额外更新，服务端验证码摘要基于 email/purpose/code，而挑战 ID 仍由数据库唯一约束保护。
        # 注：digest 的 challenge_id 固定为 email challenge purpose，防止不同用途复用验证码。
        code_digest = digest_verification_code(secret=self._verification_secret, challenge_id=f"{email}:{purpose}", email=email, purpose=purpose, code=code)
        challenge = self._repository.create_email_challenge(email=email, purpose=purpose, payload=payload, code_digest=code_digest, expires_at=expires_at)
        try:
            self._email_delivery.send_verification_code(recipient=email, code=code, purpose=purpose)
        except EmailDeliveryError as exc:
            # 邮件未送达时立即删除挑战，避免用户修复发送配置后还被发送冷却时间阻塞。
            # 删除失败不能遮蔽原始的 Resend 错误，否则浏览器无法得到可执行的修复提示。
            try:
                self._repository.discard_email_challenge(str(challenge["id"]))
            except Exception:
                logger.exception(
                    "验证码邮件投递失败后的挑战清理异常：purpose=%s reason=%s",
                    purpose,
                    exc.reason,
                )
            raise
        return {"accepted": True, "challenge_id": challenge["id"], "expires_at": expires_at.isoformat()}

    def _consume_challenge(self, *, challenge_id: str, purpose: str, code: str) -> dict[str, object]:
        if not self._verification_secret:
            raise RuntimeError("账号邮件服务尚未配置 PLATFORM_EMAIL_CODE_SECRET")
        # 读取挑战的 email 前不能构造 HMAC，仓储现在提供 purpose/id 和摘要直接比较，因此这里约定摘要算法不依赖 UUID。
        # 使用数据库中的 email 通过一个短暂查询实现安全比对。
        challenge_email = self._repository.get_challenge_email(challenge_id=challenge_id, purpose=purpose)
        code_digest = digest_verification_code(secret=self._verification_secret, challenge_id=f"{challenge_email}:{purpose}", email=challenge_email, purpose=purpose, code=code.strip())
        return self._repository.consume_email_challenge(challenge_id=challenge_id, purpose=purpose, code_digest=code_digest)

    def _create_session(self, user: PlatformUser) -> AuthenticatedSession:
        raw_token = create_session_token()
        expires_at, absolute_expires_at = self._repository.create_session(
            user, raw_token, idle_ttl_hours=self._idle_session_ttl_hours, absolute_ttl_hours=self._absolute_session_ttl_hours
        )
        return AuthenticatedSession(user=user, raw_token=raw_token, expires_at=expires_at, absolute_expires_at=absolute_expires_at)

    @staticmethod
    def _require_role(user: PlatformUser, required: PlatformRole) -> None:
        if not user.role.allows(required):
            raise PermissionError("当前账号没有执行此操作的权限")
