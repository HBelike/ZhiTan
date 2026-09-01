"""把浏览器采集结果清洗成最低可用的题目契约。"""

from __future__ import annotations

import re

from src.career_assistant.online_assessment.contracts import (
    AssessmentProblem,
    AssessmentTestCase,
    CapturedProblemInput,
    InterfaceKind,
    ProgrammingLanguage,
    TestCaseKind,
)


_LANGUAGE_ALIASES = {
    "python": ProgrammingLanguage.PYTHON,
    "python3": ProgrammingLanguage.PYTHON,
    "py": ProgrammingLanguage.PYTHON,
    "javascript": ProgrammingLanguage.JAVASCRIPT,
    "js": ProgrammingLanguage.JAVASCRIPT,
    "node": ProgrammingLanguage.JAVASCRIPT,
    "nodejs": ProgrammingLanguage.JAVASCRIPT,
    "java": ProgrammingLanguage.JAVA,
    "cpp": ProgrammingLanguage.CPP,
    "c++": ProgrammingLanguage.CPP,
    "cplusplus": ProgrammingLanguage.CPP,
}


def normalize_capture(capture: CapturedProblemInput) -> AssessmentProblem:
    """执行无模型的第一阶段清洗；不完整结果由置信度闸门阻止自动解题。"""

    statement = _clean_text(_best_statement(capture))[:25_000]
    if not statement:
        statement = "未识别到题目正文"
    language = _LANGUAGE_ALIASES.get(capture.language_hint.strip().lower(), ProgrammingLanguage.PYTHON)
    function_signature = _resolve_function_signature(capture)
    interface_kind = _infer_interface_kind(capture, function_signature)
    incomplete_reasons: list[str] = []
    if statement == "未识别到题目正文" or len(statement) < 40:
        incomplete_reasons.append("题目正文不完整")
    if interface_kind is InterfaceKind.UNKNOWN:
        incomplete_reasons.append("无法判断题目输入输出接口")
    if interface_kind is InterfaceKind.FUNCTION and not function_signature:
        incomplete_reasons.append("函数题缺少函数签名")

    examples = _normalize_public_tests(capture.public_test_candidates)
    confidence = _confidence(
        statement=statement,
        interface_kind=interface_kind,
        has_signature=bool(function_signature),
        has_examples=bool(examples),
        has_candidate=bool(capture.problem_candidates),
    )
    return AssessmentProblem(
        source_url=capture.source_url,
        source_platform=_normalize_platform(capture.source_platform),
        title=_normalize_title(capture.source_title, statement),
        statement=statement,
        constraints=_extract_constraints(statement),
        examples=examples,
        starter_code=capture.starter_code.strip(),
        language=language,
        interface_kind=interface_kind,
        function_signature=function_signature,
        confidence=confidence,
        incomplete_reasons=incomplete_reasons,
    )


def _best_statement(capture: CapturedProblemInput) -> str:
    candidates = [_clean_text(item) for item in capture.problem_candidates]
    candidates = [item for item in candidates if item]
    if candidates:
        return max(candidates, key=len)
    return capture.visible_text


def _clean_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.replace("\x00", "").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _infer_interface_kind(capture: CapturedProblemInput, function_signature: str) -> InterfaceKind:
    if function_signature:
        return InterfaceKind.FUNCTION
    starter = capture.starter_code
    if re.search(r"\b(class\s+Solution|def\s+\w+\s*\(|function\s+\w+\s*\()", starter):
        return InterfaceKind.FUNCTION
    content = f"{capture.visible_text}\n{' '.join(capture.problem_candidates)}".lower()
    if any(marker in content for marker in ("standard input", "standard output", "输入格式", "输出格式")):
        return InterfaceKind.STDIN_STDOUT
    return InterfaceKind.UNKNOWN


def _resolve_function_signature(capture: CapturedProblemInput) -> str:
    explicit = capture.function_signature.strip()
    if explicit:
        return explicit
    starter = capture.starter_code
    patterns = (
        r"(?m)^\s*((?:async\s+)?def\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\))",
        r"(?m)^\s*((?:export\s+)?(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\))",
        r"(?m)^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*\s*=\s*(?:async\s+)?function\s*\([^\n)]*\))",
        r"(?m)^\s*((?:(?:public|private|protected|static|final|virtual)\s+)*[\w:<>,\[\] ?&*]+\s+[A-Za-z_$][\w$]*\s*\([^\n)]*\)(?:\s+const)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, starter)
        if match:
            return match.group(1).strip()
    return ""


def _normalize_public_tests(candidates: list[dict[str, object]]) -> list[AssessmentTestCase]:
    results: list[AssessmentTestCase] = []
    for index, item in enumerate(candidates[:20]):
        input_payload = item.get("input", item.get("input_payload", ""))
        expected_output = str(item.get("output", item.get("expected_output", ""))).strip()
        if input_payload in (None, "") or not expected_output:
            continue
        results.append(
            AssessmentTestCase(
                test_id=f"public-{index + 1}",
                kind=TestCaseKind.PUBLIC,
                input_payload=input_payload,
                expected_output=expected_output,
                explanation=str(item.get("explanation", ""))[:1_000],
            )
        )
    return results


def _normalize_title(source_title: str, statement: str) -> str:
    title = re.sub(
        r"\s*[-|·]\s*(?:力扣\s*[（(]?LeetCode[）)]?|LeetCode|HackerRank).*?$",
        "",
        source_title,
        flags=re.IGNORECASE,
    ).strip()
    if title:
        return title[:300]
    first_line = statement.splitlines()[0].strip()
    return first_line[:300] if first_line else "未识别题目"


def _normalize_platform(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in {"leetcode", "hackerrank"} else "generic"


def _extract_constraints(statement: str) -> list[str]:
    lines = statement.splitlines()
    return [line[:500] for line in lines if re.search(r"(<=|>=|约束|constraint|范围)", line, re.IGNORECASE)][:100]


def _confidence(
    *,
    statement: str,
    interface_kind: InterfaceKind,
    has_signature: bool,
    has_examples: bool,
    has_candidate: bool,
) -> float:
    score = 0.15
    if len(statement) >= 80:
        score += 0.30
    if has_candidate:
        score += 0.15
    if interface_kind is not InterfaceKind.UNKNOWN:
        score += 0.20
    if interface_kind is InterfaceKind.STDIN_STDOUT or has_signature:
        score += 0.10
    if has_examples:
        score += 0.10
    return min(1.0, round(score, 2))
