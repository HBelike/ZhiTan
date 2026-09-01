from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID


class AttachmentKind(StrEnum):
    """求职助手本轮可接收的临时材料类型。"""

    RESUME_PDF = "resume_pdf"
    RESUME_DOCUMENT = "resume_document"
    RESUME_IMAGE = "resume_image"
    INTERVIEW_EVIDENCE_IMAGE = "interview_evidence_image"
    JOB_DESCRIPTION_IMAGE = "job_description_image"
    JOB_DESCRIPTION_FILE = "job_description_file"


class ModelCapability(StrEnum):
    """模型档案可声明的能力标签，用于后续模型路由。"""

    TEXT = "text"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    EMBEDDING = "embedding"
    TOOLS = "tools"
    TRANSCRIPTION = "transcription"


class SkillExecutionMode(StrEnum):
    """Skill 在求职助手中的运行方式。"""

    PROMPT = "prompt"
    TOOL = "tool"


class ModelSelectionMode(StrEnum):
    """用户在聊天页选择模型时可使用的策略。"""

    FREE_QUOTA_FIRST = "free_quota_first"
    SPECIFIC_PROFILE = "specific_profile"


class AgentStepName(StrEnum):
    """LangGraph 求职助手工作流的稳定步骤名称。"""

    VALIDATE_INPUT = "validate_input"
    PARSE_MATERIAL = "parse_material"
    EXTRACT_JOB_DESCRIPTION = "extract_job_description"
    REDACT_SENSITIVE_DATA = "redact_sensitive_data"
    BUILD_CONTEXT = "build_context"
    ANALYZE_MATCH = "analyze_match"
    GENERATE_RESPONSE = "generate_response"
    PERSIST_HISTORY = "persist_history"
    CLEANUP_TEMPORARY_FILES = "cleanup_temporary_files"


@dataclass(frozen=True)
class ModelSelectionRequest:
    """描述一轮对话的模型选择意图，不包含任何 API Key。"""

    mode: ModelSelectionMode = ModelSelectionMode.FREE_QUOTA_FIRST
    profile_id: UUID | None = None
    required_capabilities: frozenset[ModelCapability] = field(
        default_factory=lambda: frozenset({ModelCapability.TEXT}),
    )

    def validate(self) -> None:
        """校验策略与模型档案选择是否一致。"""

        if not self.required_capabilities:
            raise ValueError("模型选择至少需要声明一种能力")

        if self.mode is ModelSelectionMode.SPECIFIC_PROFILE and self.profile_id is None:
            raise ValueError("指定模型档案时必须提供 profile_id")

        if self.mode is ModelSelectionMode.FREE_QUOTA_FIRST and self.profile_id is not None:
            raise ValueError("免费额度优先策略不能同时绑定特定模型档案")


@dataclass(frozen=True)
class AttachmentDescriptor:
    """仅在当前 Agent Turn 内存活的附件描述符。

    temporary_path 指向本机临时目录，绝不作为持久化字段写入数据库。附件内容由
    后续解析工具读取，Agent Turn 结束时由清理步骤无条件删除该路径。
    """

    attachment_id: UUID
    kind: AttachmentKind
    original_filename: str
    media_type: str
    size_bytes: int
    temporary_path: Path
    expires_at: datetime

    def validate(self) -> None:
        """校验临时附件元数据，阻止空文件、目录路径和过期材料进入解析流程。"""

        if not self.original_filename.strip():
            raise ValueError("附件必须包含原始文件名")

        if not self.media_type.strip():
            raise ValueError("附件必须包含 MIME 类型")

        if self.size_bytes <= 0:
            raise ValueError("附件大小必须大于零")

        if self.temporary_path.name in {"", ".", ".."}:
            raise ValueError("临时附件路径无效")

        if self.expires_at <= datetime.now(tz=self.expires_at.tzinfo):
            raise ValueError("附件临时访问期限已过")


