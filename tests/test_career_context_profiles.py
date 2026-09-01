from __future__ import annotations

from src.career_assistant.context_profiles import (
    build_match_assessment,
    extract_job_requirements,
)


def test_extract_requirements_keeps_source_and_priority() -> None:
    requirements = extract_job_requirements(
        """
        任职要求：必须熟练使用 Python、FastAPI 和 PostgreSQL。
        至少 3 年后端开发经验。
        有 RAG 或 LangGraph 落地经验优先。
        """
    )

    assert len(requirements) == 3
    assert requirements[0]["priority"] == "must"
    assert requirements[0]["skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert requirements[1]["required_years"] == 3.0
    assert requirements[2]["priority"] == "preferred"
    assert requirements[2]["skills"] == ["RAG", "LangGraph"]


def test_match_assessment_is_reproducible_and_evidence_bound() -> None:
    resume = """
    【工作与实习经历】
    2.5 年 Python 后端开发经验，负责高并发 API 优化。

    【项目经历】
    使用 FastAPI 与 PostgreSQL 设计服务接口。
    """
    requirements = extract_job_requirements(
        """
        必须掌握 Python。
        必须掌握 FastAPI。
        必须掌握 PostgreSQL。
        有 RAG 经验优先。
        至少 3 年后端开发经验。
        """
    )

    assessment = build_match_assessment(resume, requirements)

    assert assessment["algorithm_version"] == "lexical-evidence-v2"
    assert assessment["dimensions"]["skill_coverage"] == {
        "label": "技能证据覆盖率",
        "score": 85.7,
        "numerator": 6.0,
        "denominator": 7.0,
        "direction": "higher",
        "status": "ready",
    }
    experience = assessment["dimensions"]["experience_coverage"]
    assert experience["score"] == 50.0
    assert any(
        item["reason_code"] == "years_near" and item["match_factor"] == 0.5
        for item in assessment["requirements"]
    )


def test_unassessed_dimension_returns_na_instead_of_fake_zero() -> None:
    requirements = extract_job_requirements("负责团队沟通与日常协作。")
    assessment = build_match_assessment("有跨团队协作经历。", requirements)

    experience = assessment["dimensions"]["experience_coverage"]
    assert experience["score"] is None
    assert experience["status"] == "insufficient_data"
