"""求职助手的可配置模型路由层。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from src.career_assistant.contracts import (
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.persistence.model_profile_repository import (
    CareerModelProfileRepository,
    ModelCostTier,
    ModelProfileRecord,
)
from src.career_assistant.settings import ModelGatewaySettings
from src.observability.langsmith_runtime import trace_operation


class ModelResolutionReason(StrEnum):
    """向前端说明本轮模型为何被选择的安全枚举。"""

    FREE_QUOTA_PRIORITY = "free_quota_priority"
    USER_SELECTED = "user_selected"


class ModelReadiness(StrEnum):
    """模型档案的当前可调用状态，不暴露任何密钥内容。"""

    READY = "ready"
    CREDENTIAL_REQUIRED = "credential_required"
    POLICY_BLOCKED = "policy_blocked"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ModelProfileAvailability:
    """供设置页展示的模型档案可用性。"""

    profile: ModelProfileRecord
    readiness: ModelReadiness
    credential_env_name: str | None
    blocked_reason: str | None


@dataclass(frozen=True)
class ModelResolution:
    """模型网关输出给未来 Provider Adapter 的无密钥路由结果。"""

    profile: ModelProfileRecord
    reason: ModelResolutionReason
    readiness: ModelReadiness
    credential_env_name: str | None
    credential: str | None = field(repr=False, default=None)


class ModelGateway:
    """按用户选择、能力标签和免费策略解析可使用的模型档案。

    本类不发起 HTTP 请求，也不读取 API Key 的真实值。它仅通过环境变量是否非空判断
    某个云端 Provider 是否已配置，真正的 Provider Adapter 将在后续步骤接入。
    """

    def __init__(
        self,
        repository: CareerModelProfileRepository,
        settings: ModelGatewaySettings,
        environment: dict[str, str] | None = None,
    ) -> None:
        """注入模型档案仓储、策略与可替换环境快照，便于无密钥测试。"""

        self._repository = repository
        self._settings = settings
        self._environment = environment if environment is not None else dict(os.environ)

    def list_availability(self, organization_id: UUID) -> list[ModelProfileAvailability]:
        """返回组织内全部模型档案及其安全可用性状态。"""

        return [
            self._to_availability(profile)
            for profile in self._repository.list_profiles(
                organization_id,
                include_disabled=True,
            )
        ]

    def resolve(
        self,
        organization_id: UUID,
        selection: ModelSelectionRequest,
    ) -> ModelResolution:
        """根据用户选择解析一个符合能力和策略的模型档案。

        此方法不会在没有凭证时偷偷切换到付费模型；它返回
        ``CREDENTIAL_REQUIRED``，由 WebUI 明确提示用户配置对应免费额度 Key。
        """

        return trace_operation(
            run_name="career.model.route",
            run_type="chain",
            inputs={
                "selection_mode": selection.mode.value,
                "required_capability_count": len(selection.required_capabilities),
            },
            metadata={
                "component": "career_model_gateway",
                "selection_mode": selection.mode.value,
            },
            execute=lambda: self._resolve_without_trace(organization_id, selection),
            summarize=self._summarize_resolution,
            tags=("career", "model-routing"),
        )

    def _resolve_without_trace(
        self,
        organization_id: UUID,
        selection: ModelSelectionRequest,
    ) -> ModelResolution:
        """执行原始路由逻辑，组织、档案及凭证标识均不进入 Trace。"""

        selection.validate()
        if selection.mode is ModelSelectionMode.SPECIFIC_PROFILE:
            return self._resolve_specific_profile(organization_id, selection)
        return self._resolve_free_quota_first(organization_id, selection)

    @staticmethod
    def _summarize_resolution(resolution: ModelResolution) -> dict[str, str]:
        """仅返回路由结果标签，不暴露组织、档案、凭证或环境变量标识。"""

        return {
            "provider": resolution.profile.provider_key,
            "model": resolution.profile.model_id,
            "reason_code": resolution.reason.value,
            "readiness_status": resolution.readiness.value,
            "status": "completed",
        }

    def _resolve_specific_profile(
        self,
        organization_id: UUID,
        selection: ModelSelectionRequest,
    ) -> ModelResolution:
        """处理页面指定模型档案的选择。"""

        assert selection.profile_id is not None
        profile = self._repository.get_profile(organization_id, selection.profile_id)
        if profile is None:
            raise LookupError("指定模型档案不存在或无访问权限")
        self._ensure_supports_selection(profile, selection)
        availability = self._to_availability(profile)
        if availability.readiness in {ModelReadiness.DISABLED, ModelReadiness.POLICY_BLOCKED}:
            raise PermissionError(availability.blocked_reason or "指定模型当前不可用")
        return ModelResolution(
            profile=profile,
            reason=ModelResolutionReason.USER_SELECTED,
            readiness=availability.readiness,
            credential_env_name=availability.credential_env_name,
            credential=self._credential_for(profile)
            if availability.readiness is ModelReadiness.READY
            else None,
        )

    def _resolve_free_quota_first(
        self,
        organization_id: UUID,
        selection: ModelSelectionRequest,
    ) -> ModelResolution:
        """在启用且能力匹配的档案中按免费额度优先级选择首项。"""
        candidates = self._repository.list_profiles(organization_id, include_disabled=False)
        eligible_candidates = [
            (profile, availability)
            for profile in candidates
            if profile.cost_tier is ModelCostTier.FREE_QUOTA
            if self._supports_required_capabilities(profile, selection.required_capabilities)
            for availability in (self._to_availability(profile),)
            if availability.readiness is ModelReadiness.READY
        ]
        if not eligible_candidates:
            raise LookupError(
                "没有已配置且可调用的免费模型连接。请在“模型与连接”中申请并保存一个免费额度服务商的 API Key。",
            )

        profile, availability = min(
            eligible_candidates,
            key=lambda item: (
                self._cost_tier_rank(item[0].cost_tier),
                item[0].priority,
                item[0].profile_key,
            ),
        )
        return ModelResolution(
            profile=profile,
            reason=ModelResolutionReason.FREE_QUOTA_PRIORITY,
            readiness=availability.readiness,
            credential_env_name=availability.credential_env_name,
            credential=self._credential_for(profile)
            if availability.readiness is ModelReadiness.READY
            else None,
        )

    def _to_availability(self, profile: ModelProfileRecord) -> ModelProfileAvailability:
        """从启用开关、全局策略和凭证存在性计算一个档案的安全状态。"""

        if not profile.enabled:
            return ModelProfileAvailability(
                profile=profile,
                readiness=ModelReadiness.DISABLED,
                credential_env_name=self._credential_env_name(profile.provider_key),
                blocked_reason="该模型档案已在设置中停用",
            )

        policy_reason = self._policy_block_reason(profile)
        credential_env_name = self._credential_env_name(profile.provider_key)
        if policy_reason is not None:
            return ModelProfileAvailability(
                profile=profile,
                readiness=ModelReadiness.POLICY_BLOCKED,
                credential_env_name=credential_env_name,
                blocked_reason=policy_reason,
            )

        has_stored_credential = self._repository.has_stored_credential(
            profile.organization_id,
            profile.id,
        )
        if has_stored_credential:
            try:
                if not self._repository.read_stored_credential(
                    profile.organization_id,
                    profile.id,
                ):
                    raise ValueError("已保存 API Key 为空")
            except (SQLAlchemyError, ValueError):
                return ModelProfileAvailability(
                    profile=profile,
                    readiness=ModelReadiness.CREDENTIAL_REQUIRED,
                    credential_env_name=None,
                    blocked_reason="无法读取已保存 API Key，请重新填写并保存该连接",
                )

        has_environment_credential = bool(
            credential_env_name and self._environment.get(credential_env_name, "").strip(),
        )
        if not has_stored_credential and not has_environment_credential:
            return ModelProfileAvailability(
                profile=profile,
                readiness=ModelReadiness.CREDENTIAL_REQUIRED,
                credential_env_name=credential_env_name,
                blocked_reason="尚未配置该 Provider 的环境变量",
            )

        return ModelProfileAvailability(
            profile=profile,
            readiness=ModelReadiness.READY,
            credential_env_name=credential_env_name,
            blocked_reason=None,
        )

    def _policy_block_reason(self, profile: ModelProfileRecord) -> str | None:
        """集中处理免费优先策略，不让 Provider Adapter 自行绕过限制。"""

        if profile.cost_tier is ModelCostTier.PAID and not self._settings.allow_paid_profiles:
            return "当前策略禁用了付费模型档案"
        if profile.cost_tier is ModelCostTier.LOCAL and not self._settings.enable_local_ollama_profile:
            return "当前策略保留了本地 Ollama 入口，但尚未启用"
        return None

    def _credential_env_name(self, provider_key: str) -> str | None:
        """查询已登记 Provider 的凭证环境变量名称。"""

        return self._settings.provider_credential_env.get(provider_key)

    def _credential_for(self, profile: ModelProfileRecord) -> str | None:
        """优先使用本机保存的 Key，兼容早期环境变量方式。"""

        if self._repository.has_stored_credential(
            profile.organization_id,
            profile.id,
        ):
            return self._repository.read_stored_credential(
                profile.organization_id,
                profile.id,
            )

        credential_env_name = self._credential_env_name(profile.provider_key)
        if credential_env_name:
            value = self._environment.get(credential_env_name, "").strip()
            return value or None
        return None

    def _ensure_supports_selection(
        self,
        profile: ModelProfileRecord,
        selection: ModelSelectionRequest,
    ) -> None:
        """拒绝让不具备视觉/文本等能力的模型处理错误任务。"""

        if not self._supports_required_capabilities(profile, selection.required_capabilities):
            required = ", ".join(sorted(capability.value for capability in selection.required_capabilities))
            raise ValueError(f"指定模型不满足所需能力：{required}")

    @staticmethod
    def _supports_required_capabilities(
        profile: ModelProfileRecord,
        required_capabilities: frozenset[ModelCapability],
    ) -> bool:
        """判断档案能力集合是否覆盖本轮输入所需能力。"""

        return required_capabilities.issubset(profile.capabilities)

    @staticmethod
    def _cost_tier_rank(cost_tier: ModelCostTier) -> int:
        """定义免费路由排序；本地模型默认排在云端免费额度之后。"""

        return {
            ModelCostTier.FREE_QUOTA: 0,
            ModelCostTier.LOCAL: 1,
            ModelCostTier.PAID: 2,
        }[cost_tier]
