from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.services.article_skill_prompt_loader import ArticleSkillPromptLoader


class _FakeSkillLibrary:
    def __init__(self, markdown: str | None) -> None:
        self._markdown = markdown
        self.detail_reads: list[str] = []

    def list_skills(self) -> list[SimpleNamespace]:
        if self._markdown is None:
            return []
        return [SimpleNamespace(id="skill-github-project-blog", name="github-project-blog")]

    def get_skill(self, skill_id: str) -> SimpleNamespace:
        self.detail_reads.append(skill_id)
        return SimpleNamespace(markdown=self._markdown)


class ArticleSkillPromptLoaderTests(unittest.TestCase):
    def test_load_returns_body_without_frontmatter(self) -> None:
        library = _FakeSkillLibrary(
            markdown=(
                "---\n"
                "name: github-project-blog\n"
                "description: test\n"
                "---\n\n"
                "# 写作规则\n\n"
                "只根据证据写作。"
            ),
        )

        instructions = ArticleSkillPromptLoader(library).load()  # type: ignore[arg-type]

        self.assertNotIn("name: github-project-blog", instructions)
        self.assertIn("只根据证据写作", instructions)
        self.assertEqual(library.detail_reads, ["skill-github-project-blog"])

    def test_load_rejects_missing_skill(self) -> None:
        loader = ArticleSkillPromptLoader(_FakeSkillLibrary(markdown=None))  # type: ignore[arg-type]

        with self.assertRaisesRegex(LookupError, "github-project-blog"):
            loader.load()

    def test_load_matches_skill_name_case_insensitively(self) -> None:
        library = _FakeSkillLibrary(markdown="# 写作规则\n\n只根据证据写作。")

        instructions = ArticleSkillPromptLoader(library).load("GitHub-Project-Blog")  # type: ignore[arg-type]

        self.assertIn("只根据证据写作", instructions)

    def test_load_rejects_empty_body(self) -> None:
        loader = ArticleSkillPromptLoader(
            _FakeSkillLibrary(
                markdown=(
                    "---\n"
                    "name: github-project-blog\n"
                    "description: test\n"
                    "---\n"
                ),
            ),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(ValueError, "没有正文"):
            loader.load()

    def test_load_rejects_oversized_body(self) -> None:
        loader = ArticleSkillPromptLoader(
            _FakeSkillLibrary(
                markdown=(
                    "---\n"
                    "name: github-project-blog\n"
                    "description: test\n"
                    "---\n\n"
                    "123456"
                ),
            ),  # type: ignore[arg-type]
            max_characters=5,
        )

        with self.assertRaisesRegex(ValueError, "超过 5 个字符"):
            loader.load()


if __name__ == "__main__":
    unittest.main()
