"""首次管理员的受控初始化用例。

该模块只在服务器本机的交互式命令中使用，避免把首个管理员创建能力暴露给公网。
密码只在调用期间用于生成 scrypt 摘要，绝不会写入日志、命令行参数或返回值。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol

from src.platform_access.contracts import PLATFORM_ADMIN_EMAIL, PlatformUser
from src.platform_access.security import hash_password, normalize_email


class FirstAdminBootstrapError(RuntimeError):
    """首次管理员无法安全初始化时抛出的可展示错误。"""


class FirstAdminRepository(Protocol):
    """首次管理员初始化所需的最小仓储契约，便于命令行与测试复用。"""

    def has_active_admin(self) -> bool:
        """返回平台是否已经存在可登录管理员。"""

    def find_user_by_email(self, email: str) -> tuple[PlatformUser, str] | None:
        """按规范化邮箱查找已存在账户。"""

    def create_first_admin(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        email: str | None = None,
        email_verified_at: datetime | None = None,
    ) -> PlatformUser:
        """在数据库事务中创建唯一的首个管理员。"""


def bootstrap_first_admin(
    repository: FirstAdminRepository,
    *,
    email: str,
    display_name: str,
    password: str,
) -> PlatformUser:
    """创建首个管理员及对应 Career actor。

    调用者负责通过可信的交互式终端采集输入；本函数不输出密码或数据库连接信息。
    ``create_first_admin`` 内部仍会再次加锁检查，避免两个管理员终端并发执行时出现竞态。
    """

    normalized_email = normalize_email(email)
    if normalized_email != PLATFORM_ADMIN_EMAIL:
        raise FirstAdminBootstrapError(f"管理员邮箱必须是 {PLATFORM_ADMIN_EMAIL}")
    if repository.has_active_admin():
        raise FirstAdminBootstrapError("管理员已初始化，请使用已有管理员账号登录")
    if repository.find_user_by_email(normalized_email) is not None:
        raise FirstAdminBootstrapError("该邮箱已经绑定历史账户，不能用于首次管理员初始化")

    try:
        return repository.create_first_admin(
            username=_build_bootstrap_username(normalized_email),
            display_name=display_name,
            password_hash=hash_password(password),
            email=normalized_email,
            email_verified_at=datetime.now(UTC),
        )
    except PermissionError as exc:
        # 数据库事务中的二次检查处理并发管理员终端或其他 bootstrap 路径的竞争。
        raise FirstAdminBootstrapError("平台已由其他操作完成管理员初始化，请使用已有管理员账号登录") from exc


def _build_bootstrap_username(email: str) -> str:
    """为邮箱登录账户生成稳定的内部用户名，不暴露邮箱正文。"""

    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:20]
    return f"admin-{digest}"
