"""平台访问模块的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from src.platform_access.settings import load_platform_admin_email


PLATFORM_ADMIN_EMAIL = load_platform_admin_email()


class PlatformRole(str, Enum):
    """平台角色只区分固定管理员与普通用户。"""

    USER = "user"
    ADMIN = "admin"

    def allows(self, required: "PlatformRole") -> bool:
        """管理员包含普通用户能力，普通用户不能执行管理员操作。"""

        return self is PlatformRole.ADMIN or required is PlatformRole.USER


@dataclass(frozen=True)
class PlatformUser:
    """已认证的最小用户视图，不携带密码散列。"""

    id: UUID
    organization_id: UUID
    username: str
    display_name: str
    email: str | None
    email_verified_at: datetime | None
    role: PlatformRole
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class SessionResolution:
    """服务端会话解析结果。"""

    user: PlatformUser
    session_id: UUID
    expires_at: datetime
    absolute_expires_at: datetime
