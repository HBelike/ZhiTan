"""Windows 实时面试助手的 REST 配置接口与业务 WebSocket。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from src.career_assistant.contracts import ModelCapability, ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.live_interview.answer_service import LiveAnswerService
from src.career_assistant.live_interview.archive import LiveInterviewArchiveService
from src.career_assistant.live_interview.asr.dashscope_realtime import (
    DEFAULT_DASHSCOPE_ASR_MODEL,
    DEFAULT_DASHSCOPE_ASR_URL,
    DashScopeRealtimeAsrProvider,
)
from src.career_assistant.live_interview.asr.fake import FakeAsrProvider
from src.career_assistant.live_interview.asr.openai_realtime import OpenAIRealtimeAsrProvider
from src.career_assistant.live_interview.context_builder import LiveAnswerContext
from src.career_assistant.live_interview.desktop_launcher import (
    DesktopLauncherError,
    launch_windows_desktop_assistant,
)
from src.career_assistant.live_interview.contracts import (
    AnswerStatus,
    AudioChannel,
    SessionEndEvent,
    TranscriptEvent,
    parse_client_event,
)
from src.career_assistant.live_interview.persistence import (
    LiveInterviewRepository,
    LiveInterviewSessionRecord,
    session_payload,
)
from src.career_assistant.live_interview.session_manager import LiveSessionManager
from src.career_assistant.live_interview.terminology import TerminologyCorrector
from src.career_assistant.model_clients import ChatMessage
from src.career_assistant.model_gateway import ModelReadiness
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
)
from src.platform_access.web import (
    SESSION_COOKIE_NAME,
    get_platform_access_service,
    platform_auth_required,
)
from src.platform_access.contracts import PlatformRole


router = APIRouter(prefix="/api/career/live-interviews", tags=["live-interview"])


class CreateLiveInterviewRequest(BaseModel):
    asr_model_profile_id: UUID | None = None
    answer_model_profile_id: UUID | None = None
    client_kind: Literal["desktop", "browser"] = "desktop"
    candidate_audio_enabled: bool = True


class LiveInterviewArchivePreviewRequest(BaseModel):
    session_ids: list[UUID] = Field(min_length=1, max_length=20)


class LiveInterviewArchiveRequest(LiveInterviewArchivePreviewRequest):
    company_name: str = Field(min_length=1, max_length=120)
    role_name: str = Field(min_length=1, max_length=160)
    interview_date: date


def get_live_actor():
    from src.career_assistant.web.router import get_request_actor

    return get_request_actor()


@router.post("/desktop/launch")
def launch_desktop(request: Request, _actor=Depends(get_live_actor)) -> dict[str, str]:
    """从求职助手页面启动本机 Windows 双路音频采集器。"""

    try:
        result = launch_windows_desktop_assistant(
            api_base_url=str(request.base_url).rstrip("/"),
        )
    except DesktopLauncherError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": result.status, "message": result.message}


def get_live_read_services(request: Request):
    from src.career_assistant.web.router import get_career_read_services

    return get_career_read_services(request)


def get_live_repository(request: Request) -> LiveInterviewRepository:
    return LiveInterviewRepository(get_live_read_services(request).database)


def get_live_archive_service(request: Request) -> LiveInterviewArchiveService:
    from src.career_assistant.web.router import get_career_services

    services = get_career_services(request)
    return LiveInterviewArchiveService(
        live_repository=LiveInterviewRepository(services.database),
        interview_repository=services.interview_library_repository,
        interview_service=services.interview_library_service,
    )


@router.get("/setup-options")
def setup_options(request: Request, actor=Depends(get_live_actor)) -> dict[str, object]:
    services = get_live_read_services(request)
    models = services.model_gateway.list_availability(actor.organization_id)
    return {
        "environment_asr": _environment_asr_config(),
        "asr_models": [
            _model_payload(item)
            for item in models
            if ModelCapability.TRANSCRIPTION in item.profile.capabilities
        ],
        "answer_models": [
            _model_payload(item)
            for item in models
            if ModelCapability.TEXT in item.profile.capabilities
        ],
        "audio_policy": {
            "platform": "桌面版 Chrome、Edge、Firefox 或 Windows 10/11 Electron",
            "raw_audio_persisted": False,
            "notice": "仅在参与者已知情并允许使用 AI 的场景开始采集。",
        },
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    payload: CreateLiveInterviewRequest,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    environment_asr = _environment_asr_config()
    if payload.asr_model_profile_id is None and environment_asr["readiness"] != "ready":
        raise HTTPException(
            status_code=422,
            detail=str(environment_asr["blocked_reason"]),
        )
    repository = get_live_repository(request)
    try:
        record = repository.create_session(
            actor.organization_id,
            actor.actor_id,
            candidate_profile_id=None,
            target_role_profile_id=None,
            interview_experience_ids=(),
            asr_provider=(
                "fake"
                if os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1"
                else (
                    "profile"
                    if payload.asr_model_profile_id is not None
                    else str(environment_asr["provider_key"])
                )
            ),
            asr_model_profile_id=payload.asr_model_profile_id,
            answer_model_profile_id=payload.answer_model_profile_id,
            client_kind=payload.client_kind,
            candidate_audio_enabled=payload.candidate_audio_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"session": session_payload(record)}


@router.get("/sessions/{session_id}")
def get_session(
    session_id: UUID,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    record = get_live_repository(request).get_session(
        actor.organization_id,
        actor.actor_id,
        session_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="实时面试会话不存在或无权访问")
    return {"session": session_payload(record)}


@router.get("/sessions/{session_id}/history")
def get_history(
    session_id: UUID,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    payload = get_live_repository(request).history(
        actor.organization_id,
        actor.actor_id,
        session_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="实时面试会话不存在或无权访问")
    return payload


@router.post("/archive/preview")
def preview_archive(
    payload: LiveInterviewArchivePreviewRequest,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    """从完整后端历史读取结束弹窗的题数和前五题。"""

    try:
        preview = get_live_archive_service(request).preview(
            organization_id=actor.organization_id,
            actor_id=actor.actor_id,
            session_ids=tuple(payload.session_ids),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _archive_preview_payload(preview)


@router.post("/archive", status_code=status.HTTP_201_CREATED)
def archive_interview(
    payload: LiveInterviewArchiveRequest,
    request: Request,
    actor=Depends(get_live_actor),
) -> dict[str, object]:
    """把本场多个实时会话中的问题归并到面经库。"""

    try:
        result = get_live_archive_service(request).archive(
            organization_id=actor.organization_id,
            actor_id=actor.actor_id,
            can_manage_all=actor.role is PlatformRole.ADMIN,
            session_ids=tuple(payload.session_ids),
            company_name=payload.company_name,
            role_name=payload.role_name,
            interview_date=payload.interview_date,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        **_archive_preview_payload(result),
        "experience": {
            "id": str(result.experience.id),
            "company_name": result.experience.company_name,
            "role_name": result.experience.role_name,
            "job_name": result.experience.job_name,
            "interview_date": (
                result.experience.interview_date.isoformat()
                if result.experience.interview_date
                else None
            ),
        },
    }


def _archive_preview_payload(preview) -> dict[str, object]:
    return {
        "question_count": preview.question_count,
        "question_preview": list(preview.question_preview),
        "started_at": preview.started_at.isoformat(),
        "ended_at": preview.ended_at.isoformat(),
    }


@router.websocket("/{session_id}/stream")
async def live_interview_stream(websocket: WebSocket, session_id: UUID) -> None:
    actor = await _resolve_websocket_actor(websocket)
    if actor is None:
        return
    repository = _repository_for_websocket(websocket)
    record = await asyncio.to_thread(
        repository.get_session,
        actor.organization_id,
        actor.actor_id,
        session_id,
    )
    if record is None:
        await websocket.close(code=4404, reason="实时面试会话不存在或无权访问")
        return

    connections: set[UUID] = getattr(websocket.app.state, "live_interview_connections", set())
    websocket.app.state.live_interview_connections = connections
    if session_id in connections:
        await websocket.close(code=4409, reason="该会话已有活动连接")
        return
    connections.add(session_id)
    manager: LiveSessionManager | None = None
    failed = False
    await websocket.accept()
    try:
        manager = await _build_live_manager(websocket, repository, record)
        await asyncio.to_thread(repository.activate, actor.organization_id, actor.actor_id, session_id)
        await manager.start()
        sender = asyncio.create_task(_send_manager_events(websocket, manager))
        try:
            while not manager.closed:
                payload = await websocket.receive_json()
                try:
                    event = parse_client_event(payload)
                    await manager.handle(event)
                except ValueError as exc:
                    await websocket.send_json(
                        {"type": "error", "code": "invalid_event", "message": str(exc)}
                    )
                    continue
                if isinstance(event, SessionEndEvent):
                    break
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception:
        failed = True
        try:
            await websocket.send_json(
                {"type": "error", "code": "session_failed", "message": "实时面试会话已中止"}
            )
        except Exception:
            pass
    finally:
        if manager is not None:
            await manager.close("disconnect")
        await asyncio.to_thread(
            repository.end,
            actor.organization_id,
            actor.actor_id,
            session_id,
            failed=failed,
        )
        connections.discard(session_id)
        try:
            await websocket.close()
        except Exception:
            pass


async def _send_manager_events(websocket: WebSocket, manager: LiveSessionManager) -> None:
    while True:
        event = await manager.next_event()
        await websocket.send_json(event.to_dict())


async def _resolve_websocket_actor(websocket: WebSocket):
    from src.career_assistant.web.router import CareerRequestActor

    requires_session = platform_auth_required()
    token = websocket.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        if not requires_session:
            return CareerRequestActor(DEFAULT_ORGANIZATION_ID, DEFAULT_ACTOR_ID)
        await websocket.close(code=4401, reason="请先登录后继续")
        return None
    try:
        resolution = get_platform_access_service(websocket).resolve_session(token)
    except HTTPException:
        resolution = None
    if resolution is None:
        if not requires_session:
            return CareerRequestActor(DEFAULT_ORGANIZATION_ID, DEFAULT_ACTOR_ID)
        await websocket.close(code=4401, reason="请先登录后继续")
        return None
    return CareerRequestActor(
        resolution.user.organization_id,
        resolution.user.id,
        resolution.user.role,
    )


def _repository_for_websocket(websocket: WebSocket) -> LiveInterviewRepository:
    from src.career_assistant.web.router import get_career_read_services

    return LiveInterviewRepository(get_career_read_services(websocket).database)


async def _build_live_manager(
    websocket: WebSocket,
    repository: LiveInterviewRepository,
    record: LiveInterviewSessionRecord,
) -> LiveSessionManager:
    from src.career_assistant.web.router import get_career_services

    services = get_career_services(websocket)
    terms: tuple[str, ...] = ()
    corrector = TerminologyCorrector(terms)

    asr_provider = _resolve_asr_provider(services, record)
    sessions = {}
    try:
        active_channels = [AudioChannel.INTERVIEWER]
        if record.candidate_audio_enabled:
            active_channels.append(AudioChannel.CANDIDATE)
        for channel in active_channels:
            sessions[channel] = await asr_provider.start(channel, prompt="、".join(terms[:100]))
    except Exception:
        await asyncio.gather(*(item.close() for item in sessions.values()), return_exceptions=True)
        raise

    answer_resolution = services.model_gateway.resolve(
        record.organization_id,
        ModelSelectionRequest(
            mode=ModelSelectionMode.SPECIFIC_PROFILE
            if record.answer_model_profile_id
            else ModelSelectionMode.FREE_QUOTA_FIRST,
            profile_id=record.answer_model_profile_id,
            required_capabilities=frozenset({ModelCapability.TEXT}),
        ),
    )
    if answer_resolution.readiness is not ModelReadiness.READY:
        raise RuntimeError("回答模型尚未配置可用凭据")

    async def prompt_streamer(prompt: str) -> AsyncIterator[str]:
        iterator = services.model_connection_client.stream_complete(
            answer_resolution.profile,
            answer_resolution.credential_env_name,
            [
                ChatMessage(role="system", content="你是低延迟实时面试回答助手。"),
                ChatMessage(role="user", content=prompt),
            ],
            api_key=answer_resolution.credential,
        )
        async for chunk in iterate_in_threadpool(iterator):
            yield chunk

    async def save_transcript(event: TranscriptEvent) -> None:
        if record.client_kind == "browser" and event.channel is AudioChannel.CANDIDATE:
            return
        correction = corrector.correct(event.text)
        await asyncio.to_thread(
            repository.append_final_utterance,
            record.organization_id,
            record.actor_id,
            record.id,
            event,
            corrected_text=correction.corrected_text,
        )

    async def save_answer(version, attempt, question, intent, answer_status, answer_text) -> None:
        await asyncio.to_thread(
            repository.upsert_answer,
            record.organization_id,
            record.actor_id,
            record.id,
            question_version=version,
            attempt=attempt,
            question=question,
            intent=intent,
            status=AnswerStatus(answer_status),
            answer_text=answer_text,
        )

    return LiveSessionManager(
        asr_sessions=sessions,
        answer_service=LiveAnswerService(prompt_streamer),
        answer_context=LiveAnswerContext(),
        transcript_hook=save_transcript,
        answer_hook=save_answer,
    )


def _resolve_asr_provider(services, record: LiveInterviewSessionRecord):
    if record.asr_provider == "fake" and os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1":
        return FakeAsrProvider()
    if record.asr_model_profile_id is None:
        if record.asr_provider == "dashscope":
            return _dashscope_environment_provider()
        if record.asr_provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY 尚未配置")
            return OpenAIRealtimeAsrProvider(
                api_key,
                model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
            )
        raise RuntimeError("实时转写 Provider 配置无效")
    resolution = services.model_gateway.resolve(
        record.organization_id,
        ModelSelectionRequest(
            mode=ModelSelectionMode.SPECIFIC_PROFILE,
            profile_id=record.asr_model_profile_id,
            required_capabilities=frozenset({ModelCapability.TRANSCRIPTION}),
        ),
    )
    if resolution.readiness is not ModelReadiness.READY or not resolution.credential:
        raise RuntimeError("实时转写模型尚未配置可用凭据")
    if resolution.profile.provider_key in {"qwen", "dashscope", "aliyun"}:
        return DashScopeRealtimeAsrProvider(
            resolution.credential,
            model=resolution.profile.model_id or DEFAULT_DASHSCOPE_ASR_MODEL,
            base_url=os.getenv("DASHSCOPE_ASR_WEBSOCKET_URL", DEFAULT_DASHSCOPE_ASR_URL),
            workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", ""),
        )
    if resolution.profile.provider_key != "openai":
        raise RuntimeError("实时转写仅支持 DashScope 或 OpenAI Provider")
    return OpenAIRealtimeAsrProvider(
        resolution.credential,
        model=resolution.profile.model_id,
        base_url=_realtime_url(resolution.profile.api_base_url),
    )


def _realtime_url(api_base_url: str | None) -> str:
    if not api_base_url:
        return "wss://api.openai.com/v1/realtime"
    base = api_base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    return base if base.endswith("/realtime") else f"{base}/realtime"


def _environment_asr_config() -> dict[str, object]:
    if os.getenv("LIVE_INTERVIEW_FAKE_ASR", "").strip() == "1":
        return {
            "readiness": "ready",
            "blocked_reason": None,
            "display_name": "本地测试转写",
            "provider_key": "fake",
            "model_id": "fake-asr",
        }
    if os.getenv("DASHSCOPE_API_KEY", "").strip():
        return {
            "readiness": "ready",
            "blocked_reason": None,
            "display_name": "阿里云百炼 · Qwen 实时转写",
            "provider_key": "dashscope",
            "model_id": os.getenv("DASHSCOPE_ASR_MODEL", DEFAULT_DASHSCOPE_ASR_MODEL),
        }
    if os.getenv("OPENAI_API_KEY", "").strip():
        return {
            "readiness": "ready",
            "blocked_reason": None,
            "display_name": "OpenAI 实时转写",
            "provider_key": "openai",
            "model_id": os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
        }
    return {
        "readiness": "blocked",
        "blocked_reason": (
            "尚未配置实时转写模型。请在服务端设置 DASHSCOPE_API_KEY；"
            "DeepSeek 继续负责生成答案，不能直接把微信通话音频转成文字。"
        ),
        "display_name": "未配置实时转写模型",
        "provider_key": None,
        "model_id": None,
    }


def _dashscope_environment_provider() -> DashScopeRealtimeAsrProvider:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 尚未配置")
    return DashScopeRealtimeAsrProvider(
        api_key,
        model=os.getenv("DASHSCOPE_ASR_MODEL", DEFAULT_DASHSCOPE_ASR_MODEL),
        base_url=os.getenv("DASHSCOPE_ASR_WEBSOCKET_URL", DEFAULT_DASHSCOPE_ASR_URL),
        workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", ""),
    )


def _model_payload(availability) -> dict[str, object]:
    profile = availability.profile
    return {
        "id": str(profile.id),
        "display_name": profile.display_name,
        "provider_key": profile.provider_key,
        "model_id": profile.model_id,
        "capabilities": sorted(item.value for item in profile.capabilities),
        "readiness": availability.readiness.value,
        "blocked_reason": availability.blocked_reason,
    }
