"""评测中心 PostgreSQL Repository。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from src.career_assistant.persistence.database import CareerDatabase
from src.evaluation.contracts import MetricScore


class EvaluationRepository:
    """持久化版本化数据集、实验、运行事实和逐项得分。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def create_dataset(
        self,
        *,
        organization_id: UUID,
        created_by: UUID,
        name: str,
        target_type: str,
        description: str,
    ) -> dict[str, Any]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO evaluation.datasets (
                        id, organization_id, name, target_type, description, created_by
                    ) VALUES (
                        :id, :organization_id, :name, :target_type, :description, :created_by
                    )
                    RETURNING id, organization_id, name, target_type, description,
                              status, created_at, updated_at
                    """
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "created_by": created_by,
                    "name": name,
                    "target_type": target_type,
                    "description": description,
                },
            ).mappings().one()
        return dict(row)

    def list_datasets(self, organization_id: UUID) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT dataset.id, dataset.name, dataset.target_type, dataset.description,
                           dataset.status, dataset.created_at, dataset.updated_at,
                           COUNT(DISTINCT version.id) AS version_count,
                           COALESCE(SUM(version.case_count), 0) AS case_count
                    FROM evaluation.datasets AS dataset
                    LEFT JOIN evaluation.dataset_versions AS version
                      ON version.dataset_id = dataset.id
                    WHERE dataset.organization_id = :organization_id
                    GROUP BY dataset.id
                    ORDER BY dataset.updated_at DESC, dataset.name ASC
                    """
                ),
                {"organization_id": organization_id},
            ).mappings().all()
            items = [dict(row) for row in rows]
            for item in items:
                version_rows = connection.execute(
                    text(
                        """
                        SELECT id, version_label, status, case_count, content_hash,
                               created_at, frozen_at
                        FROM evaluation.dataset_versions
                        WHERE dataset_id = :dataset_id
                        ORDER BY created_at DESC
                        """
                    ),
                    {"dataset_id": item["id"]},
                ).mappings().all()
                item["versions"] = [dict(version) for version in version_rows]
        return items

    def create_dataset_version(
        self,
        *,
        organization_id: UUID,
        dataset_id: UUID,
        created_by: UUID,
        version_label: str,
    ) -> dict[str, Any]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO evaluation.dataset_versions (
                        id, dataset_id, version_label, created_by
                    )
                    SELECT :id, dataset.id, :version_label, :created_by
                    FROM evaluation.datasets AS dataset
                    WHERE dataset.id = :dataset_id
                      AND dataset.organization_id = :organization_id
                      AND dataset.status = 'active'
                    RETURNING id, dataset_id, version_label, status, case_count,
                              content_hash, created_at, frozen_at
                    """
                ),
                {
                    "id": uuid4(),
                    "dataset_id": dataset_id,
                    "organization_id": organization_id,
                    "created_by": created_by,
                    "version_label": version_label,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("评测数据集不存在或已归档")
        return dict(row)

    def add_case(
        self,
        *,
        organization_id: UUID,
        dataset_version_id: UUID,
        case_key: str,
        source_kind: str,
        source_ref_id: str | None,
        input_data: Mapping[str, Any],
        expectation: Mapping[str, Any],
        metadata: Mapping[str, Any],
        content_hash: str,
    ) -> dict[str, Any]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO evaluation.cases (
                        id, dataset_version_id, case_key, source_kind, source_ref_id,
                        input_json, expectation_json, metadata_json, content_hash
                    )
                    SELECT :id, version.id, :case_key, :source_kind, :source_ref_id,
                           CAST(:input_json AS jsonb), CAST(:expectation_json AS jsonb),
                           CAST(:metadata_json AS jsonb), :content_hash
                    FROM evaluation.dataset_versions AS version
                    INNER JOIN evaluation.datasets AS dataset ON dataset.id = version.dataset_id
                    WHERE version.id = :dataset_version_id
                      AND version.status = 'draft'
                      AND dataset.organization_id = :organization_id
                    RETURNING id, dataset_version_id, case_key, source_kind, source_ref_id,
                              input_json, expectation_json, metadata_json, content_hash, created_at
                    """
                ),
                {
                    "id": uuid4(),
                    "dataset_version_id": dataset_version_id,
                    "organization_id": organization_id,
                    "case_key": case_key,
                    "source_kind": source_kind,
                    "source_ref_id": source_ref_id,
                    "input_json": _serialize_json(input_data),
                    "expectation_json": _serialize_json(expectation),
                    "metadata_json": _serialize_json(metadata),
                    "content_hash": content_hash,
                },
            ).mappings().one_or_none()
            if row is None:
                raise LookupError("评测数据版本不存在或已冻结")
            connection.execute(
                text(
                    """
                    UPDATE evaluation.dataset_versions
                    SET case_count = (
                        SELECT COUNT(*) FROM evaluation.cases
                        WHERE dataset_version_id = :dataset_version_id
                    )
                    WHERE id = :dataset_version_id
                    """
                ),
                {"dataset_version_id": dataset_version_id},
            )
        return _json_row(row)

    def list_cases(self, organization_id: UUID, dataset_version_id: UUID) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT case_item.id, case_item.case_key, case_item.source_kind,
                           case_item.source_ref_id, case_item.input_json,
                           case_item.expectation_json, case_item.metadata_json,
                           case_item.content_hash, case_item.created_at,
                           dataset.target_type
                    FROM evaluation.cases AS case_item
                    INNER JOIN evaluation.dataset_versions AS version
                      ON version.id = case_item.dataset_version_id
                    INNER JOIN evaluation.datasets AS dataset ON dataset.id = version.dataset_id
                    WHERE case_item.dataset_version_id = :dataset_version_id
                      AND dataset.organization_id = :organization_id
                    ORDER BY case_item.case_key ASC
                    """
                ),
                {
                    "dataset_version_id": dataset_version_id,
                    "organization_id": organization_id,
                },
            ).mappings().all()
        return [_json_row(row) for row in rows]

    def get_source_reference_snapshot(
        self,
        organization_id: UUID,
        dataset_version_id: UUID,
        source_ref_id: str,
    ) -> dict[str, Any] | None:
        """校验多态业务来源，并返回不含正文与凭证的稳定来源快照。"""

        try:
            reference_id = UUID(source_ref_id)
        except ValueError as exc:
            raise ValueError("真实数据来源引用必须是业务记录 UUID") from exc

        with self._database.transaction() as connection:
            target_type = connection.execute(
                text(
                    """
                    SELECT dataset.target_type
                    FROM evaluation.dataset_versions AS version
                    INNER JOIN evaluation.datasets AS dataset ON dataset.id = version.dataset_id
                    WHERE version.id = :dataset_version_id
                      AND dataset.organization_id = :organization_id
                    """
                ),
                {
                    "dataset_version_id": dataset_version_id,
                    "organization_id": organization_id,
                },
            ).scalar_one_or_none()
            if target_type is None:
                raise LookupError("评测数据版本不存在")

            queries = {
                "interview_rag": """
                    SELECT jsonb_build_object(
                        'record_id', feedback.id,
                        'target_type', 'interview_rag',
                        'conversation_id', feedback.conversation_id,
                        'experience_id', feedback.experience_id,
                        'retrieval_strategy', feedback.retrieval_strategy,
                        'rank_position', feedback.rank_position,
                        'useful', feedback.useful,
                        'created_at', feedback.created_at
                    )
                    FROM career_assistant.interview_retrieval_feedback AS feedback
                    WHERE feedback.id = :reference_id
                      AND feedback.organization_id = :organization_id
                """,
                "career_agent": """
                    SELECT jsonb_build_object(
                        'record_id', turn_item.id,
                        'target_type', 'career_agent',
                        'conversation_id', turn_item.conversation_id,
                        'status', turn_item.status,
                        'started_at', turn_item.started_at,
                        'completed_at', turn_item.completed_at,
                        'created_at', turn_item.created_at
                    )
                    FROM career_assistant.agent_turns AS turn_item
                    INNER JOIN career_assistant.conversations AS conversation
                      ON conversation.id = turn_item.conversation_id
                    WHERE turn_item.id = :reference_id
                      AND conversation.organization_id = :organization_id
                """,
                "resume_optimizer": """
                    SELECT jsonb_build_object(
                        'record_id', resume.id,
                        'target_type', 'resume_optimizer',
                        'owner_id', resume.owner_id,
                        'job_title', resume.job_title,
                        'model_provider', resume.provider_key,
                        'model_id', resume.model_id,
                        'created_at', resume.created_at
                    )
                    FROM career_assistant.resume_optimization_records AS resume
                    WHERE resume.id = :reference_id
                      AND resume.organization_id = :organization_id
                """,
            }
            query = queries.get(str(target_type))
            if query is None:
                raise ValueError(f"暂不支持目标类型 {target_type} 的真实来源绑定")
            snapshot = connection.execute(
                text(query),
                {"reference_id": reference_id, "organization_id": organization_id},
            ).scalar_one_or_none()
        return dict(snapshot) if snapshot is not None else None

    def freeze_dataset_version(
        self,
        *,
        organization_id: UUID,
        dataset_version_id: UUID,
        content_hash: str,
    ) -> dict[str, Any]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE evaluation.dataset_versions AS version
                    SET status = 'frozen', content_hash = :content_hash, frozen_at = NOW(),
                        case_count = (
                            SELECT COUNT(*) FROM evaluation.cases
                            WHERE dataset_version_id = version.id
                        )
                    FROM evaluation.datasets AS dataset
                    WHERE version.id = :dataset_version_id
                      AND version.dataset_id = dataset.id
                      AND dataset.organization_id = :organization_id
                      AND version.status = 'draft'
                    RETURNING version.id, version.dataset_id, version.version_label,
                              version.status, version.case_count, version.content_hash,
                              version.created_at, version.frozen_at
                    """
                ),
                {
                    "dataset_version_id": dataset_version_id,
                    "organization_id": organization_id,
                    "content_hash": content_hash,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("评测数据版本不存在或已经冻结")
        return dict(row)

    def create_experiment(
        self,
        *,
        organization_id: UUID,
        created_by: UUID,
        name: str,
        target_type: str,
        dataset_version_id: UUID,
        baseline_experiment_id: UUID | None,
        git_commit: str | None,
        prompt_version: str | None,
        model_provider: str | None,
        model_id: str | None,
        retrieval_strategy: str | None,
        config: Mapping[str, Any],
        config_hash: str,
        repetitions: int,
    ) -> dict[str, Any]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO evaluation.experiments (
                        id, organization_id, name, target_type, dataset_version_id,
                        baseline_experiment_id, git_commit, prompt_version,
                        model_provider, model_id, retrieval_strategy, config_json,
                        config_hash, repetitions, created_by
                    )
                    SELECT :id, :organization_id, :name, :target_type, version.id,
                           :baseline_experiment_id, :git_commit, :prompt_version,
                           :model_provider, :model_id, :retrieval_strategy,
                           CAST(:config_json AS jsonb), :config_hash, :repetitions, :created_by
                    FROM evaluation.dataset_versions AS version
                    INNER JOIN evaluation.datasets AS dataset ON dataset.id = version.dataset_id
                    WHERE version.id = :dataset_version_id
                      AND version.status = 'frozen'
                      AND dataset.organization_id = :organization_id
                      AND dataset.target_type = :target_type
                      AND (
                          :baseline_experiment_id IS NULL OR EXISTS (
                              SELECT 1 FROM evaluation.experiments AS baseline
                              WHERE baseline.id = :baseline_experiment_id
                                AND baseline.organization_id = :organization_id
                                AND baseline.target_type = :target_type
                          )
                      )
                    RETURNING id, name, target_type, dataset_version_id,
                              baseline_experiment_id, status, git_commit, prompt_version,
                              model_provider, model_id, retrieval_strategy, config_json,
                              config_hash, repetitions, created_at, started_at, completed_at
                    """
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "created_by": created_by,
                    "name": name,
                    "target_type": target_type,
                    "dataset_version_id": dataset_version_id,
                    "baseline_experiment_id": baseline_experiment_id,
                    "git_commit": git_commit,
                    "prompt_version": prompt_version,
                    "model_provider": model_provider,
                    "model_id": model_id,
                    "retrieval_strategy": retrieval_strategy,
                    "config_json": _serialize_json(config),
                    "config_hash": config_hash,
                    "repetitions": repetitions,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("只能基于同组织、同目标的冻结数据集创建实验")
        return _json_row(row)

    def get_run_context(
        self,
        organization_id: UUID,
        experiment_id: UUID,
        case_id: UUID,
    ) -> dict[str, Any]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experiment.id AS experiment_id, experiment.target_type,
                           experiment.repetitions, experiment.status AS experiment_status,
                           case_item.id AS case_id, case_item.expectation_json,
                           case_item.input_json, case_item.metadata_json
                    FROM evaluation.experiments AS experiment
                    INNER JOIN evaluation.cases AS case_item ON case_item.id = :case_id
                    WHERE experiment.id = :experiment_id
                      AND experiment.organization_id = :organization_id
                      AND case_item.dataset_version_id = experiment.dataset_version_id
                      AND experiment.status IN ('draft', 'running')
                    """
                ),
                {
                    "organization_id": organization_id,
                    "experiment_id": experiment_id,
                    "case_id": case_id,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("实验或评测案例不存在，或者实验已结束")
        return _json_row(row)

    def create_run_with_scores(
        self,
        *,
        organization_id: UUID,
        experiment_id: UUID,
        case_id: UUID,
        repetition: int,
        status: str,
        output: Mapping[str, Any],
        observation: Mapping[str, Any],
        trace_id: str | None,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        error_code: str | None,
        scores: Sequence[MetricScore],
    ) -> dict[str, Any]:
        run_id = uuid4()
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO evaluation.runs (
                        id, experiment_id, case_id, repetition, status,
                        output_json, observation_json, trace_id, latency_ms,
                        input_tokens, output_tokens, error_code
                    )
                    SELECT :id, experiment.id, :case_id, :repetition, :status,
                           CAST(:output_json AS jsonb), CAST(:observation_json AS jsonb),
                           :trace_id, :latency_ms, :input_tokens, :output_tokens, :error_code
                    FROM evaluation.experiments AS experiment
                    WHERE experiment.id = :experiment_id
                      AND experiment.organization_id = :organization_id
                      AND :repetition <= experiment.repetitions
                    RETURNING id, experiment_id, case_id, repetition, status,
                              output_json, observation_json, trace_id, latency_ms,
                              input_tokens, output_tokens, error_code, created_at, completed_at
                    """
                ),
                {
                    "id": run_id,
                    "experiment_id": experiment_id,
                    "organization_id": organization_id,
                    "case_id": case_id,
                    "repetition": repetition,
                    "status": status,
                    "output_json": _serialize_json(output),
                    "observation_json": _serialize_json(observation),
                    "trace_id": trace_id,
                    "latency_ms": latency_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "error_code": error_code,
                },
            ).mappings().one_or_none()
            if row is None:
                raise LookupError("实验不存在或重复次数超出实验配置")
            for score in scores:
                connection.execute(
                    text(
                        """
                        INSERT INTO evaluation.scores (
                            id, run_id, metric_key, metric_version, raw_value,
                            passed, evaluator_kind, evidence_json
                        ) VALUES (
                            :id, :run_id, :metric_key, :metric_version, :raw_value,
                            :passed, :evaluator_kind, CAST(:evidence_json AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "run_id": run_id,
                        "metric_key": score.metric_key,
                        "metric_version": score.metric_version,
                        "raw_value": score.raw_value,
                        "passed": score.passed,
                        "evaluator_kind": score.evaluator_kind,
                        "evidence_json": _serialize_json(score.evidence),
                    },
                )
            connection.execute(
                text(
                    """
                    UPDATE evaluation.experiments
                    SET status = 'running', started_at = COALESCE(started_at, NOW())
                    WHERE id = :experiment_id AND status = 'draft'
                    """
                ),
                {"experiment_id": experiment_id},
            )
        item = _json_row(row)
        item["scores"] = [
            {
                "metric_key": score.metric_key,
                "metric_version": score.metric_version,
                "raw_value": score.raw_value,
                "passed": score.passed,
                "evaluator_kind": score.evaluator_kind,
                "evidence": dict(score.evidence),
            }
            for score in scores
        ]
        return item

    def complete_experiment(self, organization_id: UUID, experiment_id: UUID) -> dict[str, Any]:
        with self._database.transaction() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT version.case_count * experiment.repetitions AS expected_runs,
                           COUNT(run.id) AS actual_runs
                    FROM evaluation.experiments AS experiment
                    INNER JOIN evaluation.dataset_versions AS version
                      ON version.id = experiment.dataset_version_id
                    LEFT JOIN evaluation.runs AS run ON run.experiment_id = experiment.id
                    WHERE experiment.id = :experiment_id
                      AND experiment.organization_id = :organization_id
                      AND experiment.status IN ('draft', 'running')
                    GROUP BY version.case_count, experiment.repetitions
                    """
                ),
                {"experiment_id": experiment_id, "organization_id": organization_id},
            ).mappings().one_or_none()
            if counts is None:
                raise LookupError("实验不存在或已经结束")
            if counts["actual_runs"] != counts["expected_runs"]:
                raise ValueError(
                    f"实验尚未收齐运行：{counts['actual_runs']}/{counts['expected_runs']}"
                )
            row = connection.execute(
                text(
                    """
                    UPDATE evaluation.experiments
                    SET status = 'completed', completed_at = NOW(),
                        started_at = COALESCE(started_at, NOW())
                    WHERE id = :experiment_id AND organization_id = :organization_id
                    RETURNING id, name, target_type, dataset_version_id,
                              baseline_experiment_id, status, repetitions,
                              created_at, started_at, completed_at
                    """
                ),
                {"experiment_id": experiment_id, "organization_id": organization_id},
            ).mappings().one()
        return dict(row)

    def list_metric_definitions(self) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT metric_key, version, display_name, target_type, evaluator_kind,
                           direction, unit, aggregation, formula, threshold,
                           critical, description
                    FROM evaluation.metric_definitions
                    WHERE active = TRUE
                    ORDER BY critical DESC, target_type ASC, metric_key ASC
                    """
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    def list_experiments(self, organization_id: UUID, *, limit: int = 30) -> list[dict[str, Any]]:
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT experiment.id, experiment.name, experiment.target_type,
                           experiment.status, experiment.dataset_version_id,
                           version.version_label AS dataset_version,
                           experiment.baseline_experiment_id, experiment.git_commit,
                           experiment.prompt_version, experiment.model_provider,
                           experiment.model_id, experiment.retrieval_strategy,
                           experiment.repetitions, experiment.created_at,
                           experiment.started_at, experiment.completed_at,
                           COUNT(DISTINCT run.id) AS run_count,
                           COUNT(score.id) FILTER (WHERE score.passed = FALSE) AS failed_score_count,
                           COUNT(score.id) FILTER (
                               WHERE score.passed = FALSE AND definition.critical = TRUE
                           ) AS critical_failure_count
                    FROM evaluation.experiments AS experiment
                    INNER JOIN evaluation.dataset_versions AS version
                      ON version.id = experiment.dataset_version_id
                    LEFT JOIN evaluation.runs AS run ON run.experiment_id = experiment.id
                    LEFT JOIN evaluation.scores AS score ON score.run_id = run.id
                    LEFT JOIN evaluation.metric_definitions AS definition
                      ON definition.metric_key = score.metric_key
                     AND definition.version = score.metric_version
                    WHERE experiment.organization_id = :organization_id
                    GROUP BY experiment.id, version.version_label
                    ORDER BY experiment.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"organization_id": organization_id, "limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]

    def get_experiment(self, organization_id: UUID, experiment_id: UUID) -> dict[str, Any]:
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experiment.id, experiment.name, experiment.target_type,
                           experiment.status, experiment.dataset_version_id,
                           version.version_label AS dataset_version,
                           experiment.baseline_experiment_id, experiment.git_commit,
                           experiment.prompt_version, experiment.model_provider,
                           experiment.model_id, experiment.retrieval_strategy,
                           experiment.config_json, experiment.config_hash,
                           experiment.repetitions, experiment.created_at,
                           experiment.started_at, experiment.completed_at
                    FROM evaluation.experiments AS experiment
                    INNER JOIN evaluation.dataset_versions AS version
                      ON version.id = experiment.dataset_version_id
                    WHERE experiment.id = :experiment_id
                      AND experiment.organization_id = :organization_id
                    """
                ),
                {"experiment_id": experiment_id, "organization_id": organization_id},
            ).mappings().one_or_none()
            if row is None:
                raise LookupError("评测实验不存在")
            metric_rows = connection.execute(
                text(
                    """
                    SELECT score.metric_key, definition.display_name, definition.unit,
                           definition.direction, definition.aggregation, definition.threshold,
                           definition.critical, AVG(score.raw_value) AS mean_value,
                           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY score.raw_value) AS p95_value,
                           COUNT(*) AS sample_count,
                           COUNT(*) FILTER (WHERE score.passed) AS passed_count
                    FROM evaluation.scores AS score
                    INNER JOIN evaluation.runs AS run ON run.id = score.run_id
                    INNER JOIN evaluation.metric_definitions AS definition
                      ON definition.metric_key = score.metric_key
                     AND definition.version = score.metric_version
                    WHERE run.experiment_id = :experiment_id
                    GROUP BY score.metric_key, definition.display_name, definition.unit,
                             definition.direction, definition.aggregation,
                             definition.threshold, definition.critical
                    ORDER BY definition.critical DESC, score.metric_key ASC
                    """
                ),
                {"experiment_id": experiment_id},
            ).mappings().all()
            run_rows = connection.execute(
                text(
                    """
                    SELECT run.id, run.case_id, case_item.case_key, case_item.source_kind,
                           run.repetition, run.status, run.trace_id, run.latency_ms,
                           run.input_tokens, run.output_tokens, run.error_code,
                           run.created_at, run.completed_at,
                           COUNT(score.id) FILTER (WHERE score.passed = FALSE) AS failed_metric_count
                    FROM evaluation.runs AS run
                    INNER JOIN evaluation.cases AS case_item ON case_item.id = run.case_id
                    LEFT JOIN evaluation.scores AS score ON score.run_id = run.id
                    WHERE run.experiment_id = :experiment_id
                    GROUP BY run.id, case_item.case_key, case_item.source_kind
                    ORDER BY case_item.case_key, run.repetition
                    LIMIT 200
                    """
                ),
                {"experiment_id": experiment_id},
            ).mappings().all()
        item = _json_row(row)
        item["metrics"] = [dict(metric) for metric in metric_rows]
        item["runs"] = [dict(run) for run in run_rows]
        return item

    def list_run_keys(
        self,
        organization_id: UUID,
        experiment_id: UUID,
    ) -> set[tuple[UUID, int]]:
        """读取已落盘的案例×重复序号，供可恢复 Runner 跳过完成项。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT run.case_id, run.repetition
                    FROM evaluation.runs AS run
                    INNER JOIN evaluation.experiments AS experiment
                      ON experiment.id = run.experiment_id
                    WHERE run.experiment_id = :experiment_id
                      AND experiment.organization_id = :organization_id
                    """
                ),
                {"organization_id": organization_id, "experiment_id": experiment_id},
            ).mappings().all()
        return {(row["case_id"], int(row["repetition"])) for row in rows}

    def get_overview(self, organization_id: UUID) -> dict[str, Any]:
        with self._database.transaction() as connection:
            summary = connection.execute(
                text(
                    """
                    SELECT
                        COUNT(DISTINCT dataset.id) AS dataset_count,
                        COUNT(DISTINCT version.id) FILTER (WHERE version.status = 'frozen')
                            AS frozen_version_count,
                        COUNT(DISTINCT case_item.id) AS case_count,
                        COUNT(DISTINCT case_item.id) FILTER (
                            WHERE case_item.source_kind IN ('anonymized_real', 'failure_replay')
                        ) AS real_case_count,
                        COUNT(DISTINCT experiment.id) AS experiment_count,
                        COUNT(DISTINCT experiment.id) FILTER (
                            WHERE experiment.status = 'completed'
                        ) AS completed_experiment_count
                    FROM evaluation.datasets AS dataset
                    LEFT JOIN evaluation.dataset_versions AS version
                      ON version.dataset_id = dataset.id
                    LEFT JOIN evaluation.cases AS case_item
                      ON case_item.dataset_version_id = version.id
                    LEFT JOIN evaluation.experiments AS experiment
                      ON experiment.organization_id = dataset.organization_id
                    WHERE dataset.organization_id = :organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).mappings().one()
            source_rows = connection.execute(
                text(
                    """
                    SELECT case_item.source_kind, COUNT(*) AS case_count
                    FROM evaluation.cases AS case_item
                    INNER JOIN evaluation.dataset_versions AS version
                      ON version.id = case_item.dataset_version_id
                    INNER JOIN evaluation.datasets AS dataset ON dataset.id = version.dataset_id
                    WHERE dataset.organization_id = :organization_id
                    GROUP BY case_item.source_kind
                    ORDER BY case_item.source_kind
                    """
                ),
                {"organization_id": organization_id},
            ).mappings().all()
        return {
            "summary": dict(summary),
            "source_distribution": [dict(row) for row in source_rows],
            "experiments": self.list_experiments(organization_id, limit=8),
        }


def _serialize_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_row(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in ("input_json", "expectation_json", "metadata_json", "config_json", "output_json", "observation_json"):
        if key in item and isinstance(item[key], str):
            item[key] = json.loads(item[key])
    return item
