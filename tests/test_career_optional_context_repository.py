"""求职资料可独立绑定及增量沿用的数据层回归测试。"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from src.career_assistant.persistence.context_repository import CareerContextRepository


class FakeResult:
    def __init__(self, *, row=None, scalar=None) -> None:
        self._row = row
        self._scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row

    def one(self):
        return self._row

    def scalar_one(self):
        return self._scalar


class FakeConnection:
    def __init__(self, *, current_candidate_id=None, current_target_id=None) -> None:
        self.current_candidate_id = current_candidate_id
        self.current_target_id = current_target_id
        self.insert_parameters = None

    def execute(self, statement, parameters):
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT binding.candidate_profile_id"):
            return FakeResult(
                row={
                    "candidate_profile_id": self.current_candidate_id,
                    "target_role_profile_id": self.current_target_id,
                },
            )
        if "AS owns_candidate" in sql and "AS owns_target" in sql:
            if (
                parameters["candidate_id"] is None
                and "CAST(:candidate_id AS UUID) IS NULL" not in sql
            ):
                raise RuntimeError("PostgreSQL 无法推断 candidate_id 的 NULL 参数类型")
            if (
                parameters["target_id"] is None
                and "CAST(:target_id AS UUID) IS NULL" not in sql
            ):
                raise RuntimeError("PostgreSQL 无法推断 target_id 的 NULL 参数类型")
            return FakeResult(row={"owns_candidate": True, "owns_target": True})
        if sql.startswith("SELECT COALESCE(MAX(binding_version)"):
            return FakeResult(scalar=2)
        if sql.startswith("INSERT INTO career_assistant.conversation_context_bindings"):
            self.insert_parameters = dict(parameters)
            self.current_candidate_id = parameters["candidate_id"]
            self.current_target_id = parameters["target_id"]
            return FakeResult()
        if "binding.id AS binding_id" in sql:
            return FakeResult(row=self._context_row(parameters["conversation_id"]))
        raise AssertionError(f"未覆盖的 SQL：{sql}")

    def _context_row(self, conversation_id):
        now = datetime.now(UTC)
        return {
            "binding_id": uuid4(),
            "conversation_id": conversation_id,
            "binding_version": 2,
            "binding_created_at": now,
            "candidate_id": self.current_candidate_id,
            "candidate_organization_id": uuid4() if self.current_candidate_id else None,
            "candidate_actor_id": uuid4() if self.current_candidate_id else None,
            "display_name": "后端简历" if self.current_candidate_id else None,
            "source_filename": "resume.pdf" if self.current_candidate_id else None,
            "resume_outline": "FastAPI 项目经验" if self.current_candidate_id else None,
            "candidate_version": 1 if self.current_candidate_id else None,
            "candidate_created_at": now if self.current_candidate_id else None,
            "target_id": self.current_target_id,
            "target_organization_id": uuid4() if self.current_target_id else None,
            "target_actor_id": uuid4() if self.current_target_id else None,
            "company_name": "示例公司" if self.current_target_id else None,
            "role_name": "后端工程师" if self.current_target_id else None,
            "source_kind": "text" if self.current_target_id else None,
            "source_label": "手动粘贴" if self.current_target_id else None,
            "job_text": "熟练使用 FastAPI" if self.current_target_id else None,
            "requirements": [] if self.current_target_id else None,
            "target_version": 1 if self.current_target_id else None,
            "target_created_at": now if self.current_target_id else None,
        }


class FakeDatabase:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @contextmanager
    def transaction(self):
        yield self.connection


class CareerOptionalContextRepositoryTests(unittest.TestCase):
    def test_candidate_can_be_bound_without_target_role(self) -> None:
        candidate_id = uuid4()
        connection = FakeConnection()
        repository = CareerContextRepository(FakeDatabase(connection))

        context = repository.bind_conversation(
            uuid4(),
            uuid4(),
            candidate_profile_id=candidate_id,
        )

        self.assertEqual(context.candidate.id, candidate_id)
        self.assertIsNone(context.target_role)
        self.assertEqual(connection.insert_parameters["candidate_id"], candidate_id)
        self.assertIsNone(connection.insert_parameters["target_id"])

    def test_adding_target_role_retains_current_candidate(self) -> None:
        candidate_id = uuid4()
        target_id = uuid4()
        connection = FakeConnection(current_candidate_id=candidate_id)
        repository = CareerContextRepository(FakeDatabase(connection))

        context = repository.bind_conversation(
            uuid4(),
            uuid4(),
            target_role_profile_id=target_id,
        )

        self.assertEqual(context.candidate.id, candidate_id)
        self.assertEqual(context.target_role.id, target_id)
        self.assertEqual(connection.insert_parameters["candidate_id"], candidate_id)
        self.assertEqual(connection.insert_parameters["target_id"], target_id)

    def test_target_role_can_be_bound_without_candidate(self) -> None:
        target_id = uuid4()
        connection = FakeConnection()
        repository = CareerContextRepository(FakeDatabase(connection))

        context = repository.bind_conversation(
            uuid4(),
            uuid4(),
            target_role_profile_id=target_id,
        )

        self.assertIsNone(context.candidate)
        self.assertEqual(context.target_role.id, target_id)
        self.assertIsNone(connection.insert_parameters["candidate_id"])
        self.assertEqual(connection.insert_parameters["target_id"], target_id)


if __name__ == "__main__":
    unittest.main()
