from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CareerTurnWorkerDeploymentTests(unittest.TestCase):
    def test_worker_entry_imports_project_modules_outside_repository_cwd(self) -> None:
        worker_entry = ROOT / "scripts" / "run_career_agent_worker.py"
        command = (
            "import runpy; "
            f"runpy.run_path({str(worker_entry)!r}, run_name='career_worker_import_check')"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                [sys.executable, "-c", command],
                cwd=temporary_directory,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_backend_script_runs_worker_with_api_and_cleans_it_up(self) -> None:
        script = (ROOT / "scripts" / "start_dev_backend.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("run_career_agent_worker.py", script)
        self.assertIn("Start-Process", script)
        self.assertIn("-WindowStyle Hidden", script)
        self.assertIn("-PassThru", script)
        self.assertIn("$workerProcess.WaitForExit(1000)", script)
        self.assertIn("Worker 启动失败", script)
        self.assertIn("finally", script)
        self.assertIn("Stop-Process -Id $workerProcess.Id", script)

    def test_production_compose_runs_an_independent_durable_worker(self) -> None:
        compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")

        self.assertIn("  career-agent-worker:\n", compose)
        worker = compose.split("  career-agent-worker:\n", 1)[1].split(
            "\n  pipeline-scheduler:", 1
        )[0]
        self.assertIn('command: ["python", "scripts/run_career_agent_worker.py"]', worker)
        self.assertIn("CAREER_AGENT_GLOBAL_CONCURRENCY:", worker)
        self.assertIn("CAREER_AGENT_WORKER_CONCURRENCY:", worker)
        self.assertIn("CAREER_AGENT_LEASE_SECONDS:", worker)
        self.assertIn("CAREER_AGENT_HEARTBEAT_SECONDS:", worker)
        self.assertNotIn("CAREER_TEMPORARY_ATTACHMENT_ROOT", worker)
        self.assertNotIn("tmpfs:", worker)

    def test_production_keeps_complete_parsed_text_by_default(self) -> None:
        compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.production.example").read_text(encoding="utf-8")

        self.assertIn("CAREER_REDACTION_ENABLED: ${CAREER_REDACTION_ENABLED:-false}", compose)
        self.assertIn("CAREER_REDACTION_ENABLED=false", env_example)
        self.assertIn("CAREER_AGENT_GLOBAL_CONCURRENCY=8", env_example)
        self.assertIn("CAREER_AGENT_WORKER_CONCURRENCY=4", env_example)


if __name__ == "__main__":
    unittest.main()
