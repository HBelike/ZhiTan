"""把一场实时面试中的问题确定性归档到现有面经库。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from src.career_assistant.interview_library.models import (
    IngestionTriggerType,
    InterviewExperienceRecord,
    InterviewSourceType,
)
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)


DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
QUESTION_LINE = re.compile(r"^\s*\d+[.)、]\s*(.+?)\s*$")
START_TIME_LINE = re.compile(r"^-\s*开始时间：\s*(\d{1,2}):(\d{2})\s*$", re.MULTILINE)
END_TIME_LINE = re.compile(r"^-\s*结束时间：\s*(\d{1,2}):(\d{2})\s*$", re.MULTILINE)
ARCHIVE_BLOCK_START = "<!-- live-interview-archive:start -->"
ARCHIVE_BLOCK_END = "<!-- live-interview-archive:end -->"


@dataclass(frozen=True)
class LiveInterviewArchiveResult:
    """一次归档完成后返回给 Web 层的稳定结果。"""

    experience: InterviewExperienceRecord
    questions: tuple[str, ...]
    started_at: datetime
    ended_at: datetime

    @property
    def question_count(self) -> int:
        return len(self.questions)

    @property
    def question_preview(self) -> tuple[str, ...]:
        return self.questions[:5]


@dataclass(frozen=True)
class LiveInterviewArchivePreview:
    """结束弹窗所需的最小只读信息。"""

    questions: tuple[str, ...]
    started_at: datetime
    ended_at: datetime

    @property
    def question_count(self) -> int:
        return len(self.questions)

    @property
    def question_preview(self) -> tuple[str, ...]:
        return self.questions[:5]


class LiveInterviewArchiveService:
    """聚合实时会话并复用面经库的持久化与索引链路。"""

    def __init__(self, *, live_repository, interview_repository, interview_service: InterviewLibraryService) -> None:
        self._live_repository = live_repository
        self._interview_repository = interview_repository
        self._interview_service = interview_service

    def archive(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID,
        can_manage_all: bool = False,
        session_ids: tuple[UUID, ...],
        company_name: str,
        role_name: str,
        interview_date: date,
    ) -> LiveInterviewArchiveResult:
        company = " ".join(company_name.split()).strip()
        role = " ".join(role_name.split()).strip()
        if not company:
            raise ValueError("公司名称不能为空")
        if not role:
            raise ValueError("面试职位不能为空")
        collected = self._collect(
            organization_id=organization_id,
            actor_id=actor_id,
            session_ids=session_ids,
        )
        if not collected.questions:
            raise ValueError("本场未识别到可归档的问题")

        job_name = InterviewLibraryService.build_job_name(role, interview_date)
        existing = self._interview_repository.get_experience_by_company_and_job_name(
            organization_id,
            company,
            job_name,
        )
        existing_questions = extract_questions(existing.markdown_content) if existing else ()
        questions = merge_questions(existing_questions, collected.questions)
        started_at, ended_at = _merge_time_range(
            existing.markdown_content if existing else "",
            interview_date,
            collected.started_at,
            collected.ended_at,
        )
        generated = build_archive_markdown(
            company_name=company,
            role_name=role,
            interview_date=interview_date,
            started_at=started_at,
            ended_at=ended_at,
            questions=questions,
        )
        summary = f"共 {len(questions)} 个面试问题"
        if existing is None:
            experience = self._interview_service.ingest(
                organization_id,
                InterviewExperienceDraft(
                    company_name=company,
                    role_name=role,
                    interview_date=interview_date,
                    markdown_content=generated,
                    source_type=InterviewSourceType.AUTHENTICATED_SESSION,
                    source_platform="interview_master",
                    summary_text=summary,
                    tags=("面试大师", "实时面试"),
                    job_name=job_name,
                ),
                trigger_type=IngestionTriggerType.API_SYNC,
                created_by_actor_id=actor_id,
                can_manage_all=can_manage_all,
            )
        else:
            markdown = (
                generated
                if existing.source_type is InterviewSourceType.AUTHENTICATED_SESSION
                else merge_archive_block(existing.markdown_content, questions)
            )
            experience = self._interview_service.update_markdown(
                organization_id,
                existing.id,
                actor_id=actor_id,
                can_manage_all=can_manage_all,
                markdown_content=markdown,
                summary_text=summary,
                tags=merge_tags(existing.tags, ("面试大师", "实时面试")),
            )
        return LiveInterviewArchiveResult(
            experience=experience,
            questions=questions,
            started_at=started_at,
            ended_at=ended_at,
        )

    def preview(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID,
        session_ids: tuple[UUID, ...],
    ) -> LiveInterviewArchivePreview:
        return self._collect(
            organization_id=organization_id,
            actor_id=actor_id,
            session_ids=session_ids,
        )

    def _collect(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID,
        session_ids: tuple[UUID, ...],
    ) -> LiveInterviewArchivePreview:
        unique_ids = tuple(dict.fromkeys(session_ids))
        if not unique_ids:
            raise ValueError("至少需要一个实时面试会话")
        histories: list[dict[str, object]] = []
        for session_id in unique_ids:
            history = self._live_repository.history(organization_id, actor_id, session_id)
            if history is None:
                raise LookupError("实时面试会话不存在或无权访问")
            histories.append(history)
        histories.sort(key=lambda item: _session_time(item, "started_at"))

        starts = [_session_time(item, "started_at") for item in histories]
        ends = [_session_time(item, "ended_at", fallback=starts[index]) for index, item in enumerate(histories)]
        ordered_questions: list[str] = []
        for history in histories:
            answers = list(history.get("answers") or ())
            answers.sort(key=_answer_order)
            ordered_questions.extend(
                str(answer.get("original_question") or "").strip()
                for answer in answers
                if str(answer.get("original_question") or "").strip()
            )
        return LiveInterviewArchivePreview(
            questions=merge_questions((), tuple(ordered_questions)),
            started_at=min(starts),
            ended_at=max(ends),
        )


def normalize_question_key(question: str) -> str:
    """生成保守的确定性去重键，不做语义改写。"""

    compact = re.sub(r"\s+", "", str(question or "")).casefold()
    return compact.rstrip("?？。.!！；;，,")


def merge_questions(existing: tuple[str, ...], incoming: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for question in (*existing, *incoming):
        value = " ".join(str(question).split()).strip()
        key = normalize_question_key(value)
        if value and key and key not in seen:
            seen.add(key)
            merged.append(value)
    return tuple(merged)


def extract_questions(markdown: str) -> tuple[str, ...]:
    """读取问题标题下的编号列表，避免把正文中的普通序号误判为问题。"""

    questions: list[str] = []
    in_question_section = False
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_question_section = "问题" in stripped
            continue
        if in_question_section and stripped.startswith("#"):
            in_question_section = False
        if not in_question_section:
            continue
        match = QUESTION_LINE.match(stripped)
        if match:
            questions.append(match.group(1).strip())
    return merge_questions((), tuple(questions))


def build_archive_markdown(
    *,
    company_name: str,
    role_name: str,
    interview_date: date,
    started_at: datetime,
    ended_at: datetime,
    questions: tuple[str, ...],
) -> str:
    local_start = _as_local(started_at)
    local_end = _as_local(ended_at)
    duration_minutes = max(0, round((ended_at - started_at).total_seconds() / 60))
    question_lines = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, 1))
    return (
        f"# {company_name} · {role_name}面经\n\n"
        f"- 面试日期：{interview_date.isoformat()}\n"
        f"- 开始时间：{local_start:%H:%M}\n"
        f"- 结束时间：{local_end:%H:%M}\n"
        f"- 面试时长：{duration_minutes} 分钟\n"
        "- 来源：面试大师实时整理\n\n"
        "## 面试问题\n\n"
        f"{question_lines}\n"
    )


def merge_archive_block(existing_markdown: str, questions: tuple[str, ...]) -> str:
    """在非实时来源面经末尾维护独立区块，避免覆盖用户已有正文。"""

    block_questions = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, 1))
    block = (
        f"{ARCHIVE_BLOCK_START}\n"
        "## 面试大师归档问题\n\n"
        f"{block_questions}\n"
        f"{ARCHIVE_BLOCK_END}"
    )
    pattern = re.compile(
        rf"{re.escape(ARCHIVE_BLOCK_START)}.*?{re.escape(ARCHIVE_BLOCK_END)}",
        re.DOTALL,
    )
    normalized = str(existing_markdown or "").rstrip()
    if pattern.search(normalized):
        return pattern.sub(block, normalized) + "\n"
    return f"{normalized}\n\n{block}\n" if normalized else f"{block}\n"


def merge_tags(existing: tuple[str, ...], incoming: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *incoming)))


def _session_time(history: dict[str, object], field: str, *, fallback: datetime | None = None) -> datetime:
    session = history.get("session") or {}
    value = session.get(field) or (session.get("created_at") if field == "started_at" else None) or fallback
    if not isinstance(value, datetime):
        raise ValueError("实时面试会话缺少有效时间")
    return value


def _answer_order(answer: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(answer.get("question_version") or 0),
        int(answer.get("attempt") or 0),
        str(answer.get("created_at") or ""),
    )


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=DISPLAY_TIMEZONE)
    return value.astimezone(DISPLAY_TIMEZONE)


def _merge_time_range(
    existing_markdown: str,
    interview_date: date,
    started_at: datetime,
    ended_at: datetime,
) -> tuple[datetime, datetime]:
    local_start = _parse_existing_time(existing_markdown, interview_date, START_TIME_LINE)
    local_end = _parse_existing_time(existing_markdown, interview_date, END_TIME_LINE)
    start_candidates = [started_at, *(value for value in (local_start,) if value is not None)]
    end_candidates = [ended_at, *(value for value in (local_end,) if value is not None)]
    return min(start_candidates), max(end_candidates)


def _parse_existing_time(markdown: str, interview_date: date, pattern: re.Pattern[str]) -> datetime | None:
    match = pattern.search(str(markdown or ""))
    if not match:
        return None
    return datetime(
        interview_date.year,
        interview_date.month,
        interview_date.day,
        int(match.group(1)),
        int(match.group(2)),
        tzinfo=DISPLAY_TIMEZONE,
    )
