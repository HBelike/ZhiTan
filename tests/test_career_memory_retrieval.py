"""长期求职记忆惰性检索、数量和 Token 预算测试。"""

from datetime import UTC, datetime
from uuid import uuid4

from src.career_assistant.career_memory import CareerMemoryService
from src.career_assistant.persistence.records import CareerMemoryItemRecord


def memory(memory_type: str, text: str, *, space_id=None):
    now = datetime.now(UTC)
    return CareerMemoryItemRecord(
        id=uuid4(), organization_id=uuid4(), actor_id=uuid4(),
        career_space_id=space_id, memory_type=memory_type,
        normalized_value={"summary": text}, display_text=text,
        source_kind="explicit_user_statement", status="active",
        valid_from=now, created_at=now, updated_at=now,
    )


class Repository:
    def __init__(self, intentions=(), related=()) -> None:
        self.intentions = tuple(intentions)
        self.related = tuple(related)
        self.calls = []

    def list_active_for_prompt(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        types = tuple(item.value for item in kwargs["memory_types"])
        return self.intentions if types == ("job_intention",) else self.related


def test_job_intention_is_always_scoped_and_under_300_tokens() -> None:
    space_id = uuid4()
    intentions = [memory("job_intention", "目标上海 AI 应用工程", space_id=space_id)]
    repository = Repository(intentions=intentions)
    result = CareerMemoryService(repository).retrieve_for_prompt(
        uuid4(), uuid4(), space_id, "你好",
        candidate_profile_id=None, candidate_profile_version=None,
    )
    assert all(item.career_space_id == space_id for item in result.items)
    assert result.job_intention_tokens <= 300
    assert repository.calls[0][0][2] == space_id


def test_general_chat_does_not_retrieve_resume_facts() -> None:
    repository = Repository(related=[memory("personal_advantage", "善于架构设计")])
    result = CareerMemoryService(repository).retrieve_for_prompt(
        uuid4(), uuid4(), uuid4(), "今天天气怎么样",
        candidate_profile_id=None, candidate_profile_version=None,
    )
    assert result.items == ()
    assert len(repository.calls) == 1


def test_resume_query_limits_items_and_total_tokens() -> None:
    related = [memory("work_experience", f"项目经历 {index} 支付系统") for index in range(10)]
    result = CareerMemoryService(Repository(related=related)).retrieve_for_prompt(
        uuid4(), uuid4(), uuid4(), "根据我的项目经历准备面试",
        candidate_profile_id=uuid4(), candidate_profile_version=2,
        maximum_items=5, maximum_tokens=80,
    )
    assert len(result.items) <= 5
    assert result.estimated_tokens <= 80
