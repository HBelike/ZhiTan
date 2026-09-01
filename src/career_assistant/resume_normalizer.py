"""将当前 Turn 的简历文本归纳为稳定结构的确定性解析器。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ResumeSection(StrEnum):
    """求职分析最常用的简历结构分区。

    此枚举参考 JSON Resume 的稳定字段思想，但只服务于当前 Turn 的中文简历归纳，
    不承担简历档案持久化职责。
    """

    PROFILE = "profile"
    WORK_EXPERIENCE = "work_experience"
    EDUCATION = "education"
    PROJECTS = "projects"
    SKILLS = "skills"
    CERTIFICATIONS = "certifications"
    PUBLICATIONS = "publications"
    LANGUAGES = "languages"
    OTHER = "other"


@dataclass(frozen=True)
class ResumeSectionContent:
    """一个已识别简历区块及其确定性识别置信度。

    ``content`` 仅在当前请求内存里存在。调用方必须在交给模型或保存历史前完成
    脱敏，禁止将本对象整体写入数据库。
    """

    section: ResumeSection
    content: str
    recognition_confidence: float


@dataclass(frozen=True)
class ResumeProfile:
    """一次简历文本归纳结果，不包含文件路径或附件元数据。"""

    sections: tuple[ResumeSectionContent, ...]
    unclassified_text: str
    source_character_count: int

    def has_section(self, section: ResumeSection) -> bool:
        """返回目标区块是否有可用内容，供后续低置信度确认 UI 使用。"""

        return any(item.section is section and item.content for item in self.sections)

    def to_model_outline(self, *, max_characters_per_section: int = 2_000) -> str:
        """导出有边界的简历提纲，避免单份简历挤占整个模型上下文窗口。"""

        if max_characters_per_section <= 0:
            raise ValueError("每个简历区块的最大字符数必须大于零")

        parts: list[str] = []
        for item in self.sections:
            title = _SECTION_LABELS[item.section]
            parts.append(f"【{title}】\n{_truncate(item.content, max_characters_per_section)}")
        if self.unclassified_text:
            parts.append(
                "【待确认片段】\n"
                + _truncate(self.unclassified_text, max_characters_per_section),
            )
        return "\n\n".join(parts)


_SECTION_LABELS: dict[ResumeSection, str] = {
    ResumeSection.PROFILE: "个人概述与求职意向",
    ResumeSection.WORK_EXPERIENCE: "工作与实习经历",
    ResumeSection.EDUCATION: "教育经历",
    ResumeSection.PROJECTS: "项目经历",
    ResumeSection.SKILLS: "技能与工具",
    ResumeSection.CERTIFICATIONS: "证书与奖项",
    ResumeSection.PUBLICATIONS: "论文成果",
    ResumeSection.LANGUAGES: "语言能力",
    ResumeSection.OTHER: "其他信息",
}

_SECTION_ALIASES: dict[ResumeSection, frozenset[str]] = {
    ResumeSection.PROFILE: frozenset(
        {
            "个人简介",
            "个人概述",
            "职业概述",
            "职业目标",
            "求职意向",
            "自我评价",
            "个人总结",
            "summary",
            "profile",
            "objective",
            "professional summary",
        },
    ),
    ResumeSection.WORK_EXPERIENCE: frozenset(
        {
            "工作经历",
            "工作经验",
            "职业经历",
            "实习经历",
            "实习经验",
            "experience",
            "work experience",
            "employment history",
            "internship",
            "internships",
        },
    ),
    ResumeSection.EDUCATION: frozenset(
        {
            "教育经历",
            "教育背景",
            "学历",
            "教育",
            "education",
            "education background",
            "academic background",
        },
    ),
    ResumeSection.PROJECTS: frozenset(
        {
            "项目经历",
            "项目经验",
            "项目",
            "项目实践",
            "projects",
            "project experience",
            "selected projects",
        },
    ),
    ResumeSection.SKILLS: frozenset(
        {
            "专业技能",
            "技能",
            "技术栈",
            "技能清单",
            "工具",
            "skills",
            "technical skills",
            "technologies",
            "tools",
        },
    ),
    ResumeSection.CERTIFICATIONS: frozenset(
        {
            "证书",
            "证书奖项",
            "奖项",
            "荣誉",
            "certifications",
            "certificates",
            "awards",
            "honors",
        },
    ),
    ResumeSection.PUBLICATIONS: frozenset(
        {
            "论文",
            "论文成果",
            "发表论文",
            "publications",
            "papers",
        },
    ),
    ResumeSection.LANGUAGES: frozenset(
        {
            "语言能力",
            "语言",
            "外语能力",
            "languages",
            "language skills",
        },
    ),
}

_NUMBERING_PREFIX = re.compile(r"^(?:\d+[.、．]\s*|[一二三四五六七八九十]+[、.．]\s*)")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(?P<label>.+?)\s*#*$")


class ResumeNormalizer:
    """基于标题和阅读顺序将 Markdown/纯文本简历划分为可解释区块。

    放在文档解析层之后、脱敏层之前：它只处理当前 Turn 的文本，不发起网络请求、
    不调用 LLM、没有共享可变状态，因此可被并发请求安全复用。
    """

    def normalize(self, text: str) -> ResumeProfile:
        """按中英文常见区块标题归类文本，无法归类的片段保留供模型谨慎确认。"""

        source_text = text.strip()
        if not source_text:
            return ResumeProfile(sections=(), unclassified_text="", source_character_count=0)

        buckets: dict[ResumeSection, list[str]] = {}
        confidence_by_section: dict[ResumeSection, float] = {}
        unclassified_lines: list[str] = []
        active_section: ResumeSection | None = None

        for original_line in source_text.splitlines():
            line = original_line.rstrip()
            resolved = self._resolve_heading(line)
            if resolved is not None:
                active_section, confidence = resolved
                buckets.setdefault(active_section, [])
                confidence_by_section[active_section] = max(
                    confidence_by_section.get(active_section, 0.0),
                    confidence,
                )
                continue

            if active_section is None:
                unclassified_lines.append(line)
            else:
                buckets[active_section].append(line)

        sections = tuple(
            ResumeSectionContent(
                section=section,
                content=content,
                recognition_confidence=confidence_by_section[section],
            )
            for section in ResumeSection
            if section is not ResumeSection.OTHER
            and (content := _collapse_lines(buckets.get(section, [])))
        )
        other_content = _collapse_lines(unclassified_lines)
        return ResumeProfile(
            sections=sections,
            unclassified_text=other_content,
            source_character_count=len(source_text),
        )

    @staticmethod
    def _resolve_heading(line: str) -> tuple[ResumeSection, float] | None:
        """识别明确标题，避免把普通项目描述误判为章节。"""

        stripped = line.strip()
        if not stripped:
            return None

        markdown_match = _MARKDOWN_HEADING.match(stripped)
        is_explicit_markdown_heading = markdown_match is not None
        candidate = markdown_match.group("label") if markdown_match else stripped
        normalized = _normalize_label(candidate)
        if not normalized:
            return None

        for section, aliases in _SECTION_ALIASES.items():
            if normalized in aliases:
                return section, 1.0

        if is_explicit_markdown_heading and len(normalized) <= 64:
            for section, aliases in _SECTION_ALIASES.items():
                if any(alias in normalized for alias in aliases if len(alias) >= 3):
                    return section, 0.86
        return None


def _normalize_label(value: str) -> str:
    """去除 Markdown、编号和末尾冒号，得到可稳定匹配的章节标签。"""

    normalized = value.strip().strip("#").strip()
    normalized = _NUMBERING_PREFIX.sub("", normalized)
    normalized = normalized.rstrip(":：").strip()
    return re.sub(r"\s+", " ", normalized).casefold()


def _collapse_lines(lines: list[str]) -> str:
    """保留段落结构但压缩连续空行，保证模型提纲清晰且可预测。"""

    result: list[str] = []
    previous_empty = False
    for line in lines:
        stripped_right = line.rstrip()
        if not stripped_right.strip():
            if result and not previous_empty:
                result.append("")
            previous_empty = True
            continue
        result.append(stripped_right)
        previous_empty = False
    return "\n".join(result).strip()


def _truncate(value: str, limit: int) -> str:
    """对单个区块保留明确截断标记，避免模型误把截断视作原文结尾。"""

    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n…（该区块内容已截断）"
