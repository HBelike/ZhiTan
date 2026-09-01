"""求职助手 Prompt 的保守 Token 核算与确定性裁剪。"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Protocol

from src.career_assistant.model_clients import ChatMessage
from src.career_assistant.persistence.model_profile_repository import ModelContextPolicy


TOKEN_SAFETY_FACTOR = 1.15
HARD_LIMIT_PERCENT = 95
PROVIDER_WRAPPER_TOKENS = 24
MINIMUM_VIABLE_OUTPUT_TOKENS = 1_024


class ContextUsageState(StrEnum):
    """对普通用户公开的近似上下文状态。"""

    NORMAL = "normal"
    WARNING = "warning"
    COMPACTING = "compacting"
    DEGRADED = "degraded"
    HARD_BLOCKED = "hard_blocked"


@dataclass(frozen=True)
class PromptComponent:
    """可以整体保留或整体裁剪的一组完整消息。"""

    key: str
    messages: tuple[ChatMessage, ...]
    is_required: bool
    drop_rank: int | None
    extra_token_estimate: int = 0

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Prompt 组件 key 不能为空")
        if self.extra_token_estimate < 0:
            raise ValueError("额外 Token 估算不能为负数")
        if self.is_required and self.drop_rank is not None:
            raise ValueError("必需组件不能设置裁剪顺序")
        if not self.is_required and self.drop_rank is None:
            raise ValueError("可选组件必须设置裁剪顺序")

    @classmethod
    def pinned(
        cls,
        key: str,
        messages: tuple[ChatMessage, ...],
        *,
        extra_token_estimate: int = 0,
    ) -> "PromptComponent":
        return cls(key, messages, True, None, extra_token_estimate)

    @classmethod
    def optional(
        cls,
        key: str,
        messages: tuple[ChatMessage, ...],
        *,
        drop_rank: int,
        extra_token_estimate: int = 0,
    ) -> "PromptComponent":
        return cls(key, messages, False, drop_rank, extra_token_estimate)


@dataclass(frozen=True)
class ContextUsageSnapshot:
    estimated_input_tokens: int
    reserved_output_tokens: int
    projected_total_tokens: int
    context_window_tokens: int
    used_percent: int
    remaining_percent: int
    state: ContextUsageState
    approximate: bool = True


@dataclass(frozen=True)
class ContextFitResult:
    components: tuple[PromptComponent, ...]
    usage: ContextUsageSnapshot
    dropped_component_keys: tuple[str, ...]


class ContextHardLimitError(RuntimeError):
    """必需上下文达到 95% 时拒绝继续调用模型。"""

    def __init__(self, usage: ContextUsageSnapshot) -> None:
        super().__init__("当前必要上下文已达到模型安全上限，请精简本轮材料后重试")
        self.usage = replace(usage, state=ContextUsageState.HARD_BLOCKED)


def estimate_text_tokens(text: str) -> int:
    """用中文字符数和英文四字符近似给出可重复的保守估算。"""

    chinese = sum(1 for character in text if "\u4e00" <= character <= "\u9fff")
    other = len(text) - chinese
    return chinese + math.ceil(other / 4)


class TokenEstimator(Protocol):
    def estimate_message(self, message: ChatMessage) -> int:
        """估算一条完整 Provider 消息。"""


class ConservativeTokenEstimator:
    """不依赖特定 tokenizer 的安全估算器。"""

    def estimate_message(self, message: ChatMessage) -> int:
        payload = {
            "content": message.content,
            "tool_call_id": message.tool_call_id,
            "tool_calls": [asdict(item) for item in message.tool_calls],
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return 8 + estimate_text_tokens(message.role) + estimate_text_tokens(serialized)


class ContextBudgetService:
    """统一测量 Prompt，并按组件边界执行最终降级裁剪。"""

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self._estimator = estimator or ConservativeTokenEstimator()

    def measure(
        self,
        components: tuple[PromptComponent, ...],
        policy: ModelContextPolicy,
        *,
        state: ContextUsageState | None = None,
    ) -> ContextUsageSnapshot:
        policy.validate()
        estimated_input = self._estimated_input_tokens(components)
        projected = estimated_input + policy.reserved_output_tokens
        used = math.ceil(projected / policy.context_window_tokens * 100)
        resolved_state = state or self._state_for(used, policy)
        return ContextUsageSnapshot(
            estimated_input_tokens=estimated_input,
            reserved_output_tokens=policy.reserved_output_tokens,
            projected_total_tokens=projected,
            context_window_tokens=policy.context_window_tokens,
            used_percent=used,
            remaining_percent=max(0, 100 - used),
            state=resolved_state,
        )

    def _estimated_input_tokens(
        self,
        components: tuple[PromptComponent, ...],
    ) -> int:
        """使用同一保守公式估算实际输入，供占用与动态输出额度复用。"""

        raw_input = PROVIDER_WRAPPER_TOKENS + sum(
            component.extra_token_estimate for component in components
        ) + sum(
            self._estimator.estimate_message(message)
            for component in components
            for message in component.messages
        )
        return math.ceil(raw_input * TOKEN_SAFETY_FACTOR)

    def runtime_policy(
        self,
        components: tuple[PromptComponent, ...],
        policy: ModelContextPolicy,
        *,
        system_max_output_tokens: int,
    ) -> ModelContextPolicy:
        """按模型能力、系统上限和 95% 内剩余空间生成本轮临时策略。"""

        policy.validate()
        if (
            isinstance(system_max_output_tokens, bool)
            or not isinstance(system_max_output_tokens, int)
            or system_max_output_tokens <= 0
        ):
            raise ValueError("系统最大输出必须为正整数")
        safe_total = math.floor(
            policy.context_window_tokens * HARD_LIMIT_PERCENT / 100,
        )
        available_output = max(
            1,
            safe_total - self._estimated_input_tokens(components),
        )
        return replace(
            policy,
            reserved_output_tokens=min(
                policy.reserved_output_tokens,
                system_max_output_tokens,
                available_output,
            ),
        )

    def fit(
        self,
        components: tuple[PromptComponent, ...],
        policy: ModelContextPolicy,
        *,
        target_percent: int | None = None,
    ) -> ContextFitResult:
        if target_percent is not None and not 1 <= target_percent < HARD_LIMIT_PERCENT:
            raise ValueError("目标比例必须在 1 到 94 之间")
        retained = list(components)
        dropped: list[str] = []
        desired_percent = target_percent or (HARD_LIMIT_PERCENT - 1)
        optional = sorted(
            (item for item in retained if not item.is_required),
            key=lambda item: (
                item.drop_rank if item.drop_rank is not None else 10_000,
                item.key,
            ),
        )
        for component in optional:
            if self.measure(tuple(retained), policy).used_percent <= desired_percent:
                break
            retained.remove(component)
            dropped.append(component.key)

        usage = self.measure(tuple(retained), policy)
        if usage.used_percent >= HARD_LIMIT_PERCENT:
            raise ContextHardLimitError(usage)
        if usage.used_percent > desired_percent:
            usage = replace(usage, state=ContextUsageState.DEGRADED)
        return ContextFitResult(tuple(retained), usage, tuple(dropped))

    @staticmethod
    def _state_for(used_percent: int, policy: ModelContextPolicy) -> ContextUsageState:
        if used_percent >= HARD_LIMIT_PERCENT:
            return ContextUsageState.HARD_BLOCKED
        if used_percent >= policy.compression_trigger_percent:
            return ContextUsageState.COMPACTING
        if used_percent >= policy.compression_target_percent:
            return ContextUsageState.WARNING
        return ContextUsageState.NORMAL
