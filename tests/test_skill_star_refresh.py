from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services.skill_library_service import SkillLibraryService


class SkillStarRepositoryResolutionTests(unittest.TestCase):
    def _service(self, project_root: Path) -> SkillLibraryService:
        config = SimpleNamespace(
            project_root=project_root,
            github_api_version="2022-11-28",
            github_token_env="GITHUB_TOKEN",
        )
        return SkillLibraryService(config)  # type: ignore[arg-type]

    def test_install_lock_supplies_repository_when_skill_frontmatter_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            skill_root = project_root / ".agents" / "skills" / "demo-skill"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(
                "---\nname: demo-skill\ndescription: 演示技能\n---\n\n正文。\n",
                encoding="utf-8",
            )
            (project_root / ".agents" / ".skill-lock.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "skills": {
                            "demo-skill": {
                                "source": "example/demo-skills",
                                "sourceType": "github",
                                "sourceUrl": "https://github.com/example/demo-skills.git",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = next(item for item in self._service(project_root).list_skills() if item.name == "demo-skill")

            self.assertEqual(summary.repository_full_name, "example/demo-skills")

    def test_bundled_seed_lock_supplies_repository_in_production(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            skill_root = project_root / ".agents" / "skills" / "demo-skill"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(
                "---\nname: demo-skill\ndescription: 演示技能\n---\n\n正文。\n",
                encoding="utf-8",
            )
            seed_root = project_root / "deploy" / "skill-seeds"
            seed_root.mkdir(parents=True)
            (seed_root / ".skill-lock.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "skills": {
                            "demo-skill": {
                                "source": "example/production-skills",
                                "sourceType": "github",
                                "sourceUrl": "https://github.com/example/production-skills.git",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = next(item for item in self._service(project_root).list_skills() if item.name == "demo-skill")

            self.assertEqual(summary.repository_full_name, "example/production-skills")

    def test_refresh_writes_first_snapshot_and_skips_it_within_seven_days(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            skill_root = project_root / ".agents" / "skills" / "demo-skill"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(
                "---\nname: demo-skill\ndescription: demo\nrepository: example/demo-skills\n---\n",
                encoding="utf-8",
            )
            service = self._service(project_root)

            with (
                patch("src.services.skill_library_service.Path.home", return_value=project_root),
                patch.object(service, "_fetch_repository_stars", return_value=321) as fetch_stars,
            ):
                first = service.refresh_stale_star_snapshots()
                second = service.refresh_stale_star_snapshots()

            self.assertEqual(first["refreshed"], 1)
            self.assertEqual(second["unchanged"], 1)
            fetch_stars.assert_called_once_with("example/demo-skills")
            cache = json.loads((project_root / "data" / "skill_star_cache.json").read_text(encoding="utf-8"))
            self.assertEqual(cache["repositories"]["example/demo-skills"]["stars"], 321)


class SkillStarSchedulerWiringTests(unittest.TestCase):
    def test_scheduler_refreshes_on_start_and_weekly_content_run(self) -> None:
        application_source = Path("src/app/application.py").read_text(encoding="utf-8")

        scheduler_method = re.search(
            r"def _run_scheduler_forever\(self\).*?(?=\n    def )",
            application_source,
            re.DOTALL,
        )
        weekly_method = re.search(
            r"def _run_weekly_content_production_job_unlocked\(self\).*?(?=\n    def )",
            application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(scheduler_method)
        self.assertIsNotNone(weekly_method)
        self.assertIn("self._refresh_skill_stars()", scheduler_method.group(0))  # type: ignore[union-attr]
        self.assertIn("self._refresh_skill_stars()", weekly_method.group(0))  # type: ignore[union-attr]

    def test_production_scheduler_mounts_skill_volume(self) -> None:
        compose_source = Path("docker-compose.production.yml").read_text(encoding="utf-8")
        scheduler_section = re.search(
            r"^  pipeline-scheduler:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|^networks:)",
            compose_source,
            re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(scheduler_section)
        self.assertIn("application_skills:/app/.agents", scheduler_section.group("body"))  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
