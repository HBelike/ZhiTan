from __future__ import annotations

import unittest
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.job_assessment import (
    CareerJobAssessmentService,
    JobAssessmentValidationError,
    number_source_lines,
    validate_and_score,
)
from src.career_assistant.model_gateway import ModelReadiness
from src.career_assistant.model_clients import (
    ChatMessage,
    ModelConnectionTarget,
    OpenAICompatibleChatClient,
)
from src.career_assistant.settings import JobAssessmentSettings


def _payload(*, verdict: str = "supported", cv_source_ids=None):
    return {
        "schema_version": "career-job-assessment-v1",
        "role_summary": "负责语音直播互动与才艺展示",
        "job_sections": [
            {"category": "responsibility", "title": "工作内容", "jd_source_ids": ["JD-001"]},
        ],
        "dimensions": [
            {"id": "performance", "label": "才艺与表达", "description": "考察唱歌、表达和内容展示能力"},
            {"id": "interaction", "label": "互动能力", "description": "考察直播场景中的沟通互动能力"},
        ],
        "items": [
            {
                "id": "item-1",
                "dimension_id": "performance",
                "requirement_type": "required",
                "verdict": verdict,
                "jd_source_ids": ["JD-001"],
                "cv_source_ids": ["CV-001"] if cv_source_ids is None else cv_source_ids,
                "reason": "简历包含校园主持和演唱经历",
            },
            {
                "id": "item-2",
                "dimension_id": "interaction",
                "requirement_type": "preferred",
                "verdict": "partial",
                "jd_source_ids": ["JD-002"],
                "cv_source_ids": ["CV-002"],
                "reason": "存在社群互动经验但不是直播场景",
            },
        ],
    }


class CareerJobAssessmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jd_lines = {"JD-001": "需要会唱歌或音乐", "JD-002": "以聊天互动为主"}
        self.cv_lines = {"CV-001": "担任校园主持并参加演唱", "CV-002": "负责社群用户互动"}

    def test_source_lines_are_server_numbered(self) -> None:
        self.assertEqual(
            number_source_lines("第一段\n\n第二段", "JD"),
            {"JD-001": "第一段", "JD-002": "第二段"},
        )

    def test_unknown_cv_reference_is_rejected(self) -> None:
        payload = _payload(cv_source_ids=["CV-999"])
        with self.assertRaisesRegex(JobAssessmentValidationError, "cv_reference_unknown"):
            validate_and_score(payload, jd_lines=self.jd_lines, cv_lines=self.cv_lines)

    def test_scores_are_recomputed_from_verdicts(self) -> None:
        payload = _payload()
        payload["model_score"] = 3
        result = validate_and_score(payload, jd_lines=self.jd_lines, cv_lines=self.cv_lines)
        scores = {item["id"]: item["score"] for item in result["dimensions"]}

        self.assertEqual(scores["performance"], 100.0)
        self.assertEqual(scores["interaction"], 50.0)
        self.assertEqual(scores["critical_gap"], 0.0)
        self.assertNotIn("model_score", result)

    def test_needs_confirmation_counts_as_missing_visible_evidence(self) -> None:
        payload = _payload(verdict="needs_confirmation", cv_source_ids=[])
        payload["items"] = [payload["items"][0]]
        payload["items"][0]["requirement_type"] = "required"
        result = validate_and_score(payload, jd_lines=self.jd_lines, cv_lines=self.cv_lines)
        dimensions = {item["id"]: item for item in result["dimensions"]}

        self.assertEqual(dimensions["performance"]["score"], 0.0)
        self.assertEqual(dimensions["performance"]["status"], "ready")
        self.assertEqual(dimensions["critical_gap"]["score"], 100.0)

    def test_company_history_can_stay_outside_scored_items(self) -> None:
        payload = _payload()
        payload["job_sections"].append(
            {"category": "company_information", "title": "公司信息", "jd_source_ids": ["JD-003"]}
        )
        jd_lines = {**self.jd_lines, "JD-003": "公司拥有十年行业经验"}
        result = validate_and_score(payload, jd_lines=jd_lines, cv_lines=self.cv_lines)

        self.assertEqual(result["job_sections"][-1]["category"], "company_information")
        self.assertFalse(any("JD-003" in item["jd_source_ids"] for item in result["items"]))

    def test_context_only_information_does_not_create_metric_cards(self) -> None:
        payload = _payload()
        payload["dimensions"].append(
            {"id": "compensation", "label": "薪资待遇", "description": "薪资与提成说明"}
        )
        payload["items"].append(
            {
                "id": "item-3",
                "dimension_id": "compensation",
                "requirement_type": "context_only",
                "verdict": "needs_confirmation",
                "jd_source_ids": ["JD-003"],
                "cv_source_ids": [],
                "reason": "薪资属于职位背景信息",
            }
        )
        jd_lines = {**self.jd_lines, "JD-003": "礼物提成按日结算"}

        result = validate_and_score(payload, jd_lines=jd_lines, cv_lines=self.cv_lines)

        self.assertNotIn("薪资待遇", [item["label"] for item in result["dimensions"]])
        self.assertNotIn("item-3", [item["id"] for item in result["items"]])

    def test_dynamic_dimensions_cover_technical_voice_and_sales_roles(self) -> None:
        scenarios = (
            (("engineering_delivery", "工程交付"), ("system_design", "系统设计")),
            (("performance", "才艺与表达"), ("interaction", "互动能力")),
            (("customer_development", "客户开发"), ("deal_execution", "成交推进")),
        )
        for first, second in scenarios:
            with self.subTest(labels=(first[1], second[1])):
                payload = deepcopy(_payload())
                payload["dimensions"][0].update(id=first[0], label=first[1])
                payload["dimensions"][1].update(id=second[0], label=second[1])
                payload["items"][0]["dimension_id"] = first[0]
                payload["items"][1]["dimension_id"] = second[0]

                result = validate_and_score(
                    payload,
                    jd_lines=self.jd_lines,
                    cv_lines=self.cv_lines,
                )

                self.assertEqual(
                    [item["label"] for item in result["dimensions"][:2]],
                    [first[1], second[1]],
                )


