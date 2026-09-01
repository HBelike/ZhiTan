"""平台账号、会话与运行配置的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from src.career_assistant.persistence.conversation_repository import DEFAULT_ORGANIZATION_ID
from src.career_assistant.persistence.database import CareerDatabase
from src.platform_access.contracts import PLATFORM_ADMIN_EMAIL, PlatformRole, PlatformUser, SessionResolution
from src.platform_access.security import digest_session_token, normalize_email, normalize_username


class PlatformAccessRepository:
    """封装平台访问数据，所有写操作在独立事务中完成。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def has_active_users(self) -> bool:
        """判断系统是否存在任意可登录账号。"""

        with self._database.transaction() as connection:
            return bool(
                connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM career_assistant.platform_users
                            WHERE is_active = TRUE
                        )
                        """,
                    ),
                ).scalar_one(),
            )

    def has_active_admin(self) -> bool:
        """判断固定管理员是否已经初始化。"""

        with self._database.transaction() as connection:
            return bool(
                connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM career_assistant.platform_users
                            WHERE is_active = TRUE
                              AND role = 'admin'
                              AND email_normalized = :email
                        )
                        """,
                    ),
                    {"email": PLATFORM_ADMIN_EMAIL},
                ).scalar_one(),
            )

    def create_first_admin(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        email: str | None = None,
        email_verified_at: datetime | None = None,
    ) -> PlatformUser:
        """原子创建第一个管理员和对应 Career actor，避免并发重复 bootstrap。"""

        user_id = uuid4()
        normalized_username = normalize_username(username)
        normalized_display_name = display_name.strip() or normalized_username
        normalized_email = normalize_email(email) if email else None
        if normalized_email != PLATFORM_ADMIN_EMAIL:
            raise ValueError(f"管理员邮箱必须是 {PLATFORM_ADMIN_EMAIL}")
        if len(normalized_display_name) > 120:
            raise ValueError("显示名称不能超过 120 个字符")

        with self._database.transaction() as connection:
            # PostgreSQL 事务级 advisory lock 将用户检查与后续写入合并为跨 API/CLI 进程的
            # 唯一 bootstrap 临界区，锁会随事务提交或回滚自动释放。
            connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtext('career_assistant.create_first_admin')::bigint)",
                ),
            )
            if connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM career_assistant.platform_users
                        WHERE is_active = TRUE AND role = 'admin'
                    )
                    """,
                ),
            ).scalar_one():
                raise PermissionError("管理员已初始化，不能再次创建首个管理员")

            if normalized_email and connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM career_assistant.platform_users
                        WHERE email_normalized = :email
                    )
                    """,
                ),
                {"email": normalized_email},
            ).scalar_one():
                raise ValueError("该邮箱已经绑定历史账户，不能用于首次管理员初始化")

            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.platform_users
                        (id, organization_id, username, display_name, password_hash, email, email_normalized,
                         email_verified_at, role, is_active)
                    VALUES
                        (:id, :organization_id, :username, :display_name, :password_hash, :email, :email_normalized,
                         :email_verified_at, :role, TRUE)
                    """,
                ),
                {
                    "id": user_id,
                    "organization_id": DEFAULT_ORGANIZATION_ID,
                    "username": normalized_username,
                    "display_name": normalized_display_name,
                    "password_hash": password_hash,
                    "email": normalized_email,
                    "email_normalized": normalized_email,
                    "email_verified_at": email_verified_at,
                    "role": PlatformRole.ADMIN.value,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.actors
                        (id, organization_id, display_name, actor_type, status)
                    VALUES
                        (:id, :organization_id, :display_name, 'user', 'active')
                    """,
                ),
                {
                    "id": user_id,
                    "organization_id": DEFAULT_ORGANIZATION_ID,
                    "display_name": normalized_display_name,
                },
            )
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, username, display_name, email, email_verified_at,
                           role, is_active, created_at
                    FROM career_assistant.platform_users
                    WHERE id = :id
                    """,
                ),
                {"id": user_id},
            ).mappings().one()
        return self._to_user(row)

    def create_registered_user(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
    ) -> PlatformUser:
        """创建已验证邮箱的普通账号，并为 Career 模块建立同 ID actor。"""

        user_id = uuid4()
        normalized_email = normalize_email(email)
        if normalized_email == PLATFORM_ADMIN_EMAIL:
            raise ValueError("管理员专用邮箱不能注册为普通用户")
        normalized_display_name = display_name.strip() or normalized_email.split("@", 1)[0]
        if len(normalized_display_name) > 120:
            raise ValueError("显示名称不能超过 120 个字符")
        username = f"member-{user_id.hex[:20]}"
        now = datetime.now(UTC)
        with self._database.transaction() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.platform_users
                        (id, organization_id, username, display_name, password_hash, email, email_normalized,
                         email_verified_at, role, is_active)
                    VALUES
                        (:id, :organization_id, :username, :display_name, :password_hash, :email, :email_normalized,
                         :email_verified_at, :role, TRUE)
                    """,
                ),
                {
                    "id": user_id,
                    "organization_id": DEFAULT_ORGANIZATION_ID,
                    "username": username,
                    "display_name": normalized_display_name,
                    "password_hash": password_hash,
                    "email": normalized_email,
                    "email_normalized": normalized_email,
                    "email_verified_at": now,
                    "role": PlatformRole.USER.value,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.actors
                        (id, organization_id, display_name, actor_type, status)
                    VALUES
                        (:id, :organization_id, :display_name, 'user', 'active')
                    """,
                ),
                {"id": user_id, "organization_id": DEFAULT_ORGANIZATION_ID, "display_name": normalized_display_name},
            )
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, username, display_name, email, email_verified_at,
                           role, is_active, created_at
                    FROM career_assistant.platform_users WHERE id = :id
                    """,
                ),
                {"id": user_id},
            ).mappings().one()
        return self._to_user(row)

    def find_user_for_login(self, identity: str) -> tuple[PlatformUser, str] | None:
        """按邮箱登录；兼容存量管理员临时使用原用户名登录。"""

        raw_identity = identity.strip()
        if not raw_identity:
            return None
        is_email = "@" in raw_identity
        normalized_identity = normalize_email(raw_identity) if is_email else normalize_username(raw_identity)
        field_name = "email_normalized" if is_email else "username"
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, username, display_name, password_hash,
                           email, email_verified_at, role, is_active, created_at
                    FROM career_assistant.platform_users
                    WHERE """ + field_name + """ = :identity
                    """,
                ),
                {"identity": normalized_identity},
            ).mappings().first()
        if row is None:
            return None
        return self._to_user(row), str(row["password_hash"])

    def create_session(
        self,
        user: PlatformUser,
        raw_token: str,
        *,
        idle_ttl_hours: int,
        absolute_ttl_hours: int,
    ) -> tuple[datetime, datetime]:
        """仅保存会话摘要；同时返回空闲和绝对过期时间。"""

        session_id = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=idle_ttl_hours)
        absolute_expires_at = now + timedelta(hours=absolute_ttl_hours)
        with self._database.transaction() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.platform_sessions
                        (id, user_id, token_digest, expires_at, absolute_expires_at, created_at, last_seen_at)
                    VALUES
                        (:id, :user_id, :token_digest, :expires_at, :absolute_expires_at, :created_at, :last_seen_at)
                    """,
                ),
                {
                    "id": session_id,
                    "user_id": user.id,
                    "token_digest": digest_session_token(raw_token),
                    "expires_at": expires_at,
                    "absolute_expires_at": absolute_expires_at,
                    "created_at": now,
                    "last_seen_at": now,
                },
            )
        return expires_at, absolute_expires_at

    def resolve_session(self, raw_token: str, *, idle_ttl_hours: int) -> SessionResolution | None:
        """解析会话并以 7 天空闲窗口续期，但绝不越过绝对上限。"""

        if not raw_token:
            return None
        now = datetime.now(UTC)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT s.id AS session_id, s.expires_at, s.absolute_expires_at,
                           u.id, u.organization_id, u.username, u.display_name,
                           u.email, u.email_verified_at, u.role, u.is_active, u.created_at
                    FROM career_assistant.platform_sessions s
                    JOIN career_assistant.platform_users u ON u.id = s.user_id
                    WHERE s.token_digest = :token_digest
                      AND s.revoked_at IS NULL
                      AND s.expires_at > :now
                      AND s.absolute_expires_at > :now
                      AND u.is_active = TRUE
                    """,
                ),
                {"token_digest": digest_session_token(raw_token), "now": now},
            ).mappings().first()
            if row is None:
                return None
            refreshed_expires_at = min(now + timedelta(hours=idle_ttl_hours), row["absolute_expires_at"])
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.platform_sessions
                    SET last_seen_at = :now, expires_at = :expires_at
                    WHERE id = :id
                    """,
                ),
                {"now": now, "expires_at": refreshed_expires_at, "id": row["session_id"]},
            )
        return SessionResolution(
            user=self._to_user(row),
            session_id=UUID(str(row["session_id"])),
            expires_at=refreshed_expires_at,
            absolute_expires_at=row["absolute_expires_at"],
        )

    def create_email_challenge(
        self,
        *,
        email: str,
        purpose: str,
        payload: dict[str, object],
        code_digest: str,
        expires_at: datetime,
    ) -> dict[str, object]:
        """创建一次性验证码挑战；原始验证码永不写入数据库。"""

        challenge_id = uuid4()
        normalized_email = normalize_email(email)
        now = datetime.now(UTC)
        with self._database.transaction() as connection:
            last_sent_at = connection.execute(
                text(
                    """
                    SELECT MAX(last_sent_at)
                    FROM career_assistant.platform_email_challenges
                    WHERE email_normalized = :email AND purpose = :purpose
                      AND last_sent_at > :cooldown_floor
                    """,
                ),
                {"email": normalized_email, "purpose": purpose, "cooldown_floor": now - timedelta(seconds=60)},
            ).scalar_one()
            if last_sent_at is not None:
                raise ValueError("验证码刚刚发送，请 60 秒后再试")
            connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.platform_email_challenges
                        (id, email_normalized, purpose, payload_json, code_digest, expires_at, last_sent_at)
                    VALUES (:id, :email, :purpose, CAST(:payload_json AS JSONB), :code_digest, :expires_at, :last_sent_at)
                    """,
                ),
                {
                    "id": challenge_id,
                    "email": normalized_email,
                    "purpose": purpose,
                    "payload_json": json.dumps(payload, ensure_ascii=False),
                    "code_digest": code_digest,
                    "expires_at": expires_at,
                    "last_sent_at": now,
                },
            )
        return {"id": str(challenge_id), "email": normalized_email, "purpose": purpose, "expires_at": expires_at}

    def discard_email_challenge(self, challenge_id: str) -> None:
        """在验证码邮件未成功投递时移除挑战，避免 60 秒冷却阻塞用户修复配置后的立即重试。

        原始验证码从不写入数据库；这里仅删除尚未送达对应的摘要记录，不影响任何已成功投递的挑战。
        """

        try:
            normalized_id = UUID(challenge_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("验证码会话无效，无法清理") from exc
        with self._database.transaction() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM career_assistant.platform_email_challenges
                    WHERE id = :id AND consumed_at IS NULL
                    """,
                ),
                {"id": normalized_id},
            )

    def consume_email_challenge(self, *, challenge_id: str, purpose: str, code_digest: str) -> dict[str, object]:
        """验证并消费挑战，错误验证码只递增次数，超过阈值即失效。"""

        try:
            normalized_id = UUID(challenge_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("验证码会话无效，请重新获取验证码") from exc
        now = datetime.now(UTC)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, email_normalized, purpose, payload_json, code_digest, expires_at, consumed_at, attempt_count
                    FROM career_assistant.platform_email_challenges
                    WHERE id = :id AND purpose = :purpose
                    FOR UPDATE
                    """,
                ),
                {"id": normalized_id, "purpose": purpose},
            ).mappings().first()
            if row is None or row["consumed_at"] is not None or row["expires_at"] <= now:
                raise ValueError("验证码已失效，请重新获取")
            if int(row["attempt_count"]) >= 5:
                raise ValueError("验证码错误次数过多，请重新获取")
            if str(row["code_digest"]) != code_digest:
                connection.execute(
                    text("UPDATE career_assistant.platform_email_challenges SET attempt_count = attempt_count + 1 WHERE id = :id"),
                    {"id": normalized_id},
                )
                raise ValueError("验证码不正确")
            connection.execute(
                text("UPDATE career_assistant.platform_email_challenges SET consumed_at = :now WHERE id = :id"),
                {"now": now, "id": normalized_id},
            )
        payload = row["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {"email": str(row["email_normalized"]), "payload": payload if isinstance(payload, dict) else {}}

    def get_challenge_email(self, *, challenge_id: str, purpose: str) -> str:
        """读取挑战归属邮箱，仅用于服务层构造 HMAC，不向浏览器暴露。"""

        try:
            normalized_id = UUID(challenge_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("验证码会话无效，请重新获取验证码") from exc
        with self._database.transaction() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT email_normalized FROM career_assistant.platform_email_challenges
                    WHERE id = :id AND purpose = :purpose
                    """,
                ),
                {"id": normalized_id, "purpose": purpose},
            ).scalar_one_or_none()
        if value is None:
            raise ValueError("验证码会话无效，请重新获取验证码")
        return str(value)

    def find_user_by_email(self, email: str) -> tuple[PlatformUser, str] | None:
        """读取邮箱账号以支持找回密码，调用者负责避免枚举泄露。"""

        normalized_email = normalize_email(email)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, username, display_name, password_hash, email, email_verified_at,
                           role, is_active, created_at
                    FROM career_assistant.platform_users WHERE email_normalized = :email
                    """,
                ),
                {"email": normalized_email},
            ).mappings().first()
        return None if row is None else (self._to_user(row), str(row["password_hash"]))

    def bind_verified_email(self, user_id: UUID, email: str) -> PlatformUser:
        """为旧账号绑定已验证邮箱，保持用户名不变以兼容历史记录。"""

        normalized_email = normalize_email(email)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.platform_users
                    SET email = :email, email_normalized = :email, email_verified_at = NOW(), updated_at = NOW()
                    WHERE id = :id
                    RETURNING id, organization_id, username, display_name, email, email_verified_at,
                              role, is_active, created_at
                    """,
                ),
                {"id": user_id, "email": normalized_email},
            ).mappings().one_or_none()
        if row is None:
            raise ValueError("账号不存在")
        return self._to_user(row)

    def change_password_and_revoke_sessions(self, user_id: UUID, password_hash: str) -> None:
        """重置密码后撤销全部既有会话，避免旧 Cookie 继续生效。"""

        with self._database.transaction() as connection:
            connection.execute(
                text("UPDATE career_assistant.platform_users SET password_hash = :password_hash, updated_at = NOW() WHERE id = :id"),
                {"id": user_id, "password_hash": password_hash},
            )
            connection.execute(
                text("UPDATE career_assistant.platform_sessions SET revoked_at = NOW() WHERE user_id = :id AND revoked_at IS NULL"),
                {"id": user_id},
            )

    def revoke_session(self, raw_token: str) -> None:
        """撤销当前会话；令牌缺失时保持幂等。"""

        if not raw_token:
            return
        with self._database.transaction() as connection:
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.platform_sessions
                    SET revoked_at = NOW()
                    WHERE token_digest = :token_digest AND revoked_at IS NULL
                    """,
                ),
                {"token_digest": digest_session_token(raw_token)},
            )

    def get_active_pipeline_config(self, organization_id: UUID) -> dict[str, object] | None:
        """读取当前生效的不可变运行配置版本。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, version, config_json, created_at
                    FROM career_assistant.pipeline_config_versions
                    WHERE organization_id = :organization_id AND is_active = TRUE
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                ),
                {"organization_id": organization_id},
            ).mappings().first()
        if row is None:
            return None
        config_value = row["config_json"]
        if isinstance(config_value, str):
            config_value = json.loads(config_value)
        return {
            "id": str(row["id"]),
            "version": row["version"],
            "config": config_value,
            "created_at": row["created_at"].isoformat(),
        }

    def save_pipeline_config(
        self,
        organization_id: UUID,
        actor_id: UUID,
        config_value: dict[str, object],
    ) -> dict[str, object]:
        """保存新版本并原子切换为 active，运行中的任务不会读取半成品。"""

        with self._database.transaction() as connection:
            next_version = connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM career_assistant.pipeline_config_versions
                    WHERE organization_id = :organization_id
                    """,
                ),
                {"organization_id": organization_id},
            ).scalar_one()
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.pipeline_config_versions
                    SET is_active = FALSE
                    WHERE organization_id = :organization_id AND is_active = TRUE
                    """,
                ),
                {"organization_id": organization_id},
            )
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.pipeline_config_versions
                        (id, organization_id, version, config_json, is_active, created_by)
                    VALUES
                        (:id, :organization_id, :version, CAST(:config_json AS JSONB), TRUE, :created_by)
                    RETURNING id, version, config_json, created_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "version": next_version,
                    "config_json": json.dumps(config_value, ensure_ascii=False),
                    "created_by": actor_id,
                },
            ).mappings().one()
        return {
            "id": str(row["id"]),
            "version": row["version"],
            "config": row["config_json"],
            "created_at": row["created_at"].isoformat(),
        }

    def get_route_module_settings(self, organization_id: UUID) -> dict[str, bool]:
        """读取组织已显式保存的顶级路由模块开关。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT module_key, enabled
                    FROM career_assistant.route_module_settings
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).mappings().all()
        return {str(row["module_key"]): bool(row["enabled"]) for row in rows}

    def save_route_module_settings(
        self,
        organization_id: UUID,
        actor_id: UUID,
        settings: dict[str, bool],
    ) -> dict[str, bool]:
        """原子更新完整模块目录，未提交的半成品不会被普通用户读取。"""

        with self._database.transaction() as connection:
            for module_key, enabled in settings.items():
                connection.execute(
                    text(
                        """
                        INSERT INTO career_assistant.route_module_settings
                            (organization_id, module_key, enabled, updated_by, updated_at)
                        VALUES
                            (:organization_id, :module_key, :enabled, :updated_by, NOW())
                        ON CONFLICT (organization_id, module_key)
                        DO UPDATE SET
                            enabled = EXCLUDED.enabled,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = NOW()
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "module_key": module_key,
                        "enabled": enabled,
                        "updated_by": actor_id,
                    },
                )
        return dict(settings)

    def create_pipeline_execution_request(
        self,
        *,
        organization_id: UUID,
        requested_by: UUID,
        config_version_id: UUID | None,
        idempotency_key: str,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        """登记一次手动运行请求；同一幂等键重复提交时复用原记录。"""

        if not idempotency_key.strip():
            raise ValueError("手动运行请求缺少幂等键")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.pipeline_execution_requests
                        (id, organization_id, config_version_id, requested_by, trigger_type,
                         status, idempotency_key, metadata_json)
                    VALUES
                        (:id, :organization_id, :config_version_id, :requested_by, 'manual',
                         'queued', :idempotency_key, CAST(:metadata_json AS JSONB))
                    ON CONFLICT (organization_id, idempotency_key)
                    DO UPDATE SET updated_at = NOW()
                    RETURNING id, status, error_message, metadata_json, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "config_version_id": config_version_id,
                    "requested_by": requested_by,
                    "idempotency_key": idempotency_key.strip(),
                    "metadata_json": json.dumps(metadata, ensure_ascii=False),
                },
            ).mappings().one()
        return self._execution_payload(row)

    def update_pipeline_execution_request(
        self,
        request_id: UUID,
        *,
        status: str,
        error_message: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """以原子状态更新记录后台执行结果，便于管理员轮询。"""

        if status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
            raise ValueError("不支持的流水线状态")

        # PostgreSQL 无法推断同一个绑定参数同时用于 ``IS NULL`` 和
        # ``CAST(... AS JSONB)`` 时的类型。将“是否合并元数据”的分支放到
        # Python 中，既避免参数类型歧义，也保留未传 metadata 时原值不变的语义。
        metadata_expression = "metadata_json"
        parameters: dict[str, object] = {
            "id": request_id,
            "status": status,
            "error_message": error_message,
        }
        if metadata is not None:
            metadata_expression = "metadata_json || CAST(:metadata_json AS JSONB)"
            parameters["metadata_json"] = json.dumps(metadata, ensure_ascii=False)

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    f"""
                    UPDATE career_assistant.pipeline_execution_requests
                    SET status = :status,
                        error_message = :error_message,
                        metadata_json = {metadata_expression},
                        updated_at = NOW()
                    WHERE id = :id
                    RETURNING id, status, error_message, metadata_json, created_at, updated_at
                    """,
                ),
                parameters,
            ).mappings().one_or_none()
        if row is None:
            raise ValueError("未找到流水线执行请求")
        return self._execution_payload(row)

    def list_pipeline_execution_requests(self, organization_id: UUID, *, limit: int = 12) -> list[dict[str, object]]:
        """读取最近的运行记录，避免界面只能依赖页面内存状态。"""

        safe_limit = max(1, min(limit, 50))
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, status, error_message, metadata_json, created_at, updated_at
                    FROM career_assistant.pipeline_execution_requests
                    WHERE organization_id = :organization_id
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """,
                ),
                {"organization_id": organization_id, "limit": safe_limit},
            ).mappings().all()
        return [self._execution_payload(row) for row in rows]

    def get_pipeline_execution_request(
        self,
        organization_id: UUID,
        request_id: UUID,
    ) -> dict[str, object] | None:
        """读取当前 organization 内的一次工作流运行。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, status, error_message, metadata_json, created_at, updated_at
                    FROM career_assistant.pipeline_execution_requests
                    WHERE id = :id AND organization_id = :organization_id
                    """
                ),
                {"id": request_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return None if row is None else self._execution_payload(row)

    def append_pipeline_execution_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        level: str,
        message: str,
        task_name: str | None = None,
        task_run_id: str | None = None,
    ) -> dict[str, object]:
        """追加一条结构化运行事件，数据库 ID 作为全局稳定游标。"""

        normalized_type = event_type.strip()
        normalized_message = message.strip()
        if not normalized_type or not normalized_message:
            raise ValueError("工作流日志事件类型和内容不能为空")
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.pipeline_execution_events
                        (execution_request_id, event_type, level, task_name, task_run_id, message)
                    VALUES
                        (:request_id, :event_type, :level, :task_name, :task_run_id, :message)
                    RETURNING id, event_type, level, task_name, task_run_id, message, created_at
                    """
                ),
                {
                    "request_id": request_id,
                    "event_type": normalized_type[:80],
                    "level": (level.strip() or "INFO")[:20],
                    "task_name": None if task_name is None else task_name[:240],
                    "task_run_id": None if task_run_id is None else task_run_id[:240],
                    "message": normalized_message[:8000],
                },
            ).mappings().one()
        return self._pipeline_event_payload(row)

    def list_pipeline_execution_events(
        self,
        organization_id: UUID,
        request_id: UUID,
        *,
        after_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        """按递增游标读取当前 organization 可见的工作流事件。"""

        safe_limit = max(1, min(limit, 1000))
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT event.id, event.event_type, event.level, event.task_name,
                           event.task_run_id, event.message, event.created_at
                    FROM career_assistant.pipeline_execution_events AS event
                    JOIN career_assistant.pipeline_execution_requests AS request
                      ON request.id = event.execution_request_id
                    WHERE event.execution_request_id = :request_id
                      AND request.organization_id = :organization_id
                      AND event.id > :after_id
                    ORDER BY event.id ASC
                    LIMIT :limit
                    """
                ),
                {
                    "request_id": request_id,
                    "organization_id": organization_id,
                    "after_id": max(0, after_id),
                    "limit": safe_limit,
                },
            ).mappings().all()
        return [self._pipeline_event_payload(row) for row in rows]

    @staticmethod
    def _execution_payload(row: object) -> dict[str, object]:
        """将执行记录映射为不携带内部连接对象的 API 数据。"""

        metadata = row["metadata_json"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return {
            "id": str(row["id"]),
            "status": str(row["status"]),
            "error_message": row["error_message"],
            "metadata": metadata if isinstance(metadata, dict) else {},
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    @staticmethod
    def _pipeline_event_payload(row: object) -> dict[str, object]:
        """将事件行转换为 JSON 可序列化数据。"""

        return {
            "id": int(row["id"]),
            "event_type": str(row["event_type"]),
            "level": str(row["level"]),
            "task_name": row["task_name"],
            "task_run_id": row["task_run_id"],
            "message": str(row["message"]),
            "created_at": row["created_at"].isoformat(),
        }

    @staticmethod
    def _to_user(row: object) -> PlatformUser:
        """把数据库行映射为不包含密钥的用户数据。"""

        mapping = row
        return PlatformUser(
            id=UUID(str(mapping["id"])),
            organization_id=UUID(str(mapping["organization_id"])),
            username=str(mapping["username"]),
            display_name=str(mapping["display_name"]),
            email=str(mapping["email"]) if mapping.get("email") else None,
            email_verified_at=mapping.get("email_verified_at"),
            role=PlatformRole(str(mapping["role"])),
            is_active=bool(mapping["is_active"]),
            created_at=mapping["created_at"],
        )
