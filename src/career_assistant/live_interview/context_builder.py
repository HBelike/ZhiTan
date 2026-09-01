"""为实时回答组装低延迟、可追溯且有事实边界的最小上下文。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveAnswerContext:
    candidate_facts: str = ""
    target_role: str = ""
    recent_conversation: tuple[str, ...] = ()
    interview_evidence: tuple[str, ...] = ()
    terminology: tuple[str, ...] = ()


class LiveContextBuilder:
    """从已经确认的资料快照构建回答上下文，不读取原始附件。"""

    def build(
        self,
        *,
        candidate_facts: str = "",
        target_role: str = "",
        recent_conversation: tuple[str, ...] = (),
        interview_evidence: tuple[str, ...] = (),
        terminology: tuple[str, ...] = (),
    ) -> LiveAnswerContext:
        return LiveAnswerContext(
            candidate_facts=candidate_facts.strip()[:30_000],
            target_role=target_role.strip()[:30_000],
            recent_conversation=recent_conversation[-12:],
            interview_evidence=interview_evidence[:5],
            terminology=terminology[:200],
        )
