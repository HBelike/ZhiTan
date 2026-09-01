"""对求职助手 AgentLoop 的 PostgreSQL 生命周期做真实验证。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.agent_loop import CareerAgentLoop
from src.career_assistant.contracts import CareerInboundMessage
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
    """验证会话创建、Turn 启动、收口、读取和 finally 清理。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    database = CareerDatabase(database_url)
    repository = CareerConversationRepository(database)
    agent_loop = CareerAgentLoop(repository)
    conversation_id = None

    try:
        conversation = agent_loop.open_conversation(
            DEFAULT_ORGANIZATION_ID,
            DEFAULT_ACTOR_ID,
            "AgentLoop 生命周期验证",
        )
        conversation_id = conversation.id
        active_turn = agent_loop.start_turn(
            CareerInboundMessage(
                turn_id=uuid4(),
                conversation_id=conversation.id,
                actor_id=DEFAULT_ACTOR_ID,
                text="请帮我分析职位匹配方向。",
                job_url="https://example.com/job-description",
            ),
        )
        assert active_turn.turn.status is AgentTurnStatus.RUNNING
        assert active_turn.turn.input_kind_codes == ("job_url", "text")

        succeeded_turn = agent_loop.mark_turn_succeeded(
            DEFAULT_ACTOR_ID,
            active_turn.turn.id,
        )
        saved_turn = repository.get_agent_turn(DEFAULT_ACTOR_ID, active_turn.turn.id)
        assert succeeded_turn.status is AgentTurnStatus.SUCCEEDED
        assert saved_turn is not None
        assert saved_turn.completed_at is not None
        print("career_agent_loop_real_database_ok")
    finally:
        if conversation_id is not None:
            deleted = repository.delete_conversation_permanently(
                DEFAULT_ACTOR_ID,
                conversation_id,
            )
            if not deleted:
                raise RuntimeError("AgentLoop 验证会话清理失败，请人工检查数据库")
        database.close()


if __name__ == "__main__":
    main()
