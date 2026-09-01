"""平台登录、管理员配置与 LangSmith 可观测性 Web 接口。"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.exc import SQLAlchemyError

from src.career_assistant.persistence.database import CareerDatabase
from src.app.application import Application
from src.config.config_manager import ConfigManager
from src.database.database_manager import DatabaseManager
from src.observability.langsmith_runtime import is_langsmith_enabled
from src.platform_access.contracts import PlatformRole, PlatformUser, SessionResolution
from src.platform_access.email_delivery import ResendEmailDelivery, ResendEmailSettings
from src.platform_access.repository import PlatformAccessRepository
from src.platform_access.pipeline_runner import ManualPipelineRunner
from src.platform_access.service import AuthenticatedSession, PlatformAccessService
from src.repositories.weekly_ranking_repository import WeeklyRankingRepository


SESSION_COOKIE_NAME = "platform_session"
router = APIRouter(tags=["platform-access"])


def platform_auth_required() -> bool:
    """读取业务 API 是否必须登录的生产开关。

    默认关闭，保证本地既有验收脚本无需先创建账号；生产 Compose 必须显式设为 true。
    """

    return _read_boolean_environment("PLATFORM_AUTH_REQUIRED", default=False)


def public_registration_enabled() -> bool:
    """控制是否允许首个管理员之外的公开注册。"""

    return _read_boolean_environment("PLATFORM_PUBLIC_REGISTRATION_ENABLED", default=True)


def platform_cli_bootstrap_only() -> bool:
    """控制首个管理员是否只能通过服务器交互式命令创建。

    公网首发时应开启该开关，避免平台尚无管理员的短暂窗口被陌生邮箱抢先 bootstrap；
    本地开发默认关闭，继续保留已有邮箱验证码初始化链路。
    """

    return _read_boolean_environment("PLATFORM_CLI_BOOTSTRAP_ONLY", default=False)


class SendRegistrationCodeRequest(BaseModel):
    """注册或 bootstrap 的第一步：投递邮箱验证码。"""

    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=8, max_length=256)


class VerifyEmailCodeRequest(BaseModel):
    """验证码确认输入。"""

    challenge_id: str = Field(min_length=8, max_length=64)
    code: str = Field(min_length=6, max_length=6)


class LoginRequest(BaseModel):
    """普通登录输入。"""

    identity: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class EmailLoginRequest(BaseModel):
    """邮箱验证码登录的第一步。"""

    email: str = Field(min_length=3, max_length=254)


class BindEmailRequest(BaseModel):
    """存量账号绑定登录邮箱。"""

    email: str = Field(min_length=3, max_length=254)


class PasswordResetRequest(BaseModel):
    """密码找回第一步。"""

    email: str = Field(min_length=3, max_length=254)


class PasswordResetConfirmRequest(VerifyEmailCodeRequest):
    """密码找回第二步。"""

    new_password: str = Field(min_length=8, max_length=256)


class PipelineConfigRequest(BaseModel):
    """管理员保存的运行配置，保持 JSON 可扩展边界。"""

    config: dict[str, object]


class RouteModuleConfigRequest(BaseModel):
    """管理员提交的完整顶级路由模块开关。"""

    modules: dict[str, StrictBool]


class ManualPipelineRequest(BaseModel):
    """浏览器发起手动完整流水线时使用的幂等键。"""

    client_request_id: str | None = Field(default=None, min_length=8, max_length=160)


def install_platform_access_api(app: FastAPI, project_root: Path) -> None:
    """安装平台级 API，并采用与 Career 模块一致的懒加载数据库策略。"""

    app.include_router(router)
    app.state.platform_access_project_root = project_root
    app.state.platform_access_service = None
    app.state.platform_access_lock = Lock()
    app.state.manual_pipeline_runner = None
    app.state.manual_pipeline_runner_lock = Lock()

    @app.on_event("shutdown")
    def close_platform_access_service() -> None:
        """释放平台访问数据库连接池。"""

        service: PlatformAccessService | None = app.state.platform_access_service
        if service is not None:
            repository = getattr(service, "_repository", None)
            database = getattr(repository, "_database", None)
            if database is not None:
                database.close()
            app.state.platform_access_service = None


def get_platform_access_service(request: Request) -> PlatformAccessService:
    """按需创建访问控制服务，未配置 PostgreSQL 时返回可读错误。"""

    existing: PlatformAccessService | None = request.app.state.platform_access_service
    if existing is not None:
        return existing
    with request.app.state.platform_access_lock:
        existing = request.app.state.platform_access_service
        if existing is not None:
            return existing
        _load_environment(request.app.state.platform_access_project_root)
        database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
        if not database_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="平台账号数据库尚未配置 CAREER_DATABASE_URL",
            )
        try:
            database = CareerDatabase(database_url)
            resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
            resend_from_address = os.getenv("RESEND_FROM_ADDRESS", "").strip()
            email_delivery = None
            if resend_api_key and resend_from_address:
                email_delivery = ResendEmailDelivery(
                    ResendEmailSettings(api_key=resend_api_key, from_address=resend_from_address),
                )
            service = PlatformAccessService(
                PlatformAccessRepository(database),
                email_delivery=email_delivery,
                verification_secret=os.getenv("PLATFORM_EMAIL_CODE_SECRET", ""),
            )
        except (OSError, SQLAlchemyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"平台账号服务初始化失败：{exc}",
            ) from exc
        request.app.state.platform_access_service = service
        return service


def get_manual_pipeline_runner(request: Request) -> ManualPipelineRunner:
    """按需创建单例后台执行器，避免每个 Web 请求各自维护活动任务集合。"""

    runner: ManualPipelineRunner | None = request.app.state.manual_pipeline_runner
    if runner is not None:
        return runner
    with request.app.state.manual_pipeline_runner_lock:
        runner = request.app.state.manual_pipeline_runner
        if runner is not None:
            return runner
        runner = ManualPipelineRunner(
            request.app.state.platform_access_project_root,
            get_platform_access_service(request),
        )
        request.app.state.manual_pipeline_runner = runner
        return runner


def get_current_platform_session(request: Request, response: Response) -> SessionResolution:
    """解析会话并续写 Cookie，让浏览器过期时间与服务端空闲窗口同步。"""

    service = get_platform_access_service(request)
    raw_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    session = service.resolve_session(raw_token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录后继续")
    _set_cookie(response, request, raw_token, session.expires_at)
    return session


def get_current_platform_user(session: Annotated[SessionResolution, Depends(get_current_platform_session)]) -> PlatformUser:
    """从 HttpOnly Cookie 解析当前用户，未登录统一返回 401。"""

    return session.user


def require_admin(user: Annotated[PlatformUser, Depends(get_current_platform_user)]) -> PlatformUser:
    """限制管理员配置与观测入口。"""

    if not user.role.allows(PlatformRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可以访问此页面")
    return user


@router.get("/api/auth/bootstrap-status")
def bootstrap_status(request: Request) -> dict[str, object]:
    """让登录页判断显示初始化还是登录表单。"""

    service = get_platform_access_service(request)
    return {
        "requires_bootstrap": service.requires_bootstrap(),
        "public_registration_enabled": public_registration_enabled(),
        "cli_bootstrap_only": platform_cli_bootstrap_only(),
    }


@router.post("/api/auth/bootstrap/send-code")
def send_bootstrap_code(payload: SendRegistrationCodeRequest, request: Request) -> dict[str, object]:
    """向首个管理员邮箱投递验证码，验证成功后才创建账号。"""

    if platform_cli_bootstrap_only():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前生产环境仅允许通过服务器交互式终端初始化首个管理员",
        )

    try:
        return get_platform_access_service(request).send_registration_code(
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password,
            bootstrap=True,
        )
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/auth/bootstrap/verify")
def verify_bootstrap_code(payload: VerifyEmailCodeRequest, request: Request, response: Response) -> dict[str, object]:
    """确认首个管理员邮箱验证码并写入会话。"""

    if platform_cli_bootstrap_only():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前生产环境仅允许通过服务器交互式终端初始化首个管理员",
        )

    try:
        session = get_platform_access_service(request).verify_registration_code(
            challenge_id=payload.challenge_id, code=payload.code, bootstrap=True
        )
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _set_session_cookie(response, request, session)
    return {"user": _serialize_user(session.user)}


@router.post("/api/auth/register/send-code")
def send_registration_code(payload: SendRegistrationCodeRequest, request: Request) -> dict[str, object]:
    """公开注册入口：邮箱验证码确认后以 user 角色创建账号。"""

    if not public_registration_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前平台未开放公开注册")

    try:
        return get_platform_access_service(request).send_registration_code(
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password,
        )
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/auth/register/verify")
def verify_registration_code(payload: VerifyEmailCodeRequest, request: Request, response: Response) -> dict[str, object]:
    """确认注册验证码、创建 user 账号并写入会话。"""

    try:
        session = get_platform_access_service(request).verify_registration_code(
            challenge_id=payload.challenge_id, code=payload.code
        )
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _set_session_cookie(response, request, session)
    return {"user": _serialize_user(session.user)}


@router.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, object]:
    """验证账号并写入 HttpOnly 会话 Cookie。"""

    service = get_platform_access_service(request)
    try:
        session = service.authenticate(payload.identity, payload.password)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    _set_session_cookie(response, request, session)
    return {"user": _serialize_user(session.user)}


@router.post("/api/auth/email-login/send-code")
def send_email_login_code(payload: EmailLoginRequest, request: Request) -> dict[str, object]:
    """向已注册邮箱投递一次性登录验证码。"""

    try:
        return get_platform_access_service(request).send_login_code(email=payload.email)
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/auth/email-login/verify")
def verify_email_login_code(payload: VerifyEmailCodeRequest, request: Request, response: Response) -> dict[str, object]:
    """确认邮箱登录验证码并写入正常平台会话。"""

    try:
        session = get_platform_access_service(request).authenticate_with_code(
            challenge_id=payload.challenge_id,
            code=payload.code,
        )
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    _set_session_cookie(response, request, session)
    return {"user": _serialize_user(session.user)}


@router.post("/api/auth/email/bind/send-code")
def send_bind_email_code(
    payload: BindEmailRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(get_current_platform_user)],
) -> dict[str, object]:
    """给历史用户名账号的邮箱绑定流程发送验证码。"""

    try:
        return get_platform_access_service(request).send_bind_email_code(user=user, email=payload.email)
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/auth/email/bind/verify")
def verify_bind_email_code(
    payload: VerifyEmailCodeRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(get_current_platform_user)],
) -> dict[str, object]:
    """完成当前登录账号的邮箱绑定。"""

    try:
        updated = get_platform_access_service(request).verify_bind_email_code(
            user=user, challenge_id=payload.challenge_id, code=payload.code
        )
    except (PermissionError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"user": _serialize_user(updated)}


@router.post("/api/auth/password-reset/send-code")
def send_password_reset_code(payload: PasswordResetRequest, request: Request) -> dict[str, object]:
    """投递密码重置验证码；响应不泄露邮箱是否已注册。"""

    try:
        return get_platform_access_service(request).send_password_reset_code(email=payload.email)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/auth/password-reset/verify")
def verify_password_reset_code(payload: PasswordResetConfirmRequest, request: Request) -> dict[str, bool]:
    """确认验证码、更新密码并撤销所有旧会话。"""

    try:
        get_platform_access_service(request).reset_password(
            challenge_id=payload.challenge_id, code=payload.code, new_password=payload.new_password
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    """服务端撤销会话并清除浏览器 Cookie。"""

    service = get_platform_access_service(request)
    service.logout(request.cookies.get(SESSION_COOKIE_NAME, ""))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def current_user(user: Annotated[PlatformUser, Depends(get_current_platform_user)]) -> dict[str, object]:
    """返回当前登录用户的安全资料。"""

    return {"user": _serialize_user(user)}


@router.get("/api/navigation/modules")
def get_route_modules(
    request: Request,
    user: Annotated[PlatformUser, Depends(get_current_platform_user)],
) -> dict[str, object]:
    """返回管理员配置后的有序模块目录及当前角色可访问状态。"""

    return {"items": get_platform_access_service(request).get_route_modules(user)}


@router.put("/api/admin/navigation-modules")
def save_route_modules(
    payload: RouteModuleConfigRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """保存顶级路由模块开关；普通用户由依赖统一返回 403。"""

    try:
        items = get_platform_access_service(request).save_route_modules(user, payload.modules)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"items": items}


@router.get("/api/admin/pipeline-config")
def get_pipeline_config(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """读取当前生效的运行配置快照。"""

    return {"item": get_platform_access_service(request).get_pipeline_config(user)}


@router.put("/api/admin/pipeline-config")
def save_pipeline_config(
    payload: PipelineConfigRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """保存并激活新的运行配置版本。"""

    try:
        item = get_platform_access_service(request).save_pipeline_config(user, payload.config)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"item": item}


@router.get("/api/admin/github-snapshot")
def get_github_snapshot(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """返回当前内容工作流会消费的最近 GitHub 周榜快照。"""

    del user
    return {"item": _load_github_snapshot_status(request.app.state.platform_access_project_root)}


@router.post("/api/admin/github-snapshot/refresh")
def refresh_github_snapshot(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """由管理员显式触发一次 GitHub 搜索，并用结果覆盖本周周榜快照。"""

    service = get_platform_access_service(request)
    config_item = service.get_pipeline_config(user)
    runtime_config = config_item.get("config", {}) if isinstance(config_item, dict) else {}
    if not isinstance(runtime_config, dict):
        runtime_config = {}
    try:
        result = Application(
            request.app.state.platform_access_project_root,
            runtime_config=runtime_config,
        ).refresh_github_snapshot()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "item": _load_github_snapshot_status(request.app.state.platform_access_project_root),
        "task": {
            "task_name": result.task_name,
            "run_id": result.run_id,
            "metadata": result.metadata,
        },
    }


@router.post("/api/admin/pipeline-runs")
def start_manual_pipeline(
    payload: ManualPipelineRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """登记并异步启动一次完整内容流水线，HTTP 响应不等待外部模型调用完成。"""

    snapshot = _load_github_snapshot_status(request.app.state.platform_access_project_root)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="尚无 GitHub 热门项目快照，请先到管理台点击“刷新 GitHub 热门项目”。",
        )
    idempotency_key = payload.client_request_id or str(uuid4())
    try:
        item = get_manual_pipeline_runner(request).request_run(user, idempotency_key=idempotency_key)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"item": item}


@router.get("/api/admin/pipeline-runs")
def list_manual_pipeline_runs(
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """返回手动运行历史，前端可轮询此接口展示实时状态。"""

    return {"items": get_manual_pipeline_runner(request).list_runs(user)}


@router.get("/api/admin/pipeline-runs/{run_id}/logs")
def list_manual_pipeline_logs(
    run_id: UUID,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
    after_id: int = 0,
    limit: int = 500,
) -> dict[str, object]:
    """返回一次手动工作流已经持久化的有序日志事件。"""

    if after_id < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="after_id 不能为负数")
    if limit <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="limit 必须大于 0")
    try:
        return get_platform_access_service(request).list_manual_pipeline_events(
            user,
            str(run_id),
            after_id=after_id,
            limit=min(limit, 1000),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/api/admin/pipeline-runs/{run_id}/logs/stream")
async def stream_manual_pipeline_logs(
    run_id: UUID,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
    after_id: int = 0,
) -> StreamingResponse:
    """按事件游标持续输出 SSE，运行进入终态后自然关闭。"""

    cursor = _pipeline_stream_cursor(request, after_id)
    service = get_platform_access_service(request)
    try:
        item = await asyncio.to_thread(service.get_manual_pipeline_request, user, str(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到流水线执行请求")
    return StreamingResponse(
        _pipeline_event_stream(service, user, run_id, after_id=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/observability/status")
def observability_status(
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    """只返回 LangSmith 运行状态和控制台地址，不泄露访问密钥。"""

    return {
        "enabled": is_langsmith_enabled(),
        "project": os.getenv("LANGSMITH_PROJECT", "ai-administration-platform"),
        "ui_url": os.getenv("LANGSMITH_UI_URL", "https://smith.langchain.com"),
        "tracing_backend": "langsmith",
        "privacy_mode": "metadata_only",
        "role": user.role.value,
    }


def _pipeline_stream_cursor(request: Request, after_id: int) -> int:
    """合并首次查询游标与 EventSource 自动重连时发送的事件 ID。"""

    if after_id < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="after_id 不能为负数")
    raw_last_event_id = request.headers.get("Last-Event-ID", "").strip()
    if not raw_last_event_id:
        return after_id
    try:
        last_event_id = int(raw_last_event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Last-Event-ID 必须是整数",
        ) from exc
    if last_event_id < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Last-Event-ID 不能为负数",
        )
    return max(after_id, last_event_id)


async def _pipeline_event_stream(
    service: Any,
    user: PlatformUser,
    run_id: UUID,
    *,
    after_id: int,
    poll_seconds: float = 0.3,
) -> AsyncIterator[str]:
    """从持久事件游标增量输出，避免断线造成日志缺口。"""

    cursor = after_id
    terminal_statuses = {"succeeded", "failed", "cancelled"}
    while True:
        payload = await asyncio.to_thread(
            service.list_manual_pipeline_events,
            user,
            str(run_id),
            after_id=cursor,
            limit=500,
        )
        events = payload.get("events", [])
        if not isinstance(events, list):
            events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = int(event.get("id", cursor))
            cursor = max(cursor, event_id)
            yield _pipeline_sse_event(event)

        item = payload.get("item", {})
        run_status = item.get("status") if isinstance(item, dict) else None
        if run_status in terminal_statuses and not events:
            return
        if not events:
            yield ": heartbeat\n\n"
        await asyncio.sleep(max(0, poll_seconds))


def _pipeline_sse_event(event: dict[str, object]) -> str:
    """序列化一条带稳定游标的工作流 SSE 事件。"""

    event_id = int(event["id"])
    event_type = str(event.get("event_type") or "log")
    data = json.dumps(event, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


def _load_github_snapshot_status(project_root: Path) -> dict[str, Any] | None:
    """从既有 SQLite 周榜表读取快照元数据，不触发任何 GitHub 网络请求。"""

    config = ConfigManager(project_root=project_root).load()
    database = DatabaseManager(config=config)
    database.initialize()
    return WeeklyRankingRepository(database_manager=database).latest_snapshot_status()


def _serialize_user(user: PlatformUser) -> dict[str, object]:
    """构造可安全发送给浏览器的用户结构。"""

    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_email_verified": user.email_verified_at is not None,
        "role": user.role.value,
    }


def _set_session_cookie(response: Response, request: Request, session: AuthenticatedSession) -> None:
    """以生产优先策略写入会话；本地 HTTP 自动关闭 secure 标记。"""

    _set_cookie(response, request, session.raw_token, session.expires_at)


def refresh_platform_session_cookie(
    response: Response,
    request: Request,
    session: SessionResolution,
) -> None:
    """供全局业务 API 鉴权中间件续写已验证会话的 Cookie。"""

    _set_cookie(response, request, request.cookies.get(SESSION_COOKIE_NAME, ""), session.expires_at)


def _set_cookie(response: Response, request: Request, raw_token: str, expires_at: datetime) -> None:
    """写入与服务端空闲会话过期时间一致的 HttpOnly Cookie。"""

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
        max_age=max(1, int((expires_at - datetime.now(expires_at.tzinfo)).total_seconds())),
    )


def _load_environment(project_root: Path) -> None:
    """读取服务端私有环境文件，不从浏览器接收任何数据库或密钥参数。"""

    environment_path = project_root / ".env.career-assistant"
    if not environment_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("检测到 .env.career-assistant，但未安装 python-dotenv") from exc
    load_dotenv(dotenv_path=environment_path, override=False)


def _read_boolean_environment(name: str, *, default: bool) -> bool:
    """以严格、可读的方式读取生产安全开关。"""

    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")
