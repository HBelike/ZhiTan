"""简历助手的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ResumeSuggestion:
    """模型提出的一条可独立勾选的简历修改意见。"""

    id: str
    category: str
    title: str
    rationale: str
    original_evidence: str
    job_evidence: str
    proposed_change: str
    priority: str


@dataclass(frozen=True)
class ResumeAnalysisResult:
    """分析阶段返回给大画布的原文、岗位和结构化建议。"""

    original_markdown: str
    job_description_text: str
    job_title: str
    analysis_summary: str
    suggestions: tuple[ResumeSuggestion, ...]
    model_profile_id: UUID
    model_display_name: str
    provider_key: str
    model_id: str


@dataclass(frozen=True)
class ResumeOptimizationRecord:
    """一次用户显式保存后的不可变历史记录。"""

    id: UUID
    organization_id: UUID
    owner_id: UUID
    creator_name: str
    job_title: str
    model_profile_id: UUID | None
    model_display_name: str
    provider_key: str
    model_id: str
    source_filename: str
    source_media_type: str
    original_file_path: str
    original_preview_pdf_path: str
    original_preview_markdown: str
    job_description_text: str
    extra_prompt: str | None
    suggestions: tuple[ResumeSuggestion, ...]
    optimized_markdown: str
    optimized_text_path: str
    optimized_docx_path: str
    optimized_pdf_path: str
    created_at: datetime
