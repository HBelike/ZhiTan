"""模型上下文自动识别迁移测试。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    PROJECT_ROOT
    / "migrations"
    / "versions"
    / "20260827_27_auto_detect_model_context.py"
)


def test_migration_backfills_verified_and_unknown_model_contexts() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260827_27"' in source
    assert 'down_revision = "20260827_26"' in source
    assert "deepseek-v4-flash" in source
    assert "deepseek-v4-pro" in source
    assert "1048576" in source
    assert "1000000" in source
    assert "context_window_source <> 'admin'" in source


def test_migration_changes_future_database_default_to_one_million() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "ALTER COLUMN context_window_tokens SET DEFAULT 1000000" in source
    assert "reserved_output_tokens = LEAST(reserved_output_tokens, 4096)" in source
