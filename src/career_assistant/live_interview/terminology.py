"""从确认材料提取术语，并进行不新增事实的受约束文本纠正。"""

from __future__ import annotations

import re

from src.career_assistant.live_interview.contracts import CorrectionResult


_TERM_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9.+#/-]{1,}|[A-Z]{2,}(?:\s+\d{2,5})?|[A-Za-z]+\s+[A-Z][A-Za-z0-9.+#/-]+)\b"
)


def extract_terms(*confirmed_texts: str) -> tuple[str, ...]:
    """只从用户已确认文本中提取英文、缩写、标准号等候选术语。"""

    seen: dict[str, str] = {}
    for text in confirmed_texts:
        for match in _TERM_PATTERN.finditer(text):
            term = match.group(0).strip(".,;:()[]{}")
            if len(term) >= 2:
                seen.setdefault(term.casefold(), term)
    return tuple(seen.values())


class TerminologyCorrector:
    def __init__(self, confirmed_terms: tuple[str, ...]) -> None:
        self._terms = confirmed_terms

    def correct(self, raw_text: str) -> CorrectionResult:
        corrected = raw_text
        replacements: list[tuple[str, str]] = []
        for term in self._terms:
            variants = _safe_variants(term)
            for variant in variants:
                pattern = re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)", re.IGNORECASE)
                if pattern.search(corrected):
                    before = corrected
                    corrected = pattern.sub(term, corrected)
                    if corrected != before:
                        replacements.append((variant, term))
                    break
        return CorrectionResult(raw_text=raw_text, corrected_text=corrected, replacements=tuple(replacements))


def _safe_variants(term: str) -> tuple[str, ...]:
    """仅生成保守的空格/大小写变体，不做会补写内容的语义猜测。"""

    variants = {term.casefold()}
    if term.casefold() == "postgresql":
        variants.add("postgres sql")
    return tuple(sorted(variants, key=len, reverse=True))
