"""密码和会话令牌的本地安全处理。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_LENGTH = 32


def normalize_username(value: str) -> str:
    """规范化登录名，避免大小写导致重复账号。"""

    normalized = value.strip().lower()
    if not 3 <= len(normalized) <= 64:
        raise ValueError("用户名长度必须在 3 到 64 个字符之间")
    if not all(character.isalnum() or character in {"-", "_", "."} for character in normalized):
        raise ValueError("用户名仅支持字母、数字、连字符、下划线和点号")
    return normalized


def validate_password(value: str) -> str:
    """验证个人平台密码的最低复杂度，不保留原始密码之外的副本。"""

    if len(value) < 8:
        raise ValueError("密码至少需要 8 个字符")
    if len(value) > 256:
        raise ValueError("密码长度不能超过 256 个字符")
    return value


def normalize_email(value: str) -> str:
    """规范化邮箱并完成基础边界校验，最终投递仍由邮件服务商校验。"""

    normalized = value.strip().lower()
    if not 3 <= len(normalized) <= 254 or "@" not in normalized:
        raise ValueError("请输入有效的邮箱地址")
    local_part, _, domain = normalized.rpartition("@")
    if not local_part or not domain or "." not in domain or any(character.isspace() for character in normalized):
        raise ValueError("请输入有效的邮箱地址")
    return normalized


def create_verification_code() -> str:
    """生成六位一次性验证码，原始值只在投递请求中短暂存在。"""

    return f"{secrets.randbelow(1_000_000):06d}"


def digest_verification_code(*, secret: str, challenge_id: str, email: str, purpose: str, code: str) -> str:
    """以服务端密钥 HMAC 化验证码，避免六位验证码被离线枚举。"""

    message = f"{challenge_id}:{normalize_email(email)}:{purpose}:{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def utc_now() -> datetime:
    """集中提供时区明确的当前时间，便于会话和验证码统一比较。"""

    return datetime.now(UTC)


def hash_password(password: str) -> str:
    """使用带随机盐的 scrypt 生成可持久化密码散列。"""

    password = validate_password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_KEY_LENGTH,
    )
    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii")
    encoded_hash = base64.urlsafe_b64encode(derived).decode("ascii")
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${encoded_salt}${encoded_hash}"


def verify_password(password: str, encoded_value: str) -> bool:
    """验证输入密码；格式损坏或计算失败统一视为不匹配。"""

    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_hash = encoded_value.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_hash.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (TypeError, ValueError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_session_token() -> str:
    """创建仅在浏览器 Cookie 中出现一次的不透明会话令牌。"""

    return secrets.token_urlsafe(48)


def digest_session_token(token: str) -> str:
    """生成可安全存入数据库的会话令牌摘要。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
