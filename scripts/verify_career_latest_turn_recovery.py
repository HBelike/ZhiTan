"""验证会话刷新后可恢复最近 Agent Turn 的真实 PostgreSQL 查询。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.contracts import ModelSelectionRequest
from src.career_assistant.persistence import (
    AgentTurnStatus,
    CareerConversationRepository,
    CareerDatabase,
)
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
)


def main() -> None:
    """创建两轮真实测试任务，确认查询只返回当前会话最近的一轮。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供 PostgreSQL 连接。")

    database = CareerDatabase(database_url)
    repository = CareerConversationRepository(database)
    conversation_id = None
    try:
        conversation = repository.create_conversation(
            DEFAULT_ORGANIZATION_ID,
            DEFAULT_ACTOR_ID,
            "最近任务恢复验证",
        )
        conversation_id = conversation.id
        first_turn = repository.create_agent_turn(
            uuid4(),
            conversation.id,
            DEFAULT_ACTOR_ID,
            ModelSelectionRequest(),
            frozenset({"text"}),
        )
        repository.mark_agent_turn_running(DEFAULT_ACTOR_ID, first_turn.id)
        repository.finish_agent_turn(
            DEFAULT_ACTOR_ID,
            first_turn.id,
            AgentTurnStatus.SUCCEEDED,
        )
        second_turn = repository.create_agent_turn(
            uuid4(),
            conversation.id,
            DEFAULT_ACTOR_ID,
            ModelSelectionRequest(),
            frozenset({"text"}),
        )

        latest = repository.get_latest_agent_turn(
            DEFAULT_ACTOR_ID,
            conversation.id,
        )
        assert latest is not None
        assert latest.id == second_turn.id
        assert latest.status is AgentTurnStatus.QUEUED
        print("career_latest_turn_recovery_real_database_ok")
    finally:
        if conversation_id is not None:
            deleted = repository.delete_conversation_permanently(
                DEFAULT_ACTOR_ID,
                conversation_id,
            )
            if not deleted:
                raise RuntimeError("最近任务恢复验证会话清理失败，请人工检查数据库。")
        database.close()


if __name__ == "__main__":
    main()
