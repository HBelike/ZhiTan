from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.career_assistant import greetings as greeting_module
from src.career_assistant.context_profiles import CandidateProfileRecord
from src.career_assistant.contracts import ModelCapability, ModelSelectionMode
from src.career_assistant.greetings import (
    CareerGreetingService,
    GreetingCandidateNotFoundError,
    GreetingGenerationError,
    GreetingJobInput,
    GreetingJobValidationError,
    GreetingModelUnavailableError,
)
from src.career_assistant.model_gateway import (
    ModelProfileAvailability,
    ModelReadiness,
    ModelResolution,
    ModelResolutionReason,
)
from src.career_assistant.persistence.model_profile_repository import (
    ModelCostTier,
    ModelProfileRecord,
)


ORGANIZATION_ID = uuid4()
ACTOR_ID = uuid4()
CANDIDATE_ID = uuid4()


def profile(
    provider_key: str,
    model_id: str,
    *,
    priority: int = 100,
    readiness: ModelReadiness = ModelReadiness.READY,
) -> tuple[ModelProfileRecord, ModelProfileAvailability]:
    now = datetime.now(UTC)
    record = ModelProfileRecord(
        id=uuid4(),
        organization_id=ORGANIZATION_ID,
        profile_key=f"{provider_key}-{model_id}-{priority}",
        display_name=model_id,
        provider_key=provider_key,
        model_id=model_id,
        capabilities=frozenset({ModelCapability.TEXT}),
        cost_tier=ModelCostTier.PAID,
        priority=priority,
        enabled=readiness is not ModelReadiness.DISABLED,
        api_base_url="https://api.deepseek.com",
        created_at=now,
        updated_at=now,
    )
    return record, ModelProfileAvailability(
        profile=record,
        readiness=readiness,
        credential_env_name="DEEPSEEK_API_KEY",
        blocked_reason=None,
    )


def candidate() -> CandidateProfileRecord:
    return CandidateProfileRecord(
        id=CANDIDATE_ID,
        organization_id=ORGANIZATION_ID,
        actor_id=ACTOR_ID,
        display_name="候选人",
        source_filename="resume.pdf",
        resume_outline=(
            "教育经历\n211 院校计算机专业本科\n"
            "工作经历\n在银行业务系统中参与 Java 微服务开发与投产，负责接口实现和故障排查。\n"
            "项目经历\n参与分布式任务平台建设，完成监控告警与上线交付。"
        ),
        version=1,
        created_at=datetime.now(UTC),
    )


def job() -> GreetingJobInput:
    return GreetingJobInput(
        id="job-1",
        title="Java 微服务工程师",
        company="示例科技",
        recruiter="彭女士·招聘专员",
        description=(
            "岗位职责\n负责 Java 微服务接口开发、线上故障排查和版本交付。\n"
            "任职要求\n本科及以上学历，有分布式系统项目经验。"
        ),
        skills=("Java", "微服务", "分布式"),
        source_url="https://www.zhipin.com/job-1",
    )


def output(
    *,
    message: str | None = None,
    resume_ids: list[str] | None = None,
    jd_ids: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "message": message or (
                "彭女士您好，我是 211 院校计算机本科，参与过银行业务系统的 Java 微服务开发与投产，"
                "也做过分布式任务平台的监控告警和上线交付。岗位关注服务开发与故障排查，"
                "与我的实际经历比较贴近，方便的话想进一步了解团队当前项目。"
            ),
            "resume_evidence_ids": resume_ids or ["CV-002", "CV-004", "CV-006"],
            "jd_evidence_ids": jd_ids or ["JD-002", "JD-004"],
            "warnings": [],
        },
        ensure_ascii=False,
    )


class FakeContextRepository:
    def __init__(self, record: CandidateProfileRecord | None) -> None:
        self.record = record
        self.calls: list[tuple[UUID, UUID]] = []

    def get_candidate_profile(
        self,
        actor_id: UUID,
        profile_id: UUID,
    ) -> CandidateProfileRecord | None:
        self.calls.append((actor_id, profile_id))
        return self.record


