from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi import BackgroundTasks

from src.career_assistant.web.router import (
    _enqueue_job_assessment,
    _job_assessment_payload,
)


class CareerJobAssessmentApiTests(unittest.TestCase):
    def test_existing_queued_record_is_scheduled_again_for_recovery(self) -> None:
        record = SimpleNamespace(id=uuid4(), status=SimpleNamespace(value="queued"))
        service = SimpleNamespace(
            enqueue_context=lambda context: (record, False),
            run_assessment=lambda assessment_id: assessment_id,
        )
        tasks = BackgroundTasks()

        _enqueue_job_assessment(
            SimpleNamespace(job_assessment_service=service),
            tasks,
            SimpleNamespace(),
        )

        self.assertEqual(len(tasks.tasks), 1)
        self.assertEqual(tasks.tasks[0].args, (record.id,))

    def test_lifecycle_payload_keeps_validated_result_and_metadata(self) -> None:
        record = SimpleNamespace(
            id=uuid4(),
            status=SimpleNamespace(value="ready"),
            attempt_count=2,
            error_code=None,
            judge_model_id="deepseek-chat",
            prompt_version="career-job-judge-v1",
            result={
                "algorithm_version": "llm-judge-v1",
                "dimensions": [{"id": "interaction", "score": 75}],
            },
        )

        payload = _job_assessment_payload(record)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["attempt_count"], 2)
        self.assertEqual(payload["judge_model"], "deepseek-chat")
        self.assertEqual(payload["dimensions"][0]["id"], "interaction")


if __name__ == "__main__":
    unittest.main()
