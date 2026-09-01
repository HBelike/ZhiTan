"""旧版默认求职数据归属管理员的数据迁移契约测试。"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT
    / "migrations"
    / "versions"
    / "20260828_29_assign_default_career_history_to_admin.py"
)


def _migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_targets_the_unique_active_admin_by_role() -> None:
    source = _migration_source()

    assert 'revision = "20260828_29"' in source
    assert 'down_revision = "20260828_28"' in source
    assert "role = 'admin'" in source
    assert "is_active" in source
    assert "email_normalized" not in source
    assert "管理员与旧默认用户不在同一组织" in source


def test_migration_reassigns_complete_conversation_ownership() -> None:
    source = _migration_source()

    for table_name in (
        "conversations",
        "agent_turns",
        "candidate_profiles",
        "target_role_profiles",
        "conversation_compaction_jobs",
        "job_match_assessments",
        "career_memory_items",
        "career_memory_jobs",
        "turn_memory_usages",
    ):
        assert f"UPDATE career_assistant.{table_name}" in source


def test_migration_merges_duplicate_default_career_space_without_deleting_data() -> None:
    source = _migration_source()

    assert "admin_space.id" in source
    assert "legacy_space.normalized_name" in source
    assert "SET career_space_id = admin_space.id" in source
    assert "DELETE FROM career_assistant.career_spaces" not in source


def test_migration_does_not_claim_unrelated_modules_or_guess_a_downgrade_owner() -> None:
    source = _migration_source()

    assert "UPDATE career_assistant.live_interview_sessions" not in source
    assert "UPDATE career_assistant.interview_experiences" not in source
    assert "UPDATE career_assistant.resume_optimization_records" not in source
    assert "数据归属迁移不可逆" in source
