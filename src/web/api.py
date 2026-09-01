from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src.config.config_manager import ConfigManager
from src.database.database_manager import DatabaseManager
from src.repositories.content_approval_repository import (
    ContentApprovalInput,
    ContentApprovalRepository,
    VALID_CONTENT_APPROVAL_DECISIONS,
)
from src.repositories.error_event_repository import ErrorEventRepository
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.json_utils import loads_json_or_empty
from src.repositories.task_run_repository import TaskRunRepository
from src.services.github_image_upgrade_service import GitHubImageUpgradeService
from src.services.media_preview_service import MediaPreviewError, MediaPreviewService
from src.services.skill_library_service import SkillDetail, SkillLibraryService, SkillSaveResult, SkillSearchResult
from src.tasks.article_layout_task import ArticleLayoutTask
from src.tasks.deliver_task import DeliverTask
from src.tasks.summary_task import SummaryTask
from src.tasks.task_context import TaskContext
from src.tasks.task_result import TaskResult
from src.career_assistant.web import install_career_assistant_api
from src.career_assistant.web.router import CareerRequestActor, reset_request_actor, set_request_actor
from src.evaluation.web import install_evaluation_api
from src.platform_access.web import (
    get_platform_access_service,
    install_platform_access_api,
    platform_auth_required,
    refresh_platform_session_cookie,
)


logger = logging.getLogger(__name__)
_PUBLIC_API_PATHS = frozenset({"/api/health"})
_PUBLIC_API_PREFIXES = ("/api/auth/",)


async def enforce_platform_access(request: Request, call_next: Any) -> Any:
    """校验业务 API 会话，并把已登录身份注入 Career 请求上下文。

    ``PLATFORM_AUTH_REQUIRED`` 只控制未登录请求是否必须返回 401。只要请求携带
    有效会话，即使本地关闭强制登录，也应继续传递真实角色，避免页面账户区显示
    管理员而 Career 接口仍按默认普通用户判权。
    """

    request_path = request.url.path
    is_business_api = (
        request_path.startswith("/api/")
        and request_path not in _PUBLIC_API_PATHS
        and not request_path.startswith(_PUBLIC_API_PREFIXES)
    )
    if not is_business_api:
        return await call_next(request)

    requires_session = platform_auth_required()
    raw_token = request.cookies.get("platform_session", "")
    if not requires_session and not raw_token:
        return await call_next(request)

    try:
        access_service = get_platform_access_service(request)
        session = access_service.resolve_session(raw_token)
    except HTTPException as exc:
        if not requires_session:
            return await call_next(request)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception:
        if not requires_session:
            logger.exception("可选平台身份解析失败，当前请求按未登录身份继续")
            return await call_next(request)
        logger.exception("平台身份服务初始化失败")
        return JSONResponse(status_code=503, content={"detail": "身份服务暂不可用，请稍后重试"})

    if session is None:
        if not requires_session:
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "请先登录后继续"})

    actor_token = set_request_actor(
        CareerRequestActor(
            organization_id=session.user.organization_id,
            actor_id=session.user.id,
            role=session.user.role,
        )
    )
    try:
        response = await call_next(request)
        refresh_platform_session_cookie(response, request, session)
        return response
    finally:
        reset_request_actor(actor_token)


class ContentApprovalRequest(BaseModel):
    """内容审核接口的请求体。"""

    content_id: int = Field(gt=0)
    decision: str
    operator: str | None = None
    comment: str | None = None


class ReviewActionRequest(BaseModel):
    """审核动作接口的请求体。

    这个接口会在记录审核结论后触发后续任务：
    - approved：排版并推送微信公众号草稿箱；
    - rejected / regenerate_requested：把备注作为反馈，触发 SummaryTask 再生成。
    """

    content_id: int = Field(gt=0)
    decision: str
    operator: str | None = None
    comment: str | None = None


class SkillSearchRequest(BaseModel):
    """技能库搜索接口的请求体。"""

    query: str = Field(default="", max_length=300)
    force_refresh: bool = False


class SkillSaveRequest(BaseModel):
    """技能库保存接口的请求体。"""

    name: str = Field(min_length=2, max_length=80)
    markdown: str = Field(min_length=20, max_length=200_000)
    source_repository_full_name: str | None = None
    source_homepage_url: str | None = None
    source_author: str | None = None


