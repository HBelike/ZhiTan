"""求职助手会话重命名与永久删除的数据层测试。"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from src.career_assistant.persistence.conversation_repository import (
    CareerConversationRepository,
)


class FakeResult:
    def __init__(self, *, row=None, rowcount: int = 1) -> None:
        self.row = row
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, dict(parameters)))
        if sql.startswith("UPDATE career_assistant.conversations"):
            now = datetime.now(UTC)
            return FakeResult(
                row={
                    "id": parameters["conversation_id"],
                    "organization_id": uuid4(),
                    "actor_id": parameters["actor_id"],
                    "title": parameters["title"],
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "archived_at": None,
                },
            )
        return FakeResult()


class FakeDatabase:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    @contextmanager
    def transaction(self):
        yield self.connection


class CareerConversationManagementTests(unittest.TestCase):
    def test_rename_trims_title_and_scopes_update_to_actor(self) -> None:
        database = FakeDatabase()
        repository = CareerConversationRepository(database)
        actor_id = uuid4()
        conversation_id = uuid4()

        conversation = repository.rename_conversation(
            actor_id,
            conversation_id,
            "  后端岗位准备  ",
        )

        self.assertIsNotNone(conversation)
        self.assertEqual(conversation.title, "后端岗位准备")
        sql, parameters = database.connection.calls[0]
        self.assertIn("actor_id = :actor_id", sql)
        self.assertIn("status <> 'deleted'", sql)
        self.assertEqual(parameters["actor_id"], actor_id)
        self.assertEqual(parameters["conversation_id"], conversation_id)

    def test_delete_removes_feedback_then_owned_conversation(self) -> None:
        database = FakeDatabase()
        repository = CareerConversationRepository(database)
        actor_id = uuid4()
        conversation_id = uuid4()

        deleted = repository.delete_conversation_permanently(actor_id, conversation_id)

        self.assertTrue(deleted)
        self.assertEqual(len(database.connection.calls), 2)
        feedback_sql, feedback_parameters = database.connection.calls[0]
        conversation_sql, conversation_parameters = database.connection.calls[1]
        self.assertIn("DELETE FROM career_assistant.interview_retrieval_feedback", feedback_sql)
        self.assertIn("actor_id = :actor_id", feedback_sql)
        self.assertIn("DELETE FROM career_assistant.conversations", conversation_sql)
        self.assertIn("actor_id = :actor_id", conversation_sql)
        self.assertEqual(feedback_parameters, conversation_parameters)


if __name__ == "__main__":
    unittest.main()
