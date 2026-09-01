"""单次滚动摘要调用与失败语义测试。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.conversation_memory import (
    SUMMARY_SCHEMA_VERSION,
    ConversationMemoryService,
)
from src.career_assistant.model_clients import CompletionUsage
from src.career_assistant.model_gateway import (
    ModelReadiness,
    ModelResolution,
    ModelResolutionReason,
)
from src.career_assistant.persistence import ModelCostTier, ModelProfileRecord
from src.career_assistant.persistence.compaction_repository import CompactionJobRecord
from src.career_assistant.persistence.records import MessageRecord, MessageRole, SessionSummaryRecord


ORGANIZATION_ID = uuid4()
ACTOR_ID = uuid4()
CONVERSATION_ID = uuid4()
TURN_ID = uuid4()
PROFILE_ID = uuid4()
NOW = datetime.now(UTC)


def summary_json() -> str:
    import json

    return json.dumps(
        {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "current_tasks": ["完善项目经历"],
            "decisions": [],
            "open_loops": [],
            "user_corrections": [],
            "temporary_user_context": [],
            "assistant_commitments": [],
            "companies": [],
            "roles": ["AI 应用工程师"],
        },
        ensure_ascii=False,
    )


def resolution() -> ModelResolution:
    profile = ModelProfileRecord(
        id=PROFILE_ID,
        organization_id=ORGANIZATION_ID,
        profile_key="answer-model",
        display_name="回答模型",
        provider_key="openai-compatible",
        model_id="answer-model",
        capabilities=frozenset({ModelCapability.TEXT}),
        cost_tier=ModelCostTier.PAID,
        priority=1,
        enabled=True,
        api_base_url="https://example.com/v1",
        created_at=NOW,
        updated_at=NOW,
    )
    return ModelResolution(
        profile=profile,
        reason=ModelResolutionReason.USER_SELECTED,
        readiness=ModelReadiness.READY,
        credential_env_name=None,
        credential="secret",
    )


def messages() -> list[MessageRecord]:
    result = []
    for index in range(6):
        turn_id = uuid4()
        result.extend(
            [
                MessageRecord(
                    uuid4(), CONVERSATION_ID, turn_id, MessageRole.USER,
                    f"用户问题 {index}", True, NOW + timedelta(minutes=index * 2),
                ),
                MessageRecord(
                    uuid4(), CONVERSATION_ID, turn_id, MessageRole.ASSISTANT,
                    f"助手回答 {index}", True, NOW + timedelta(minutes=index * 2 + 1),
                ),
            ],
        )
    return result


class FakeConversations:
    def __init__(self) -> None:
        self.saved_summaries = []

    def get_valid_summary(self, *_args):
        return None

    def list_completed_dialogue_messages(self, *_args, **_kwargs):
        return messages()

    def save_summary_if_current(self, *_args, **kwargs):
        self.saved_summaries.append(kwargs)
        return SessionSummaryRecord(
            id=uuid4(),
            conversation_id=CONVERSATION_ID,
            summary_text=kwargs["summary_text"],
            summary_version=1,
            contains_sensitive_data=False,
            created_at=NOW,
            updated_at=NOW,
            covered_through_message_id=kwargs["covered_through_message_id"],
        )


class FakeJobs:
    def __init__(self) -> None:
        self.finished = []
        self.job = CompactionJobRecord(
            id=uuid4(), organization_id=ORGANIZATION_ID, actor_id=ACTOR_ID,
            conversation_id=CONVERSATION_ID, trigger_turn_id=TURN_ID,
            requested_profile_id=PROFILE_ID, status="running",
            expected_summary_version=0, attempt_count=1, lease_owner="memory-test",
            lease_expires_at=NOW + timedelta(minutes=2), error_code=None,
        )

    def claim_for_turn(self, *_args, **_kwargs):
        return self.job

    def finish(self, *args, **kwargs):
        self.finished.append((args, kwargs))
        return True


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.operations = []

    def complete_json(self, profile, _credential_name, _messages, **kwargs):
        self.operations.append(
            (profile.id, kwargs["operation"], kwargs["options"].temperature, kwargs["options"].max_tokens),
        )
        kwargs["usage_callback"](CompletionUsage(800, 120))
        return self.response


class FakeUsage:
    def __init__(self) -> None:
        self.finished = []

    def start(self, *_args):
        return uuid4()

    def finish(self, *args, **kwargs):
        self.finished.append((args, kwargs))


def make_service(response: str):
    conversations = FakeConversations()
    jobs = FakeJobs()
    client = FakeClient(response)
    usage = FakeUsage()
    service = ConversationMemoryService(
        conversations, jobs, client, usage, worker_id="memory-test",
    )
    return service, conversations, jobs, client, usage


def test_compaction_uses_current_answer_profile_once() -> None:
    service, conversations, _jobs, client, _usage = make_service(summary_json())

    outcome = service.compact_once(
        actor_id=ACTOR_ID,
        conversation_id=CONVERSATION_ID,
        trigger_turn_id=TURN_ID,
        resolution=resolution(),
        target_prompt_tokens=38_400,
    )

    assert client.operations == [(PROFILE_ID, "conversation_memory_compaction", 0.0, 1_600)]
    assert outcome.status == "succeeded"
    assert len(conversations.saved_summaries) == 1
    assert conversations.saved_summaries[0]["compacted_input_tokens"] == 800


def test_invalid_json_does_not_advance_cursor() -> None:
    service, conversations, jobs, client, usage = make_service("not-json")

    outcome = service.compact_once(
        actor_id=ACTOR_ID,
        conversation_id=CONVERSATION_ID,
        trigger_turn_id=TURN_ID,
        resolution=resolution(),
        target_prompt_tokens=38_400,
    )

    assert outcome.status == "failed"
    assert client.operations == [(PROFILE_ID, "conversation_memory_compaction", 0.0, 1_600)]
    assert conversations.saved_summaries == []
    assert usage.finished[0][1]["status"] == "failed"
    assert jobs.finished[0][1]["error_code"] == "invalid_compaction_summary"
