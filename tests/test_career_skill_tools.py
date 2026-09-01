from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
from zipfile import ZipFile

import httpx

from src.career_assistant.contracts import (
    ActivatedSkill,
    ModelCapability,
)
from src.career_assistant.intake_graph import ModelTurnContext
from src.career_assistant.model_gateway import (
    ModelReadiness,
    ModelResolution,
    ModelResolutionReason,
)
from src.career_assistant.model_clients import (
    ChatMessage,
    FunctionToolCall,
    FunctionToolDefinition,
    ModelConnectionTarget,
    OpenAICompatibleChatClient,
    ToolModelResponse,
)
from src.career_assistant.persistence import ModelCostTier, ModelProfileRecord
from src.career_assistant.skill_tools import SkillToolRegistry
from src.career_assistant.response_runner import CareerResponseRunner
from src.services.skill_library_service import (
    SkillRepositoryInspection,
    SkillRepositoryInstallResult,
    SkillRepositoryItem,
    SkillLibraryService,
)


class _RepositoryLibrary:
    def __init__(self) -> None:
        self.inspected: list[str] = []
        self.installed: list[tuple[str, tuple[str, ...]]] = []

    def inspect_skill_repository(self, source: str) -> SkillRepositoryInspection:
        self.inspected.append(source)
        return SkillRepositoryInspection(
            repository_full_name="JimLiu/baoyu-skills",
            default_branch="main",
            homepage_url="https://github.com/JimLiu/baoyu-skills",
            stars=25_191,
            forks=2_801,
            license_spdx="MIT",
            pushed_at="2026-07-04T16:25:18Z",
            skills=(
                SkillRepositoryItem(
                    name="baoyu-image-gen",
                    description="生成图片",
                    path="skills/baoyu-image-gen/SKILL.md",
                    file_count=8,
                    size_bytes=20_000,
                ),
            ),
        )

    def install_skill_repository(
        self,
        source: str,
        skill_names: list[str] | tuple[str, ...],
    ) -> SkillRepositoryInstallResult:
        self.installed.append((source, tuple(skill_names)))
        return SkillRepositoryInstallResult(
            repository_full_name="JimLiu/baoyu-skills",
            installed_names=("baoyu-image-gen",),
            skipped_names=(),
            installed_file_count=8,
        )


def _profile(
    *,
    provider_key: str = "custom",
    capabilities: frozenset[ModelCapability] | None = None,
) -> ModelProfileRecord:
    now = datetime.now(UTC)
    return ModelProfileRecord(
        id=uuid4(),
        organization_id=uuid4(),
        profile_key="tool-model",
        display_name="Tool Model",
        provider_key=provider_key,
        model_id="tool-model",
        capabilities=capabilities or frozenset({ModelCapability.TEXT, ModelCapability.TOOLS}),
        cost_tier=ModelCostTier.FREE_QUOTA,
        priority=1,
        enabled=True,
        api_base_url="https://example.test/v1",
        created_at=now,
        updated_at=now,
    )


class SkillToolRegistryTests(unittest.TestCase):
    def test_repository_inspection_and_install_execute_real_library_operations(self) -> None:
        library = _RepositoryLibrary()
        registry = SkillToolRegistry(library)  # type: ignore[arg-type]

        inspection = registry.execute(
            FunctionToolCall(
                "call-1",
                "inspect_skill_repository",
                '{"repository":"git@github.com:JimLiu/baoyu-skills.git"}',
            ),
            execution_mode="model_tool_call",
            skill_name="find-skills",
        )
        installation = registry.execute(
            FunctionToolCall(
                "call-2",
                "install_skill_repository",
                '{"repository":"JimLiu/baoyu-skills","skill_names":["baoyu-image-gen"]}',
            ),
            execution_mode="model_tool_call",
            skill_name="find-skills",
        )

        inspection_payload = json.loads(inspection.content)
        installation_payload = json.loads(installation.content)
        self.assertEqual(library.inspected, ["git@github.com:JimLiu/baoyu-skills.git"])
        self.assertEqual(library.installed, [("JimLiu/baoyu-skills", ("baoyu-image-gen",))])
        self.assertEqual(inspection_payload["skills"][0]["name"], "baoyu-image-gen")
        self.assertEqual(installation_payload["installed_names"], ["baoyu-image-gen"])
        self.assertEqual(inspection.trace.result_count, 1)
        self.assertEqual(installation.trace.result_count, 1)

    def test_registry_search_uses_skills_sh_live_api(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "skills": [
                {
                    "skillId": "baoyu-image-gen",
                    "source": "jimliu/baoyu-skills",
                    "installs": 31542,
                },
            ],
        }
        with patch("src.career_assistant.skill_tools.requests.get", return_value=response):
            result = SkillToolRegistry(_RepositoryLibrary()).execute(  # type: ignore[arg-type]
                FunctionToolCall(
                    "call-search",
                    "search_skill_registry",
                    '{"query":"baoyu image"}',
                ),
                execution_mode="model_tool_call",
                skill_name="find-skills",
            )

        payload = json.loads(result.content)
        self.assertEqual(payload["data_source"], "skills.sh_live")
        self.assertEqual(payload["items"][0]["installs"], 31542)


