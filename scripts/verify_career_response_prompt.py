"""验证求职助手的普通对话、历史消息与 PDF 附件 Prompt 结构。"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.agent_loop import ActiveAgentTurn
from src.career_assistant.contracts import AttachmentKind, ModelCapability, ModelSelectionMode
from src.career_assistant.intake_graph import ModelTurnContext
from src.career_assistant.persistence import AgentTurnRecord, AgentTurnStatus, ConversationRecord, MessageRecord, MessageRole
from src.career_assistant.response_runner import CareerResponseRunner


class _Repository:
    """提供最小历史消息读取能力，避免验证触发真实数据库或模型调用。"""

    def __init__(self, messages: list[MessageRecord]) -> None:
        self._messages = messages

    def list_messages(self, actor_id, conversation_id, *, limit: int):
        """返回固定测试历史；参数用于保持与真实仓储接口一致。"""

        del actor_id, conversation_id, limit
        return self._messages


class _AgentLoop:
    """为 Prompt 构造器提供受控的仓储入口。"""

    def __init__(self, repository: _Repository) -> None:
        self.repository = repository


def _active_turn() -> ActiveAgentTurn:
    """构造无敏感数据的当前 Turn。"""

    now = datetime.now(UTC)
    conversation = ConversationRecord(
        id=uuid4(),
        organization_id=uuid4(),
        actor_id=uuid4(),
        title="Prompt 验证",
        status="active",
        created_at=now,
        updated_at=now,
        archived_at=None,
    )
    turn = AgentTurnRecord(
        id=uuid4(),
        conversation_id=conversation.id,
        actor_id=conversation.actor_id,
        requested_selection_mode=ModelSelectionMode.FREE_QUOTA_FIRST,
        requested_model_profile_id=None,
        input_kind_codes=("text",),
        status=AgentTurnStatus.RUNNING,
        error_code=None,
        error_message=None,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    return ActiveAgentTurn(conversation=conversation, turn=turn)


def _message(active_turn: ActiveAgentTurn) -> MessageRecord:
    """构造上一轮已经脱敏的历史消息。"""

    return MessageRecord(
        id=uuid4(),
        conversation_id=active_turn.conversation.id,
        turn_id=uuid4(),
        role=MessageRole.ASSISTANT,
        content_text="上一轮已讨论简历中的项目经历。",
        is_redacted=True,
        created_at=datetime.now(UTC),
    )


def main() -> None:
    """确认普通问候不会被要求上传简历，PDF 也会被明确标记为已接收。"""

    active_turn = _active_turn()
    runner = CareerResponseRunner(
        _AgentLoop(_Repository([_message(active_turn)])),
        model_gateway=object(),
    )
    assert runner._model_resolution_label(
        SimpleNamespace(
            profile=SimpleNamespace(
                display_name="DeepSeek 模型连接",
                model_id="deepseek-v4-pro",
            )
        )
    ) == "DeepSeek 模型连接 · deepseek-v4-pro"
    assert runner._model_resolution_label(
        SimpleNamespace(
            profile=SimpleNamespace(
                display_name="deepseek-v4-flash",
                model_id="deepseek-v4-flash",
            )
        )
    ) == "deepseek-v4-flash"
    normal_messages = runner._build_prompt(
        active_turn,
        ModelTurnContext(
            redacted_user_text="你好，我想了解产品经理的职业发展。",
            redacted_material_text="",
            redacted_job_text="",
            required_capabilities=frozenset({ModelCapability.TEXT}),
            contains_image_material=False,
            vision_images=(),
            received_attachment_kinds=(),
            pdf_without_extractable_text_count=0,
            redacted_resume_outline="",
        ),
    )
    assert normal_messages[-1].role == "user"
    assert "你好，我想了解产品经理的职业发展。" in str(normal_messages[-1].content)
    assert "没有资料时也要正常回答通用求职问题" in str(normal_messages[0].content)
    assert "请用户先建立上下文" not in str(normal_messages[0].content)
    assert any(message.content == "上一轮已讨论简历中的项目经历。" for message in normal_messages)

    pdf_messages = runner._build_prompt(
        active_turn,
        ModelTurnContext(
            redacted_user_text="请帮我分析这份简历。",
            redacted_material_text="",
            redacted_job_text="",
            required_capabilities=frozenset({ModelCapability.TEXT}),
            contains_image_material=False,
            vision_images=(),
            received_attachment_kinds=(AttachmentKind.RESUME_PDF,),
            pdf_without_extractable_text_count=1,
            redacted_resume_outline="",
        ),
    )
    assert "已收到 PDF 附件" in str(pdf_messages[-1].content)
    assert "不要说没有收到附件" in str(pdf_messages[-1].content)

    normalized_resume_messages = runner._build_prompt(
        active_turn,
        ModelTurnContext(
            redacted_user_text="请分析这份简历。",
            redacted_material_text="",
            redacted_job_text="",
            required_capabilities=frozenset({ModelCapability.TEXT}),
            contains_image_material=False,
            vision_images=(),
            received_attachment_kinds=(AttachmentKind.RESUME_PDF,),
            pdf_without_extractable_text_count=0,
            redacted_resume_outline="【项目经历】\n智能客服系统：负责 RAG 检索链路。",
        ),
    )
    assert "已确认的基准简历上下文" in str(normalized_resume_messages[-1].content)
    assert "RAG 检索链路" in str(normalized_resume_messages[-1].content)

    degraded_document_messages = runner._build_prompt(
        active_turn,
        ModelTurnContext(
            redacted_user_text="请分析这份简历。",
            redacted_material_text="",
            redacted_job_text="",
            required_capabilities=frozenset({ModelCapability.TEXT}),
            contains_image_material=False,
            vision_images=(),
            received_attachment_kinds=(AttachmentKind.RESUME_PDF,),
            pdf_without_extractable_text_count=1,
            document_processing_notices=(
                "无法连接文档解析服务，请确认 Docling 服务已启动后重试",
            ),
        ),
    )
    assert "附件已经收到" in str(degraded_document_messages[-1].content)
    assert "无法连接文档解析服务" in str(degraded_document_messages[-1].content)
    print("career_response_prompt_structure_ok")


if __name__ == "__main__":
    main()
