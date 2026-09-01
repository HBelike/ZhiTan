from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.career_assistant.settings import load_job_assessment_settings


class JobAssessmentSettingsTests(unittest.TestCase):
    def test_fixed_judge_settings_are_loaded(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "career.yaml"
            path.write_text(
                """
career_assistant:
  job_assessment:
    judge_profile_key: DeepSeek-V4-Flash
    prompt_version: career-job-judge-v1
    max_attempts: 3
""".strip(),
                encoding="utf-8",
            )
            settings = load_job_assessment_settings(path)

        self.assertEqual(settings.judge_profile_key, "deepseek-v4-flash")
        self.assertEqual(settings.prompt_version, "career-job-judge-v1")
        self.assertEqual(settings.max_attempts, 3)

    def test_attempts_above_three_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "career.yaml"
            path.write_text(
                """
career_assistant:
  job_assessment:
    judge_profile_key: judge
    prompt_version: v1
    max_attempts: 4
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "1 到 3"):
                load_job_assessment_settings(path)


if __name__ == "__main__":
    unittest.main()
