from __future__ import annotations

import re

from src.services.skill_library_service import SkillLibraryService


class ArticleSkillPromptLoader:
    """按固定名称读取文章生成 Skill，并返回可挂载到模型的正文。"""

    _frontmatter_pattern = re.compile(
        r"\A---\s*\r?\n.*?\r?\n---\s*(?:\r?\n)?",
        re.DOTALL,
    )

    def __init__(
        self,
        skill_library: SkillLibraryService,
        *,
        max_characters: int = 32_000,
    ) -> None:
        self._skill_library = skill_library
        self._max_characters = max(1, max_characters)

    def load(self, name: str = "github-project-blog") -> str:
        """读取指定 Skill；缺失、空正文或超长时拒绝静默降级。"""

        normalized_name = name.strip().casefold()
        summary = next(
            (
                item
                for item in self._skill_library.list_skills()
                if item.name.casefold() == normalized_name
            ),
            None,
        )
        if summary is None:
            raise LookupError(f"缺少文章生成 Skill：{name}")

        detail = self._skill_library.get_skill(summary.id)
        instructions = self._frontmatter_pattern.sub("", detail.markdown, count=1).strip()
        if not instructions:
            raise ValueError(f"文章生成 Skill {name} 的 SKILL.md 没有正文")
        if len(instructions) > self._max_characters:
            raise ValueError(
                f"文章生成 Skill {name} 正文超过 {self._max_characters} 个字符",
            )
        return instructions