class _AssessmentRepository:
    def __init__(self, record) -> None:
        self.record = record
        self.saved = None

    def claim(self, assessment_id):
        return self.record

    def get(self, assessment_id):
        return self.saved

    def save_ready(self, assessment_id, *, result, attempt_count, fallback=False):
        self.saved = SimpleNamespace(
            status=SimpleNamespace(value="fallback_ready" if fallback else "ready"),
            result=result,
            attempt_count=attempt_count,
            error_code=None,
        )
        return self.saved

    def save_failed(self, assessment_id, *, error_code, attempt_count):
        self.saved = SimpleNamespace(
            status=SimpleNamespace(value="failed"),
            result=None,
            attempt_count=attempt_count,
            error_code=error_code,
        )
        return self.saved


class _ContextRepository:
    def __init__(self, candidate, target) -> None:
        self.candidate = candidate
        self.target = target

    def get_candidate_profile(self, actor_id, candidate_profile_id):
        return self.candidate

    def get_target_role(self, actor_id, target_role_profile_id):
        return self.target


class _ServiceUnderTest(CareerJobAssessmentService):
    def __init__(self, *, candidate, target, outputs) -> None:
        record = SimpleNamespace(
            id=uuid4(),
            organization_id=uuid4(),
            actor_id=uuid4(),
            candidate_profile_id=candidate.id,
            target_role_profile_id=target.id,
        )
        self.test_repository = _AssessmentRepository(record)
        self.outputs = iter(outputs)
        self.invoke_count = 0
        super().__init__(
            repository=self.test_repository,
            context_repository=_ContextRepository(candidate, target),
            model_repository=object(),
            model_gateway=object(),
            model_client=object(),
            settings=JobAssessmentSettings(
                judge_profile_key="judge",
                prompt_version="v1",
                max_attempts=3,
            ),
        )

    def _configured_profile(self, organization_id):
        return SimpleNamespace(id=uuid4())

    def _invoke(self, profile, resume, job_text):
        self.invoke_count += 1
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output


def _context_records(*, resume: str, job_text: str):
    return (
        SimpleNamespace(id=uuid4(), resume_outline=resume),
        SimpleNamespace(id=uuid4(), job_text=job_text),
    )


