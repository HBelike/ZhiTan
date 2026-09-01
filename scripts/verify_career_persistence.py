"""对求职助手 PostgreSQL 仓储做一次真实的写入、读取和清理验证。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.persistence import (
    CareerConversationRepository,
    CareerDatabase,
    MessageRole,
)
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
)


def main() -> None:
    """执行临时会话的完整生命周期，并在 finally 中清除验证数据。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    database = CareerDatabase(database_url)
    repository = CareerConversationRepository(database)
    conversation_id: UUID | None = None

    try:
        conversation = repository.create_conversation(
            DEFAULT_ORGANIZATION_ID,
            DEFAULT_ACTOR_ID,
            "仓储验证：求职助手会话",
        )
        conversation_id = conversation.id

        user_message = repository.append_redacted_message(
            conversation.id,
            MessageRole.USER,
            "请基于已脱敏信息分析岗位匹配度。",
        )
        assistant_message = repository.append_redacted_message(
            conversation.id,
            MessageRole.ASSISTANT,
            "已记录分析方向，后续会结合职位描述给出建议。",
        )
        summary_v1 = repository.upsert_redacted_summary(
            conversation.id,
            "用户希望分析简历与职位的匹配度。",
        )
        summary_v2 = repository.upsert_redacted_summary(
            conversation.id,
            "用户希望获得匹配度、优势和待补足能力的建议。",
        )

        saved_conversation = repository.get_conversation(DEFAULT_ACTOR_ID, conversation.id)
        saved_messages = repository.list_messages(DEFAULT_ACTOR_ID, conversation.id)
        saved_conversations = repository.list_conversations(DEFAULT_ACTOR_ID)

        assert saved_conversation is not None
        assert [message.id for message in saved_messages] == [
            user_message.id,
            assistant_message.id,
        ]
        assert all(message.is_redacted for message in saved_messages)
        assert summary_v1.summary_version == 1
        assert summary_v2.summary_version == 2
        assert any(item.id == conversation.id for item in saved_conversations)
        print("career_persistence_real_database_ok")
    finally:
        if conversation_id is not None:
            deleted = repository.delete_conversation_permanently(
                DEFAULT_ACTOR_ID,
                conversation_id,
            )
            if not deleted:
                raise RuntimeError("验证会话清理失败，请人工检查数据库")
        database.close()


if __name__ == "__main__":
    main()
