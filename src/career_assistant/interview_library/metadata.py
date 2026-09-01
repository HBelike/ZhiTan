"""面经材料的本地元数据提取。

该模块只从已经解析出的文本中推断公司、岗位、日期与标签；不保存原文件，也不依赖
额外的云端模型。因此即使图片 OCR 已经可读，也能在进入向量库前给用户一个可校正的
结构化草稿。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class InferredInterviewMetadata:
    """导入页面可编辑的面经元数据草稿。"""

    company_name: str | None
    role_name: str | None
    interview_date: date | None
    source_platform: str | None
    tags: tuple[str, ...]
    summary_text: str | None
    confidence: float
    evidence: tuple[str, ...]


class InterviewMaterialMetadataExtractor:
    """用确定性规则从面经 OCR/文档文本中预填元数据。"""

    _COMPANY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("字节跳动", ("字节跳动", "字节", "TikTok", "抖音")),
        ("阿里巴巴", ("阿里", "阿里云", "淘宝", "蚂蚁")),
        ("腾讯", ("腾讯", "微信")),
        ("百度", ("百度", "文心")),
        ("美团", ("美团",)),
        ("小红书", ("小红书",)),
        ("京东", ("京东",)),
        ("拼多多", ("拼多多", "PDD")),
        ("快手", ("快手",)),
        ("华为", ("华为",)),
    )
    _ROLE_PHRASES: tuple[str, ...] = (
        "AI Agent开发工程师",
        "AI Agent开发",
        "AI Agent工程师",
        "后端开发工程师",
        "Java后端开发",
        "Java开发工程师",
        "前端开发工程师",
        "算法工程师",
        "测试开发工程师",
        "数据工程师",
        "运维工程师",
        "产品经理",
        "AI Agent",
    )
    _TOPIC_TAGS: tuple[str, ...] = (
        "ASR",
        "RAG",
        "Transformer",
        "Multi-Agent",
        "Agent",
        "Token",
        "上下文",
        "系统设计",
        "向量检索",
        "SFT",
        "PPO",
        "DPO",
        "GRPO",
        "隐私",
        "树结构",
    )
    _STAGE_PATTERN = re.compile(r"(?P<stage>[一二三四五六七八九十]+面|终面|HR面|面试)")
    _TITLE_PATTERN = re.compile(r"(?P<title>[^\n]{1,120}(?:面经|面试)[^\n]{0,30})", re.IGNORECASE)
    _DATE_PATTERN = re.compile(
        r"(?P<year>20\d{2})[./-](?P<month>\d{1,2})(?:[./-](?P<day>\d{1,2}))?"
    )

    def extract(
        self,
        extracted_text: str,
        *,
        filename: str = "",
        source_platform: str | None = None,
    ) -> InferredInterviewMetadata:
        """返回可编辑的预填草稿；不确定的字段保持 ``None``。"""

        text = self._normalize(extracted_text)
        title = self._find_title(text, filename)
        company = self._extract_labeled_value(text, "公司") or self._match_company(text)
        role = self._extract_labeled_value(text, "岗位") or self._match_role(title, text)
        interview_date = self._extract_date(text)
        platform = source_platform or self._infer_platform(title)
        stage = self._match_stage(title)
        tags = self._build_tags(text, stage)
        question_count = self._count_questions(text)
        summary = self._build_summary(question_count, tags)
        evidence = self._build_evidence(title, company, role, interview_date, stage)
        confidence = self._confidence(company, role, question_count)
        return InferredInterviewMetadata(
            company_name=company,
            role_name=role,
            interview_date=interview_date,
            source_platform=platform,
            tags=tuple(tags),
            summary_text=summary,
            confidence=confidence,
            evidence=tuple(evidence),
        )

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = value.replace("AlAgent", "AI Agent").replace("AIAgent", "AI Agent")
        normalized = normalized.replace("，", ",").replace("：", ":")
        return re.sub(r"[ \t]+", " ", normalized).strip()

    def _find_title(self, text: str, filename: str) -> str:
        for line in text.splitlines()[:18]:
            match = self._TITLE_PATTERN.search(line.strip())
            if match:
                return match.group("title").strip()
        match = self._TITLE_PATTERN.search(text)
        if match:
            return match.group("title").strip()
        return filename.rsplit(".", 1)[0].strip()

    @staticmethod
    def _extract_labeled_value(text: str, field_name: str) -> str | None:
        match = re.search(rf"(?:^|\n)\s*{re.escape(field_name)}(?:名称)?\s*[:：]\s*([^\n，,；;]{{2,40}})", text)
        return match.group(1).strip() if match else None

    def _match_company(self, text: str) -> str | None:
        for canonical, aliases in self._COMPANY_ALIASES:
            if any(re.search(rf"(?<![A-Za-z]){re.escape(alias)}", text, re.IGNORECASE) for alias in aliases):
                return canonical
        return None

    def _match_role(self, title: str, text: str) -> str | None:
        candidate = f"{title}\n{text[:500]}"
        for phrase in self._ROLE_PHRASES:
            if phrase.lower() in candidate.lower():
                return phrase
        return None

    def _extract_date(self, text: str) -> date | None:
        for line in text.splitlines()[:25]:
            if "日期" not in line and "时间" not in line:
                continue
            match = self._DATE_PATTERN.search(line)
            if match:
                try:
                    return date(
                        int(match.group("year")),
                        int(match.group("month")),
                        int(match.group("day") or "1"),
                    )
                except ValueError:
                    return None
        return None

    @staticmethod
    def _infer_platform(title: str) -> str | None:
        parenthetical = re.search(r"[（(]([^()（）]{2,30})[）)]", title)
        return parenthetical.group(1).strip() if parenthetical else None

    def _match_stage(self, title: str) -> str | None:
        match = self._STAGE_PATTERN.search(title)
        return match.group("stage") if match else None

    def _build_tags(self, text: str, stage: str | None) -> list[str]:
        tags = [stage] if stage else []
        lowered = text.lower()
        for tag in self._TOPIC_TAGS:
            if tag.lower() in lowered and tag not in tags:
                tags.append(tag)
            if len(tags) >= 10:
                break
        return tags

    @staticmethod
    def _count_questions(text: str) -> int:
        numbered = re.findall(r"(?:^|\n)\s*(?:[-*]?\s*)?(\d{1,2})[.、)]", text)
        values = [int(item) for item in numbered]
        # OCR 可能漏掉某一个序号，但后续仍保留了“23.”。面经题号通常连续，使用最大序号
        # 比简单计数更接近原始材料，同时不会影响没有编号的普通文章。
        return max(values, default=0)

    @staticmethod
    def _build_summary(question_count: int, tags: list[str]) -> str | None:
        if question_count:
            topics = "、".join(tag for tag in tags if tag not in {"一面", "二面", "三面", "终面", "HR面", "面试"})
            suffix = f"，覆盖 {topics}" if topics else ""
            return f"自动识别到 {question_count} 个面试问题{suffix}。"
        return None

    @staticmethod
    def _build_evidence(
        title: str,
        company: str | None,
        role: str | None,
        interview_date: date | None,
        stage: str | None,
    ) -> list[str]:
        evidence = [f"标题：{title}"] if title else []
        if company:
            evidence.append(f"公司：{company}")
        if role:
            evidence.append(f"岗位：{role}")
        if stage:
            evidence.append(f"轮次：{stage}")
        if interview_date:
            evidence.append(f"日期：{interview_date.isoformat()}")
        return evidence

    @staticmethod
    def _confidence(company: str | None, role: str | None, question_count: int) -> float:
        score = 0.25 + (0.3 if company else 0.0) + (0.3 if role else 0.0) + (0.15 if question_count else 0.0)
        return round(min(score, 1.0), 2)
