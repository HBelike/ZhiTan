from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from src.career_assistant.contracts import ModelCapability, SkillExecutionMode
from src.career_assistant.intake_graph import ModelTurnContext
from src.career_assistant.model_clients import ModelConnectionTarget, OpenAICompatibleChatClient
from src.career_assistant.response_runner import CareerResponseRunner
from src.career_assistant.skill_runtime import CareerSkillRuntime
from src.services.skill_library_service import SkillDetail, SkillSummary


def _summary(name: str) -> SkillSummary:
    return SkillSummary(
        id=f"id-{name}",
        name=name,
        description=f"Use {name} for career work",
        description_zh=f"使用 {name} 完成求职任务",
        author="test",
        homepage_url=None,
        repository_full_name=None,
        stars=None,
        previous_stars=None,
        star_delta=None,
        star_growth_rate=None,
        stars_updated_at=None,
        source="project",
        source_label="项目 Skill",
        path_hint=f".agents/skills/{name}/SKILL.md",
        editable=True,
    )


class _FakeSkillLibrary:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.summaries = [_summary(name) for name in names]
        self.detail_reads: list[str] = []

    def list_skills(self) -> list[SkillSummary]:
        return list(self.summaries)

    def get_skill(self, skill_id: str) -> SkillDetail:
        self.detail_reads.append(skill_id)
        summary = next(item for item in self.summaries if item.id == skill_id)
        markdown = (
            "---\n"
            f"name: {summary.name}\n"
            f"description: {summary.description}\n"
            "---\n\n"
            f"# {summary.name}\n\n"
            "请按事实完成求职分析。任务参数：$ARGUMENTS。"
            "备用参数：${ARGUMENTS}。"
            "Skill 目录：${SKILL_DIR}；兼容目录：${CLAUDE_SKILL_DIR}。"
        )
        return SkillDetail(summary=summary, markdown=markdown)


class _LongSkillLibrary(_FakeSkillLibrary):
    def get_skill(self, skill_id: str) -> SkillDetail:
        self.detail_reads.append(skill_id)
        summary = next(item for item in self.summaries if item.id == skill_id)
        markdown = (
            "---\n"
            f"name: {summary.name}\n"
            f"description: {summary.description}\n"
            "---\n\n"
            + ("完整工作流。" * 2_200)
            + "\nEND_OF_SKILL"
        )
        return SkillDetail(summary=summary, markdown=markdown)


