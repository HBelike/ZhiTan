"""评测模块的确定性指标、真实数据准入和 API 路由测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI

from src.evaluation.contracts import EvaluationSourceKind
from src.evaluation.metrics import evaluate_observation
from src.evaluation.runners import InterviewRagExperimentRunner
from src.evaluation.service import EvaluationService
from src.evaluation.web import install_evaluation_api, router


class FakeEvaluationRepository:
    def __init__(self) -> None:
        self.cases = []
        self.added = []
        self.frozen = []

    def add_case(self, **payload):
        self.added.append(payload)
        return payload

    def list_cases(self, organization_id, dataset_version_id):
        del organization_id, dataset_version_id
        return list(self.cases)

    def freeze_dataset_version(self, **payload):
        self.frozen.append(payload)
        return payload

    def get_source_reference_snapshot(self, organization_id, dataset_version_id, source_ref_id):
        del organization_id, dataset_version_id
        return {
            "target_type": "interview_rag",
            "record_id": source_ref_id,
            "retrieval_strategy": "lexical",
        }


class EvaluationMetricTests(unittest.TestCase):
    def test_rag_metrics_use_gold_chunk_ids_and_top_six_order(self) -> None:
        scores = evaluate_observation(
            target_type="interview_rag",
            expectation={"relevant_chunk_ids": ["a", "c"]},
            observation={
                "retrieved_items": [
                    {"chunk_id": "x"},
                    {"chunk_id": "a"},
                    {"chunk_id": "y"},
                    {"chunk_id": "c"},
                    {"chunk_id": "z"},
                    {"chunk_id": "outside"},
                    {"chunk_id": "ignored"},
                ]
            },
            run_status="succeeded",
            latency_ms=1_200,
        )
        by_key = {score.metric_key: score for score in scores}
        self.assertEqual(by_key["rag.recall_at_6"].raw_value, 1.0)
        self.assertEqual(by_key["rag.mrr"].raw_value, 0.5)
        self.assertGreater(by_key["rag.ndcg_at_6"].raw_value, 0.5)
        self.assertEqual(by_key["system.success"].raw_value, 1.0)

    def test_agent_scores_required_and_unnecessary_steps_separately(self) -> None:
        scores = evaluate_observation(
            target_type="career_agent",
            expectation={
                "required_step_names": ["parse", "respond"],
                "allowed_step_names": ["parse", "retrieve", "respond"],
            },
            observation={
                "agent_steps": [
                    {"name": "parse"},
                    {"name": "unrelated"},
                    {"name": "respond"},
                ]
            },
            run_status="succeeded",
            latency_ms=800,
        )
        by_key = {score.metric_key: score for score in scores}
        self.assertEqual(by_key["agent.required_step_coverage"].raw_value, 1.0)
        self.assertAlmostEqual(by_key["agent.unnecessary_step_rate"].raw_value, 1 / 3)
        self.assertFalse(by_key["agent.unnecessary_step_rate"].passed)

    def test_resume_forbidden_claim_is_a_critical_deterministic_failure(self) -> None:
        scores = evaluate_observation(
            target_type="resume_optimizer",
            expectation={"forbidden_claims": ["负责千万级高并发系统", "精通 Rust"]},
            observation={"output_text": "新增描述：负责千万级高并发系统。"},
            run_status="succeeded",
            latency_ms=500,
        )
        by_key = {score.metric_key: score for score in scores}
        self.assertEqual(by_key["resume.forbidden_claim_match_rate"].raw_value, 0.5)
        self.assertFalse(by_key["resume.forbidden_claim_match_rate"].passed)


class EvaluationAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeEvaluationRepository()
        self.service = EvaluationService(self.repository)
        self.organization_id = uuid4()
        self.version_id = uuid4()

    def test_real_case_requires_traceable_source_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "数据来源引用"):
            self.service.add_case(
                organization_id=self.organization_id,
                dataset_version_id=self.version_id,
                case_key="rag-real-1",
                source_kind=EvaluationSourceKind.ANONYMIZED_REAL,
                source_ref_id=None,
                input_data={"query": "事务隔离"},
                expectation={"relevant_chunk_ids": ["chunk-1"]},
                metadata={},
            )

    def test_case_hash_is_stable_for_same_content(self) -> None:
        payload = dict(
            organization_id=self.organization_id,
            dataset_version_id=self.version_id,
            case_key="rag-real-1",
            source_kind=EvaluationSourceKind.ANONYMIZED_REAL,
            source_ref_id=str(uuid4()),
            input_data={"query": "事务隔离"},
            expectation={"relevant_chunk_ids": ["chunk-1"]},
            metadata={"label_status": "approved"},
        )
        self.service.add_case(**payload)
        self.service.add_case(**payload)
        self.assertEqual(self.repository.added[0]["content_hash"], self.repository.added[1]["content_hash"])
        self.assertIn("source_binding", self.repository.added[0]["metadata"])

    def test_freeze_requires_real_data_and_two_reviewers(self) -> None:
        self.repository.cases = [
            {
                "case_key": "rag-real-1",
                "source_kind": "anonymized_real",
                "content_hash": "a" * 64,
                "metadata_json": {
                    "label_status": "approved",
                    "anonymization_status": "verified",
                    "reviewer_ids": ["reviewer-a"],
                },
            }
        ]
        with self.assertRaisesRegex(ValueError, "至少需要两名"):
            self.service.freeze_dataset_version(
                organization_id=self.organization_id,
                dataset_version_id=self.version_id,
            )

    def test_freeze_accepts_reviewed_real_case_and_generates_version_hash(self) -> None:
        self.repository.cases = [
            {
                "case_key": "rag-real-1",
                "source_kind": "anonymized_real",
                "content_hash": "a" * 64,
                "metadata_json": {
                    "label_status": "approved",
                    "anonymization_status": "verified",
                    "reviewer_ids": ["reviewer-a", "reviewer-b"],
                },
            }
        ]
        result = self.service.freeze_dataset_version(
            organization_id=self.organization_id,
            dataset_version_id=self.version_id,
        )
        self.assertEqual(len(result["content_hash"]), 64)
        self.assertEqual(len(self.repository.frozen), 1)


class EvaluationApiTests(unittest.TestCase):
    def test_install_registers_evaluation_routes(self) -> None:
        app = FastAPI()
        install_evaluation_api(app)
        paths = {route.path for route in router.routes if hasattr(route, "path")}
        self.assertIn("/api/evaluations/overview", paths)
        self.assertIn("/api/evaluations/datasets", paths)
        self.assertIn("/api/evaluations/experiments/{experiment_id}/runs", paths)
        self.assertIn("/api/evaluations/experiments/{experiment_id}/execute-interview-rag", paths)
        self.assertIn("/api/evaluations/runtime-config/interview-rag", paths)


class InterviewRagRunnerTests(unittest.TestCase):
    def test_runner_records_actual_retrieval_observation_and_completes(self) -> None:
        organization_id = uuid4()
        experiment_id = uuid4()
        version_id = uuid4()
        case_id = uuid4()
        relevant_chunk_id = uuid4()
        repository = SimpleNamespace(
            get_experiment=lambda *_: {
                "id": experiment_id,
                "target_type": "interview_rag",
                "status": "draft",
                "dataset_version_id": version_id,
                "repetitions": 1,
                "config_json": {"final_limit": 6, "embedding_enabled": False},
            },
            list_cases=lambda *_: [{
                "id": case_id,
                "case_key": "rag-real-1",
                "input_json": {"query": "事务隔离"},
            }],
            list_run_keys=lambda *_: set(),
            complete_experiment=lambda *_: {"id": experiment_id, "status": "completed"},
        )
        recorded: list[dict] = []
        evaluation_service = SimpleNamespace(record_run=lambda **payload: recorded.append(payload))
        candidate = SimpleNamespace(
            chunk=SimpleNamespace(id=relevant_chunk_id, experience_id=uuid4()),
            lexical_score=0.8,
            semantic_score=None,
        )
        retrieval_service = SimpleNamespace(
            configuration_snapshot={
                "lexical_candidate_limit": 24,
                "semantic_candidate_limit": 24,
                "final_limit": 6,
                "reciprocal_rank_fusion_k": 60,
                "embedding_enabled": False,
                "embedding_model_id": None,
            },
            retrieve=lambda *_args, **_kwargs: SimpleNamespace(
                candidates=(candidate,),
                retrieval_mode="lexical",
                degraded_reason=None,
            ),
        )

        result = InterviewRagExperimentRunner(
            repository,
            evaluation_service,
            retrieval_service,
        ).run(organization_id, experiment_id)

        self.assertEqual(result["executed_run_count"], 1)
        self.assertEqual(result["failed_run_count"], 0)
        self.assertEqual(recorded[0]["status"].value, "succeeded")
        self.assertEqual(
            recorded[0]["observation"]["retrieved_items"][0]["chunk_id"],
            str(relevant_chunk_id),
        )

    def test_runner_rejects_configuration_drift_before_execution(self) -> None:
        repository = SimpleNamespace(get_experiment=lambda *_: {
            "target_type": "interview_rag",
            "status": "draft",
            "config_json": {"final_limit": 5},
        })
        retrieval_service = SimpleNamespace(configuration_snapshot={
            "final_limit": 6,
            "lexical_candidate_limit": 24,
            "semantic_candidate_limit": 24,
            "reciprocal_rank_fusion_k": 60,
            "embedding_enabled": False,
            "embedding_model_id": None,
        })
        runner = InterviewRagExperimentRunner(
            repository,
            SimpleNamespace(),
            retrieval_service,
        )
        with self.assertRaisesRegex(ValueError, "配置不一致"):
            runner.run(uuid4(), uuid4())


if __name__ == "__main__":
    unittest.main()
