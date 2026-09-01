"""基于 LangGraph 的求职助手输入处理图。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.career_assistant.agent_loop import ActiveAgentTurn, CareerAgentLoop
from src.career_assistant.attachments import (
    AttachmentParser,
    ParsedAttachment,
    TemporaryAttachmentStore,
)
from src.career_assistant.contracts import (
    ActivatedSkill,
    AgentStepName,
    AttachmentKind,
    CareerInboundMessage,
    InterviewEvidence,
    ModelCapability,
)
from src.career_assistant.job_sources import (
    JobPostingExtractor,
    JobPostingSnapshot,
    JobSourceError,
)
from src.career_assistant.persistence import MessageRecord, MessageRole
from src.career_assistant.privacy import SensitiveDataRedactor
from src.career_assistant.resume_normalizer import ResumeNormalizer
from src.observability.langsmith_runtime import ensure_langsmith_privacy_defaults


@dataclass(frozen=True)
class TurnRuntimeContext:
    """仅在当前 Graph 内存中存在的输入上下文。

    它可以暂存用户原始文本，以便后续解析或模型调用使用；它不会被仓储写入，也不会从
    ``run`` 返回给 Web 层。职位 URL 也只保留“是否提供”的事实，避免长期保存链接。
    """

    original_text: str
    model_text: str
    has_job_url: bool
    attachment_kind_codes: tuple[str, ...]


@dataclass(frozen=True)
class IntakeGraphResult:
    """输入处理完成后交给后续 AgentRunner 的安全结果。"""

    active_turn: ActiveAgentTurn
    persisted_message: MessageRecord
    completed_steps: tuple[AgentStepName, ...]
    model_context: "ModelTurnContext"
    job_source_status: str
    job_source_message: str | None


@dataclass(frozen=True)
class IntakeGraphStreamEvent:
    """输入处理图对外发送的安全进展事件。

    ``step`` 只表示已完成的 LangGraph 节点名称，不携带简历正文、文件路径或职位链接。
    ``result`` 仅在图全部完成、临时文件已经清理后出现一次，用于让 Web 层取得已持久化
    的用户消息并进入模型回复阶段。
    """

    step: AgentStepName | None = None
    result: IntakeGraphResult | None = None


@dataclass(frozen=True)
class ModelTurnContext:
    """传给模型执行器的本轮脱敏上下文，不含文件路径、URL 或原始身份信息。"""

    redacted_user_text: str
    redacted_material_text: str
    redacted_job_text: str
    required_capabilities: frozenset[ModelCapability]
    contains_image_material: bool
    vision_images: tuple["VisionImageInput", ...]
    received_attachment_kinds: tuple[AttachmentKind, ...]
    pdf_without_extractable_text_count: int
    redacted_resume_outline: str = ""
    redacted_interview_evidence: tuple[InterviewEvidence, ...] = ()
    activated_skills: tuple[ActivatedSkill, ...] = ()
    document_processing_notices: tuple[str, ...] = ()
    attachment_processing_summaries: tuple["AttachmentProcessingSummary", ...] = ()
    candidate_profile_context: str = ""
    target_role_context: str = ""
    context_binding_version: int | None = None


@dataclass(frozen=True)
class VisionImageInput:
    """当前 Turn 的内存图像载荷，绝不写入数据库或 API 响应。"""

    media_type: str
    data_base64: str


@dataclass(frozen=True)
class AttachmentProcessingSummary:
    """可安全返回页面的附件处理状态，不包含文件名、路径或简历正文。"""

    kind: AttachmentKind
    page_count: int | None
    processing_route: str
    parser_name: str | None
    parser_status: str | None
    native_text_quality_score: float | None
    issue_codes: tuple[str, ...]


class CareerIntakeGraphState(TypedDict, total=False):
    """LangGraph 节点之间共享的内部状态。"""

    inbound_message: CareerInboundMessage
    active_turn: ActiveAgentTurn
    runtime_context: TurnRuntimeContext
    parsed_attachments: tuple[ParsedAttachment, ...]
    job_posting_snapshot: JobPostingSnapshot | None
    job_source_status: str
    job_source_message: str | None
    redacted_user_text: str
    model_context: ModelTurnContext
    persisted_message: MessageRecord
    completed_steps: tuple[AgentStepName, ...]


class CareerIntakeGraph:
    _MAX_MODEL_MATERIAL_CHARACTERS = 16_000
    _MAX_MODEL_JOB_CHARACTERS = 12_000
    _MAX_MODEL_RESUME_OUTLINE_CHARACTERS = 10_000

    """将求职输入处理拆为可观察、可扩展的 LangGraph 节点。

    当前图固定为 ``VALIDATE_INPUT → BUILD_CONTEXT → REDACT_SENSITIVE_DATA →
    PERSIST_HISTORY``。模型路由、职位解析和简历解析会在后续图中作为新节点接入，
    但不能绕过当前的脱敏与持久化边界。
    """

    def __init__(
        self,
        agent_loop: CareerAgentLoop,
        redactor: SensitiveDataRedactor | None = None,
        attachment_parser: AttachmentParser | None = None,
        temporary_attachment_store: TemporaryAttachmentStore | None = None,
        job_posting_extractor: JobPostingExtractor | None = None,
        resume_normalizer: ResumeNormalizer | None = None,
    ) -> None:
        """注入生命周期协调器与可配置脱敏器，并在启动时编译图结构。"""

        self._agent_loop = agent_loop
        self._redactor = redactor or SensitiveDataRedactor()
        self._attachment_parser = attachment_parser
        self._temporary_attachment_store = temporary_attachment_store
        self._job_posting_extractor = job_posting_extractor
        self._resume_normalizer = resume_normalizer or ResumeNormalizer()
        self._graph = self._build_graph()

    def run(self, inbound_message: CareerInboundMessage) -> IntakeGraphResult:
        """同步执行输入处理图，兼容既有非流式调用方。

        流式与非流式路径必须共享同一套 LangGraph 节点，避免出现一边能持久化用户消息、
        另一边却遗漏清理临时附件的分叉实现。这里消费 ``stream`` 的最终结果即可。
        """

        result: IntakeGraphResult | None = None
        for event in self.stream(inbound_message):
            if event.result is not None:
                result = event.result
        if result is None:
            raise RuntimeError("输入处理图未返回最终结果")
        return result

    def run_prepared(
        self,
        inbound_message: CareerInboundMessage,
        active_turn: ActiveAgentTurn,
        parsed_attachments: tuple[ParsedAttachment, ...],
    ) -> IntakeGraphResult:
        """执行 Worker 已领取的 Turn，直接复用 API 预先解析的完整附件内容。"""

        result: IntakeGraphResult | None = None
        for event in self.stream_prepared(
            inbound_message,
            active_turn,
            parsed_attachments,
        ):
            if event.result is not None:
                result = event.result
        if result is None:
            raise RuntimeError("输入处理图未返回最终结果")
        return result

    def stream_prepared(
        self,
        inbound_message: CareerInboundMessage,
        active_turn: ActiveAgentTurn,
        parsed_attachments: tuple[ParsedAttachment, ...],
    ) -> Iterator[IntakeGraphStreamEvent]:
        """流式执行已持久化输入，不再次创建 Turn 或读取临时附件。"""

        if active_turn.turn.id != inbound_message.turn_id:
            raise ValueError("Active Turn 与输入 Turn ID 不一致")
        restored_kinds = tuple(item.kind for item in parsed_attachments)
        if restored_kinds != inbound_message.prepared_attachment_kinds:
            raise ValueError("已解析附件类型与持久化输入不一致")
        yield from self.stream(
            inbound_message,
            active_turn=active_turn,
            parsed_attachments=parsed_attachments,
        )

    def stream(
        self,
        inbound_message: CareerInboundMessage,
        *,
        active_turn: ActiveAgentTurn | None = None,
        parsed_attachments: tuple[ParsedAttachment, ...] = (),
    ) -> Iterator[IntakeGraphStreamEvent]:
        """逐节点运行 LangGraph，并输出可供 SSE 展示的真实处理进展。

        调用方得到的 ``step`` 事件均来自 LangGraph 的 ``updates`` 流，故不会把“正在
        解析”伪装成已完成。图完成后才输出 ``result``，此时用户消息已落库，前端可将
        临时消息安全替换为正式历史消息。
        """

        active_turn = active_turn or self._agent_loop.start_turn(inbound_message)
        final_state: CareerIntakeGraphState | None = None
        try:
            # LangGraph 会自动建立图与节点层级；先启用全局输入/输出隐藏，避免简历、
            # 职位描述和聊天正文作为 Graph state 离开本机。
            ensure_langsmith_privacy_defaults()
            for part in self._graph.stream(
                {
                    "inbound_message": inbound_message,
                    "active_turn": active_turn,
                    "parsed_attachments": parsed_attachments,
                    "completed_steps": (),
                },
                config={
                    "run_name": "career.intake.graph",
                    "tags": ["career", "langgraph", "intake"],
                    "metadata": {
                        "privacy_mode": "metadata_only",
                        "attachment_count": len(inbound_message.attachments),
                        "interview_evidence_count": len(inbound_message.interview_evidence),
                        "has_text": bool(inbound_message.text.strip()),
                        "has_job_url": bool((inbound_message.job_url or "").strip()),
                    },
                },
                stream_mode=["updates", "values"],
                version="v2",
            ):
                if part.get("type") == "updates":
                    updates = part.get("data", {})
                    if not isinstance(updates, dict):
                        continue
                    for node_name in updates:
                        try:
                            step = AgentStepName(node_name)
                        except ValueError:
                            continue
                        yield IntakeGraphStreamEvent(step=step)
                elif part.get("type") == "values":
                    state = part.get("data")
                    if isinstance(state, dict):
                        final_state = state
        except Exception:
            self._mark_intake_failure(active_turn)
            raise
        finally:
            if self._temporary_attachment_store is not None:
                self._temporary_attachment_store.cleanup(inbound_message.attachments)

        if final_state is None:
            self._mark_intake_failure(active_turn)
            raise RuntimeError("输入处理图未生成最终状态")

        yield IntakeGraphStreamEvent(
            result=IntakeGraphResult(
                active_turn=active_turn,
                persisted_message=final_state["persisted_message"],
                completed_steps=final_state["completed_steps"],
                model_context=final_state["model_context"],
                job_source_status=final_state.get("job_source_status", "not_provided"),
                job_source_message=final_state.get("job_source_message"),
            ),
        )

    def _build_graph(self):
        """声明并编译静态 Graph；编译会检查孤立节点和边连接。"""

        builder = StateGraph(CareerIntakeGraphState)
        builder.add_node(AgentStepName.VALIDATE_INPUT.value, self._validate_input)
        builder.add_node(AgentStepName.BUILD_CONTEXT.value, self._build_context)
        builder.add_node(AgentStepName.PARSE_MATERIAL.value, self._parse_material)
        builder.add_node(
            AgentStepName.EXTRACT_JOB_DESCRIPTION.value,
            self._extract_job_description,
        )
        builder.add_node(
            AgentStepName.REDACT_SENSITIVE_DATA.value,
            self._redact_sensitive_data,
        )
        builder.add_node(AgentStepName.PERSIST_HISTORY.value, self._persist_history)
        builder.add_node(
            AgentStepName.CLEANUP_TEMPORARY_FILES.value,
            self._cleanup_temporary_files,
        )
        builder.add_edge(START, AgentStepName.VALIDATE_INPUT.value)
        builder.add_edge(
            AgentStepName.VALIDATE_INPUT.value,
            AgentStepName.BUILD_CONTEXT.value,
        )
        builder.add_edge(
            AgentStepName.BUILD_CONTEXT.value,
            AgentStepName.PARSE_MATERIAL.value,
        )
        builder.add_edge(
            AgentStepName.PARSE_MATERIAL.value,
            AgentStepName.EXTRACT_JOB_DESCRIPTION.value,
        )
        builder.add_edge(
            AgentStepName.EXTRACT_JOB_DESCRIPTION.value,
            AgentStepName.REDACT_SENSITIVE_DATA.value,
        )
        builder.add_edge(
            AgentStepName.REDACT_SENSITIVE_DATA.value,
            AgentStepName.PERSIST_HISTORY.value,
        )
        builder.add_edge(
            AgentStepName.PERSIST_HISTORY.value,
            AgentStepName.CLEANUP_TEMPORARY_FILES.value,
        )
        builder.add_edge(AgentStepName.CLEANUP_TEMPORARY_FILES.value, END)
        return builder.compile()

    def _validate_input(
        self,
        state: CareerIntakeGraphState,
    ) -> dict[str, tuple[AgentStepName, ...]]:
        """在图入口再次校验稳定 contract，抵御未来非 Web 调用方。"""

        state["inbound_message"].validate()
        return self._append_step(state, AgentStepName.VALIDATE_INPUT)

    def _build_context(
        self,
        state: CareerIntakeGraphState,
    ) -> dict[str, TurnRuntimeContext | tuple[AgentStepName, ...]]:
        """构造仅内存可见的原始输入上下文，不向数据库写入 URL 或附件路径。"""

        inbound_message = state["inbound_message"]
        prepared_kinds = tuple(
            item.kind.value for item in state.get("parsed_attachments", ())
        )
        context = TurnRuntimeContext(
            original_text=inbound_message.text.strip(),
            model_text=(
                inbound_message.effective_text.strip()
                if inbound_message.effective_text is not None
                else inbound_message.text.strip()
            ),
            has_job_url=bool((inbound_message.job_url or "").strip()),
            attachment_kind_codes=tuple(
                sorted(
                    {
                        *(attachment.kind.value for attachment in inbound_message.attachments),
                        *prepared_kinds,
                    },
                ),
            ),
        )
        return {
            "runtime_context": context,
            **self._append_step(state, AgentStepName.BUILD_CONTEXT),
        }

    def _parse_material(
        self,
        state: CareerIntakeGraphState,
    ) -> dict[str, tuple[ParsedAttachment, ...] | tuple[AgentStepName, ...]]:
        """在当前 Turn 内解析临时材料，解析产物不会直接进入持久化层。"""

        inbound_message = state["inbound_message"]
        if state.get("parsed_attachments"):
            parsed_attachments = state["parsed_attachments"]
        elif not inbound_message.attachments:
            parsed_attachments: tuple[ParsedAttachment, ...] = ()
        elif self._attachment_parser is None:
            raise RuntimeError("附件解析器尚未初始化")
        else:
            parsed_attachments = tuple(
                self._attachment_parser.parse(attachment)
                for attachment in inbound_message.attachments
            )

        return {
            "parsed_attachments": parsed_attachments,
            **self._append_step(state, AgentStepName.PARSE_MATERIAL),
        }

    def _extract_job_description(
        self,
        state: CareerIntakeGraphState,
    ) -> dict[str, str | tuple[AgentStepName, ...]]:
        """读取公开职位页的临时快照；访问失败不阻断用户继续粘贴职位描述。"""

        job_url = (state["inbound_message"].job_url or "").strip()
        if not job_url:
            job_source_status = "not_provided"
            job_posting_snapshot = None
            job_source_message = None
        elif self._job_posting_extractor is None:
            job_source_status = "not_configured"
            job_posting_snapshot = None
            job_source_message = "职位链接解析服务尚未配置，请直接粘贴职位描述。"
        else:
            try:
                job_posting_snapshot = self._job_posting_extractor.extract(job_url)
                job_source_status = "fetched"
                job_source_message = None
            except JobSourceError as exc:
                job_source_status = "unavailable"
                job_posting_snapshot = None
                job_source_message = str(exc)

        return {
            "job_posting_snapshot": job_posting_snapshot,
            "job_source_status": job_source_status,
            "job_source_message": job_source_message,
            **self._append_step(state, AgentStepName.EXTRACT_JOB_DESCRIPTION),
        }

    def _redact_sensitive_data(
        self,
        state: CareerIntakeGraphState,
    ) -> dict[str, str | tuple[AgentStepName, ...]]:
        """将可写入历史的用户消息与临时原始上下文分离。"""

        context = state["runtime_context"]
        if context.original_text:
            redacted_text = self._redactor.redact(context.original_text)
        else:
            redacted_text = self._build_non_text_history_placeholder(context)

        material_summary = self._build_safe_material_summary(
            state.get("parsed_attachments", ()),
        )
        if material_summary:
            redacted_text = f"{redacted_text}\n\n{material_summary}"

        job_source_summary = self._build_safe_job_source_summary(
            state.get("job_source_status", "not_provided"),
        )
        if job_source_summary:
            redacted_text = f"{redacted_text}\n\n{job_source_summary}"

        model_context = self._build_model_context(state, context)

        return {
            "redacted_user_text": redacted_text,
            "model_context": model_context,
            **self._append_step(state, AgentStepName.REDACT_SENSITIVE_DATA),
        }

    def _persist_history(
        self,
        state: CareerIntakeGraphState,
    ) -> dict[str, MessageRecord | tuple[AgentStepName, ...]]:
        """写入当前部署策略处理后的用户消息，并绑定到当前 running Turn。"""

        active_turn = state["active_turn"]
        persisted_message = self._agent_loop.repository.append_message(
            active_turn.conversation.id,
            MessageRole.USER,
            state["redacted_user_text"],
            turn_id=active_turn.turn.id,
            is_redacted=self._redactor.enabled,
        )
        return {
            "persisted_message": persisted_message,
            **self._append_step(state, AgentStepName.PERSIST_HISTORY),
        }

    def _cleanup_temporary_files(
        self,
        state: CareerIntakeGraphState,
    ) -> dict[str, tuple[AgentStepName, ...]]:
        """记录清理节点；实际删除在 run 的 finally 中保证失败时同样执行。"""

        return self._append_step(state, AgentStepName.CLEANUP_TEMPORARY_FILES)

    @staticmethod
    def _append_step(
        state: CareerIntakeGraphState,
        step: AgentStepName,
    ) -> dict[str, tuple[AgentStepName, ...]]:
        """顺序追加完成节点，供未来运行状态 API 与监控界面使用。"""

        return {"completed_steps": (*state["completed_steps"], step)}

    @staticmethod
    def _build_non_text_history_placeholder(context: TurnRuntimeContext) -> str:
        """对只含 URL 或附件的输入生成不含原始材料的历史占位说明。"""

        if context.has_job_url and context.attachment_kind_codes:
            return "用户提交了职位链接和附件材料，等待安全解析结果。"
        if context.has_job_url:
            return "用户提交了职位链接，等待安全解析结果。"
        if context.attachment_kind_codes:
            return "用户提交了附件材料，等待安全解析结果。"
        return "用户提交了材料，等待安全解析结果。"

    @staticmethod
    def _build_safe_material_summary(
        parsed_attachments: tuple[ParsedAttachment, ...],
    ) -> str:
        """仅保留材料类型与页数等非敏感状态，不记录文件名、路径与原文。"""

        if not parsed_attachments:
            return ""

        labels: list[str] = []
        for parsed_attachment in parsed_attachments:
            kind = parsed_attachment.kind.value
            if kind == "resume_pdf":
                labels.append(f"简历 PDF（{parsed_attachment.page_count or 0} 页）")
            elif kind == "resume_document":
                labels.append(_structured_document_label(parsed_attachment, "简历"))
            elif kind == "resume_image":
                labels.append(_image_material_label(parsed_attachment, "简历图片"))
            elif kind == "job_description_image":
                labels.append(_image_material_label(parsed_attachment, "职位图片"))
            else:
                labels.append(_structured_document_label(parsed_attachment, "职位材料"))

        return (
            "已接收并临时解析材料："
            + "、".join(labels)
            + "。原文件不会写入历史记录，当前任务结束后自动删除。"
        )

    @staticmethod
    def _build_safe_job_source_summary(status: str) -> str:
        """将职位链接的处理结果降级为不含 URL 与正文的持久化提示。"""

        if status == "fetched":
            return "已读取公开职位页的可见内容，原始链接和页面正文不会写入历史记录。"
        if status == "unavailable":
            return "职位链接暂时无法读取；可继续粘贴职位描述以完成匹配分析。"
        return ""

    def _build_model_context(
        self,
        state: CareerIntakeGraphState,
        runtime_context: TurnRuntimeContext,
    ) -> ModelTurnContext:
        """构造发送给模型的脱敏上下文，材料原文只在本轮内存中短暂存在。"""

        parsed_attachments = state.get("parsed_attachments", ())
        inbound_message = state["inbound_message"]
        resume_outline = self._build_redacted_resume_outline(parsed_attachments)
        if inbound_message.candidate_profile_context.strip():
            resume_outline = self._redactor.redact(
                inbound_message.candidate_profile_context,
            )[: self._MAX_MODEL_RESUME_OUTLINE_CHARACTERS]
        material_parts = [
            self._redactor.redact(parsed_attachment.extracted_text)
            for parsed_attachment in parsed_attachments
            if (
                parsed_attachment.extracted_text
                and parsed_attachment.kind
                not in {
                    AttachmentKind.RESUME_PDF,
                    AttachmentKind.RESUME_DOCUMENT,
                }
            )
        ]
        job_snapshot = state.get("job_posting_snapshot")
        redacted_job_text = ""
        if job_snapshot is not None:
            redacted_job_text = self._redactor.redact(
                job_snapshot.visible_text,
            )[: self._MAX_MODEL_JOB_CHARACTERS]
        if inbound_message.target_role_context.strip():
            redacted_job_text = self._redactor.redact(
                inbound_message.target_role_context,
            )[: self._MAX_MODEL_JOB_CHARACTERS]

        contains_image_material = any(
            parsed_attachment.kind
            in {AttachmentKind.RESUME_IMAGE, AttachmentKind.JOB_DESCRIPTION_IMAGE}
            for parsed_attachment in parsed_attachments
        )
        required_capabilities = {ModelCapability.TEXT}
        redacted_interview_evidence = tuple(
            InterviewEvidence(
                experience_id=evidence.experience_id,
                citation=evidence.citation,
                content=self._redactor.redact(evidence.content)[:2_000],
                source_url=evidence.source_url,
            )
            for evidence in state["inbound_message"].interview_evidence
            if evidence.content.strip()
        )

        return ModelTurnContext(
            redacted_user_text=(
                self._redactor.redact(runtime_context.model_text)
                if runtime_context.model_text
                else ""
            ),
            redacted_material_text="\n\n".join(material_parts)[
                : self._MAX_MODEL_MATERIAL_CHARACTERS
            ],
            redacted_job_text=redacted_job_text,
            required_capabilities=frozenset(required_capabilities),
            contains_image_material=contains_image_material,
            # 图片已经在 AttachmentParser 内部完成 Docling/Qwen 解析；当前选中的
            # 聊天模型永远只接收文本，不要求它具备 Vision 能力。
            vision_images=(),
            received_attachment_kinds=tuple(
                parsed_attachment.kind for parsed_attachment in parsed_attachments
            ),
            pdf_without_extractable_text_count=sum(
                1
                for parsed_attachment in parsed_attachments
                if parsed_attachment.media_type == "application/pdf"
                and not parsed_attachment.extracted_text.strip()
            ),
            redacted_resume_outline=resume_outline,
            redacted_interview_evidence=redacted_interview_evidence,
            activated_skills=state["inbound_message"].activated_skills,
            document_processing_notices=tuple(
                parsed_attachment.document_understanding_error
                for parsed_attachment in parsed_attachments
                if parsed_attachment.document_understanding_error
            ),
            attachment_processing_summaries=self._build_attachment_processing_summaries(
                parsed_attachments,
            ),
            candidate_profile_context=resume_outline,
            target_role_context=redacted_job_text,
            context_binding_version=inbound_message.context_binding_version,
        )

    @staticmethod
    def _build_attachment_processing_summaries(
        parsed_attachments: tuple[ParsedAttachment, ...],
    ) -> tuple[AttachmentProcessingSummary, ...]:
        """提取可观察但不泄露简历内容的解析状态，供 SSE 与 WebUI 渲染。"""

        summaries: list[AttachmentProcessingSummary] = []
        for parsed_attachment in parsed_attachments:
            document_probe = parsed_attachment.document_probe
            document_result = parsed_attachment.document_understanding_result
            cloud_vision_result = parsed_attachment.cloud_vision_result
            if cloud_vision_result is not None:
                processing_route = "cloud_vision"
            elif document_result is not None:
                processing_route = "structured_document"
            elif document_probe is not None:
                processing_route = document_probe.recommended_route.value
            else:
                processing_route = "metadata_only"

            summaries.append(
                AttachmentProcessingSummary(
                    kind=parsed_attachment.kind,
                    page_count=parsed_attachment.page_count,
                    processing_route=processing_route,
                    parser_name=(
                        f"{cloud_vision_result.provider_key}-{cloud_vision_result.model_id}"
                        if cloud_vision_result is not None
                        else document_result.parser_name
                        if document_result is not None
                        else None
                    ),
                    parser_status=(
                        "success"
                        if cloud_vision_result is not None
                        else document_result.status
                        if document_result is not None
                        else None
                    ),
                    native_text_quality_score=(
                        document_probe.native_text_quality_score
                        if document_probe is not None
                        else None
                    ),
                    issue_codes=(
                        tuple(issue.value for issue in document_probe.issues)
                        if document_probe is not None
                        else ()
                    ),
                ),
            )
        return tuple(summaries)

    def _build_redacted_resume_outline(
        self,
        parsed_attachments: tuple[ParsedAttachment, ...],
    ) -> str:
        """将简历正文归类后再脱敏，避免原文与提纲重复占用模型上下文。"""

        outlines = [
            self._resume_normalizer.normalize(parsed_attachment.extracted_text).to_model_outline()
            for parsed_attachment in parsed_attachments
            if (
                parsed_attachment.kind
                in {
                    AttachmentKind.RESUME_PDF,
                    AttachmentKind.RESUME_DOCUMENT,
                }
                and parsed_attachment.extracted_text.strip()
            )
        ]
        combined_outline = "\n\n".join(outline for outline in outlines if outline.strip())
        if not combined_outline:
            return ""
        return self._redactor.redact(combined_outline)[
            : self._MAX_MODEL_RESUME_OUTLINE_CHARACTERS
        ]

    def _mark_intake_failure(self, active_turn: ActiveAgentTurn) -> None:
        """尽力收口失败 Turn；若收口本身失败，保留原始异常供上层记录。"""

        try:
            self._agent_loop.mark_turn_failed(
                active_turn.conversation.actor_id,
                active_turn.turn.id,
                "intake_graph_failed",
                "输入处理失败，请重新提交材料或稍后重试。",
            )
        except LookupError:
            pass


def _structured_document_label(
    parsed_attachment: ParsedAttachment,
    prefix: str,
) -> str:
    """只按 MIME 展示材料类型，避免把原始文件名写入会话历史。"""

    if (
        parsed_attachment.media_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return f"{prefix} Word 文档"
    if parsed_attachment.media_type == "application/msword":
        return f"{prefix} Word 文档"
    if (
        parsed_attachment.media_type
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ):
        return f"{prefix} Excel 文档"
    if parsed_attachment.media_type == "application/vnd.ms-excel":
        return f"{prefix} Excel 文档"
    return f"{prefix} PDF"


def _image_material_label(
    parsed_attachment: ParsedAttachment,
    prefix: str,
) -> str:
    """展示图片实际解析路径，避免把已完成的自动能力误写成“等待 Vision”。"""

    if parsed_attachment.cloud_vision_result is not None:
        return f"{prefix}（云端图片理解已完成）"
    if parsed_attachment.extracted_text.strip():
        return f"{prefix}（Docling OCR 已完成）"
    return f"{prefix}（解析未完成）"
