"""平台唯一管理员数据库约束的迁移契约测试。"""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "versions" / "20260825_20_single_platform_admin.py"


class SinglePlatformAdminMigrationTests(unittest.TestCase):
    def test_migration_normalizes_roles_without_pinning_an_email(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        self.assertIn("DROP CONSTRAINT platform_users_role_check", source)
        self.assertIn("UPDATE career_assistant.platform_users", source)
        self.assertIn("role IN ('user', 'admin')", source)
        self.assertIn("ROW_NUMBER() OVER", source)
        self.assertNotIn("platform_users_admin_email_check", source)
        self.assertNotIn("admin@example.com", source)

    def test_migration_enforces_one_admin_for_the_whole_platform(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        self.assertIn('revision = "20260825_20"', source)
        self.assertIn('down_revision = "20260823_19"', source)
        self.assertIn("CREATE UNIQUE INDEX uq_platform_users_single_admin", source)
        self.assertIn("ON career_assistant.platform_users (role)", source)
        self.assertIn("WHERE role = 'admin'", source)

    def test_migration_can_remove_the_single_admin_constraint(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        self.assertIn("DROP INDEX IF EXISTS career_assistant.uq_platform_users_single_admin", source)
        self.assertIn("SET role = 'viewer'", source)
        self.assertIn("role IN ('viewer', 'operator', 'admin')", source)


if __name__ == "__main__":
    unittest.main()
