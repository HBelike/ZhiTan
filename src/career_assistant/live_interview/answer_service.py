"""统一中文、受个人事实约束的实时回答流。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from src.career_assistant.live_interview.context_builder import LiveAnswerContext
from src.career_assistant.live_interview.contracts import QuestionIntent


PromptStreamer = Callable[[str], AsyncIterator[str]]


def build_answer_prompt(
    question: str,
    intent: QuestionIntent,
    context: LiveAnswerContext,
) -> str:
    follow_up_context = ""
    context_rule = "2. 只根据下方“面试官问题”作答，不使用简历、岗位、面经或历史对话。"
    if intent is QuestionIntent.FOLLOW_UP and context.recent_conversation:
        recent = "\n".join(context.recent_conversation[-4:])
        follow_up_context = f"\n仅用于理解追问指代的最近对话：\n{recent}\n"
        context_rule = (
            "2. 以当前面试官问题为核心；最近对话只用于理解“它、刚才、那个方案”等指代，"
            "不得把其中内容扩写成个人事实。"
        )
    return f"""你是实时面试回答助手。请立即给出可扫读的回答建议。

硬性规则：
1. 回答统一使用中文；技术、医疗、金融、法律、制造、教育等领域的专有名词保留原文。
{context_rule}
3. 涉及个人经历、业绩和数字时，只提供回答结构与可替换占位提示，不得编造事实。
4. 先输出“直接结论”和 3～5 个短要点，再给出简短表达示例与可能追问；避免冗长铺垫。

问题类型：{intent.value}
面试官问题：{question.strip()}
{follow_up_context}
"""


class LiveAnswerService:
    def __init__(self, prompt_streamer: PromptStreamer) -> None:
        self._prompt_streamer = prompt_streamer

    async def stream(
        self,
        question: str,
        intent: QuestionIntent,
        context: LiveAnswerContext,
    ) -> AsyncIterator[str]:
        prompt = build_answer_prompt(question, intent, context)
        async for chunk in self._prompt_streamer(prompt):
            if chunk:
                yield chunk


__all__ = ["LiveAnswerContext", "LiveAnswerService", "build_answer_prompt"]