class FakeGateway:
    def __init__(self, items: list[ModelProfileAvailability]) -> None:
        self.items = items
        self.selection = None

    def list_availability(self, organization_id: UUID) -> list[ModelProfileAvailability]:
        assert organization_id == ORGANIZATION_ID
        return self.items

    def resolve(self, organization_id: UUID, selection):
        assert organization_id == ORGANIZATION_ID
        self.selection = selection
        selected = next(item.profile for item in self.items if item.profile.id == selection.profile_id)
        return ModelResolution(
            profile=selected,
            reason=ModelResolutionReason.USER_SELECTED,
            readiness=ModelReadiness.READY,
            credential_env_name="DEEPSEEK_API_KEY",
            credential="secret",
        )


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete_json(
        self,
        profile,
        credential_env_name,
        messages,
        *,
        api_key,
        options,
        operation,
    ) -> str:
        self.calls.append(
            {
                "profile": profile,
                "credential_env_name": credential_env_name,
                "messages": list(messages),
                "api_key": api_key,
                "options": options,
                "operation": operation,
            },
        )
        return self.responses.pop(0)


def build_service(
    responses: list[str],
    *,
    candidate_record: CandidateProfileRecord | None = None,
    availability: list[ModelProfileAvailability] | None = None,
):
    if availability is None:
        availability = [profile("deepseek", "deepseek-v4-pro")[1]]
    repository = FakeContextRepository(candidate() if candidate_record is None else candidate_record)
    gateway = FakeGateway(availability)
    client = FakeClient(responses)
    service = CareerGreetingService(
        context_repository=repository,
        model_gateway=gateway,
        model_client=client,
    )
    return service, repository, gateway, client


def test_prompt_prioritizes_supported_candidate_advantages_without_becoming_a_template() -> None:
    prompt = greeting_module._SYSTEM_PROMPT

    assert "候选人优势扫描" in prompt
    assert all(category in prompt for category in ("学历", "工作经验", "奖项", "个人项目"))
    assert "某类没有简历证据时必须彻底省略" in prompt
    assert "不得只用同一段工作或同一个项目的多个技术细节占满全文" in prompt
    assert "仅示范选材思路和信息密度，不是固定模板" in prompt
    assert "示例中的学校层次、年限、公司、项目、网址、技术和成果" in prompt
    assert "禁止迁移到真实输出" in prompt


def test_prompt_requires_academic_distinctions_and_verifiable_result_links() -> None:
    prompt = greeting_module._SYSTEM_PROMPT

    assert all(signal in prompt for signal in ("985", "211", "双一流", "QS", "博士", "硕士"))
    assert all(signal in prompt for signal in ("论文", "专利", "个人网站", "GitHub", "PR"))
    assert "至少原样附上一个" in prompt
    assert "简历没有明确写出" in prompt


def test_user_prompt_requires_a_full_resume_scan_before_jd_matching() -> None:
    cv_evidence = CareerGreetingService._number_evidence(candidate().resume_outline, "CV")
    jd_evidence = CareerGreetingService._number_evidence(job().description, "JD")

    prompt = CareerGreetingService._user_prompt(job(), cv_evidence, jd_evidence, "")

    assert "先完整阅读全部 CV 证据" in prompt
    assert "不要只选择与 JD 最接近的单一工作或项目技术片段" in prompt


def test_user_prompt_lists_required_advantage_categories_and_result_links() -> None:
    candidate_record = replace(
        candidate(),
        resume_outline=(
            "教育经历\n某大学 QS 前 200 计算机博士\n"
            "论文成果\n第一作者发表 SCI 论文。\n"
            "个人成果\nGitHub PR：https://github.com/pulls?q=is%3Apr+author%3Atest"
        ),
    )
    cv_evidence = CareerGreetingService._number_evidence(candidate_record.resume_outline, "CV")
    jd_evidence = CareerGreetingService._number_evidence(job().description, "JD")

    prompt = CareerGreetingService._user_prompt(job(), cv_evidence, jd_evidence, "")

    assert "<required_resume_advantages>" in prompt
    assert "学校层次或排名：CV-002" in prompt
    assert "博士或硕士学历：CV-002" in prompt
    assert "论文或专利成果：CV-003、CV-004" in prompt
    assert "[CV-006] https://github.com/pulls?q=is%3Apr+author%3Atest" in prompt


