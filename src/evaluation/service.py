"""评测数据准入、版本冻结、实验和确定性评分服务。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from src.evaluation.contracts import (
    EvaluationRunStatus,
    EvaluationSourceKind,
    EvaluationTargetType,
)
from src.evaluation.metrics import evaluate_observation
from src.evaluation.repository import EvaluationRepository


class EvaluationService:
    """确保真实数据准入和实验复现信息在写入前完成校验。"""

    def __init__(self, repository: EvaluationRepository) -> None:
        self._repository = repository

    def create_dataset(
        self,
        *,
        organization_id: UUID,
        created_by: UUID,
        name: str,
        target_type: EvaluationTargetType,
        description: str,
    ) -> dict[str, Any]:
        return self._repository.create_dataset(
            organization_id=organization_id,
            created_by=created_by,
            name=_text(name, "数据集名称", 120),
            target_type=target_type.value,
            description=_text(description, "数据集说明", 1_000, allow_empty=True),
        )

    def create_dataset_version(
        self,
        *,
        organization_id: UUID,
        dataset_id: UUID,
        created_by: UUID,
        version_label: str,
    ) -> dict[str, Any]:
        label = _text(version_label, "版本标签", 60)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}", label):
            raise ValueError("版本标签只能包含字母、数字、点、下划线和连字符")
        return self._repository.create_dataset_version(
            organization_id=organization_id,
            dataset_id=dataset_id,
            created_by=created_by,
            version_label=label,
        )

    def add_case(
        self,
        *,
        organization_id: UUID,
        dataset_version_id: UUID,
        case_key: str,
        source_kind: EvaluationSourceKind,
        source_ref_id: str | None,
        input_data: Mapping[str, Any],
        expectation: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_case_key = _text(case_key, "案例编号", 100)
        normalized_source_ref = (
            _text(source_ref_id, "数据来源引用", 200) if source_ref_id is not None else None
        )
        if source_kind in {
            EvaluationSourceKind.ANONYMIZED_REAL,
            EvaluationSourceKind.FAILURE_REPLAY,
        } and not normalized_source_ref:
            raise ValueError("真实或失败回放案例必须提供可追溯的数据来源引用")
        input_copy = _json_object(input_data, "案例输入")
        expectation_copy = _json_object(expectation, "案例期望")
        metadata_copy = _json_object(metadata, "案例元数据")
        if source_kind in {
            EvaluationSourceKind.ANONYMIZED_REAL,
            EvaluationSourceKind.FAILURE_REPLAY,
        }:
            source_snapshot = self._repository.get_source_reference_snapshot(
                organization_id,
                dataset_version_id,
                normalized_source_ref or "",
            )
            if source_snapshot is None:
                raise ValueError("数据来源引用未在当前组织的业务记录中命中")
            metadata_copy["source_binding"] = {
                "target_type": source_snapshot["target_type"],
                "record_id": str(source_snapshot["record_id"]),
                "record_hash": _stable_hash(source_snapshot),
            }
        content_hash = _stable_hash(
            {
                "case_key": normalized_case_key,
                "source_kind": source_kind.value,
                "source_ref_id": normalized_source_ref,
                "input": input_copy,
                "expectation": expectation_copy,
                "metadata": metadata_copy,
            }
        )
        return self._repository.add_case(
            organization_id=organization_id,
            dataset_version_id=dataset_version_id,
            case_key=normalized_case_key,
            source_kind=source_kind.value,
            source_ref_id=normalized_source_ref,
            input_data=input_copy,
            expectation=expectation_copy,
            metadata=metadata_copy,
            content_hash=content_hash,
        )

    def freeze_dataset_version(
        self,
        *,
        organization_id: UUID,
        dataset_version_id: UUID,
    ) -> dict[str, Any]:
        cases = self._repository.list_cases(organization_id, dataset_version_id)
        if not cases:
            raise ValueError("空数据集不能冻结")
        real_count = 0
        for item in cases:
            metadata = item["metadata_json"]
            reviewers = metadata.get("reviewer_ids")
            if not isinstance(reviewers, list) or not any(str(value).strip() for value in reviewers):
                raise ValueError(f"案例 {item['case_key']} 缺少 reviewer_ids")
            if metadata.get("label_status") != "approved":
                raise ValueError(f"案例 {item['case_key']} 尚未通过标注审核")
            if item["source_kind"] in {
                EvaluationSourceKind.ANONYMIZED_REAL.value,
                EvaluationSourceKind.FAILURE_REPLAY.value,
            }:
                real_count += 1
                if metadata.get("anonymization_status") != "verified":
                    raise ValueError(f"真实案例 {item['case_key']} 尚未完成脱敏确认")
                distinct_reviewers = {str(value).strip() for value in reviewers if str(value).strip()}
                if len(distinct_reviewers) < 2:
                    raise ValueError(f"真实案例 {item['case_key']} 至少需要两名标注复核人")
        if real_count == 0:
            raise ValueError("主评测版本必须至少包含一条真实脱敏或历史失败案例")
        version_hash = _stable_hash([item["content_hash"] for item in cases])
        return self._repository.freeze_dataset_version(
            organization_id=organization_id,
            dataset_version_id=dataset_version_id,
            content_hash=version_hash,
        )

    def create_experiment(
        self,
        *,
        organization_id: UUID,
        created_by: UUID,
        name: str,
        target_type: EvaluationTargetType,
        dataset_version_id: UUID,
        baseline_experiment_id: UUID | None,
        git_commit: str | None,
        prompt_version: str | None,
        model_provider: str | None,
        model_id: str | None,
        retrieval_strategy: str | None,
        config: Mapping[str, Any],
        repetitions: int,
    ) -> dict[str, Any]:
        if not 1 <= repetitions <= 20:
            raise ValueError("重复运行次数必须在 1 到 20 之间")
        normalized_config = _json_object(config, "实验配置")
        identity = {
            "target_type": target_type.value,
            "dataset_version_id": str(dataset_version_id),
            "git_commit": _optional_text(git_commit, "Git Commit", 64),
            "prompt_version": _optional_text(prompt_version, "Prompt 版本", 80),
            "model_provider": _optional_text(model_provider, "模型 Provider", 80),
            "model_id": _optional_text(model_id, "模型 ID", 200),
            "retrieval_strategy": _optional_text(retrieval_strategy, "检索策略", 100),
            "config": normalized_config,
            "repetitions": repetitions,
        }
        return self._repository.create_experiment(
            organization_id=organization_id,
            created_by=created_by,
            name=_text(name, "实验名称", 160),
            target_type=target_type.value,
            dataset_version_id=dataset_version_id,
            baseline_experiment_id=baseline_experiment_id,
            git_commit=identity["git_commit"],
            prompt_version=identity["prompt_version"],
            model_provider=identity["model_provider"],
            model_id=identity["model_id"],
            retrieval_strategy=identity["retrieval_strategy"],
            config=normalized_config,
            config_hash=_stable_hash(identity),
            repetitions=repetitions,
        )

    def record_run(
        self,
        *,
        organization_id: UUID,
        experiment_id: UUID,
        case_id: UUID,
        repetition: int,
        status: EvaluationRunStatus,
        output: Mapping[str, Any],
        observation: Mapping[str, Any],
        trace_id: str | None,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        error_code: str | None,
    ) -> dict[str, Any]:
        if repetition <= 0:
            raise ValueError("重复序号必须大于零")
        if latency_ms < 0:
            raise ValueError("运行耗时不能为负数")
        context = self._repository.get_run_context(organization_id, experiment_id, case_id)
        if repetition > int(context["repetitions"]):
            raise ValueError("重复序号超过实验配置")
        output_copy = _json_object(output, "运行输出")
        observation_copy = _json_object(observation, "运行观察")
        scores = evaluate_observation(
            target_type=context["target_type"],
            expectation=context["expectation_json"],
            observation=observation_copy,
            run_status=status.value,
            latency_ms=latency_ms,
        )
        return self._repository.create_run_with_scores(
            organization_id=organization_id,
            experiment_id=experiment_id,
            case_id=case_id,
            repetition=repetition,
            status=status.value,
            output=output_copy,
            observation=observation_copy,
            trace_id=_optional_text(trace_id, "Trace ID", 300),
            latency_ms=latency_ms,
            input_tokens=_non_negative_optional(input_tokens, "输入 Token"),
            output_tokens=_non_negative_optional(output_tokens, "输出 Token"),
            error_code=_optional_text(error_code, "错误代码", 100),
            scores=scores,
        )


def _text(value: str | None, field_name: str, maximum: int, *, allow_empty: bool = False) -> str:
    normalized = "" if value is None else value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name}不能为空")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name}不能超过 {maximum} 个字符")
    return normalized


def _optional_text(value: str | None, field_name: str, maximum: int) -> str | None:
    if value is None or not value.strip():
        return None
    return _text(value, field_name, maximum)


def _json_object(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        normalized = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是可序列化 JSON 对象") from exc
    if not isinstance(normalized, dict):
        raise ValueError(f"{field_name}必须是 JSON 对象")
    if len(serialized.encode("utf-8")) > 1_000_000:
        raise ValueError(f"{field_name}不能超过 1MB")
    return normalized


def _stable_hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _non_negative_optional(value: int | None, field_name: str) -> int | None:
    if value is not None and value < 0:
        raise ValueError(f"{field_name}不能为负数")
    return value
