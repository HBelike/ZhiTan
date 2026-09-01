"""求职记忆停用后的运行边界回归测试。"""

from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_memory_api_is_not_installed() -> None:
    source = _source("src/career_assistant/web/router.py")
    install_body = source[source.index("def install_career_assistant_api(") : source.index("def get_career_read_services(")]
    assert "memory_router" not in install_body


def test_runtime_does_not_read_or_extract_career_memory() -> None:
    prompt_source = _source("src/career_assistant/prompt_context.py")
    router_source = _source("src/career_assistant/web/router.py")
    runner_source = _source("src/career_assistant/response_runner.py")
    assert "CareerMemoryService" not in prompt_source
    assert "retrieve_for_prompt" not in prompt_source
    assert "enqueue_resume(" not in router_source
    assert "memory_extraction_service=memory_extraction_service" not in router_source
    assert "memory_service=career_memory_service" not in router_source
    assert "enqueue_turn(" not in runner_source
    assert "record_turn_usages(" not in runner_source


def test_worker_only_processes_conversation_compaction() -> None:
    source = _source("scripts/run_career_agent_worker.py")
    assert "services.memory_repository" not in source
    assert "services.memory_extraction_service" not in source


def test_conversation_creation_no_longer_creates_or_reads_career_spaces() -> None:
    source = _source("src/career_assistant/persistence/conversation_repository.py")
    create_body = source[source.index("    def create_conversation(") : source.index("    def list_conversations(")]
    assert "career_assistant.career_spaces" not in create_body


def test_schema_allows_new_conversations_without_career_space() -> None:
    source = _source("migrations/versions/20260827_26_deactivate_career_memory.py")
    assert 'revision = "20260827_26"' in source
    assert 'down_revision = "20260827_25"' in source
    assert "ALTER COLUMN career_space_id DROP NOT NULL" in source
