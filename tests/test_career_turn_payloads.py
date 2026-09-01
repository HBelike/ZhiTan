"""持久化 Turn 完整输入的序列化与恢复测试。"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from src.career_assistant.agent_loop import CareerAgentLoop
from src.career_assistant.attachments import ParsedAttachment
from src.career_assistant.contracts import (
    ActivatedSkill,
    AttachmentKind,
    CareerInboundMessage,
    InterviewEvidence,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.persistence.records import (
    AgentTurnRecord,
    AgentTurnStatus,
    ConversationRecord,
)
from src.career_assistant.privacy import SensitiveDataRedactor


def _turn_record(*, actor_id, conversation_id, turn_id) -> AgentTurnRecord:
    now = datetime.now(UTC)
    return AgentTurnRecord(
        id=turn_id,
        conversation_id=conversation_id,
        actor_id=actor_id,
        requested_selection_mode=ModelSelectionMode.FREE_QUOTA_FIRST,
        requested_model_profile_id=None,
        input_kind_codes=("resume_pdf",),
        status=AgentTurnStatus.RUNNING,
        error_code=None,
        error_message=None,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )


class CareerTurnPayloadTests(unittest.TestCase):
    def test_round_trip_keeps_complete_unredacted_details(self) -> None:
        from src.career_assistant.turn_payloads import (
            prepare_turn_payload,
            restore_turn_input,
        )

        actor_id = uuid4()
        conversation_id = uuid4()
        turn_id = uuid4()
        source_text = "张三在字节跳动负责推荐系统，手机号 13800138000"
        inbound = CareerInboundMessage(
            turn_id=turn_id,
            conversation_id=conversation_id,
            actor_id=actor_id,
            text="请帮我分析简历",
            effective_text="请完整分析我的简历",
            interview_evidence=(
                InterviewEvidence(
                    experience_id=uuid4(),
                    citation="面经 1",
                    content="一面重点追问推荐系统召回链路",
                ),
            ),
            activated_skills=(
                ActivatedSkill(
                    skill_id="asu",
                    name="经历酥化",
                    description="重组真实经历",
                    instructions="保留事实并突出岗位匹配",
                ),
            ),
            candidate_profile_context="候选人完整基准简历",
            target_role_context="后端开发岗位完整要求",
            context_binding_version=3,
        )
        parsed = ParsedAttachment(
            kind=AttachmentKind.RESUME_PDF,
            media_type="application/pdf",
            page_count=2,
            image_width=None,
            image_height=None,
            extracted_text=source_text,
            requires_vision_model=False,
            image_bytes=None,
        )
        turn = _turn_record(
            actor_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )

        payload = prepare_turn_payload(inbound, (parsed,))
        restored = restore_turn_input(turn, payload)

        self.assertEqual(restored.inbound_message.text, "请帮我分析简历")
        self.assertEqual(restored.inbound_message.effective_text, "请完整分析我的简历")
        self.assertEqual(restored.parsed_attachments[0].extracted_text, source_text)
        self.assertIn("13800138000", restored.parsed_attachments[0].extracted_text)
        self.assertEqual(
            restored.inbound_message.prepared_attachment_kinds,
            (AttachmentKind.RESUME_PDF,),
        )
        self.assertEqual(restored.inbound_message.interview_evidence[0].citation, "面经 1")
        self.assertEqual(restored.inbound_message.activated_skills[0].skill_id, "asu")
        self.assertEqual(restored.inbound_message.context_binding_version, 3)

    def test_attachment_only_restored_message_passes_validation(self) -> None:
        from src.career_assistant.turn_payloads import (
            prepare_turn_payload,
            restore_turn_input,
        )

        actor_id = uuid4()
        conversation_id = uuid4()
        turn_id = uuid4()
        inbound = CareerInboundMessage(
            turn_id=turn_id,
            conversation_id=conversation_id,
            actor_id=actor_id,
            text="",
        )
        parsed = ParsedAttachment(
            kind=AttachmentKind.JOB_DESCRIPTION_FILE,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            page_count=None,
            image_width=None,
            image_height=None,
            extracted_text="岗位要求：Python、PostgreSQL、asyncio",
            requires_vision_model=False,
            image_bytes=None,
        )
        turn = _turn_record(
            actor_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )

        restored = restore_turn_input(turn, prepare_turn_payload(inbound, (parsed,)))

        restored.inbound_message.validate()


class FakeAgentLoopRepository:
    def __init__(self, conversation: ConversationRecord, turn: AgentTurnRecord) -> None:
        self.conversation = conversation
        self.turn = turn
        self.created_turns = 0

    def get_conversation(self, actor_id, conversation_id):
        if actor_id == self.conversation.actor_id and conversation_id == self.conversation.id:
            return self.conversation
        return None

    def get_agent_turn(self, actor_id, turn_id):
        if actor_id == self.turn.actor_id and turn_id == self.turn.id:
            return self.turn
        return None

    def create_agent_turn(self, *args, **kwargs):
        self.created_turns += 1
        raise AssertionError("activate_turn 不应创建新 Turn")


class CareerAgentLoopActivationTests(unittest.TestCase):
    def test_activate_turn_reuses_worker_claimed_running_turn(self) -> None:
        actor_id = uuid4()
        conversation_id = uuid4()
        turn_id = uuid4()
        now = datetime.now(UTC)
        conversation = ConversationRecord(
            id=conversation_id,
            organization_id=uuid4(),
            actor_id=actor_id,
            title="后端求职",
            status="active",
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        turn = _turn_record(
            actor_id=actor_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        repository = FakeAgentLoopRepository(conversation, turn)
        agent_loop = CareerAgentLoop(repository)

        active = agent_loop.activate_turn(actor_id, turn_id)

        self.assertEqual(active.turn.id, turn_id)
        self.assertEqual(active.conversation.id, conversation_id)
        self.assertEqual(repository.created_turns, 0)


class CareerPreparedIntakeGraphTests(unittest.TestCase):
    def test_parse_material_reuses_prepared_attachment_without_file_parser(self) -> None:
        from src.career_assistant.intake_graph import CareerIntakeGraph

        parsed = ParsedAttachment(
            kind=AttachmentKind.RESUME_PDF,
            media_type="application/pdf",
            page_count=1,
            image_width=None,
            image_height=None,
            extracted_text="完整简历内容",
            requires_vision_model=False,
            image_bytes=None,
        )
        inbound = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            actor_id=uuid4(),
            text="",
            prepared_attachment_kinds=(AttachmentKind.RESUME_PDF,),
        )
        graph = CareerIntakeGraph(
            agent_loop=object(),
            redactor=SensitiveDataRedactor(enabled=False),
            attachment_parser=None,
        )

        update = graph._parse_material(
            {
                "inbound_message": inbound,
                "parsed_attachments": (parsed,),
                "completed_steps": (),
            },
        )

        self.assertEqual(update["parsed_attachments"], (parsed,))

    def test_disabled_redactor_keeps_phone_number_for_model_context(self) -> None:
        from src.career_assistant.intake_graph import CareerIntakeGraph, TurnRuntimeContext

        inbound = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            actor_id=uuid4(),
            text="手机号 13800138000，负责支付系统",
        )
        graph = CareerIntakeGraph(
            agent_loop=object(),
            redactor=SensitiveDataRedactor(enabled=False),
        )

        update = graph._redact_sensitive_data(
            {
                "inbound_message": inbound,
                "runtime_context": TurnRuntimeContext(
                    original_text=inbound.text,
                    model_text=inbound.text,
                    has_job_url=False,
                    attachment_kind_codes=(),
                ),
                "parsed_attachments": (),
                "job_source_status": "not_provided",
                "completed_steps": (),
            },
        )

        self.assertIn("13800138000", update["redacted_user_text"])
        self.assertIn("13800138000", update["model_context"].redacted_user_text)


if __name__ == "__main__":
    unittest.main()
