"""求职助手历史列表轻量初始化边界测试。"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Lock
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Request

from src.career_assistant.persistence.records import ConversationRecord
from src.career_assistant.web import router as career_router


class FakeDatabase:
    """记录测试连接边界，不创建真实 PostgreSQL Engine。"""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url


class FakeConversationRepository:
    """用于确认快速路径只构造会话仓储。"""

    def __init__(self, database: FakeDatabase) -> None:
        self.database = database


class FakeTurnJobRepository:
    """记录活动 Turn 仓储复用的轻量数据库。"""

    def __init__(self, database: FakeDatabase) -> None:
        self.database = database


class FakeRepository:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database


class FakeModelGateway:
    def __init__(self, repository: FakeRepository, _settings) -> None:
        self.repository = repository


class CareerHistoryFastPathTests(unittest.TestCase):
    def test_active_turn_repository_reuses_read_database_without_full_services(self) -> None:
        database = FakeDatabase("postgresql://career:test@localhost/career")
        request = Request({"type": "http", "app": FastAPI()})
        request.app.state.career_assistant_services = None

        with (
            patch.object(
                career_router,
                "get_career_read_services",
                return_value=SimpleNamespace(database=database),
            ),
            patch.object(
                career_router,
                "CareerTurnJobRepository",
                FakeTurnJobRepository,
            ),
            patch.object(
                career_router,
                "get_career_services",
                side_effect=AssertionError("活动 Turn 仓储不应初始化完整 Agent 服务"),
            ),
        ):
            repository = career_router.get_career_turn_job_repository(request)

        self.assertIsInstance(repository, FakeTurnJobRepository)
        self.assertIs(repository.database, database)

    def test_history_repository_does_not_initialize_full_career_services(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            app = FastAPI()
            app.state.career_assistant_project_root = Path(temporary_directory)
            app.state.career_assistant_read_services = None
            app.state.career_assistant_repository_lock = Lock()
            app.state.career_assistant_services = None

            with (
                patch.dict(
                    os.environ,
                    {"CAREER_DATABASE_URL": "postgresql://career:test@localhost/career"},
                ),
                patch.object(career_router, "_load_career_environment", lambda _root: None),
                patch.object(career_router, "CareerDatabase", FakeDatabase),
                patch.object(career_router, "CareerContextRepository", FakeRepository),
                patch.object(career_router, "CareerModelProfileRepository", FakeRepository),
                patch.object(career_router, "ModelGateway", FakeModelGateway),
                patch.object(career_router, "load_model_gateway_settings", lambda _path: object()),
                patch.object(
                    career_router,
                    "CareerConversationRepository",
                    FakeConversationRepository,
                ),
            ):
                request = Request({"type": "http", "app": app})
                first = career_router.get_career_read_services(request)
                second = career_router.get_career_read_services(request)

        self.assertIs(first, second)
        self.assertIs(first, app.state.career_assistant_read_services)
        self.assertIsNone(app.state.career_assistant_services)

    def test_empty_conversation_creation_does_not_initialize_full_agent_services(self) -> None:
        now = datetime.now(UTC)
        conversation = ConversationRecord(
            id=uuid4(),
            organization_id=career_router.DEFAULT_ORGANIZATION_ID,
            actor_id=career_router.DEFAULT_ACTOR_ID,
            title="新的求职咨询",
            status="active",
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        calls: list[tuple[object, object, str]] = []

        class CreatingConversationRepository:
            def create_conversation(self, organization_id, actor_id, title):
                calls.append((organization_id, actor_id, title))
                return conversation

        read_services = SimpleNamespace(
            conversation_repository=CreatingConversationRepository(),
        )
        request = Request({"type": "http", "app": FastAPI()})

        with (
            patch.object(career_router, "get_career_read_services", return_value=read_services),
            patch.object(
                career_router,
                "get_career_services",
                side_effect=AssertionError("空会话不应初始化完整 Agent 服务"),
            ),
        ):
            payload = career_router.create_conversation(
                career_router.CreateConversationRequest(title="新的求职咨询"),
                request,
                BackgroundTasks(),
            )

        self.assertEqual(
            calls,
            [
                (
                    career_router.DEFAULT_ORGANIZATION_ID,
                    career_router.DEFAULT_ACTOR_ID,
                    "新的求职咨询",
                ),
            ],
        )
        self.assertEqual(payload["id"], str(conversation.id))
        self.assertIsNone(payload["context"])

    def test_contextual_conversation_creation_keeps_full_service_flow(self) -> None:
        now = datetime.now(UTC)
        candidate_profile_id = uuid4()
        conversation = ConversationRecord(
            id=uuid4(),
            organization_id=career_router.DEFAULT_ORGANIZATION_ID,
            actor_id=career_router.DEFAULT_ACTOR_ID,
            title="带简历的求职咨询",
            status="active",
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        context = object()
        bind_calls: list[tuple[object, object, object, object]] = []

        class FakeAgentLoop:
            def open_conversation(self, organization_id, actor_id, title):
                self.call = (organization_id, actor_id, title)
                return conversation

        class FakeContextRepository:
            def bind_conversation(
                self,
                actor_id,
                conversation_id,
                candidate_id,
                target_id,
            ):
                bind_calls.append((actor_id, conversation_id, candidate_id, target_id))
                return context

        agent_loop = FakeAgentLoop()
        services = SimpleNamespace(
            agent_loop=agent_loop,
            context_repository=FakeContextRepository(),
            conversation_repository=SimpleNamespace(),
        )
        request = Request({"type": "http", "app": FastAPI()})

        with (
            patch.object(career_router, "get_career_services", return_value=services),
            patch.object(
                career_router,
                "get_career_read_services",
                side_effect=AssertionError("带上下文会话必须保留完整服务流程"),
            ),
            patch.object(career_router, "_enqueue_job_assessment") as enqueue_assessment,
            patch.object(career_router, "_current_job_assessment", return_value=None),
            patch.object(
                career_router,
                "_conversation_context_payload",
                return_value={"bound": True},
            ),
        ):
            payload = career_router.create_conversation(
                career_router.CreateConversationRequest(
                    title="带简历的求职咨询",
                    candidate_profile_id=candidate_profile_id,
                ),
                request,
                BackgroundTasks(),
            )

        self.assertEqual(
            agent_loop.call,
            (
                career_router.DEFAULT_ORGANIZATION_ID,
                career_router.DEFAULT_ACTOR_ID,
                "带简历的求职咨询",
            ),
        )
        self.assertEqual(
            bind_calls,
            [
                (
                    career_router.DEFAULT_ACTOR_ID,
                    conversation.id,
                    candidate_profile_id,
                    None,
                ),
            ],
        )
        enqueue_assessment.assert_called_once()
        self.assertEqual(payload["context"], {"bound": True})

    def test_ready_history_detail_uses_read_services_without_full_agent_initialization(
        self,
    ) -> None:
        now = datetime.now(UTC)
        conversation = ConversationRecord(
            id=uuid4(),
            organization_id=career_router.DEFAULT_ORGANIZATION_ID,
            actor_id=career_router.DEFAULT_ACTOR_ID,
            title="历史求职咨询",
            status="active",
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        candidate = SimpleNamespace(actor_id=career_router.DEFAULT_ACTOR_ID, id=uuid4())
        target_role = SimpleNamespace(id=uuid4())
        context = SimpleNamespace(candidate=candidate, target_role=target_role)
        ready_assessment = SimpleNamespace(status=SimpleNamespace(value="ready"))

        class ReadingConversationRepository:
            def get_conversation(self, actor_id, conversation_id):
                self.get_call = (actor_id, conversation_id)
                return conversation

            def list_messages(self, actor_id, conversation_id):
                return []

            def get_last_model_selection(self, actor_id, conversation_id):
                return None

            def get_latest_agent_turn(self, actor_id, conversation_id):
                return None

        class ReadingContextRepository:
            def get_conversation_context(self, actor_id, conversation_id):
                self.get_call = (actor_id, conversation_id)
                return context

        class ReadingAssessmentRepository:
            def get_current(self, actor_id, candidate_id, target_role_id):
                self.get_call = (actor_id, candidate_id, target_role_id)
                return ready_assessment

        conversation_repository = ReadingConversationRepository()
        context_repository = ReadingContextRepository()
        read_services = SimpleNamespace(
            conversation_repository=conversation_repository,
            context_repository=context_repository,
            job_assessment_repository=ReadingAssessmentRepository(),
        )
        request = Request({"type": "http", "app": FastAPI()})
        background_tasks = BackgroundTasks()

        with (
            patch.object(career_router, "get_career_read_services", return_value=read_services),
            patch.object(
                career_router,
                "get_career_services",
                side_effect=AssertionError("历史详情响应不应初始化完整 Agent 服务"),
            ),
            patch.object(
                career_router,
                "_conversation_context_payload",
                return_value={"bound": True},
            ),
        ):
            payload = career_router.get_conversation(
                conversation.id,
                request,
                background_tasks,
            )

        self.assertEqual(payload["conversation"]["id"], str(conversation.id))
        self.assertEqual(payload["messages"], [])
        self.assertEqual(payload["context"], {"bound": True})
        self.assertEqual(len(background_tasks.tasks), 1)
        self.assertIs(
            background_tasks.tasks[0].func,
            career_router._refresh_job_assessment_after_response,
        )
        self.assertEqual(
            conversation_repository.get_call,
            (career_router.DEFAULT_ACTOR_ID, conversation.id),
        )

    def test_unassessed_history_detail_keeps_full_assessment_enqueue_flow(self) -> None:
        now = datetime.now(UTC)
        conversation = ConversationRecord(
            id=uuid4(),
            organization_id=career_router.DEFAULT_ORGANIZATION_ID,
            actor_id=career_router.DEFAULT_ACTOR_ID,
            title="尚未分析的历史求职咨询",
            status="active",
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        candidate = SimpleNamespace(actor_id=career_router.DEFAULT_ACTOR_ID, id=uuid4())
        target_role = SimpleNamespace(id=uuid4())
        context = SimpleNamespace(candidate=candidate, target_role=target_role)

        class ReadingConversationRepository:
            def get_conversation(self, actor_id, conversation_id):
                return conversation

            def list_messages(self, actor_id, conversation_id):
                return []

            def get_last_model_selection(self, actor_id, conversation_id):
                return None

            def get_latest_agent_turn(self, actor_id, conversation_id):
                return None

        class ReadingContextRepository:
            def get_conversation_context(self, actor_id, conversation_id):
                return context

        class EmptyAssessmentRepository:
            def get_current(self, actor_id, candidate_id, target_role_id):
                return None

        read_services = SimpleNamespace(
            conversation_repository=ReadingConversationRepository(),
            context_repository=ReadingContextRepository(),
            job_assessment_repository=EmptyAssessmentRepository(),
        )
        full_services = SimpleNamespace(job_assessment_repository=EmptyAssessmentRepository())
        request = Request({"type": "http", "app": FastAPI()})

        with (
            patch.object(career_router, "get_career_read_services", return_value=read_services),
            patch.object(career_router, "get_career_services", return_value=full_services),
            patch.object(career_router, "_enqueue_job_assessment") as enqueue_assessment,
            patch.object(
                career_router,
                "_conversation_context_payload",
                return_value={"bound": True},
            ),
        ):
            payload = career_router.get_conversation(
                conversation.id,
                request,
                BackgroundTasks(),
            )

        enqueue_assessment.assert_called_once()
        self.assertEqual(payload["context"], {"bound": True})


if __name__ == "__main__":
    unittest.main()