class SkillRepositoryInstallerTests(unittest.TestCase):
    def test_installer_copies_complete_skill_directory_and_skips_existing(self) -> None:
        inspection = SkillRepositoryInspection(
            repository_full_name="example/skills",
            default_branch="main",
            homepage_url="https://github.com/example/skills",
            stars=10,
            forks=2,
            license_spdx="MIT",
            pushed_at="2026-08-20T00:00:00Z",
            skills=(
                SkillRepositoryItem(
                    name="demo-skill",
                    description="演示 Skill",
                    path="skills/demo-skill/SKILL.md",
                    file_count=2,
                    size_bytes=100,
                ),
            ),
        )
        archive_buffer = BytesIO()
        with ZipFile(archive_buffer, "w") as archive:
            archive.writestr(
                "skills-main/skills/demo-skill/SKILL.md",
                "---\nname: demo-skill\ndescription: demo\n---\n\nFollow workflow.",
            )
            archive.writestr(
                "skills-main/skills/demo-skill/scripts/run.py",
                "print('ok')\n",
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            config = SimpleNamespace(
                project_root=Path(temporary_directory),
                github_api_version="2022-11-28",
                github_token_env="GITHUB_TOKEN",
            )
            service = SkillLibraryService(config)  # type: ignore[arg-type]
            response = Mock(ok=True, content=archive_buffer.getvalue())
            with (
                patch.object(service, "inspect_skill_repository", return_value=inspection),
                patch("src.services.skill_library_service.requests.get", return_value=response),
            ):
                installed = service.install_skill_repository("example/skills")

            destination = Path(temporary_directory) / ".agents" / "skills" / "demo-skill"
            self.assertEqual(installed.installed_names, ("demo-skill",))
            self.assertEqual(installed.installed_file_count, 2)
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertTrue((destination / "scripts" / "run.py").is_file())

            with (
                patch.object(service, "inspect_skill_repository", return_value=inspection),
                patch("src.services.skill_library_service.requests.get") as get_request,
            ):
                repeated = service.install_skill_repository("example/skills")

            self.assertEqual(repeated.installed_names, ())
            self.assertEqual(repeated.skipped_names, ("demo-skill",))
            get_request.assert_not_called()


class ToolCallingClientTests(unittest.TestCase):
    def test_complete_with_tools_uses_official_message_contract(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "inspect_skill_repository",
                                            "arguments": '{"repository":"JimLiu/baoyu-skills"}',
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                },
            )

        client = OpenAICompatibleChatClient(
            httpx.Client(transport=httpx.MockTransport(handler)),
        )
        definition = FunctionToolDefinition(
            "inspect_skill_repository",
            "检查 Skill 仓库",
            {"type": "object", "properties": {"repository": {"type": "string"}}},
        )
        response = client.complete_with_tools(
            _profile(),
            None,
            [ChatMessage("user", "检查 JimLiu/baoyu-skills")],
            (definition,),
            tool_choice="auto",
            api_key="test-key",
        )

        self.assertEqual(response.tool_calls[0].name, "inspect_skill_repository")
        self.assertEqual(captured["tools"][0]["function"]["name"], "inspect_skill_repository")  # type: ignore[index]
        self.assertEqual(captured["tool_choice"], "auto")

        target = ModelConnectionTarget("custom", "tool-model", "https://example.test/v1")
        followup = OpenAICompatibleChatClient._build_request_payload(
            target,
            [
                ChatMessage("assistant", None, tool_calls=response.tool_calls),
                ChatMessage("tool", '{"items":[]}', tool_call_id="call-1"),
            ],
            max_tokens=100,
        )
        self.assertEqual(followup["messages"][0]["tool_calls"][0]["id"], "call-1")  # type: ignore[index]
        self.assertEqual(followup["messages"][1]["tool_call_id"], "call-1")  # type: ignore[index]


