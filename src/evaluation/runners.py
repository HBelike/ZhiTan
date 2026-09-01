"""把业务模块的真实执行转换为可复算 Evaluation Observation。"""

from __future__ import annotations

from time import perf_counter
from typing import Any
from uuid import UUID

from src.career_assistant.interview_library.retrieval import InterviewRetrievalService
from src.evaluation.contracts import EvaluationRunStatus
from src.evaluation.repository import EvaluationRepository
from src.evaluation.service import EvaluationService


class InterviewRagExperimentRunner:
    """执行冻结案例中的真实面经检索，并持久化候选证据与确定性得分。"""

    def __init__(
        self,
        repository: EvaluationRepository,
        service: EvaluationService,
        retrieval_service: InterviewRetrievalService,
    ) -> None:
        self._repository = repository
        self._service = service
        self._retrieval_service = retrieval_service

    def run(self, organization_id: UUID, experiment_id: UUID) -> dict[str, Any]:
        experiment = self._repository.get_experiment(organization_id, experiment_id)
        if experiment["target_type"] != "interview_rag":
            raise ValueError("该 Runner 只支持 interview_rag 实验")
        if experiment["status"] == "completed":
            raise ValueError("实验已经完成，冻结结果不能重复执行")

        self._verify_configuration(experiment.get("config_json") or {})
        cases = self._repository.list_cases(
            organization_id,
            experiment["dataset_version_id"],
        )
        existing = self._repository.list_run_keys(organization_id, experiment_id)
        executed = 0
        skipped = 0
        failed = 0

        for case in cases:
            for repetition in range(1, int(experiment["repetitions"]) + 1):
                run_key = (case["id"], repetition)
                if run_key in existing:
                    skipped += 1
                    continue
                succeeded = self._run_case(
                    organization_id=organization_id,
                    experiment_id=experiment_id,
                    case=case,
                    repetition=repetition,
                    experiment_config=experiment.get("config_json") or {},
                )
                executed += 1
                if not succeeded:
                    failed += 1

        completed = self._repository.complete_experiment(organization_id, experiment_id)
        return {
            "experiment": completed,
            "executed_run_count": executed,
            "skipped_run_count": skipped,
            "failed_run_count": failed,
        }

    def _run_case(
        self,
        *,
        organization_id: UUID,
        experiment_id: UUID,
        case: dict[str, Any],
        repetition: int,
        experiment_config: dict[str, Any],
    ) -> bool:
        started_at = perf_counter()
        status = EvaluationRunStatus.SUCCEEDED
        output: dict[str, Any] = {}
        observation: dict[str, Any] = {"retrieved_items": []}
        error_code: str | None = None
        try:
            query = self._read_query(case)
            experience_ids = self._read_experience_ids(case)
            requested_limit = experiment_config.get("final_limit")
            result = self._retrieval_service.retrieve(
                organization_id,
                query,
                limit=int(requested_limit) if requested_limit is not None else None,
                experience_ids=experience_ids,
            )
            candidates = [
                {
                    "chunk_id": str(candidate.chunk.id),
                    "experience_id": str(candidate.chunk.experience_id),
                    "rank": rank,
                    "lexical_score": candidate.lexical_score,
                    "semantic_score": candidate.semantic_score,
                }
                for rank, candidate in enumerate(result.candidates, start=1)
            ]
            observation = {"retrieved_items": candidates, "structure_valid": True}
            output = {
                "retrieval_mode": result.retrieval_mode,
                "degraded_reason": result.degraded_reason,
                "result_count": len(candidates),
                "configuration": self._retrieval_service.configuration_snapshot,
            }
        except (LookupError, TypeError, ValueError) as exc:
            status = EvaluationRunStatus.FAILED
            error_code = type(exc).__name__
            output = {"failure_stage": "case_validation_or_retrieval"}
            observation = {"retrieved_items": [], "structure_valid": False}

        latency_ms = max(0, round((perf_counter() - started_at) * 1_000))
        self._service.record_run(
            organization_id=organization_id,
            experiment_id=experiment_id,
            case_id=case["id"],
            repetition=repetition,
            status=status,
            output=output,
            observation=observation,
            trace_id=None,
            latency_ms=latency_ms,
            input_tokens=None,
            output_tokens=None,
            error_code=error_code,
        )
        return status is EvaluationRunStatus.SUCCEEDED

    def _verify_configuration(self, expected: dict[str, Any]) -> None:
        actual = self._retrieval_service.configuration_snapshot
        comparable_keys = {
            "lexical_candidate_limit",
            "semantic_candidate_limit",
            "final_limit",
            "reciprocal_rank_fusion_k",
            "embedding_enabled",
            "embedding_model_id",
        }
        mismatches = [
            key
            for key in comparable_keys
            if key in expected and expected[key] != actual[key]
        ]
        if mismatches:
            details = "、".join(sorted(mismatches))
            raise ValueError(f"实验配置与当前 RAG 运行配置不一致：{details}")

    @staticmethod
    def _read_query(case: dict[str, Any]) -> str:
        value = case.get("input_json", {}).get("query")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"案例 {case.get('case_key', case.get('id'))} 缺少非空 query")
        return value.strip()

    @staticmethod
    def _read_experience_ids(case: dict[str, Any]) -> tuple[UUID, ...]:
        values = case.get("input_json", {}).get("experience_ids", [])
        if not isinstance(values, list):
            raise ValueError("experience_ids 必须是数组")
        return tuple(UUID(str(value)) for value in values)
