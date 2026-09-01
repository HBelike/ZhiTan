"""求职助手基准简历、目标岗位与证据化匹配画像。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CandidateProfileRecord:
    """用户确认后可复用的基准简历版本。"""

    id: UUID
    organization_id: UUID
    actor_id: UUID
    display_name: str
    source_filename: str
    resume_outline: str
    version: int
    created_at: datetime


@dataclass(frozen=True)
class TargetRoleRecord:
    """用户确认后的目标岗位版本。"""

    id: UUID
    organization_id: UUID
    actor_id: UUID
    company_name: str
    role_name: str
    source_kind: str
    source_label: str
    job_text: str
    requirements: tuple[dict[str, Any], ...]
    version: int
    created_at: datetime


@dataclass(frozen=True)
class ConversationContextRecord:
    """一个会话当前启用的可选简历、可选岗位与绑定版本。"""

    binding_id: UUID
    conversation_id: UUID
    binding_version: int
    candidate: CandidateProfileRecord | None
    target_role: TargetRoleRecord | None
    created_at: datetime


_SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "Python": ("python",),
    "Java": ("java",),
    "Go": ("golang", "go语言", "go language"),
    "C++": ("c++", "cpp"),
    "JavaScript": ("javascript", "js"),
    "TypeScript": ("typescript", "ts"),
    "Vue": ("vue", "vue.js", "vue3"),
    "React": ("react", "react.js"),
    "Node.js": ("node.js", "nodejs"),
    "FastAPI": ("fastapi", "fast api"),
    "Django": ("django",),
    "Flask": ("flask",),
    "Spring": ("spring", "spring boot", "springboot"),
    "PostgreSQL": ("postgresql", "postgres", "pg数据库"),
    "MySQL": ("mysql",),
    "SQL": ("sql",),
    "Redis": ("redis",),
    "MongoDB": ("mongodb", "mongo"),
    "Kafka": ("kafka",),
    "Elasticsearch": ("elasticsearch", "elastic search", "es检索"),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    "Linux": ("linux",),
    "Git": ("git",),
    "AWS": ("aws",),
    "Azure": ("azure",),
    "GCP": ("gcp", "google cloud"),
    "RAG": ("rag", "检索增强生成"),
    "LangChain": ("langchain",),
    "LangGraph": ("langgraph",),
    "LLM": ("llm", "大语言模型", "大模型"),
    "MCP": ("mcp", "model context protocol"),
    "NLP": ("nlp", "自然语言处理"),
    "机器学习": ("机器学习", "machine learning"),
    "深度学习": ("深度学习", "deep learning"),
    "微服务": ("微服务", "microservice", "microservices"),
    "REST API": ("rest api", "restful", "api设计", "接口设计"),
    "CI/CD": ("ci/cd", "cicd", "持续集成"),
    "Vite": ("vite",),
    "Next.js": ("next.js", "nextjs"),
    "AI 辅助编程": (
        "ai辅助编程",
        "ai协同开发",
        "ai copilot",
        "cursor",
        "claude code",
        "claudecode",
        "chatgpt pro",
        "chatgptpro",
        "gptpro",
        "github copilot",
    ),
    "全栈开发": ("全栈开发", "全栈工程", "full stack", "full-stack"),
    "高并发": ("高并发",),
    "高可用": ("高可用",),
    "分布式系统": ("分布式系统", "分布式"),
}

_PROJECT_SIGNAL_ALIASES: dict[str, tuple[str, ...]] = {
    "端到端交付": ("端到端", "全链路", "从需求", "生产发布", "独立交付"),
    "产品 Owner": ("产品owner", "product owner", "产品负责人", "产品sense", "产品思维"),
    "电商营销": ("电商营销", "营销玩法", "商家营销", "交易营销"),
    "促销与优惠": ("促销", "优惠券", "发券", "计价", "价格模型"),
    "用户增长": ("用户增长", "裂变", "用户转化", "gmv", "成交"),
    "数据分析": ("数据分析", "数据迭代", "指标分析", "看数据"),
    "资金安全": ("资金安全", "资金", "交易安全"),
    "敏捷交付": ("敏捷", "快速部署", "极速交付", "极速迭代", "灰度上线", "mvp"),
    "研发 SOP": ("sop", "流程沉淀", "沉淀并推广", "研发效能", "产研效率"),
    "用户体验": ("用户体验", "ux", "业务痛点"),
}

_MUST_MARKERS = ("必须", "要求", "至少", "精通", "熟练", "必备", "硬性", "任职资格")
_PREFERRED_MARKERS = ("优先", "加分", "preferred", "bonus", "更佳")
_EXPERIENCE_MARKERS = ("经验", "年", "背景", "负责过", "主导过", "管理", "带领")
_PROJECT_MARKERS = (
    "设计",
    "开发",
    "建设",
    "构建",
    "落地",
    "优化",
    "架构",
    "系统",
    "平台",
    "项目",
    "交付",
    "迭代",
    "产品",
    "业务",
)
_YEAR_PATTERN = re.compile(r"(?P<years>\d+(?:\.\d+)?)\s*年")
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•·▪◦]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*")
_MARKDOWN_HEADING_PREFIX = re.compile(r"^\s*#{1,6}\s*")
_SECTION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("qualification", ("任职资格", "任职要求", "职位要求", "岗位要求", "基本要求", "核心要求", "我们希望")),
    ("preferred", ("加分项", "优先条件", "优先考虑", "加分条件")),
    ("responsibility", ("核心职责", "主要职责", "工作职责", "岗位职责", "职位职责", "工作内容")),
    ("highlight", ("职位亮点", "岗位亮点", "我们提供")),
)
_NOISE_PATTERNS = (
    re.compile(r"^<!--\s*image\s*-->$", re.IGNORECASE),
    re.compile(r"^\d{1,3}\s*[-~至]\s*\d{1,3}k(?:·\d+薪)?$", re.IGNORECASE),
    re.compile(r"^(?:微信扫码|扫码|举报|立即沟通|申请职位|收藏职位)"),
)


def extract_job_requirements(job_text: str, *, limit: int = 36) -> tuple[dict[str, Any], ...]:
    """从 JD 中确定性拆出要求项，并优先保留可计算的任职资格。"""

    normalized = job_text.replace("\r", "\n")
    raw_parts = re.split(r"\n+|(?<=[。；;])", normalized)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    current_section = "general"
    for source_index, raw_part in enumerate(raw_parts):
        raw_text = raw_part.strip(" \t。；;")
        detected_section = _section_name(raw_text)
        if detected_section is not None:
            current_section = detected_section
            continue

        text = _normalize_requirement_text(raw_text)
        if len(text) < 3 or text in seen or _is_noise(text):
            continue
        seen.add(text)
        lowered = text.casefold()
        skills = _find_skills(lowered)
        project_signals = _find_project_signals(lowered)
        year_match = _YEAR_PATTERN.search(text)
        dimensions: list[str] = []
        if skills:
            dimensions.append("skill")
        if year_match or any(marker in text for marker in _EXPERIENCE_MARKERS):
            dimensions.append("experience")
        if (
            project_signals
            or current_section in {"responsibility", "highlight"}
            or any(marker in text for marker in _PROJECT_MARKERS)
        ):
            dimensions.append("project")

        if skills:
            category = "skill"
        elif year_match or any(marker in text for marker in _EXPERIENCE_MARKERS):
            category = "experience"
        elif "project" in dimensions:
            category = "project"
        else:
            category = "other"
        if any(marker in lowered for marker in _PREFERRED_MARKERS):
            priority = "preferred"
            weight = 1
        elif current_section == "qualification" or any(marker in text for marker in _MUST_MARKERS):
            priority = "must"
            weight = 2
        else:
            priority = "preferred"
            weight = 1
        candidates.append(
            {
                "source_index": source_index,
                "text": text[:600],
                "category": category,
                "dimensions": dimensions,
                "section": current_section,
                "priority": priority,
                "weight": weight,
                "skills": list(skills),
                "project_signals": list(project_signals),
                "required_years": float(year_match.group("years")) if year_match else None,
            }
        )

    selected = _select_requirements(candidates, limit)
    return tuple(
        {
            "id": f"req-{index}",
            **{key: value for key, value in item.items() if key != "source_index"},
        }
        for index, item in enumerate(selected, start=1)
    )


def build_match_assessment(
    resume_outline: str,
    requirements: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """按透明规则计算四维岗位画像，并返回逐要求证据。"""

    resume_lower = resume_outline.casefold()
    resume_skills = set(_find_skills(resume_lower))
    resume_project_signals = set(_find_project_signals(resume_lower))
    resume_years = [float(item.group("years")) for item in _YEAR_PATTERN.finditer(resume_outline)]
    max_resume_years = max(resume_years, default=0.0)
    results: list[dict[str, Any]] = []

    for requirement in requirements:
        required_skills = set(requirement.get("skills") or ())
        required_project_signals = set(requirement.get("project_signals") or ())
        required_years = requirement.get("required_years")
        dimension_matches: dict[str, dict[str, Any]] = {}
        if required_skills:
            dimension_matches["skill"] = _term_match(
                resume_outline,
                required_skills,
                resume_skills,
                _SKILL_ALIASES,
                "skill",
            )
        if required_years is not None:
            if max_resume_years >= float(required_years):
                experience_factor = 1.0
                experience_reason = "years_meet"
            elif max_resume_years >= float(required_years) * 0.8:
                experience_factor = 0.5
                experience_reason = "years_near"
            else:
                experience_factor = 0.0
                experience_reason = "years_below"
            dimension_matches["experience"] = {
                "factor": experience_factor,
                "reason_code": experience_reason,
                "evidence": (
                    f"简历可识别的最高年限表述：{max_resume_years:g} 年"
                    if max_resume_years
                    else ""
                ),
            }
        if "project" in _requirement_dimensions(requirement):
            if required_project_signals:
                dimension_matches["project"] = _term_match(
                    resume_outline,
                    required_project_signals,
                    resume_project_signals,
                    _PROJECT_SIGNAL_ALIASES,
                    "project",
                )
            elif required_skills:
                dimension_matches["project"] = dict(dimension_matches["skill"])

        scored_matches = [
            match for match in dimension_matches.values() if match.get("factor") is not None
        ]
        primary_match = min(
            scored_matches,
            key=lambda item: float(item["factor"]),
            default={"factor": None, "reason_code": "not_assessed", "evidence": ""},
        )

        results.append(
            {
                **requirement,
                "dimension_matches": dimension_matches,
                "match_factor": primary_match["factor"],
                "reason_code": primary_match["reason_code"],
                "evidence": primary_match["evidence"],
            }
        )

    dimensions = {
        "skill_coverage": _dimension(results, "skill", "技能证据覆盖率", "higher"),
        "experience_coverage": _dimension(results, "experience", "经验要求达成率", "higher"),
        "project_relevance": _dimension(results, "project", "项目场景适配率", "higher"),
        "critical_gap": _critical_gap(results),
    }
    return {
        "algorithm_version": "lexical-evidence-v2",
        "dimensions": dimensions,
        "requirements": results,
        "recognized_resume_skills": sorted(resume_skills),
        "recognized_resume_project_signals": sorted(resume_project_signals),
        "disclaimer": "画像只衡量已确认 JD 与基准简历之间的证据覆盖，不代表录用概率。",
    }


def _dimension(
    results: list[dict[str, Any]],
    category: str,
    label: str,
    direction: str,
) -> dict[str, Any]:
    eligible = [
        item for item in results
        if (
            category in _requirement_dimensions(item)
            and item.get("dimension_matches", {}).get(category, {}).get("factor") is not None
        )
    ]
    denominator = sum(float(item["weight"]) for item in eligible)
    numerator = sum(
        float(item["weight"])
        * float(item["dimension_matches"][category]["factor"])
        for item in eligible
    )
    return _score_payload(label, numerator, denominator, direction)


def _critical_gap(results: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        item for item in results
        if item["priority"] == "must" and item["match_factor"] is not None
    ]
    denominator = sum(float(item["weight"]) for item in eligible)
    numerator = sum(
        float(item["weight"]) * (1.0 - float(item["match_factor"]))
        for item in eligible
    )
    return _score_payload("关键要求缺口率", numerator, denominator, "lower")


def _score_payload(
    label: str,
    numerator: float,
    denominator: float,
    direction: str,
) -> dict[str, Any]:
    score = round(numerator / denominator * 100, 1) if denominator else None
    return {
        "label": label,
        "score": score,
        "numerator": round(numerator, 2),
        "denominator": round(denominator, 2),
        "direction": direction,
        "status": "ready" if denominator else "insufficient_data",
    }


def _find_skills(text: str) -> tuple[str, ...]:
    return _find_terms(text, _SKILL_ALIASES)


def _find_project_signals(text: str) -> tuple[str, ...]:
    return _find_terms(text, _PROJECT_SIGNAL_ALIASES)


def _find_terms(text: str, aliases_by_name: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    padded = f" {text.casefold()} "
    found: list[str] = []
    for canonical, aliases in aliases_by_name.items():
        if any(_contains_alias(padded, alias.casefold()) for alias in aliases):
            found.append(canonical)
    return tuple(found)


def _contains_alias(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9+#./-]+", alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    return alias in text


def _find_evidence_line(
    resume_outline: str,
    terms: tuple[str, ...],
    aliases_by_name: dict[str, tuple[str, ...]],
) -> str:
    aliases = tuple(
        alias.casefold()
        for term in terms
        for alias in aliases_by_name.get(term, (term,))
    )
    for line in resume_outline.splitlines():
        lowered = line.casefold()
        if any(alias in lowered for alias in aliases):
            return line.strip()[:280]
    return ""


def _term_match(
    resume_outline: str,
    required_terms: set[str],
    resume_terms: set[str],
    aliases_by_name: dict[str, tuple[str, ...]],
    reason_prefix: str,
) -> dict[str, Any]:
    matched = required_terms & resume_terms
    if not matched:
        return {
            "factor": 0.0,
            "reason_code": f"{reason_prefix}_not_found",
            "evidence": "",
        }
    coverage = len(matched) / len(required_terms)
    factor = 1.0 if coverage == 1 else 0.5
    return {
        "factor": factor,
        "reason_code": (
            f"direct_{reason_prefix}_evidence"
            if factor == 1
            else f"partial_{reason_prefix}_evidence"
        ),
        "evidence": _find_evidence_line(
            resume_outline,
            tuple(sorted(matched)),
            aliases_by_name,
        ),
    }


def _requirement_dimensions(requirement: dict[str, Any]) -> tuple[str, ...]:
    dimensions = tuple(requirement.get("dimensions") or ())
    if dimensions:
        return dimensions
    category = str(requirement.get("category") or "")
    return (category,) if category in {"skill", "experience", "project"} else ()


def _normalize_requirement_text(raw_text: str) -> str:
    without_heading = _MARKDOWN_HEADING_PREFIX.sub("", raw_text)
    return _BULLET_PREFIX.sub("", without_heading).strip(" \t。；;")


def _section_name(raw_text: str) -> str | None:
    normalized = _MARKDOWN_HEADING_PREFIX.sub("", raw_text).strip()
    normalized = normalized.strip("【】[] ")
    normalized = normalized.rstrip("：:").strip()
    if not normalized or len(normalized) > 32:
        return None
    if "：" in normalized or ":" in normalized:
        return None
    for section, markers in _SECTION_MARKERS:
        if any(marker in normalized for marker in markers):
            return section
    return None


def _is_noise(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in _NOISE_PATTERNS)


def _select_requirements(
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("岗位要求数量上限必须大于零")
    if len(candidates) <= limit:
        return candidates

    def priority(item: dict[str, Any]) -> tuple[int, int, int, int]:
        assessable = bool(
            item.get("skills")
            or item.get("project_signals")
            or item.get("required_years") is not None
        )
        return (
            1 if item.get("section") == "qualification" else 0,
            1 if item.get("priority") == "must" else 0,
            1 if assessable else 0,
            1 if item.get("dimensions") else 0,
        )

    selected = sorted(
        candidates,
        key=lambda item: (priority(item), -int(item["source_index"])),
        reverse=True,
    )[:limit]
    return sorted(selected, key=lambda item: int(item["source_index"]))
