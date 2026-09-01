"""求职助手会话列表分页契约测试。"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from inspect import signature
from uuid import uuid4

from src.career_assistant.persistence.conversation_repository import (
    CareerConversationRepository,
)
from src.career_assistant.web.router import list_conversations


class FakeResult:
    def __init__(self, *, scalar=None, rows=None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters):
        sql = " ".join(str(statement).split())
        copied = dict(parameters)
        self.calls.append((sql, copied))
        if sql.startswith("SELECT COUNT(*)"):
            return FakeResult(scalar=32)
        now = datetime.now(UTC)
        return FakeResult(
            rows=[
                {
                    "id": uuid4(),
                    "organization_id": uuid4(),
                    "actor_id": parameters["actor_id"],
                    "title": "分页会话",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "archived_at": None,
                }
            ],
        )


class FakeDatabase:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    @contextmanager
    def transaction(self):
        yield self.connection


class CareerConversationPaginationTests(unittest.TestCase):
    def test_api_defaults_to_fifteen_conversations_per_page(self) -> None:
        page_size = signature(list_conversations).parameters["page_size"].default

        self.assertEqual(page_size.default, 15)

    def test_page_uses_limit_offset_and_returns_total(self) -> None:
        database = FakeDatabase()
        repository = CareerConversationRepository(database)

        conversations, total = repository.list_conversation_page(
            uuid4(),
            page=3,
            page_size=10,
        )

        self.assertEqual(total, 32)
        self.assertEqual(len(conversations), 1)
        list_sql, list_parameters = database.connection.calls[1]
        self.assertIn("LIMIT :page_size OFFSET :offset", list_sql)
        self.assertEqual(list_parameters["page_size"], 10)
        self.assertEqual(list_parameters["offset"], 20)

    def test_page_and_page_size_are_bounded(self) -> None:
        repository = CareerConversationRepository(FakeDatabase())

        with self.assertRaisesRegex(ValueError, "page 必须大于等于 1"):
            repository.list_conversation_page(uuid4(), page=0, page_size=10)
        with self.assertRaisesRegex(ValueError, "page_size 必须在 1 到 100"):
            repository.list_conversation_page(uuid4(), page=1, page_size=101)


if __name__ == "__main__":
    unittest.main()