class _ToolDecisionClient:
    def __init__(self) -> None:
        self.calls = 0
        self.message_roles: list[list[str]] = []

    def complete_with_tools(self, _profile, _credential_env, messages, *_args, **_kwargs) -> ToolModelResponse:
        self.calls += 1
        self.message_roles.append([item.role for item in messages])
        if self.calls == 1:
            return ToolModelResponse(
                "",
                (
                    FunctionToolCall(
                        "call-inspect",
                        "inspect_skill_repository",
                        '{"repository":"git@github.com:JimLiu/baoyu-skills.git"}',
                    ),
                ),
            )
        if self.calls == 2:
            return ToolModelResponse(
                "",
                (
                    FunctionToolCall(
                        "call-install",
                        "install_skill_repository",
                        '{"repository":"JimLiu/baoyu-skills","skill_names":["baoyu-image-gen"]}',
                    ),
                ),
            )
        return ToolModelResponse("已检查并安装 baoyu-image-gen。", ())


def _tool_context() -> ModelTurnContext:
    return ModelTurnContext(
        redacted_user_text="分析并安装 JimLiu/baoyu-skills",
        redacted_material_text="",
        redacted_job_text="",
        required_capabilities=frozenset({ModelCapability.TEXT}),
        contains_image_material=False,
        vision_images=(),
        received_attachment_kinds=(),
        pdf_without_extractable_text_count=0,
        activated_skills=(
            ActivatedSkill(
                skill_id="find-skills",
                name="find-skills",
                description="寻找 Skill",
                instructions="先检查具体仓库，再安装用户要求的 Skill。",
                invocation_source="slash",
                arguments="分析并安装 JimLiu/baoyu-skills",
                primary=True,
            ),
        ),
    )


class CareerResponseToolRuntimeTests(unittest.TestCase):
    def test_agent_loop_inspects_installs_and_then_returns_final_answer(self) -> None:
        library = _RepositoryLibrary()
        client = _ToolDecisionClient()
        runner = CareerResponseRunner.__new__(CareerResponseRunner)
        runner._skill_tool_registry = SkillToolRegistry(library)  # type: ignore[arg-type]
        runner._chat_client = client
        resolution = ModelResolution(
            _profile(),
            ModelResolutionReason.USER_SELECTED,
            ModelReadiness.READY,
            None,
            "test-key",
        )

        final_content, traces = runner._complete_skill_agent(
            resolution,
            [ChatMessage("user", "分析并安装 JimLiu/baoyu-skills")],
            _tool_context(),
        )

        self.assertEqual(client.calls, 3)
        self.assertEqual(final_content, "已检查并安装 baoyu-image-gen。")
        self.assertEqual(library.inspected, ["git@github.com:JimLiu/baoyu-skills.git"])
        self.assertEqual(library.installed, [("JimLiu/baoyu-skills", ("baoyu-image-gen",))])
        self.assertEqual([trace.tool_name for trace in traces], [
            "inspect_skill_repository",
            "install_skill_repository",
        ])
        self.assertEqual(client.message_roles[1][-2:], ["assistant", "tool"])
        self.assertEqual(client.message_roles[2][-4:], ["assistant", "tool", "assistant", "tool"])

    def test_deepseek_text_profile_uses_official_tool_calling_compatibility(self) -> None:
        runner = CareerResponseRunner.__new__(CareerResponseRunner)
        runner._skill_tool_registry = SkillToolRegistry(_RepositoryLibrary())  # type: ignore[arg-type]
        resolution = ModelResolution(
            _profile(
                provider_key="deepseek",
                capabilities=frozenset({ModelCapability.TEXT}),
            ),
            ModelResolutionReason.USER_SELECTED,
            ModelReadiness.READY,
            None,
            "test-key",
        )

        self.assertTrue(runner._can_run_skill_tools(resolution, _tool_context()))

    def test_unknown_text_provider_without_tools_keeps_prompt_only_path(self) -> None:
        runner = CareerResponseRunner.__new__(CareerResponseRunner)
        runner._skill_tool_registry = SkillToolRegistry(_RepositoryLibrary())  # type: ignore[arg-type]
        resolution = ModelResolution(
            _profile(
                provider_key="custom",
                capabilities=frozenset({ModelCapability.TEXT}),
            ),
            ModelResolutionReason.USER_SELECTED,
            ModelReadiness.READY,
            None,
            "test-key",
        )

        self.assertFalse(runner._can_run_skill_tools(resolution, _tool_context()))


if __name__ == "__main__":
    unittest.main()