def test_generate_uses_actor_resume_and_exact_deepseek_profile() -> None:
    flash = profile("deepseek", "deepseek-v4-flash", priority=1)[1]
    lower_priority_pro = profile("deepseek", "deepseek-v4-pro", priority=20)[1]
    preferred_pro = profile("deepseek", "deepseek-v4-pro", priority=5)[1]
    service, repository, gateway, client = build_service(
        [output()],
        availability=[flash, lower_priority_pro, preferred_pro],
    )

    result = service.generate(
        ORGANIZATION_ID,
        ACTOR_ID,
        CANDIDATE_ID,
        job(),
    )

    assert repository.calls == [(ACTOR_ID, CANDIDATE_ID)]
    assert gateway.selection.mode is ModelSelectionMode.SPECIFIC_PROFILE
    assert gateway.selection.profile_id == preferred_pro.profile.id
    assert gateway.selection.required_capabilities == frozenset({ModelCapability.TEXT})
    assert result.model_id == "deepseek-v4-pro"
    assert result.provider_key == "deepseek"
    assert [item.id for item in result.resume_evidence] == ["CV-002", "CV-004", "CV-006"]
    assert [item.id for item in result.jd_highlights] == ["JD-002", "JD-004"]
    call = client.calls[0]
    assert call["operation"] == "greeting"
    assert call["options"].temperature == 0.2
    assert call["options"].max_tokens == 800
    assert call["options"].thinking is False
    user_prompt = call["messages"][-1].content
    assert "<candidate_resume>" in user_prompt
    assert "<job_description>" in user_prompt
    assert "彭女士·招聘专员" in user_prompt
    assert "严格输出 json" in user_prompt


def test_generate_rejects_missing_candidate_for_actor() -> None:
    service, _, _, client = build_service([output()], candidate_record=SimpleNamespace())
    service._context_repository.record = None

    with pytest.raises(GreetingCandidateNotFoundError):
        service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert client.calls == []


def test_generate_never_falls_back_from_deepseek_v4_pro() -> None:
    service, _, _, client = build_service(
        [output()],
        availability=[profile("deepseek", "deepseek-v4-flash")[1]],
    )

    with pytest.raises(GreetingModelUnavailableError):
        service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert client.calls == []


def test_generate_requires_complete_job_description() -> None:
    service, _, _, _ = build_service([output()])

    with pytest.raises(GreetingJobValidationError):
        service.generate(
            ORGANIZATION_ID,
            ACTOR_ID,
            CANDIDATE_ID,
            GreetingJobInput(id="job-1", title="Java 工程师", company="示例", recruiter="", description=""),
        )


