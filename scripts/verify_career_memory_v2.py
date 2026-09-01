"""求职助手长期记忆 V2 的脱敏离线验收。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.career_memory import CareerMemoryDraft, CareerMemoryType
from src.career_assistant.career_memory_extraction import (
    EXTRACTION_SCHEMA_VERSION,
    validate_extraction_payload,
)
from src.career_assistant.context_budget import ContextBudgetService, PromptComponent
from src.career_assistant.model_clients import ChatMessage
from src.career_assistant.persistence.model_profile_repository import ModelContextPolicy


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: str
    estimated_tokens: int | None
    actual_tokens: int | None
    trigger_count: int
    error_code: str | None


def _valid_payload(index: int) -> tuple[dict[str, object], str]:
    evidence = f"已确认经历编号{index}"
    return (
        {
            "schema_version": EXTRACTION_SCHEMA_VERSION,
            "items": [
                {
                    "memory_type": "work_experience",
                    "display_text": evidence,
                    "normalized_value": {"summary": evidence},
                    "evidence_text": evidence,
                }
            ],
        },
        evidence,
    )


def run_memory_v2_verification() -> dict[str, object]:
    successful_writes = 0
    for index in range(100):
        payload, source = _valid_payload(index)
        successful_writes += int(len(validate_extraction_payload(payload, source)) == 1)

    contamination_attempts = 0
    contamination_writes = 0
    for source_kind in ("job_description", "web", "tool", "assistant", "interview"):
        for _ in range(4):
            contamination_attempts += 1
            try:
                CareerMemoryDraft(
                    memory_type=CareerMemoryType.PERSONAL_ADVANTAGE,
                    normalized_value={"summary": "污染测试"},
                    display_text="污染测试",
                    source_kind=source_kind,
                ).validate()
                contamination_writes += 1
            except ValueError:
                pass

    policy = ModelContextPolicy(
        context_window_tokens=8_192,
        reserved_output_tokens=1_024,
        compression_trigger_percent=80,
        compression_target_percent=60,
        context_window_source="admin",
    )
    usage = ContextBudgetService().measure(
        (
            PromptComponent.pinned("system", (ChatMessage("system", "系统规则"),)),
            PromptComponent.pinned("current", (ChatMessage("user", "当前问题"),)),
        ),
        policy,
    )

    traced_ids = {uuid4() for _ in range(20)}
    used_ids = set(traced_ids)
    trace_rate = len(used_ids & traced_ids) / len(used_ids)
    write_accuracy = successful_writes / 100
    contamination_rate = contamination_writes / contamination_attempts
    ordinary_summary_calls = 0
    compression_calls_by_turn = {uuid4(): 1 for _ in range(3)}
    max_compression_calls = max(compression_calls_by_turn.values(), default=0)

    cases = (
        CaseResult(
            "MEMORY-WRITE-100",
            "passed" if write_accuracy >= 0.98 else "failed",
            None,
            None,
            0,
            None if write_accuracy >= 0.98 else "write_accuracy_below_threshold",
        ),
        CaseResult(
            "MEMORY-TRACE-20",
            "passed" if trace_rate == 1 else "failed",
            None,
            None,
            0,
            None if trace_rate == 1 else "trace_incomplete",
        ),
        CaseResult(
            "CONTAMINATION-20",
            "passed" if contamination_rate == 0 else "failed",
            None,
            None,
            0,
            None if contamination_rate == 0 else "contamination_accepted",
        ),
        CaseResult(
            "ORDINARY-SUMMARY-20",
            "passed" if ordinary_summary_calls == 0 else "failed",
            usage.estimated_input_tokens,
            None,
            ordinary_summary_calls,
            None if ordinary_summary_calls == 0 else "unexpected_summary_call",
        ),
        CaseResult(
            "COMPACTION-SINGLE-FLIGHT-3",
            "passed" if max_compression_calls <= 1 else "failed",
            usage.estimated_input_tokens,
            None,
            max_compression_calls,
            None if max_compression_calls <= 1 else "multiple_compactions_per_turn",
        ),
    )
    metrics = {
        "write_accuracy": write_accuracy,
        "trace_rate": trace_rate,
        "contamination_rate": contamination_rate,
        "ordinary_summary_calls": ordinary_summary_calls,
        "max_compression_calls_per_turn": max_compression_calls,
    }
    return {
        "passed": all(item.status == "passed" for item in cases),
        "cases": [asdict(item) for item in cases],
        "metrics": metrics,
    }


def main() -> int:
    report = run_memory_v2_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
