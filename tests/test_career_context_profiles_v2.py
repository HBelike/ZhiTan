"""结构感知 JD 抽取与四维证据评分回归测试。"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from src.career_assistant.context_profiles import (
    build_match_assessment,
    extract_job_requirements,
)
from src.career_assistant.contracts import CareerInboundMessage
from src.career_assistant.web.router import (
    UpdateConversationContextRequest,
    _attach_conversation_context,
    _build_interview_evidence,
    _conversation_context_payload,
)


class CareerContextProfilesV2Tests(unittest.TestCase):
    def test_colon_section_headings_keep_intro_out_of_responsibilities(self) -> None:
        requirements = extract_job_requirements(
            """
            ## 职位描述
            岗位情况：隶属于食物主义集团，建设全球 AI 工作站。
            核心职责：
            1、开发并优化核心 AI 模型与 Agent 系统。
            2、设计高可用核心后端，管理 API 调用与微服务架构。
            职位要求：
            1、熟练使用 React、Python 与 MCP。
            2、至少 3 年全栈研发经验。
            """
        )

        responsibility_items = [item for item in requirements if item["section"] == "responsibility"]
        qualification_items = [item for item in requirements if item["section"] == "qualification"]

        self.assertEqual(len(responsibility_items), 2)
        self.assertEqual(len(qualification_items), 2)
        self.assertFalse(any("食物主义集团" in item["text"] for item in responsibility_items))
        self.assertTrue(any("MCP" in item["skills"] for item in qualification_items))

    def test_qualification_items_survive_long_intro_and_inherit_must_priority(self) -> None:
        intro = "\n".join(
            f"{index}、负责营销活动创意和业务协作" for index in range(1, 15)
        )
        job_text = f"""
        <!-- image -->
        ## 职位亮点
        {intro}
        ## 任职资格
        熟练使用 Cursor、Vite、React/Vue、FastAPI/Next.js 等现代全栈技术栈。
        至少 2 年互联网研发经验。
        具备电商营销、促销优惠券或用户增长经验。
        40-70K·15薪
        微信扫码沟通
        """

        requirements = extract_job_requirements(job_text, limit=6)

        qualification_items = [
            item for item in requirements if item["section"] == "qualification"
        ]
        self.assertEqual(len(qualification_items), 3)
        self.assertTrue(all(item["priority"] == "must" for item in qualification_items))
        self.assertTrue(any("AI 辅助编程" in item["skills"] for item in qualification_items))
        self.assertTrue(any(item["required_years"] == 2.0 for item in qualification_items))
        self.assertFalse(any("image" in item["text"] for item in requirements))
        self.assertFalse(any("微信扫码" in item["text"] for item in requirements))

    def test_all_four_dimensions_are_computable_with_real_evidence(self) -> None:
        requirements = extract_job_requirements(
            """
            ## 工作职责
            利用 Cursor 进行 AI 全栈极速交付，端到端落地电商营销玩法。
            依托促销中心和优惠券系统保障高并发、高可用与资金安全。
            ## 任职资格
            熟练使用 Vue、FastAPI 和 AI 辅助编程工具。
            至少 2 年互联网研发经验。
            具备电商营销和用户增长经验。
            """
        )
        resume = """
        3 年全栈开发经验，熟练使用 Vue、FastAPI、Cursor。
        作为平台负责人端到端交付促销优惠券系统，支持高并发和高可用。
        负责电商营销数据分析与用户增长项目。
        """

        assessment = build_match_assessment(resume, requirements)

        self.assertEqual(assessment["algorithm_version"], "lexical-evidence-v2")
        for dimension in (
            "skill_coverage",
            "experience_coverage",
            "project_relevance",
            "critical_gap",
        ):
            with self.subTest(dimension=dimension):
                metric = assessment["dimensions"][dimension]
                self.assertEqual(metric["status"], "ready")
                self.assertIsNotNone(metric["score"])
                self.assertGreater(metric["denominator"], 0)

        project_items = [
            item
            for item in assessment["requirements"]
            if "project" in item["dimensions"]
        ]
        self.assertTrue(project_items)
        self.assertTrue(
            any(item["dimension_matches"].get("project") for item in project_items)
        )

    def test_existing_context_recomputes_stale_requirement_snapshot_without_sync_scoring(self) -> None:
        now = datetime.now(UTC)
        context = SimpleNamespace(
            binding_id=uuid4(),
            binding_version=1,
            created_at=now,
            candidate=SimpleNamespace(
                id=uuid4(),
                display_name="基准简历",
                source_filename="resume.pdf",
                resume_outline="3 年 FastAPI、Vue 和 Cursor 全栈开发经验。",
                version=1,
                created_at=now,
            ),
            target_role=SimpleNamespace(
                id=uuid4(),
                company_name="示例公司",
                role_name="AI 全栈工程师",
                source_kind="text",
                source_label="手工粘贴",
                job_text="## 任职资格\n熟练使用 Cursor、Vue 和 FastAPI。\n至少 2 年研发经验。",
                requirements=({"id": "old", "text": "旧解析快照", "skills": []},),
                version=1,
                created_at=now,
            ),
        )

        payload = _conversation_context_payload(context)

        self.assertNotEqual(payload["target_role"]["requirements"][0]["id"], "old")
        self.assertIsNone(payload["assessment"])

    def test_partial_context_payload_keeps_available_profile_without_match_assessment(self) -> None:
        now = datetime.now(UTC)
        candidate = SimpleNamespace(
            id=uuid4(),
            display_name="后端简历",
            source_filename="resume.pdf",
            resume_outline="3 年 FastAPI 开发经验。",
            version=2,
            created_at=now,
        )
        context = SimpleNamespace(
            binding_id=uuid4(),
            binding_version=3,
            created_at=now,
            candidate=candidate,
            target_role=None,
        )

        payload = _conversation_context_payload(context)

        self.assertEqual(payload["candidate_profile"]["display_name"], "后端简历")
        self.assertIsNone(payload["target_role"])
        self.assertIsNone(payload["assessment"])

    def test_attach_context_injects_whichever_material_is_available(self) -> None:
        actor_id = uuid4()
        conversation_id = uuid4()
        inbound = CareerInboundMessage(
            turn_id=uuid4(),
            conversation_id=conversation_id,
            actor_id=actor_id,
            text="帮我准备面试",
        )
        candidate_context = SimpleNamespace(
            binding_version=4,
            candidate=SimpleNamespace(resume_outline="熟练使用 Vue 和 FastAPI。"),
            target_role=None,
        )
        services = SimpleNamespace(
            context_repository=SimpleNamespace(
                get_conversation_context=lambda requested_actor, requested_conversation: (
                    candidate_context
                    if requested_actor == actor_id and requested_conversation == conversation_id
                    else None
                ),
            ),
        )

        enriched = _attach_conversation_context(services, inbound)

        self.assertEqual(enriched.candidate_profile_context, "熟练使用 Vue 和 FastAPI。")
        self.assertEqual(enriched.target_role_context, "")
        self.assertEqual(enriched.context_binding_version, 4)

    def test_update_context_request_accepts_resume_or_job_independently(self) -> None:
        candidate_id = uuid4()
        target_id = uuid4()

        resume_only = UpdateConversationContextRequest(candidate_profile_id=candidate_id)
        job_only = UpdateConversationContextRequest(target_role_profile_id=target_id)

        self.assertEqual(resume_only.candidate_profile_id, candidate_id)
        self.assertIsNone(resume_only.target_role_profile_id)
        self.assertEqual(job_only.target_role_profile_id, target_id)
        self.assertIsNone(job_only.candidate_profile_id)

    def test_candidate_only_context_does_not_assume_target_for_auto_retrieval(self) -> None:
        actor_id = uuid4()
        conversation_id = uuid4()
        services = SimpleNamespace(
            context_repository=SimpleNamespace(
                get_conversation_context=lambda *_args: SimpleNamespace(
                    candidate=SimpleNamespace(resume_outline="FastAPI 项目"),
                    target_role=None,
                ),
            ),
        )

        evidence = _build_interview_evidence(
            services,
            SimpleNamespace(actor_id=actor_id, organization_id=uuid4()),
            (),
            "帮我准备面试",
            conversation_id=conversation_id,
        )

        self.assertEqual(evidence, ())


if __name__ == "__main__":
    unittest.main()
