"""压缩任务仓储静态契约测试。"""

from pathlib import Path


def test_compaction_repository_uses_skip_locked_and_lease_owner() -> None:
    source = Path("src/career_assistant/persistence/compaction_repository.py").read_text(
        encoding="utf-8",
    )
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "lease_owner = :worker_id" in source
    assert "ON CONFLICT (trigger_turn_id)" in source


def test_summary_cas_is_actor_and_organization_scoped() -> None:
    source = Path("src/career_assistant/persistence/conversation_repository.py").read_text(
        encoding="utf-8",
    )
    assert "conversation.organization_id = :organization_id" in source
    assert "conversation.actor_id = :actor_id" in source
    assert "IS NOT DISTINCT FROM :expected_covered_through_message_id" in source
