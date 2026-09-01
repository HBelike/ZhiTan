"""管理员专用的求职模型上下文策略接口。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.career_assistant.persistence import ModelContextPolicy, ModelProfileRecord
from src.career_assistant.web.router import get_career_read_services, get_request_actor
from src.platform_access.contracts import PlatformUser
from src.platform_access.web import require_admin


router = APIRouter(prefix="/api/admin/career", tags=["career-admin-context"])


class UpdateModelContextPolicyRequest(BaseModel):
    """管理员提交的精确模型上下文能力与压缩策略。"""

    context_window_tokens: int = Field(ge=4_096, le=2_000_000)
    reserved_output_tokens: int = Field(ge=1, le=2_000_000)
    compression_trigger_percent: int = Field(ge=50, le=90)
    compression_target_percent: int = Field(ge=30, le=75)
    context_window_source: Literal["built_in", "admin"] = "admin"


@router.get("/model-profiles/{profile_key}/context-policy")
def get_policy(
    profile_key: str,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    del user
    actor = get_request_actor()
    profile = get_career_read_services(request).model_profile_repository.get_profile_by_key(
        actor.organization_id,
        profile_key,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="模型档案不存在")
    return {"item": _policy_payload(profile)}


@router.put("/model-profiles/{profile_key}/context-policy")
def update_policy(
    profile_key: str,
    payload: UpdateModelContextPolicyRequest,
    request: Request,
    user: Annotated[PlatformUser, Depends(require_admin)],
) -> dict[str, object]:
    del user
    policy = ModelContextPolicy(**payload.model_dump())
    try:
        policy.validate()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    actor = get_request_actor()
    profile = get_career_read_services(request).model_profile_repository.update_context_policy(
        actor.organization_id,
        profile_key,
        policy,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="模型档案不存在")
    return {"item": _policy_payload(profile)}


def _policy_payload(profile: ModelProfileRecord) -> dict[str, object]:
    return {
        "profile_key": profile.profile_key,
        "display_name": profile.display_name,
        **asdict(profile.context_policy),
        "updated_at": profile.updated_at.isoformat(),
    }