class CareerSkillRuntimeTests(unittest.TestCase):
    def test_grill_me_seed_contains_complete_round_based_workflow(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        markdown = (project_root / "deploy/skill-seeds/grill-me/SKILL.md").read_text(
            encoding="utf-8",
        )
        instructions = CareerSkillRuntime._skill_body(markdown)

        self.assertIn("Interview the user relentlessly", instructions)
        self.assertIn("Then wait for the user's answers before the next round", instructions)
        self.assertIn("Do not act on it until the user confirms", instructions)
        self.assertNotIn("Run a `/grilling` session", instructions)

    def test_mentions_only_disclose_metadata(self) -> None:
        library = _FakeSkillLibrary(("resume-review", "interview-coach"))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        mentions = runtime.list_mentions("resume")

        self.assertEqual([item.name for item in mentions], ["resume-review"])
        self.assertEqual(library.detail_reads, [])

    def test_activate_merges_selected_id_and_slash_command_without_duplicates(self) -> None:
        library = _FakeSkillLibrary(("resume-review", "interview-coach"))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        activated = runtime.activate(
            ["id-resume-review"],
            "/resume-review 请优化这份简历",
        )

        self.assertEqual([item.name for item in activated], ["resume-review"])
        self.assertEqual(library.detail_reads, ["id-resume-review"])
        self.assertNotIn("---", activated[0].instructions)
        self.assertIn("请按事实完成求职分析", activated[0].instructions)

    def test_at_reference_does_not_activate_or_strip_an_installed_skill_name(self) -> None:
        library = _FakeSkillLibrary(("resume-review",))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        result = runtime.resolve([], "请结合 @resume-review 分析面经")

        self.assertEqual(result.skills, ())
        self.assertEqual(result.user_task_text, "请结合 @resume-review 分析面经")

    def test_conversation_does_not_inherit_skill_from_at_reference(self) -> None:
        library = _FakeSkillLibrary(("resume-review",))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        result = runtime.resolve_for_conversation(
            [],
            "继续分析",
            ["请结合 @resume-review 分析面经"],
        )

        self.assertEqual(result.skills, ())
        self.assertEqual(result.user_task_text, "继续分析")

    def test_resolve_strips_invocation_and_ignores_stale_selected_skill(self) -> None:
        library = _FakeSkillLibrary(("find-skills", "resume-review"))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        result = runtime.resolve(
            ["id-find-skills", "id-resume-review"],
            "/find-skills 帮我找制作 PPT 的 Skill",
        )

        self.assertEqual([item.name for item in result.skills], ["find-skills"])
        self.assertEqual(result.user_task_text, "帮我找制作 PPT 的 Skill")
        self.assertEqual(result.skills[0].execution_mode, SkillExecutionMode.PROMPT)
        self.assertEqual(result.skills[0].tool_names, ())
        self.assertTrue(result.skills[0].primary)

    def test_resolve_renders_arguments_and_skill_directory(self) -> None:
        library = _FakeSkillLibrary(("resume-review",))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        result = runtime.resolve([], "/resume-review 优化 Java 后端简历")

        self.assertEqual(result.user_task_text, "优化 Java 后端简历")
        self.assertIn("任务参数：优化 Java 后端简历", result.skills[0].instructions)
        self.assertIn("备用参数：优化 Java 后端简历", result.skills[0].instructions)
        self.assertIn("Skill 目录：.agents/skills/resume-review", result.skills[0].instructions)
        self.assertIn("兼容目录：.agents/skills/resume-review", result.skills[0].instructions)
        self.assertNotIn("$ARGUMENTS", result.skills[0].instructions)
        self.assertNotIn("${SKILL_DIR}", result.skills[0].instructions)
        self.assertNotIn("${CLAUDE_SKILL_DIR}", result.skills[0].instructions)

    def test_conversation_follow_up_inherits_latest_explicit_skill(self) -> None:
        library = _FakeSkillLibrary(("find-skills", "resume-review"))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        result = runtime.resolve_for_conversation(
            [],
            "继续检查这个 GitHub 仓库并告诉我有哪些 Skill",
            [
                "/resume-review 优化简历",
                "/find-skills 分析 https://github.com/example/skills",
            ],
        )

        self.assertEqual([item.name for item in result.skills], ["find-skills"])
        self.assertEqual(result.skills[0].invocation_source, "session")
        self.assertTrue(result.skills[0].primary)
        self.assertEqual(
            result.skills[0].arguments,
            "继续检查这个 GitHub 仓库并告诉我有哪些 Skill",
        )
        self.assertIn(
            "任务参数：继续检查这个 GitHub 仓库并告诉我有哪些 Skill",
            result.skills[0].instructions,
        )

    def test_current_explicit_skill_replaces_conversation_skill(self) -> None:
        library = _FakeSkillLibrary(("find-skills", "resume-review"))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        result = runtime.resolve_for_conversation(
            [],
            "/resume-review 改写这份简历",
            ["/find-skills 搜索简历 Skill"],
        )

        self.assertEqual([item.name for item in result.skills], ["resume-review"])
        self.assertEqual(result.skills[0].invocation_source, "slash")

    def test_long_skill_is_mounted_completely_instead_of_silently_truncated(self) -> None:
        library = _LongSkillLibrary(("long-review",))
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        result = runtime.resolve([], "/long-review 完成任务")

        self.assertGreater(len(result.skills[0].instructions), 12_000)
        self.assertTrue(result.skills[0].instructions.endswith("END_OF_SKILL"))

    def test_unknown_leading_slash_command_returns_clear_error(self) -> None:
        runtime = CareerSkillRuntime(_FakeSkillLibrary(("resume-review",)))  # type: ignore[arg-type]

        with self.assertRaisesRegex(LookupError, "未找到 Skill：missing-skill"):
            runtime.activate([], "/missing-skill 帮我分析")

    def test_rejects_more_than_three_activated_skills(self) -> None:
        names = ("skill-one", "skill-two", "skill-three", "skill-four")
        library = _FakeSkillLibrary(names)
        runtime = CareerSkillRuntime(library)  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "单轮最多激活 3 个 Skill"):
            runtime.activate([], " ".join(f"/{name}" for name in names))

    def test_response_prompt_contains_activated_skill_and_runtime_boundary(self) -> None:
        library = _FakeSkillLibrary(("resume-review",))
        activated = CareerSkillRuntime(library).activate(  # type: ignore[arg-type]
            [],
            "/resume-review",
        )
        runner = CareerResponseRunner.__new__(CareerResponseRunner)
        runner._agent_loop = SimpleNamespace(
            repository=SimpleNamespace(list_messages=lambda *_args, **_kwargs: []),
        )
        active_turn = SimpleNamespace(
            conversation=SimpleNamespace(actor_id="actor", id="conversation"),
            turn=SimpleNamespace(id="turn"),
        )
        context = ModelTurnContext(
            redacted_user_text="请优化这份简历",
            redacted_material_text="",
            redacted_job_text="",
            required_capabilities=frozenset({ModelCapability.TEXT}),
            contains_image_material=False,
            vision_images=(),
            received_attachment_kinds=(),
            pdf_without_extractable_text_count=0,
            activated_skills=activated,
        )

        messages = runner._build_prompt(active_turn, context)
        skill_prompt = messages[1].content

        self.assertIn('<activated_skill name="resume-review" invocation="slash">', skill_prompt)
        self.assertIn("确定性加载并挂载", skill_prompt)
        self.assertIn("请按事实完成求职分析", skill_prompt)
        self.assertIn("本轮用户输入：\n请优化这份简历", messages[-1].content)
        self.assertNotIn("/resume-review", messages[-1].content)

        payload = OpenAICompatibleChatClient._build_request_payload(
            ModelConnectionTarget(
                provider_key="deepseek",
                model_id="deepseek-v4-pro",
                api_base_url="https://api.deepseek.com/v1",
            ),
            messages,
            max_tokens=800,
        )
        serialized_messages = payload["messages"]
        self.assertIn("请按事实完成求职分析", serialized_messages[1]["content"])
        self.assertNotIn("/resume-review", serialized_messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
