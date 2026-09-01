"""模型最大输出能力迁移契约测试。"""

from pathlib import Path


MIGRATION = Path("migrations/versions/20260830_32_model_output_capability.py")


def test_migration_replaces_half_context_constraint() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "CHECK (reserved_output_tokens <= context_window_tokens)" in source
    assert "CHECK (reserved_output_tokens * 2 <= context_window_tokens)" in source


def test_migration_backfills_verified_deepseek_output_for_non_admin_profiles() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "SET reserved_output_tokens = 384000" in source
    assert "context_window_source <> 'admin'" in source
    assert "'deepseek-v4-pro'" in source
