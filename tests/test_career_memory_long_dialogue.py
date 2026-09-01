"""30 轮长对话与跨会话长期记忆边界验收。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.career_assistant.career_memory import CareerMemoryService
from src.career_assistant.context_budget import ContextBudgetService, PromptComponent
from src.career_assistant.conversation_memory import (
    ConversationSummary,
    group_complete_turns,
    render_summary_data,
    validate_summary,
)
from src.career_assistant.model_clients import ChatMessage
from src.career_assistant.persistence.model_profile_repository import ModelContextPolicy
from src.career_assistant.persistence.records import (
    CareerMemoryItemRecord,
    MessageRecord,
    MessageRole,
)


def _message(
    conversation_id: UUID,
    turn_id: UUID,
    role: MessageRole,
    text: str,
    offset: int,
) -> MessageRecord:
    return MessageRecord(
        id=uuid4(),
        conversation_id=conversation_id,
        turn_id=turn_id,
        role=role,
        content_text=text,
        is_redacted=False,
        created_at=datetime.now(UTC) + timedelta(seconds=offset),
    )


def _memory(
    organization_id: UUID,
    actor_id: UUID,
    career_space_id: UUID,
    memory_type: str,
    text: str,
    *,
    status: str = "active",
) -> CareerMemoryItemRecord:
    now = datetime.now(UTC)
    return CareerMemoryItemRecord(
        id=uuid4(),
        organization_id=organization_id,
        actor_id=actor_id,
        career_space_id=career_space_id if memory_type == "job_intention" else None,
        memory_type=memory_type,
        normalized_value={"statement": text},
        display_text=text,
        source_kind="explicit_user_correction",
        status=status,
        valid_from=now,
        created_at=now,
        updated_at=now,
    )


class ScopedMemoryRepository:
    def __init__(self, items: tuple[CareerMemoryItemRecord, ...]) -> None:
        self.items = items

    def list_active_for_prompt(self, organization_id, actor_id, career_space_id, **kwargs):
        allowed_types = {item.value for item in kwargs["memory_types"]}
        return tuple(
            item
            for item in self.items
            if item.organization_id == organization_id
            and item.actor_id == actor_id
            and item.status == "active"
            and item.memory_type in allowed_types
            and (
                item.memory_type != "job_intention"
                or item.career_space_id == career_space_id
            )
        )[: kwargs["limit"]]


def test_thirty_turn_dialogue_retains_correction_open_loop_and_intention() -> None:
    conversation_id = uuid4()
    messages: list[MessageRecord] = []
    for number in range(1, 31):
        turn_id = uuid4()
        user_text = {
            1: "目标是 Java 后端，地点上海",
            4: "后续提醒我补充支付项目的性能数据",
            16: "纠正：目标改为 AI 应用工程，不再投 Java 后端",
        }.get(number, f"第 {number} 轮普通求职讨论")
        messages.extend(
            (
                _message(conversation_id, turn_id, MessageRole.USER, user_text, number * 2),
                _message(conversation_id, turn_id, MessageRole.ASSISTANT, "已处理本轮问题", number * 2 + 1),
            )
        )

    assert len(group_complete_turns(messages)) == 30

    summary = validate_summary(
        {
            "schema_version": "career-conversation-summary-v2",
            "current_tasks": ["继续准备 AI 应用工程岗位"],
            "decisions": ["不再投递 Java 后端"],
            "open_loops": ["补充支付项目的性能数据"],
            "user_corrections": ["目标岗位已改为 AI 应用工程"],
            "temporary_user_context": [],
            "assistant_commitments": ["下一轮继续核对量化结果"],
            "companies": [],
            "roles": ["AI 应用工程"],
        }
    )
    rendered_summary = render_summary_data(summary)
    assert "补充支付项目的性能数据" in rendered_summary
    assert "目标岗位已改为 AI 应用工程" in rendered_summary

    organization_id, actor_id, space_id = uuid4(), uuid4(), uuid4()
    current_intention = _memory(
        organization_id, actor_id, space_id, "job_intention", "目标是上海 AI 应用工程岗位"
    )
    superseded = _memory(
        organization_id,
        actor_id,
        space_id,
        "job_intention",
        "目标是上海 Java 后端岗位",
        status="superseded",
    )
    retrieved = CareerMemoryService(
        ScopedMemoryRepository((superseded, current_intention))
    ).retrieve_for_prompt(
        organization_id,
        actor_id,
        space_id,
        "继续准备目标岗位",
        candidate_profile_id=None,
        candidate_profile_version=None,
    )
    assert current_intention in retrieved.items
    assert superseded not in retrieved.items

    policy = ModelContextPolicy(
        context_window_tokens=16_384,
        reserved_output_tokens=2_048,
        compression_trigger_percent=80,
        compression_target_percent=60,
        context_window_source="admin",
    )
    fitted = ContextBudgetService().fit(
        (
            PromptComponent.pinned(
                "system",
                (ChatMessage("system", "求职助手系统规则"),),
            ),
            PromptComponent.optional(
                "summary",
                (ChatMessage("system", rendered_summary),),
                drop_rank=10,
            ),
            PromptComponent.pinned(
                "current",
                (ChatMessage("user", "请继续准备面试"),),
            ),
        ),
        policy,
    )
    assert fitted.usage.used_percent < 95


def test_new_conversation_inherits_only_active_whitelisted_facts() -> None:
    organization_id, actor_id, space_id = uuid4(), uuid4(), uuid4()
    active_items = (
        _memory(organization_id, actor_id, space_id, "job_intention", "目标 AI 应用工程"),
        _memory(organization_id, actor_id, space_id, "education", "已确认学历信息"),
    )
    retrieved = CareerMemoryService(ScopedMemoryRepository(active_items)).retrieve_for_prompt(
        organization_id,
        actor_id,
        space_id,
        "根据我的学历准备面试",
        candidate_profile_id=None,
        candidate_profile_version=None,
    )

    new_conversation_summary = ConversationSummary.empty()
    assert {item.memory_type for item in retrieved.items} == {"job_intention", "education"}
    assert new_conversation_summary.open_loops == ()
    assert new_conversation_summary.assistant_commitments == ()
    assert "下一轮继续核对量化结果" not in retrieved.rendered_data
