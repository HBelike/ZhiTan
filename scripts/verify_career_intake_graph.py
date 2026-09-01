"""验证 LangGraph 输入处理图的真实 PostgreSQL 行为。"""

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

from src.career_assistant import CareerAgentLoop, CareerIntakeGraph
from src.career_assistant.contracts import AgentStepName, CareerInboundMessage
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
    """验证 Graph 顺序、手机号和邮箱脱敏、历史写入与失败前的运行状态。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    database = CareerDatabase(database_url)
    repository = CareerConversationRepository(database)
    agent_loop = CareerAgentLoop(repository)
    intake_graph = CareerIntakeGraph(agent_loop)
    conversation_id = None

    try:
        conversation = agent_loop.open_conversation(
            DEFAULT_ORGANIZATION_ID,
            DEFAULT_ACTOR_ID,
            "LangGraph 输入处理验证",
        )
        conversation_id = conversation.id
        inbound_message = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation.id,
            actor_id=DEFAULT_ACTOR_ID,
            text="联系方式是 13800138000，邮箱为 hello@example.com，请分析岗位匹配。",
        )
        streamed_steps = []
        result = None
        for event in intake_graph.stream(inbound_message):
            if event.step is not None:
                streamed_steps.append(event.step)
            if event.result is not None:
                result = event.result

        assert result is not None

        assert result.active_turn.turn.status is AgentTurnStatus.RUNNING
        assert result.completed_steps == (
            AgentStepName.VALIDATE_INPUT,
            AgentStepName.BUILD_CONTEXT,
            AgentStepName.PARSE_MATERIAL,
            AgentStepName.EXTRACT_JOB_DESCRIPTION,
            AgentStepName.REDACT_SENSITIVE_DATA,
            AgentStepName.PERSIST_HISTORY,
            AgentStepName.CLEANUP_TEMPORARY_FILES,
        )
        assert tuple(streamed_steps) == result.completed_steps
        assert result.model_context.redacted_material_text == ""
        assert "13800138000" not in result.persisted_message.content_text
        assert "hello@example.com" not in result.persisted_message.content_text
        assert "【已隐藏手机号】" in result.persisted_message.content_text
        assert "【已隐藏邮箱】" in result.persisted_message.content_text
        print("career_intake_graph_real_database_ok")
    finally:
        if conversation_id is not None:
            deleted = repository.delete_conversation_permanently(
                DEFAULT_ACTOR_ID,
                conversation_id,
            )
            if not deleted:
                raise RuntimeError("Intake Graph 验证会话清理失败，请人工检查数据库")
        database.close()


if __name__ == "__main__":
    main()