def test_generate_retries_once_after_unknown_evidence_reference() -> None:
    service, _, _, client = build_service(
        [output(resume_ids=["CV-999"]), output()],
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message.startswith("彭女士您好")
    assert len(client.calls) == 2
    correction = client.calls[1]["messages"][-1].content
    assert "CV-999" in correction
    assert "纠正" in correction


def test_generate_rejects_second_invalid_response() -> None:
    service, _, _, client = build_service(
        [output(resume_ids=["CV-999"]), output(resume_ids=["CV-999"])],
    )

    with pytest.raises(GreetingGenerationError, match="CV-999"):
        service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert len(client.calls) == 2


def test_generate_retries_when_supported_academic_advantage_is_omitted() -> None:
    academic_candidate = replace(
        candidate(),
        resume_outline=(
            "教育经历\n某大学 QS前200 计算机博士\n"
            "论文成果\n以第一作者发表 SCI论文。\n"
            "工作经历\n参与 Java 服务开发与交付。"
        ),
    )
    omitted = output(
        message=(
            "彭女士您好，我是计算机专业毕业，参与过 Java 服务开发与交付。"
            "看到岗位关注微服务接口与版本交付，这与我的实际经历有交集，"
            "方便的话想进一步了解团队目前的项目重点。"
        ),
        resume_ids=["CV-006"],
    )
    supported = output(
        message=(
            "彭女士您好，我是某大学 QS前200 计算机博士，以第一作者发表过 SCI论文，"
            "也参与过 Java 服务开发与交付。看到岗位关注微服务接口与版本交付，"
            "与我的经历有交集，想进一步沟通。"
        ),
        resume_ids=["CV-002", "CV-004", "CV-006"],
    )
    service, _, _, client = build_service(
        [omitted, supported],
        candidate_record=academic_candidate,
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert "QS前200" in result.message
    assert "博士" in result.message
    assert len(client.calls) == 2
    assert "高价值学历或学术成果" in client.calls[1]["messages"][-1].content


def test_generate_retries_when_supported_portfolio_link_is_omitted() -> None:
    linked_candidate = replace(
        candidate(),
        resume_outline=(
            "教育经历\n东华大学（211）计算机硕士\n"
            "个人成果\n个人网站：https://example.dev\n"
            "工作经历\n参与 Java 微服务开发与投产。"
        ),
    )
    omitted = output(
        message=(
            "彭女士您好，我是东华大学 211 计算机硕士，参与过 Java 微服务开发与投产。"
            "看到岗位关注服务开发、故障排查和版本交付，与我的实际经历有交集，"
            "方便的话想进一步了解团队目前的项目。"
        ),
        resume_ids=["CV-002", "CV-006"],
    )
    supported = output(
        message=(
            "彭女士您好，我是东华大学 211 计算机硕士，参与过 Java 微服务开发与投产；"
            "个人成果可查看 https://example.dev。岗位方向与我的经历有交集，"
            "方便的话想进一步沟通。"
        ),
        resume_ids=["CV-002", "CV-004", "CV-006"],
    )
    service, _, _, client = build_service(
        [omitted, supported],
        candidate_record=linked_candidate,
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert "https://example.dev" in result.message
    assert len(client.calls) == 2
    assert "个人成果链接" in client.calls[1]["messages"][-1].content


def test_literal_validation_accepts_supported_combined_tech_stacks() -> None:
    message = "我使用 Java/SpringBoot 开发服务，并通过 Vue3+FastAPI+LangGraph 搭建应用。"
    source_text = "技术栈包括 Java、SpringBoot、Vue3、FastAPI、LangGraph。"

    CareerGreetingService._validate_literal_tokens(message, source_text)


def test_literal_validation_preserves_special_characters_inside_tech_names() -> None:
    message = "项目使用 C++/Python、C#、.NET、Node.js 和 CI/CD。"
    source_text = "掌握 C++、Python、C#、.NET、Node.js、CI/CD。"

    CareerGreetingService._validate_literal_tokens(message, source_text)


def test_literal_validation_rejects_unsupported_component_in_combined_stack() -> None:
    message = "我使用 Java/ImaginarySDK 开发服务。"
    source_text = "项目使用 Java 和 SpringBoot。"

    with pytest.raises(GreetingGenerationError, match="ImaginarySDK"):
        CareerGreetingService._validate_literal_tokens(message, source_text)


def test_regeneration_includes_previous_message_and_rejects_same_text() -> None:
    previous = json.loads(output())["message"]
    revised = (
        "彭女士您好，我是 211 院校计算机本科，看到岗位侧重 Java 服务交付和线上问题处理。"
        "我在银行业务系统中参与过"
        "微服务接口开发、投产和故障排查，也完成过分布式任务平台的监控告警建设。"
        "这些经历与岗位方向有交集，想和您进一步聊聊团队目前的工作重点。"
    )
    service, _, _, client = build_service([output(message=previous), output(message=revised)])

    result = service.generate(
        ORGANIZATION_ID,
        ACTOR_ID,
        CANDIDATE_ID,
        job(),
        previous_message=previous,
    )

    assert result.message == revised
    assert len(client.calls) == 2
    first_prompt = client.calls[0]["messages"][-1].content
    assert previous in first_prompt