class CareerJobAssessmentServiceTests(unittest.TestCase):
    @staticmethod
    def _profile_service(configured_key, profiles):
        class ModelRepository:
            def list_profiles(self, organization_id, *, include_disabled):
                return profiles

        return CareerJobAssessmentService(
            repository=object(),
            context_repository=object(),
            model_repository=ModelRepository(),
            model_gateway=object(),
            model_client=object(),
            settings=JobAssessmentSettings(configured_key, "v1", 3),
        )

    def test_configured_profile_accepts_unique_model_id(self) -> None:
        expected = SimpleNamespace(
            profile_key="deepseek-deepseek-v4-flash",
            model_id="deepseek-v4-flash",
        )
        service = self._profile_service("deepseek-v4-flash", [expected])

        self.assertIs(service._configured_profile(uuid4()), expected)

    def test_configured_profile_prefers_exact_profile_key(self) -> None:
        model_id_match = SimpleNamespace(profile_key="provider-judge", model_id="judge")
        exact_match = SimpleNamespace(profile_key="judge", model_id="another-model")
        service = self._profile_service("judge", [model_id_match, exact_match])

        self.assertIs(service._configured_profile(uuid4()), exact_match)

    def test_configured_profile_rejects_ambiguous_model_id(self) -> None:
        profiles = [
            SimpleNamespace(profile_key="provider-a-judge", model_id="judge"),
            SimpleNamespace(profile_key="provider-b-judge", model_id="judge"),
        ]
        service = self._profile_service("judge", profiles)

        self.assertIsNone(service._configured_profile(uuid4()))

    def test_json_completion_request_contains_json_object_mode(self) -> None:
        payload = OpenAICompatibleChatClient._build_request_payload(
            ModelConnectionTarget("deepseek", "deepseek-chat", None),
            [ChatMessage(role="user", content="分析")],
            max_tokens=1000,
            response_format={"type": "json_object"},
        )

        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_text_only_profile_can_use_validated_json_mode(self) -> None:
        class Gateway:
            request = None

            def resolve(self, organization_id, request):
                self.request = request
                return SimpleNamespace(
                    readiness=ModelReadiness.READY,
                    credential_env_name="JUDGE_KEY",
                    credential="secret",
                )

        class Client:
            def complete_json(self, profile, credential_env_name, messages, *, api_key):
                return json.dumps(_payload(), ensure_ascii=False)

        gateway = Gateway()
        service = CareerJobAssessmentService(
            repository=object(),
            context_repository=object(),
            model_repository=object(),
            model_gateway=gateway,
            model_client=Client(),
            settings=JobAssessmentSettings("judge", "v1", 3),
        )
        profile = SimpleNamespace(
            id=uuid4(),
            organization_id=uuid4(),
            capabilities=frozenset({ModelCapability.TEXT}),
        )

        result = service._invoke(
            profile,
            "担任校园主持并参加演唱\n负责社群用户互动",
            "需要会唱歌或音乐\n以聊天互动为主",
        )

        self.assertEqual(result["role_summary"], "负责语音直播互动与才艺展示")
        self.assertEqual(gateway.request.required_capabilities, frozenset({ModelCapability.TEXT}))

    def test_text_only_profile_fills_missing_server_schema_version(self) -> None:
        class Gateway:
            def resolve(self, organization_id, request):
                return SimpleNamespace(
                    readiness=ModelReadiness.READY,
                    credential_env_name="JUDGE_KEY",
                    credential="secret",
                )

        class Client:
            def complete_json(self, profile, credential_env_name, messages, *, api_key):
                payload = _payload()
                payload.pop("schema_version")
                return json.dumps(payload, ensure_ascii=False)

        service = CareerJobAssessmentService(
            repository=object(),
            context_repository=object(),
            model_repository=object(),
            model_gateway=Gateway(),
            model_client=Client(),
            settings=JobAssessmentSettings("judge", "v1", 3),
        )
        profile = SimpleNamespace(
            id=uuid4(),
            organization_id=uuid4(),
            capabilities=frozenset({ModelCapability.TEXT}),
        )

        result = service._invoke(profile, "简历", "岗位")

        self.assertEqual(result["schema_version"], "career-job-assessment-v1")

    def test_dynamic_non_technical_dimensions_are_saved_after_one_judge_call(self) -> None:
        candidate, target = _context_records(
            resume="担任校园主持并参加演唱\n负责社群用户互动",
            job_text="需要会唱歌或音乐\n以聊天互动为主",
        )
        service = _ServiceUnderTest(candidate=candidate, target=target, outputs=[_payload()])

        result = service.run_assessment(service.test_repository.record.id)

        self.assertEqual(result.status.value, "ready")
        self.assertEqual(service.invoke_count, 1)
        self.assertEqual(
            [dimension["label"] for dimension in result.result["dimensions"][:2]],
            ["才艺与表达", "互动能力"],
        )

    def test_three_invalid_judge_outputs_then_use_legacy_fallback(self) -> None:
        candidate, target = _context_records(
            resume="5 年 Python 开发经验，负责端到端系统开发和交付",
            job_text="任职要求\n熟练 Python，至少 3 年开发经验\n工作职责\n负责端到端系统开发和交付",
        )
        invalid = JobAssessmentValidationError("judge_schema_invalid")
        service = _ServiceUnderTest(
            candidate=candidate,
            target=target,
            outputs=[invalid, invalid, invalid],
        )

        result = service.run_assessment(service.test_repository.record.id)

        self.assertEqual(service.invoke_count, 3)
        self.assertEqual(result.status.value, "fallback_ready")
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual(result.result["analysis_source"], "lexical_fallback")

    def test_three_invalid_judge_outputs_and_failed_fallback_are_reported(self) -> None:
        candidate, target = _context_records(
            resume="喜欢音乐",
            job_text="在家唱歌聊天",
        )
        invalid = JobAssessmentValidationError("judge_schema_invalid")
        service = _ServiceUnderTest(
            candidate=candidate,
            target=target,
            outputs=[invalid, invalid, invalid],
        )

        result = service.run_assessment(service.test_repository.record.id)

        self.assertEqual(service.invoke_count, 3)
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual(result.error_code, "judge_schema_invalid")

    def test_legacy_fallback_exception_is_reported_as_failed(self) -> None:
        candidate, target = _context_records(
            resume="喜欢音乐",
            job_text="在家唱歌聊天",
        )
        invalid = JobAssessmentValidationError("judge_schema_invalid")
        service = _ServiceUnderTest(
            candidate=candidate,
            target=target,
            outputs=[invalid, invalid, invalid],
        )

        with patch(
            "src.career_assistant.job_assessment.extract_job_requirements",
            side_effect=RuntimeError("词典解析异常"),
        ):
            result = service.run_assessment(service.test_repository.record.id)

        self.assertEqual(result.status.value, "failed")
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual(result.error_code, "fallback_analysis_failed")


if __name__ == "__main__":
    unittest.main()
