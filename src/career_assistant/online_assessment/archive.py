"""把用户确认的线上笔试结果转换为可检索、可合并的面经 Markdown。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import re
from uuid import UUID

from src.career_assistant.interview_library.models import (
    IngestionTriggerType,
    InterviewExperienceRecord,
    InterviewSourceType,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)
from src.career_assistant.online_assessment.contracts import (
    AssessmentExecutionReport,
    AssessmentProblem,
    AssessmentSolution,
)


@dataclass(frozen=True)
class AssessmentArchiveItem:
    problem: AssessmentProblem
    solution: AssessmentSolution
    report: AssessmentExecutionReport | None = None


@dataclass(frozen=True)
class AssessmentArchivePreview:
    markdown: str
    question_count: int
    passed_question_count: int


class AssessmentArchiveService:
    """按一场笔试一份面经合并题目，并用规范题目哈希去重。"""

    def __init__(
        self,
        repository: InterviewLibraryRepository,
        library_service: InterviewLibraryService,
    ) -> None:
        self._repository = repository
        self._library_service = library_service

    def preview(
        self,
        *,
        company_name: str,
        role_name: str,
        assessment_date: date | None,
        items: tuple[AssessmentArchiveItem, ...],
    ) -> AssessmentArchivePreview:
        markdown = build_assessment_markdown(
            company_name=company_name,
            role_name=role_name,
            assessment_date=assessment_date,
            items=items,
        )
        return AssessmentArchivePreview(
            markdown=markdown,
            question_count=len(items),
            passed_question_count=sum(
                item.report is not None and item.report.final_status.value == "passed" for item in items
            ),
        )

    def archive(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID,
        can_manage_all: bool,
        company_name: str,
        role_name: str,
        assessment_date: date | None,
        items: tuple[AssessmentArchiveItem, ...],
    ) -> InterviewExperienceRecord:
        preview = self.preview(
            company_name=company_name,
            role_name=role_name,
            assessment_date=assessment_date,
            items=items,
        )
        job_name = (
            f"{InterviewLibraryService.build_job_name(role_name, assessment_date)} · 线上笔试"
        )
        existing = self._repository.get_experience_by_company_and_job_name(
            organization_id,
            company_name,
            job_name,
        )
        if existing is None:
            return self._library_service.ingest(
                organization_id,
                InterviewExperienceDraft(
                    company_name=company_name,
                    role_name=role_name,
                    interview_date=assessment_date,
                    markdown_content=preview.markdown,
                    source_type=InterviewSourceType.ONLINE_ASSESSMENT,
                    source_platform="browser_extension",
                    summary_text=f"线上笔试共 {preview.question_count} 题",
                    tags=("线上笔试",),
                    job_name=job_name,
                ),
                trigger_type=IngestionTriggerType.API_SYNC,
                created_by_actor_id=actor_id,
                can_manage_all=can_manage_all,
            )

        merged = merge_assessment_markdown(existing.markdown_content, preview.markdown)
        if merged == existing.markdown_content:
            return existing
        return self._library_service.update_markdown(
            organization_id,
            existing.id,
            actor_id=actor_id,
            can_manage_all=can_manage_all,
            markdown_content=merged,
            summary_text=f"线上笔试题目已合并，共 {len(_question_hashes(merged))} 题",
            tags=tuple(dict.fromkeys([*existing.tags, "线上笔试"])),
        )


def normalize_question_hash(problem: AssessmentProblem) -> str:
    """URL 参数和空白不影响同一题目的去重键。"""

    normalized_title = re.sub(r"\s+", " ", problem.title).strip().casefold()
    normalized_statement = re.sub(r"\s+", " ", problem.statement).strip().casefold()
    return sha256(f"{normalized_title}\n{normalized_statement}".encode("utf-8")).hexdigest()[:24]


def build_assessment_markdown(
    *,
    company_name: str,
    role_name: str,
    assessment_date: date | None,
    items: tuple[AssessmentArchiveItem, ...],
) -> str:
    if not company_name.strip() or not role_name.strip():
        raise ValueError("归档前必须填写公司和岗位")
    if not items:
        raise ValueError("至少需要一道确认后的笔试题")
    header = [
        f"# {company_name.strip()} · {role_name.strip()}线上笔试",
        "",
        f"- 笔试日期：{assessment_date.isoformat() if assessment_date else '日期待补充'}",
        f"- 题目数量：{len(items)}",
        "- 来源：线上笔试助手（用户确认后归档）",
        "",
    ]
    sections = [_item_markdown(item, index) for index, item in enumerate(items, start=1)]
    return "\n".join([*header, *sections]).strip()


def merge_assessment_markdown(existing: str, incoming: str) -> str:
    """保留已有内容，只追加尚未归档的题目块。"""

    known = _question_hashes(existing)
    new_sections = [section for key, section in _question_sections(incoming) if key not in known]
    if not new_sections:
        return existing
    return f"{existing.rstrip()}\n\n" + "\n\n".join(new_sections).strip()


def _item_markdown(item: AssessmentArchiveItem, index: int) -> str:
    problem, solution, report = item.problem, item.solution, item.report
    key = normalize_question_hash(problem)
    public_results = [] if report is None else [test for test in report.tests if test.kind.value == "public"]
    generated_results = [] if report is None else [test for test in report.tests if test.kind.value == "generated"]
    test_summary = "尚未执行"
    if report is not None:
        test_summary = f"{report.passed_count}/{report.passed_count + report.failed_count} 通过（{report.final_status.value}）"
    return "\n".join(
        [
            f"<!-- assessment-question:{key}:start -->",
            f"## 第 {index} 题：{problem.title}",
            "",
            problem.statement.strip(),
            "",
            "### 解题思路",
            "",
            solution.approach_markdown.strip(),
            "",
            "### 复杂度",
            "",
            f"- 时间复杂度：{solution.time_complexity}",
            f"- 空间复杂度：{solution.space_complexity}",
            "",
            "### 最终代码",
            "",
            f"```{solution.language.value}",
            solution.code.rstrip(),
            "```",
            "",
            "### 测试摘要",
            "",
            f"- 总结果：{test_summary}",
            f"- 公开样例：{sum(test.passed for test in public_results)}/{len(public_results)} 通过",
            f"- AI 测试：{sum(test.passed for test in generated_results)}/{len(generated_results)} 通过",
            f"<!-- assessment-question:{key}:end -->",
        ]
    )


def _question_hashes(markdown: str) -> set[str]:
    return {key for key, _ in _question_sections(markdown)}


def _question_sections(markdown: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"<!-- assessment-question:([0-9a-f]{24}):start -->(.*?)"
        r"<!-- assessment-question:\1:end -->",
        flags=re.DOTALL,
    )
    return [(match.group(1), match.group(0).strip()) for match in pattern.finditer(markdown)]
