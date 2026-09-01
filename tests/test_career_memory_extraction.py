"""长期求职事实抽取 Schema、证据和快速路径测试。"""

from uuid import uuid4

import pytest

from src.career_assistant.career_memory import CareerMemoryType
from src.career_assistant.career_memory_extraction import (
    EXTRACTION_SCHEMA_VERSION,
    extract_job_intention,
    validate_extraction_payload,
)
from src.career_assistant.resume_normalizer import ResumeNormalizer, ResumeSection


def payload(**overrides):
    item = {
        "memory_type": "work_experience",
        "display_text": "负责支付系统接口开发",
        "normalized_value": {"summary": "负责支付系统接口开发"},
        "evidence_text": "负责支付系统接口开发",
    }
    item.update(overrides)
    return {"schema_version": EXTRACTION_SCHEMA_VERSION, "items": [item]}


def test_valid_payload_requires_evidence_from_source() -> None:
    items = validate_extraction_payload(
        payload(),
        "我在某公司负责支付系统接口开发，并参与稳定性治理。",
    )
    assert items[0].draft.memory_type is CareerMemoryType.WORK_EXPERIENCE


def test_missing_contiguous_evidence_rejects_whole_batch() -> None:
    with pytest.raises(ValueError, match="连续原文"):
        validate_extraction_payload(payload(evidence_text="模型自行补充"), "用户没有说过")


def test_unknown_type_and_extra_field_are_rejected() -> None:
    with pytest.raises(ValueError, match="六类白名单"):
        validate_extraction_payload(payload(memory_type="hobby"), "负责支付系统接口开发")
    invalid = payload()
    invalid["items"][0]["confidence"] = 0.9
    with pytest.raises(ValueError, match="条目字段"):
        validate_extraction_payload(invalid, "负责支付系统接口开发")


def test_explicit_intention_and_correction_are_active_ready_drafts() -> None:
    space_id = uuid4()
    initial = extract_job_intention("我现在只找上海的 Java 后端岗位", space_id)
    corrected = extract_job_intention("纠正：目标岗位改成 AI 应用工程", space_id)
    assert initial is not None and initial.source_kind == "explicit_user_statement"
    assert corrected is not None and corrected.source_kind == "explicit_user_correction"
    assert corrected.career_space_id == space_id


def test_resume_normalizer_recognizes_publications_section() -> None:
    profile = ResumeNormalizer().normalize("# 发表论文\nA Study on Agents\n# 工作经历\n负责后端开发")
    assert profile.has_section(ResumeSection.PUBLICATIONS)
    assert "A Study on Agents" in next(
        item.content for item in profile.sections if item.section is ResumeSection.PUBLICATIONS
    )
