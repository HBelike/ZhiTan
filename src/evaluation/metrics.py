"""首版确定性指标注册表与可复算实现。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from src.evaluation.contracts import MetricDefinition, MetricScore


METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        key="system.success",
        version="1.0",
        display_name="运行成功率",
        target_type="all",
        evaluator_kind="deterministic",
        direction="higher_is_better",
        unit="ratio",
        aggregation="mean",
        formula="succeeded_runs / all_runs",
        threshold=0.99,
        critical=True,
        description="业务目标执行完成且未返回错误。",
    ),
    MetricDefinition(
        key="system.structure_valid",
        version="1.0",
        display_name="结构合法率",
        target_type="all",
        evaluator_kind="deterministic",
        direction="higher_is_better",
        unit="ratio",
        aggregation="mean",
        formula="valid_structured_outputs / checked_outputs",
        threshold=1.0,
        critical=True,
        description="需要结构化输出的运行是否满足约定 Schema。",
    ),
    MetricDefinition(
        key="system.latency_ms",
        version="1.0",
        display_name="端到端耗时",
        target_type="all",
        evaluator_kind="deterministic",
        direction="lower_is_better",
        unit="ms",
        aggregation="p95",
        formula="completed_at - started_at",
        threshold=120_000.0,
        critical=False,
        description="从目标执行开始到结果收口的毫秒数。",
    ),
    MetricDefinition(
        key="rag.recall_at_6",
        version="1.0",
        display_name="Recall@6",
        target_type="interview_rag",
        evaluator_kind="deterministic",
        direction="higher_is_better",
        unit="ratio",
        aggregation="macro_mean",
        formula="|retrieved_top6 ∩ relevant| / |relevant|",
        threshold=0.85,
        critical=False,
        description="前六个召回结果覆盖金标相关 Chunk 的比例。",
    ),
    MetricDefinition(
        key="rag.mrr",
        version="1.0",
        display_name="MRR",
        target_type="interview_rag",
        evaluator_kind="deterministic",
        direction="higher_is_better",
        unit="ratio",
        aggregation="mean",
        formula="mean(1 / first_relevant_rank)",
        threshold=0.80,
        critical=False,
        description="第一个相关 Chunk 出现位置的倒数。",
    ),
    MetricDefinition(
        key="rag.ndcg_at_6",
        version="1.0",
        display_name="nDCG@6",
        target_type="interview_rag",
        evaluator_kind="deterministic",
        direction="higher_is_better",
        unit="ratio",
        aggregation="macro_mean",
        formula="DCG@6 / IDCG@6",
        threshold=0.75,
        critical=False,
        description="相关证据在前六个结果中的排序质量。",
    ),
    MetricDefinition(
        key="agent.required_step_coverage",
        version="1.0",
        display_name="必要步骤覆盖率",
        target_type="career_agent",
        evaluator_kind="deterministic",
        direction="higher_is_better",
        unit="ratio",
        aggregation="macro_mean",
        formula="executed_required_steps / required_steps",
        threshold=0.95,
        critical=False,
        description="Agent 实际执行路径覆盖期望必要步骤的比例。",
    ),
    MetricDefinition(
        key="agent.unnecessary_step_rate",
        version="1.0",
        display_name="不必要步骤率",
        target_type="career_agent",
        evaluator_kind="deterministic",
        direction="lower_is_better",
        unit="ratio",
        aggregation="macro_mean",
        formula="steps_outside_allowed_set / executed_steps",
        threshold=0.10,
        critical=False,
        description="实际轨迹中不属于允许步骤集合的比例。",
    ),
    MetricDefinition(
        key="resume.forbidden_claim_match_rate",
        version="1.0",
        display_name="禁止事实命中率",
        target_type="resume_optimizer",
        evaluator_kind="deterministic",
        direction="lower_is_better",
        unit="ratio",
        aggregation="macro_mean",
        formula="matched_forbidden_claims / forbidden_claims",
        threshold=0.0,
        critical=True,
        description="输出命中样本中明确标记为禁止新增或错误的事实比例。",
    ),
)

_DEFINITIONS_BY_KEY = {definition.key: definition for definition in METRIC_DEFINITIONS}


def evaluate_observation(
    *,
    target_type: str,
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
    run_status: str,
    latency_ms: int,
) -> tuple[MetricScore, ...]:
    """只依据已落盘事实计算确定性指标，不调用模型也不补造缺失信息。"""

    scores: list[MetricScore] = []
    scores.append(_score("system.success", 1.0 if run_status == "succeeded" else 0.0, {
        "run_status": run_status,
    }))
    scores.append(_score("system.latency_ms", float(latency_ms), {
        "latency_ms": latency_ms,
    }))

    structure_valid = observation.get("structure_valid")
    if isinstance(structure_valid, bool):
        scores.append(_score("system.structure_valid", 1.0 if structure_valid else 0.0, {
            "structure_valid": structure_valid,
        }))

    if target_type == "interview_rag":
        scores.extend(_evaluate_rag(expectation, observation))
    elif target_type == "career_agent":
        scores.extend(_evaluate_agent(expectation, observation))
    elif target_type == "resume_optimizer":
        scores.extend(_evaluate_resume(expectation, observation))
    return tuple(scores)


def _evaluate_rag(
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> list[MetricScore]:
    relevant = _string_set(expectation.get("relevant_chunk_ids"))
    if not relevant:
        return []
    retrieved = _retrieved_ids(observation.get("retrieved_items"))[:6]
    retrieved_set = set(retrieved)
    recall = len(retrieved_set & relevant) / len(relevant)
    first_rank = next((index for index, item_id in enumerate(retrieved, 1) if item_id in relevant), None)
    mrr = 0.0 if first_rank is None else 1.0 / first_rank
    dcg = sum(1.0 / math.log2(index + 1) for index, item_id in enumerate(retrieved, 1) if item_id in relevant)
    ideal_count = min(len(relevant), 6)
    ideal_dcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_count + 1))
    ndcg = 0.0 if ideal_dcg == 0 else dcg / ideal_dcg
    evidence = {
        "relevant_chunk_ids": sorted(relevant),
        "retrieved_chunk_ids": retrieved,
    }
    return [
        _score("rag.recall_at_6", recall, evidence),
        _score("rag.mrr", mrr, {**evidence, "first_relevant_rank": first_rank}),
        _score("rag.ndcg_at_6", ndcg, evidence),
    ]


def _evaluate_agent(
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> list[MetricScore]:
    executed = _step_names(observation.get("agent_steps"))
    required = _string_set(expectation.get("required_step_names"))
    allowed = _string_set(expectation.get("allowed_step_names"))
    scores: list[MetricScore] = []
    if required:
        coverage = len(set(executed) & required) / len(required)
        scores.append(_score("agent.required_step_coverage", coverage, {
            "required_step_names": sorted(required),
            "executed_step_names": executed,
        }))
    if allowed and executed:
        unnecessary = [step for step in executed if step not in allowed]
        scores.append(_score("agent.unnecessary_step_rate", len(unnecessary) / len(executed), {
            "allowed_step_names": sorted(allowed),
            "executed_step_names": executed,
            "unnecessary_step_names": unnecessary,
        }))
    return scores


def _evaluate_resume(
    expectation: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> list[MetricScore]:
    forbidden = [value for value in _string_list(expectation.get("forbidden_claims")) if value]
    if not forbidden:
        return []
    output_text = str(observation.get("output_text") or "").casefold()
    matched = [claim for claim in forbidden if claim.casefold() in output_text]
    return [_score("resume.forbidden_claim_match_rate", len(matched) / len(forbidden), {
        "forbidden_claim_count": len(forbidden),
        "matched_forbidden_claims": matched,
    })]


def _score(metric_key: str, raw_value: float, evidence: Mapping[str, Any]) -> MetricScore:
    definition = _DEFINITIONS_BY_KEY[metric_key]
    if definition.direction == "higher_is_better":
        passed = raw_value >= definition.threshold
    else:
        passed = raw_value <= definition.threshold
    return MetricScore(
        metric_key=definition.key,
        metric_version=definition.version,
        raw_value=round(float(raw_value), 8),
        passed=passed,
        evaluator_kind=definition.evaluator_kind,
        evidence=evidence,
    )


def _retrieved_ids(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            item_id = item.get("chunk_id", item.get("id"))
        else:
            item_id = item
        if item_id is not None and str(item_id).strip():
            result.append(str(item_id).strip())
    return result


def _step_names(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        name = item.get("name", item.get("step_name")) if isinstance(item, Mapping) else item
        if name is not None and str(name).strip():
            result.append(str(name).strip())
    return result


def _string_set(value: Any) -> set[str]:
    return set(_string_list(value))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item).strip() for item in value if item is not None and str(item).strip()]
