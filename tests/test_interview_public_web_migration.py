"""全网公开面经账本与来源别名迁移契约测试。"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT
    / "migrations"
    / "versions"
    / "20260827_25_interview_public_web_sources.py"
)


def test_public_web_migration_creates_ledger_and_source_aliases() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision = "20260827_25"' in source
    assert 'down_revision = "20260826_24"' in source
    assert "CREATE TABLE career_assistant.interview_web_documents" in source
    assert "UNIQUE (organization_id, canonical_url)" in source
    assert "CREATE TABLE career_assistant.interview_experience_sources" in source
    assert "uq_interview_experience_sources_primary" in source
    assert "WHERE is_primary" in source
    assert "idx_interview_web_documents_content_hash" in source
    assert "idx_interview_web_documents_retry" in source


def test_public_web_migration_backfills_existing_primary_sources() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "INSERT INTO career_assistant.interview_experience_sources" in source
    assert "FROM career_assistant.interview_experiences" in source
    assert "source_url IS NOT NULL" in source
    assert "ON CONFLICT (organization_id, canonical_url) DO NOTHING" in source


def test_public_web_migration_downgrade_only_removes_new_tables() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "DROP TABLE IF EXISTS career_assistant.interview_experience_sources" in source
    assert "DROP TABLE IF EXISTS career_assistant.interview_web_documents" in source
    assert "DROP TABLE IF EXISTS career_assistant.interview_experiences" not in source
