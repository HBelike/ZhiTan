from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from src.career_assistant.attachments import ParsedAttachment
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind, ModelCapability
from src.career_assistant.model_gateway import (
    ModelReadiness,
    ModelResolution,
    ModelResolutionReason,
)
from src.career_assistant.persistence.model_profile_repository import (
    ModelCostTier,
    ModelProfileRecord,
)
from src.career_assistant.resume_assistant.models import ResumeSuggestion
from src.career_assistant.resume_assistant.service import ResumeOptimizationService


class FakeResumeRepository:
    def __init__(self) -> None:
        self.created = []

    def create(self, record):
        self.created.append(record)
        return record


class FakeModelRepository:
    def __init__(self, profiles: list[ModelProfileRecord]) -> None:
        self.profiles = profiles

    def list_profiles(self, organization_id: UUID, *, include_disabled: bool):
        return [
            profile
            for profile in self.profiles
            if profile.organization_id == organization_id
            and (include_disabled or profile.enabled)
        ]

    def get_profile(self, organization_id: UUID, profile_id: UUID):
        return next(
            (
                profile
                for profile in self.profiles
                if profile.organization_id == organization_id and profile.id == profile_id
            ),
            None,
        )


class FakeModelGateway:
    def __init__(self, model_repository: FakeModelRepository) -> None:
        self.model_repository = model_repository
        self.requests = []

    def resolve(self, organization_id: UUID, selection):
        self.requests.append(selection)
        profile = self.model_repository.get_profile(organization_id, selection.profile_id)
        if profile is None:
            raise LookupError("测试模型不存在")
        return ModelResolution(
            profile=profile,
            reason=ModelResolutionReason.USER_SELECTED,
            readiness=ModelReadiness.READY,
            credential_env_name="TEST_API_KEY",
            credential="test-key",
        )


class FakeChatClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def complete(self, *args, **kwargs) -> str:
        return self.responses.pop(0)


class FakeAttachmentParser:
    def __init__(self, text_by_filename: dict[str, str]) -> None:
        self.text_by_filename = text_by_filename

    def parse(self, descriptor: AttachmentDescriptor) -> ParsedAttachment:
        return ParsedAttachment(
            kind=descriptor.kind,
            media_type=descriptor.media_type,
            page_count=1,
            image_width=None,
            image_height=None,
            extracted_text=self.text_by_filename[descriptor.original_filename],
            requires_vision_model=False,
            image_bytes=None,
        )


class ResumeAssistantServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.organization_id = uuid4()
        self.owner_id = uuid4()
        self.profile = self._profile(
            profile_key="deepseek-v4-flash",
            display_name="DeepSeek V4 Flash",
            model_id="deepseek-v4-flash",
        )

    def _profile(
        self,
        *,
        profile_key: str,
        display_name: str,
        model_id: str,
    ) -> ModelProfileRecord:
        now = datetime.now(UTC)
        return ModelProfileRecord(
            id=uuid4(),
            organization_id=self.organization_id,
            profile_key=profile_key,
            display_name=display_name,
            provider_key="deepseek",
            model_id=model_id,
            capabilities=frozenset({ModelCapability.TEXT}),
            cost_tier=ModelCostTier.FREE_QUOTA,
            priority=100,
            enabled=True,
            api_base_url="https://api.deepseek.com/v1",
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _descriptor(path: Path, filename: str, kind: AttachmentKind) -> AttachmentDescriptor:
        return AttachmentDescriptor(
            attachment_id=uuid4(),
            kind=kind,
            original_filename=filename,
            media_type="application/pdf" if filename.endswith(".pdf") else "image/png",
            size_bytes=path.stat().st_size,
            temporary_path=path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    @staticmethod
    def _suggestion() -> ResumeSuggestion:
        return ResumeSuggestion(
            id="s1",
            category="匹配",
            title="突出 Agent 工作流经验",
            rationale="岗位要求具备 Agent 编排经验",
            original_evidence="参与多 Agent 工作流开发",
            job_evidence="负责 Agent 应用开发",
            proposed_change="将 Agent 编排能力前置展示",
            priority="high",
        )

    def _service(
        self,
        *,
        storage_root: Path,
        parser: FakeAttachmentParser,
        responses: list[str],
        profiles: list[ModelProfileRecord] | None = None,
    ):
        repository = FakeResumeRepository()
        model_repository = FakeModelRepository(profiles or [self.profile])
        gateway = FakeModelGateway(model_repository)
        service = ResumeOptimizationService(
            repository=repository,
            model_repository=model_repository,
            model_gateway=gateway,
            chat_client=FakeChatClient(responses),
            attachment_parser=parser,
            storage_root=storage_root,
        )
        return service, repository, gateway

    def test_analyze_returns_suggestions_without_persisting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resume_path = root / "resume.pdf"
            screenshot_path = root / "job.png"
            resume_path.write_bytes(b"resume")
            screenshot_path.write_bytes(b"image")
            payload = {
                "job_title": "Agent 开发工程师",
                "analysis_summary": "经历匹配，但 Agent 编排能力需要前置。",
                "suggestions": [self._suggestion().__dict__],
            }
            service, repository, gateway = self._service(
                storage_root=root / "storage",
                parser=FakeAttachmentParser(
                    {"resume.pdf": "原始简历正文", "job.png": "岗位要求：Agent 编排"},
                ),
                responses=[json.dumps(payload, ensure_ascii=False)],
            )

            result = service.analyze(
                self.organization_id,
                self._descriptor(resume_path, "resume.pdf", AttachmentKind.RESUME_PDF),
                "",
                (
                    self._descriptor(
                        screenshot_path,
                        "job.png",
                        AttachmentKind.JOB_DESCRIPTION_IMAGE,
                    ),
                ),
                "突出工程落地能力",
                None,
            )

            self.assertEqual(result.job_title, "Agent 开发工程师")
            self.assertEqual(len(result.suggestions), 1)
            self.assertEqual(repository.created, [])
            self.assertEqual(gateway.requests[0].profile_id, self.profile.id)

    def test_generate_does_not_persist_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = (
                "# 张三\n\n联系方式：zhangsan@example.com\n\n## 项目经历\n\n"
                "参与多 Agent 工作流开发，负责人工审核流程。\n" * 3
            )
            generated = (
                "# 张三\n\n联系方式：zhangsan@example.com\n\n## 项目经历\n\n"
                "参与多 Agent 工作流开发，基于 LangGraph 完善人工审核流程。\n" * 3
            )
            service, repository, _ = self._service(
                storage_root=root / "storage",
                parser=FakeAttachmentParser({}),
                responses=[generated],
            )

            result = service.generate(
                self.organization_id,
                original,
                "Agent 开发工程师岗位要求",
                (self._suggestion(),),
                "保持事实不变",
                self.profile.id,
            )

            self.assertIn("LangGraph", result)
            self.assertEqual(repository.created, [])

    def test_generate_retries_once_when_first_result_deviates_from_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = (
                "# 张三\n\n联系方式：zhangsan@example.com\n\n## 项目经历\n\n"
                "参与多 Agent 工作流开发，负责人工审核流程。\n" * 3
            )
            deviated = "# 李四\n\n负责完全无关的销售管理工作。\n" * 6
            faithful = original.replace("负责人工审核流程", "基于 LangGraph 完善人工审核流程")
            service, repository, _ = self._service(
                storage_root=root / "storage",
                parser=FakeAttachmentParser({}),
                responses=[deviated, faithful],
            )

            result = service.generate(
                self.organization_id,
                original,
                "Agent 开发工程师岗位要求",
                (self._suggestion(),),
                "保持事实不变",
                self.profile.id,
            )

            self.assertEqual(result, faithful.strip())
            self.assertEqual(repository.created, [])

    def test_generate_rejects_two_results_that_deviate_from_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = (
                "# 张三\n\n联系方式：zhangsan@example.com\n\n## 项目经历\n\n"
                "参与多 Agent 工作流开发，负责人工审核流程。\n" * 3
            )
            service, _, _ = self._service(
                storage_root=root / "storage",
                parser=FakeAttachmentParser({}),
                responses=[
                    "# 李四\n\n负责完全无关的销售管理工作。\n" * 6,
                    "# 王五\n\n负责完全无关的市场策划工作。\n" * 6,
                ],
            )

            with self.assertRaisesRegex(ValueError, "与原始简历偏离较大"):
                service.generate(
                    self.organization_id,
                    original,
                    "Agent 开发工程师岗位要求",
                    (self._suggestion(),),
                    "保持事实不变",
                    self.profile.id,
                )

    def test_save_record_is_the_only_step_that_creates_history_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "resume.pdf"
            source_path.write_bytes(b"original-resume")
            storage_root = root / "storage"
            service, repository, _ = self._service(
                storage_root=storage_root,
                parser=FakeAttachmentParser({}),
                responses=[],
            )

            record = service.save_record(
                organization_id=self.organization_id,
                owner_id=self.owner_id,
                creator_name="测试用户",
                source_descriptor=self._descriptor(
                    source_path,
                    "resume.pdf",
                    AttachmentKind.RESUME_PDF,
                ),
                original_preview_markdown="原始简历正文",
                job_title="Agent 开发工程师",
                job_description_text="岗位要求",
                extra_prompt="突出项目落地",
                suggestions=(self._suggestion(),),
                optimized_markdown="# 优化后简历\n\n完整正文",
                model_profile_id=self.profile.id,
            )

            self.assertEqual(len(repository.created), 1)
            self.assertEqual(Path(record.original_file_path).read_bytes(), b"original-resume")
            self.assertEqual(
                Path(record.optimized_text_path)
                .read_text(encoding="utf-8")
                .strip(),
                "# 优化后简历\n\n完整正文",
            )
            self.assertTrue(Path(record.optimized_docx_path).exists())
            self.assertTrue(Path(record.optimized_pdf_path).exists())
            self.assertTrue(Path(record.original_preview_pdf_path).exists())

    def test_default_model_selection_matches_normalized_deepseek_v4_flash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resume_path = root / "resume.pdf"
            resume_path.write_bytes(b"resume")
            other = self._profile(
                profile_key="other-model",
                display_name="其他模型",
                model_id="other-model",
            )
            payload = {
                "job_title": "Agent 开发工程师",
                "analysis_summary": "匹配",
                "suggestions": [self._suggestion().__dict__],
            }
            service, _, gateway = self._service(
                storage_root=root / "storage",
                parser=FakeAttachmentParser({"resume.pdf": "原始简历正文"}),
                responses=[json.dumps(payload, ensure_ascii=False)],
                profiles=[other, self.profile],
            )

            service.analyze(
                self.organization_id,
                self._descriptor(resume_path, "resume.pdf", AttachmentKind.RESUME_PDF),
                "Agent 开发工程师岗位要求",
                (),
                "",
                None,
            )

            self.assertEqual(gateway.requests[0].profile_id, self.profile.id)


if __name__ == "__main__":
    unittest.main()