@dataclass(frozen=True)
class InterviewEvidence:
    """单轮对话可引用的面经证据片段，仅在内存中的 Prompt 上下文存活。"""

    experience_id: UUID
    citation: str
    content: str
    source_url: str | None = None

    def validate(self) -> None:
        """阻止空引用或超长资料片段绕过面经检索边界进入模型上下文。"""

        if not self.citation.strip():
            raise ValueError("面经引用标识不能为空")
        if not self.content.strip():
            raise ValueError("面经引用内容不能为空")
        if len(self.content) > 2_000:
            raise ValueError("单条面经引用内容不能超过 2000 个字符")


@dataclass(frozen=True)
class ActivatedSkill:
    """用户在当前 Turn 显式激活的一份 Skill 指令。"""

    skill_id: str
    name: str
    description: str
    instructions: str
    execution_mode: SkillExecutionMode = SkillExecutionMode.PROMPT
    tool_names: tuple[str, ...] = ()
    invocation_source: str = "mention"
    arguments: str = ""
    primary: bool = False

    def validate(self) -> None:
        """限制 Skill 注入长度，避免异常文件挤占整轮模型上下文。"""

        if not self.skill_id.strip():
            raise ValueError("Skill ID 不能为空")
        if not self.name.strip():
            raise ValueError("Skill 名称不能为空")
        if not self.instructions.strip():
            raise ValueError(f"Skill {self.name} 没有可执行指令")
        if len(self.instructions) > 48_000:
            raise ValueError(f"Skill {self.name} 的激活指令超过 48000 个字符")
        if self.execution_mode is SkillExecutionMode.TOOL and not self.tool_names:
            raise ValueError(f"Tool 型 Skill {self.name} 没有关联任何受控工具")


@dataclass(frozen=True)
class SkillExecutionTrace:
    """单次真实 Skill 工具执行的安全摘要，不包含工具原始结果。"""

    skill_name: str
    tool_name: str
    execution_mode: str
    status: str
    result_count: int = 0
    message: str = ""


@dataclass(frozen=True)
class CareerInboundMessage:
    """从 WebUI 进入 AgentLoop 的统一输入事件。"""

    turn_id: UUID
    conversation_id: UUID
    actor_id: UUID
    text: str
    model_selection: ModelSelectionRequest = field(default_factory=ModelSelectionRequest)
    job_url: str | None = None
    attachments: tuple[AttachmentDescriptor, ...] = ()
    prepared_attachment_kinds: tuple[AttachmentKind, ...] = ()
    interview_evidence: tuple[InterviewEvidence, ...] = ()
    activated_skills: tuple[ActivatedSkill, ...] = ()
    effective_text: str | None = None
    candidate_profile_context: str = ""
    target_role_context: str = ""
    context_binding_version: int | None = None

    def validate(self) -> None:
        """确保本轮输入至少有文本、职位链接或临时附件之一。"""

        has_text = bool(self.text.strip())
        has_job_url = bool((self.job_url or "").strip())
        if (
            not has_text
            and not has_job_url
            and not self.attachments
            and not self.prepared_attachment_kinds
        ):
            raise ValueError("至少需要提供文本、职位链接或附件中的一种输入")

        self.model_selection.validate()
        for attachment in self.attachments:
            attachment.validate()
        if len(set(self.prepared_attachment_kinds)) != len(self.prepared_attachment_kinds):
            raise ValueError("已解析附件类型不能重复")
        if len(self.interview_evidence) > 6:
            raise ValueError("单轮最多引用 6 条面经证据")
        for evidence in self.interview_evidence:
            evidence.validate()
        if len(self.activated_skills) > 3:
            raise ValueError("单轮最多激活 3 个 Skill")
        for skill in self.activated_skills:
            skill.validate()
        if len(self.candidate_profile_context) > 30_000:
            raise ValueError("基准简历上下文不能超过 30000 个字符")
        if len(self.target_role_context) > 30_000:
            raise ValueError("目标岗位上下文不能超过 30000 个字符")
        if self.context_binding_version is not None and self.context_binding_version <= 0:
            raise ValueError("上下文绑定版本必须为正整数")