def create_app(project_root: Path | None = None) -> FastAPI:
    """创建用于本地审核台的 FastAPI 应用。

    这个函数由 preview_server.py 调用。它只负责读取本地数据库和提交人工审核结果，
    不会主动触发 GitHub、LLM、图片、视频或微信公众号外部接口。
    """

    resolved_project_root = project_root or Path.cwd()
    config = ConfigManager(project_root=resolved_project_root).load()
    database_manager = DatabaseManager(config=config)
    database_manager.initialize()

    preview_service = MediaPreviewService(
        config=config,
        database_manager=database_manager,
    )
    github_image_upgrade_service = GitHubImageUpgradeService(
        config=config,
        database_manager=database_manager,
    )
    task_run_repository = TaskRunRepository(database_manager=database_manager)
    error_event_repository = ErrorEventRepository(database_manager=database_manager)
    generated_content_repository = GeneratedContentRepository(database_manager=database_manager)
    content_approval_repository = ContentApprovalRepository(database_manager=database_manager)
    skill_library_service = SkillLibraryService(config=config)

    def run_review_task(task_class: type[Any]) -> dict[str, Any]:
        """从审核接口同步触发一个任务，并返回前端可展示的结果。"""

        task = task_class(
            task_run_repository=task_run_repository,
            error_event_repository=error_event_repository,
        )
        result: TaskResult = task.run(
            TaskContext(
                config=config,
                database_manager=database_manager,
            )
        )
        return {
            "task_name": result.task_name,
            "run_id": result.run_id,
            "metadata": result.metadata,
        }

    def skill_detail_payload(detail: SkillDetail) -> dict[str, Any]:
        """把 SkillDetail 转换为前端可消费的 JSON。"""

        return {
            "skill": skill_summary_payload(detail.summary),
            "markdown": detail.markdown,
        }

    def skill_summary_payload(skill: Any) -> dict[str, Any]:
        """把 Skill 摘要转换为前端可消费的 JSON。"""

        return {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "description_zh": skill.description_zh,
            "author": skill.author,
            "homepage_url": skill.homepage_url,
            "repository_full_name": skill.repository_full_name,
            "stars": skill.stars,
            "previous_stars": skill.previous_stars,
            "star_delta": skill.star_delta,
            "star_growth_rate": skill.star_growth_rate,
            "stars_updated_at": skill.stars_updated_at,
            "source": skill.source,
            "source_label": skill.source_label,
            "path_hint": skill.path_hint,
            "editable": skill.editable,
        }

    def skill_search_payload(result: SkillSearchResult) -> dict[str, Any]:
        """把 SkillSearchResult 转换为前端可消费的 JSON。"""

        return {
            "items": [
                {
                    "skill": skill_summary_payload(item.skill),
                    "score": item.score,
                    "match_reason": item.match_reason,
                    "markdown": item.markdown,
                }
                for item in result.items
            ],
            "used_llm": result.used_llm,
            "model": result.model,
            "fallback_reason": result.fallback_reason,
            "search_scope": result.search_scope,
            "normalized_query": result.normalized_query,
            "status_message": result.status_message,
            "cache_hit": result.cache_hit,
            "elapsed_ms": result.elapsed_ms,
            "snapshot_at": result.snapshot_at,
            "cache_state": result.cache_state,
            "data_source": result.data_source,
        }

    def skill_save_payload(result: SkillSaveResult) -> dict[str, Any]:
        """把 SkillSaveResult 转换为前端可消费的 JSON。"""

        return {
            "skill": skill_summary_payload(result.skill),
            "markdown": result.markdown,
            "created": result.created,
            "saved_path_hint": result.skill.path_hint,
        }

    app = FastAPI(
        title="GitHub Weekly WeChat Agent Preview API",
        version="0.1.0",
    )
    install_career_assistant_api(
        app,
        resolved_project_root,
        skill_library_service=skill_library_service,
    )
    install_evaluation_api(app)
    install_platform_access_api(app, resolved_project_root)

    app.middleware("http")(enforce_platform_access)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """返回 Web API 的轻量健康状态与求职模块运行版本。"""

        return {
            "status": "ok",
            "app": config.app_name,
            "run_mode": config.run_mode,
            # 该标记仅用于排查浏览器是否仍在连接旧开发后端，不包含配置或密钥。
            "career_runtime_revision": "2026-08-28-interview-admin-permissions-v2",
        }

    @app.get("/api/skills")
    def list_skills() -> dict[str, Any]:
        """读取当前本地可见的 Skill 列表。"""

        skills = skill_library_service.list_skills()
        return {
            "items": [skill_summary_payload(skill) for skill in skills],
            "total": len(skills),
        }

    @app.get("/api/skills/{skill_id}")
    def get_skill(skill_id: str) -> dict[str, Any]:
        """读取指定 Skill 的 SKILL.md 内容。"""

        try:
            detail = skill_library_service.get_skill(skill_id=skill_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return skill_detail_payload(detail)

    @app.post("/api/skills/search")
    def search_skills(request: SkillSearchRequest) -> dict[str, Any]:
        """按 find-skills 思路检索 GitHub 开放 Skill，超时回退本地结果。"""

        result = skill_library_service.search_skills(
            query=request.query,
            force_refresh=request.force_refresh,
        )
        return skill_search_payload(result)

    @app.post("/api/skills/save")
    def save_skill(request: SkillSaveRequest) -> dict[str, Any]:
        """把编辑后的 Skill.md 保存为项目本地 Skill。"""

        try:
            result = skill_library_service.save_skill(
                name=request.name,
                markdown=request.markdown,
                source_repository_full_name=request.source_repository_full_name,
                source_homepage_url=request.source_homepage_url,
                source_author=request.source_author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return skill_save_payload(result)

    @app.get("/api/system/health-summary")
    def system_health_summary() -> dict[str, Any]:
        """读取 CatTask 最近一次生成的全局健康摘要。

        CatTask 是当前系统的“巡检猫”：它不会修复问题，只把最近任务状态、历史异常、
        缺失资源等信息整理成适合 UI 展示的摘要。
        """

        records = task_run_repository.list_recent(limit=100)
        cat_record = next((record for record in records if record.task_name == "CatTask"), None)
        if cat_record is None:
            return {
                "health_status": "unknown",
                "message": "CatTask 还没有运行，暂无全局巡检摘要。",
                "blocking_item_count": 0,
                "failed_run_count": 0,
                "historical_failed_run_count": 0,
                "top_blocking_reasons": [],
                "blocking_items": [],
                "latest_tasks": [],
                "recent_errors": [],
            }

        metadata = loads_json_or_empty(cat_record.metadata_json)
        return {
            "run_id": cat_record.run_id,
            "status": cat_record.status,
            "health_status": metadata.get("health_status", "unknown"),
            "message": metadata.get("message"),
            "blocking_item_count": metadata.get("blocking_item_count", 0),
            "failed_run_count": metadata.get("failed_run_count", 0),
            "historical_failed_run_count": metadata.get("historical_failed_run_count", 0),
            "top_blocking_reasons": metadata.get("top_blocking_reasons", []),
            "blocking_items": metadata.get("blocking_items", []),
            "latest_tasks": metadata.get("latest_tasks", []),
            "recent_errors": metadata.get("recent_errors", []),
        }

    @app.get("/api/preview/latest")
    def latest_preview() -> dict[str, Any]:
        """读取最新图文、脚本、素材和人工审核状态。"""

        return preview_service.build_latest_preview()

    @app.get("/api/execution-history")
    def execution_history(limit: int = 50) -> dict[str, Any]:
        """读取按 content_id 归档的执行历史索引。"""

        return preview_service.build_execution_history(limit=limit)

    @app.get("/api/execution-history/{content_id}")
    def execution_history_detail(content_id: int) -> dict[str, Any]:
        """读取指定 content_id 对应的推文与专属资源库。"""

        if content_id <= 0:
            raise HTTPException(status_code=400, detail="content_id 必须大于 0")

        payload = preview_service.build_content_preview(content_id=content_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"执行历史不存在：content_id={content_id}")
        return payload

    @app.get("/api/media-assets")
    def media_asset_library(limit: int = 300, content_id: int | None = None) -> dict[str, Any]:
        """读取媒体资源库；传 content_id 时仅返回该执行批次的素材。"""

        if content_id is not None and content_id <= 0:
            raise HTTPException(status_code=400, detail="content_id 必须大于 0")
        return preview_service.build_media_library(limit=limit, content_id=content_id)

    @app.get("/api/tasks/recent")
    def recent_tasks(limit: int = 20) -> dict[str, Any]:
        """读取最近任务运行记录，供审核台展示 Pipeline 状态。"""

        normalized_limit = min(max(limit, 1), 100)
        records = task_run_repository.list_recent(limit=normalized_limit)
        return {
            "items": [
                {
                    "id": record.id,
                    "run_id": record.run_id,
                    "task_name": record.task_name,
                    "status": record.status,
                    "retry_count": record.retry_count,
                    "error_message": record.error_message,
                    "metadata": loads_json_or_empty(record.metadata_json),
                }
                for record in records
            ]
        }

    @app.get("/api/approvals/content/{content_id}/latest")
    def latest_content_approval(content_id: int) -> dict[str, Any]:
        """读取指定内容的最新人工审核决策。"""

        approval = content_approval_repository.latest_for_content(content_id=content_id)
        return {
            "approval": None
            if approval is None
            else {
                "id": approval.id,
                "content_id": approval.content_id,
                "decision": approval.decision,
                "operator": approval.operator,
                "comment": approval.comment,
                "created_at": approval.created_at,
            }
        }

    @app.post("/api/approvals/content")
    def create_content_approval(request: ContentApprovalRequest) -> dict[str, Any]:
        """提交内容级人工审核决策。

        通过后，generated_contents.status 会同步变成 approved；
        DeliverTask 后续只会处理 approved 的内容。
        """

        normalized_decision = request.decision.strip()
        if normalized_decision not in VALID_CONTENT_APPROVAL_DECISIONS:
            supported = ", ".join(sorted(VALID_CONTENT_APPROVAL_DECISIONS))
            raise HTTPException(status_code=400, detail=f"decision 只支持：{supported}")

        if not generated_content_repository.exists(request.content_id):
            raise HTTPException(status_code=404, detail=f"内容不存在：content_id={request.content_id}")

        approval = content_approval_repository.create(
            ContentApprovalInput(
                content_id=request.content_id,
                decision=normalized_decision,
                operator=request.operator,
                comment=request.comment,
            )
        )
        generated_content_repository.update_status(
            content_id=request.content_id,
            status=normalized_decision,
        )

        return {
            "approval": {
                "id": approval.id,
                "content_id": approval.content_id,
                "decision": approval.decision,
                "operator": approval.operator,
                "comment": approval.comment,
                "created_at": approval.created_at,
            }
        }

    @app.post("/api/review/content-action")
    def execute_review_action(request: ReviewActionRequest) -> dict[str, Any]:
        """执行审核动作，并按动作类型触发后续任务链。"""

        normalized_decision = request.decision.strip()
        if normalized_decision not in VALID_CONTENT_APPROVAL_DECISIONS:
            supported = ", ".join(sorted(VALID_CONTENT_APPROVAL_DECISIONS))
            raise HTTPException(status_code=400, detail=f"decision 只支持：{supported}")

        if not generated_content_repository.exists(request.content_id):
            raise HTTPException(status_code=404, detail=f"内容不存在：content_id={request.content_id}")

        approval = content_approval_repository.create(
            ContentApprovalInput(
                content_id=request.content_id,
                decision=normalized_decision,
                operator=request.operator,
                comment=request.comment,
            )
        )
        generated_content_repository.update_status(
            content_id=request.content_id,
            status=normalized_decision,
        )

        task_results: list[dict[str, Any]] = []
        if normalized_decision == "approved":
            task_results.append(run_review_task(ArticleLayoutTask))
            task_results.append(run_review_task(DeliverTask))
        else:
            task_results.append(run_review_task(SummaryTask))

        return {
            "approval": {
                "id": approval.id,
                "content_id": approval.content_id,
                "decision": approval.decision,
                "operator": approval.operator,
                "comment": approval.comment,
                "created_at": approval.created_at,
            },
            "task_results": task_results,
        }

    @app.post("/api/content/{content_id}/github-image-upgrade")
    def upgrade_github_images(content_id: int, force: bool = False) -> dict[str, Any]:
        """手动为指定内容尝试新增 GitHub 仓库图。

        这个接口会访问 GitHub，但不会调用 Seedream，不会删除旧图片。
        它适合在审核台里把本地兜底图升级为更贴近项目的仓库截图/架构图。
        """

        task_run = task_run_repository.create(
            task_name="GitHubImageUpgradeAction",
            metadata={
                "phase": "created",
                "content_id": content_id,
                "force": force,
                "paid_generation_called": False,
            },
        )
        try:
            task_run_repository.mark_running(task_run.id)
            result = github_image_upgrade_service.upgrade_content_images(content_id=content_id, force=force)
            metadata = result.to_dict()
            task_run_repository.mark_succeeded(task_run.id, metadata=metadata)
            return {
                "run_id": task_run.run_id,
                "result": metadata,
            }
        except Exception as exc:
            metadata = {
                "content_id": content_id,
                "force": force,
                "error_type": exc.__class__.__name__,
                "paid_generation_called": False,
            }
            task_run_repository.mark_failed(
                task_run_id=task_run.id,
                error_message=str(exc) or exc.__class__.__name__,
                metadata=metadata,
            )
            error_event_repository.create(
                task_run_id=task_run.id,
                task_name="GitHubImageUpgradeAction",
                severity="error",
                message=str(exc) or exc.__class__.__name__,
                error_type=exc.__class__.__name__,
                stack_trace=traceback.format_exc(),
                metadata=metadata,
            )
            raise HTTPException(status_code=500, detail=str(exc) or exc.__class__.__name__) from exc

    @app.get("/api/media-assets/{asset_id}/file")
    def media_asset_file(asset_id: int) -> FileResponse:
        """安全返回本地媒体文件，用于审核台预览。"""

        try:
            file_path, media_type = preview_service.resolve_local_media_path(asset_id)
        except MediaPreviewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return FileResponse(
            path=str(file_path),
            media_type=media_type,
            filename=file_path.name,
        )

    return app
