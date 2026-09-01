"""顶级路由模块目录的默认值、校验与角色可见性测试。"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from src.platform_access.contracts import PLATFORM_ADMIN_EMAIL, PlatformRole, PlatformUser
from src.platform_access.navigation_config import (
    DEFAULT_ROUTE_MODULE_SETTINGS,
    normalize_route_module_settings,
    route_modules_for_ui,
)
from src.platform_access.service import PlatformAccessService


class FakeNavigationRepository:
    def __init__(self) -> None:
        self.settings: dict[str, bool] = {}

    def get_route_module_settings(self, organization_id):
        del organization_id
        return dict(self.settings)

    def save_route_module_settings(self, organization_id, actor_id, settings):
        del organization_id, actor_id
        self.settings = dict(settings)
        return dict(self.settings)


def build_user(role: PlatformRole) -> PlatformUser:
    return PlatformUser(
        id=uuid4(),
        organization_id=uuid4(),
        username=f"{role.value}-user",
        display_name=role.value,
        email=None,
        email_verified_at=None,
        role=role,
        is_active=True,
        created_at=datetime.now(UTC),
    )


class NavigationConfigTests(unittest.TestCase):
    def test_role_model_only_contains_user_and_admin(self) -> None:
        self.assertEqual({role.value for role in PlatformRole}, {"user", "admin"})
        self.assertEqual(PLATFORM_ADMIN_EMAIL, "admin@example.com")

    def test_default_catalog_contains_job_library_as_top_level_module(self) -> None:
        modules = route_modules_for_ui(None, PlatformRole.ADMIN)
        by_key = {item["key"]: item for item in modules}

        self.assertEqual(len([item for item in modules if item["scope"] == "route"]), 7)
        self.assertEqual(by_key["job_library"]["label"], "职位库")
        self.assertEqual(by_key["job_library"]["path"], "/interviews/jobs")
        self.assertFalse(by_key["job_library"]["admin_only"])

    def test_retired_routes_are_not_returned_but_legacy_settings_are_accepted(self) -> None:
        modules = route_modules_for_ui(
            {"resume_assistant": False, "evaluation_center": False},
            PlatformRole.ADMIN,
        )
        keys = {item["key"] for item in modules}

        self.assertNotIn("resume_assistant", keys)
        self.assertNotIn("evaluation_center", keys)
        self.assertEqual(len([item for item in modules if item["scope"] == "route"]), 7)

    def test_default_catalog_contains_seven_routes_and_two_enabled_features(self) -> None:
        modules = route_modules_for_ui(None, PlatformRole.ADMIN)

        self.assertEqual(len(modules), 9)
        self.assertTrue(all(item["enabled"] for item in modules))
        self.assertEqual(modules[0]["key"], "career_assistant")
        self.assertEqual(modules[1]["key"], "workbench")
        self.assertEqual(modules[-1]["key"], "admin_console")
        self.assertEqual(modules[-1]["path"], "/admin/modules")

        features = [item for item in modules if item["scope"] == "feature"]
        self.assertEqual(
            {item["key"] for item in features},
            {"career_interview_master", "career_online_assessment"},
        )
        self.assertTrue(all(item["parent_key"] == "career_assistant" for item in features))

    def test_legacy_shared_switch_is_inherited_by_both_features_for_all_roles(self) -> None:
        modules = route_modules_for_ui(
            {"career_interview_tools": False},
            PlatformRole.ADMIN,
        )
        features = [item for item in modules if item["scope"] == "feature"]

        self.assertEqual(len(features), 2)
        self.assertTrue(all(not item["enabled"] for item in features))
        self.assertTrue(all(not item["accessible"] for item in features))

    def test_career_feature_switches_are_independent_and_also_apply_to_admin(self) -> None:
        modules = route_modules_for_ui(
            {
                "career_interview_master": False,
                "career_online_assessment": True,
            },
            PlatformRole.ADMIN,
        )
        by_key = {item["key"]: item for item in modules}

        self.assertFalse(by_key["career_interview_master"]["accessible"])
        self.assertTrue(by_key["career_online_assessment"]["accessible"])

    def test_admin_accesses_every_top_level_module_but_not_disabled_features(self) -> None:
        modules = route_modules_for_ui(
            {
                "career_assistant": False,
                "career_interview_master": False,
                "workbench": False,
            },
            PlatformRole.ADMIN,
        )
        by_key = {item["key"]: item for item in modules}

        self.assertFalse(by_key["career_assistant"]["enabled"])
        self.assertFalse(by_key["workbench"]["enabled"])
        self.assertTrue(all(item["accessible"] for item in modules if item["scope"] == "route"))
        self.assertFalse(by_key["career_interview_master"]["accessible"])

    def test_user_only_accesses_enabled_non_admin_modules(self) -> None:
        modules = route_modules_for_ui(
            {"career_assistant": False, "skill_library": False},
            PlatformRole.USER,
        )
        by_key = {item["key"]: item for item in modules}

        self.assertFalse(by_key["career_assistant"]["accessible"])
        self.assertFalse(by_key["skill_library"]["accessible"])
        self.assertTrue(by_key["workbench"]["accessible"])
        self.assertFalse(by_key["admin_console"]["accessible"])

    def test_admin_console_cannot_be_disabled(self) -> None:
        normalized = normalize_route_module_settings({"admin_console": False})

        self.assertTrue(normalized["admin_console"])
        self.assertEqual(set(normalized), set(DEFAULT_ROUTE_MODULE_SETTINGS))

    def test_unknown_module_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知路由模块"):
            normalize_route_module_settings({"unknown": True})

    def test_non_boolean_state_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是布尔值"):
            normalize_route_module_settings({"workbench": 1})

    def test_user_cannot_save_route_modules(self) -> None:
        service = PlatformAccessService(FakeNavigationRepository())

        with self.assertRaises(PermissionError):
            service.save_route_modules(build_user(PlatformRole.USER), {"workbench": False})

    def test_admin_save_is_returned_as_accessible_catalog(self) -> None:
        repository = FakeNavigationRepository()
        service = PlatformAccessService(repository)

        modules = service.save_route_modules(build_user(PlatformRole.ADMIN), {"career_assistant": False})
        by_key = {item["key"]: item for item in modules}

        self.assertFalse(by_key["career_assistant"]["enabled"])
        self.assertTrue(by_key["career_assistant"]["accessible"])
        self.assertTrue(by_key["admin_console"]["enabled"])


if __name__ == "__main__":
    unittest.main()
