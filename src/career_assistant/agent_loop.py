"""Nanobot 风格的求职助手 AgentLoop 生命周期入口。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.career_assistant.contracts import CareerInboundMessage
from src.career_assistant.persistence import (
    AgentTurnRecord,
    AgentTurnStatus,
    CareerConversationRepository,
    ConversationRecord,
)


@dataclass(frozen=True)
class ActiveAgentTurn:
    """AgentLoop 成功启动一轮执行后交给 AgentRunner 的上下文。"""

    conversation: ConversationRecord
    turn: AgentTurnRecord


class CareerAgentLoop:
    """协调会话边界和 Turn 状态，不承担模型调用或附件解析。

    Web 层先通过 ``open_conversation`` 创建新会话；之后每次用户提问都调用
    ``start_turn``。未来的 LangGraph AgentRunner 会在返回的 ActiveAgentTurn 上继续执行
    解析、脱敏、模型路由和回复生成节点。
    """

    def __init__(self, repository: CareerConversationRepository) -> None:
        """注入仓储接口，保持 AgentLoop 不感知 SQLAlchemy 与具体数据库连接。"""

        self._repository = repository

    @property
    def repository(self) -> CareerConversationRepository:
        """暴露受控仓储接口，供同一运行时内的 Graph 持久化已脱敏结果。"""

        return self._repository

    def open_conversation(
        self,
        organization_id: UUID,
        actor_id: UUID,
        title: str,
    ) -> ConversationRecord:
        """创建新会话；将来登录完成后由身份上下文提供组织与用户标识。"""

        return self._repository.create_conversation(organization_id, actor_id, title)

    def resume_conversation(
        self,
        actor_id: UUID,
        conversation_id: UUID,
    ) -> ConversationRecord:
        """恢复用户本人可访问的会话，不允许跨用户恢复。"""

        conversation = self._repository.get_conversation(actor_id, conversation_id)
        if conversation is None:
            raise LookupError("会话不存在、已删除或无访问权限")
        return conversation

    def start_turn(self, inbound_message: CareerInboundMessage) -> ActiveAgentTurn:
        """校验输入、确认会话所有权并原子启动 Turn。

        本阶段故意不将 ``inbound_message.text`` 写进 messages 表；只有后续 LangGraph
        脱敏节点产出的文本才能调用仓储写入历史。
        """

        inbound_message.validate()
        conversation = self.resume_conversation(
            inbound_message.actor_id,
            inbound_message.conversation_id,
        )
        created_turn = self._repository.create_agent_turn(
            inbound_message.turn_id,
            inbound_message.conversation_id,
            inbound_message.actor_id,
            inbound_message.model_selection,
            self._collect_input_kind_codes(inbound_message),
        )
        running_turn = self._repository.mark_agent_turn_running(
            inbound_message.actor_id,
            created_turn.id,
        )
        return ActiveAgentTurn(conversation=conversation, turn=running_turn)

    def activate_turn(self, actor_id: UUID, turn_id: UUID) -> ActiveAgentTurn:
        """装载已经由 PostgreSQL Worker 领取并切换到 running 的 Turn。"""

        turn = self._repository.get_agent_turn(actor_id, turn_id)
        if turn is None:
            raise LookupError("Agent Turn 不存在或无访问权限")
        if turn.status is not AgentTurnStatus.RUNNING:
            raise LookupError("Agent Turn 尚未由 Worker 领取或已经结束")
        conversation = self.resume_conversation(actor_id, turn.conversation_id)
        return ActiveAgentTurn(conversation=conversation, turn=turn)

    def mark_turn_succeeded(self, actor_id: UUID, turn_id: UUID) -> AgentTurnRecord:
        """由 AgentRunner 在完整工作流成功后调用，收口 Turn 状态。"""

        return self._repository.finish_agent_turn(
            actor_id,
            turn_id,
            AgentTurnStatus.SUCCEEDED,
        )

    def mark_turn_failed(
        self,
        actor_id: UUID,
        turn_id: UUID,
        error_code: str,
        public_error_message: str | None = None,
    ) -> AgentTurnRecord:
        """由 AgentRunner 在可预期失败时调用，只记录已整理的安全错误信息。"""

        return self._repository.finish_agent_turn(
            actor_id,
            turn_id,
            AgentTurnStatus.FAILED,
            error_code=error_code,
            error_message=public_error_message,
        )

    @staticmethod
    def _collect_input_kind_codes(inbound_message: CareerInboundMessage) -> frozenset[str]:
        """将当前请求转换为不含内容本身的输入类型标签。"""

        kinds: set[str] = set()
        if inbound_message.text.strip():
            kinds.add("text")
        if (inbound_message.job_url or "").strip():
            kinds.add("job_url")
        kinds.update(attachment.kind.value for attachment in inbound_message.attachments)
        kinds.update(kind.value for kind in inbound_message.prepared_attachment_kinds)
        return frozenset(kinds)
