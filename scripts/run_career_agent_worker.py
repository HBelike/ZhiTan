"""生产容器中的求职助手 Agent Worker 入口。"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 直接执行 scripts 下的入口时，Python 只把 scripts 目录加入模块搜索路径。
# 显式加入项目根目录，保证本地隐藏进程和生产容器都能导入 src 包。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.conversation_memory import ConversationMemoryService
from src.career_assistant.memory_worker import CareerMemoryJobProcessor, CareerMemoryWorker
from src.career_assistant.persistence import (
    CareerCompactionRepository,
    CareerModelUsageRepository,
    CareerTurnJobRepository,
)
from src.career_assistant.settings import (
    load_career_memory_worker_settings,
    load_career_turn_worker_settings,
)
from src.career_assistant.turn_worker import (
    CareerAgentTurnProcessor,
    CareerTurnWorker,
)
from src.career_assistant.web.router import (
    get_career_services,
    install_career_assistant_api,
)

async def run_worker() -> None:
    """复用求职助手服务工厂并启动不依赖 HTTP 连接的 Worker。"""

    app = FastAPI()
    install_career_assistant_api(app, PROJECT_ROOT)
    request = Request({"type": "http", "app": app})
    services = get_career_services(request)
    repository = CareerTurnJobRepository(services.database)
    turn_worker = CareerTurnWorker(
        repository,
        CareerAgentTurnProcessor(
            services.agent_loop,
            services.intake_graph,
            services.response_runner,
            repository,
        ),
        load_career_turn_worker_settings(),
    )
    memory_settings = load_career_memory_worker_settings()
    compaction_repository = CareerCompactionRepository(services.database)
    memory_service = ConversationMemoryService(
        services.conversation_repository,
        compaction_repository,
        services.model_connection_client,
        CareerModelUsageRepository(services.database),
        worker_id=memory_settings.worker_id,
        lease_seconds=max(10, int(memory_settings.lease_seconds)),
    )
    memory_worker = CareerMemoryWorker(
        compaction_repository,
        CareerMemoryJobProcessor(
            memory_service,
            services.model_gateway,
        ),
        memory_settings,
    )
    try:
        await asyncio.gather(
            turn_worker.run_forever(),
            memory_worker.run_forever(),
        )
    finally:
        turn_worker.stop()
        memory_worker.stop()
        services.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
