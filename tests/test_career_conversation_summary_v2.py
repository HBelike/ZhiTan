"""滚动摘要 V2 领域测试。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.career_assistant.conversation_memory import (
    SUMMARY_SCHEMA_VERSION,
    group_complete_turns,
    render_summary_data,
    validate_summary,
)
from src.career_assistant.persistence.records import MessageRecord, MessageRole


def valid_summary_payload() -> dict[str, object]:
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "current_tasks": ["修改项目经历"],
        "decisions": [],
        "open_loops": ["补充性能数据"],
        "user_corrections": [],
        "temporary_user_context": [],
        "assistant_commitments": [],
        "companies": [],
        "roles": ["Java 后端"],
    }


def message(turn_id, role, content, minute) -> MessageRecord:
    return MessageRecord(
        id=uuid4(),
        conversation_id=CONVERSATION_ID,
        turn_id=turn_id,
        role=role,
        content_text=content,
        is_redacted=True,
        created_at=NOW + timedelta(minutes=minute),
    )


NOW = datetime.now(UTC)
CONVERSATION_ID = uuid4()


def test_summary_rejects_unknown_field() -> None:
    payload = valid_summary_payload()
    payload["user_profile"] = ["不允许"]

    with pytest.raises(ValueError, match="字段"):
        validate_summary(payload)


def test_summary_rejects_missing_field_and_old_schema() -> None:
    missing = valid_summary_payload()
    missing.pop("roles")
    with pytest.raises(ValueError, match="字段"):
        validate_summary(missing)

    old = valid_summary_payload()
    old["schema_version"] = "v1"
    with pytest.raises(ValueError, match="Schema"):
        validate_summary(old)


def test_group_complete_turns_skips_orphans_and_keeps_complete_pairs() -> None:
    succeeded_turn = uuid4()
    orphan_turn = uuid4()
    user = message(succeeded_turn, MessageRole.USER, "请修改经历", 1)
    assistant = message(succeeded_turn, MessageRole.ASSISTANT, "好的", 2)
    orphan = message(orphan_turn, MessageRole.USER, "没有回答", 3)
    turns = group_complete_turns([user, assistant, orphan])

    assert [item.turn_id for item in turns] == [succeeded_turn]
    assert turns[0].covered_through_message_id == assistant.id
    assert turns[0].estimated_tokens > 0


def test_summary_render_marks_data_as_non_authoritative() -> None:
    rendered = render_summary_data(validate_summary(valid_summary_payload()))

    assert 'instruction_authority="none"' in rendered
    assert "权威低于当前用户输入" in rendered
