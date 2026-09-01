from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from src.career_assistant.contracts import ActivatedSkill
from src.services.skill_library_service import SkillLibraryService, SkillSummary


@dataclass(frozen=True)
class SkillMention:
    """聊天输入框可展示的一条轻量 Skill 候选。"""

    id: str
    name: str
    description: str
    description_zh: str
    source_label: str


@dataclass(frozen=True)
class SkillActivationResult:
    """服务端解析后的 Skill 调用及去除调用标记后的真实任务文本。"""

    skills: tuple[ActivatedSkill, ...]
    user_task_text: str


class CareerSkillRuntime:
    """解析显式 Skill 调用，并把对应 ``SKILL.md`` 确定性挂载到当前会话。"""

    _frontmatter_pattern = re.compile(r"\A---\s*\r?\n.*?\r?\n---\s*(?:\r?\n)?", re.DOTALL)
    _explicit_token_pattern = re.compile(
        r"(?<!\S)/(?P<name>[A-Za-z0-9][A-Za-z0-9_-]{0,79})(?=\s|$)",
    )
    _leading_slash_pattern = re.compile(
        r"\A\s*/(?P<name>[A-Za-z0-9][A-Za-z0-9_-]{0,79})(?=\s|$)",
    )
    def __init__(
        self,
        skill_library: SkillLibraryService,
        *,
        max_activated_skills: int = 3,
        max_instruction_characters: int = 48_000,
        max_total_instruction_characters: int = 80_000,
    ) -> None:
        self._skill_library = skill_library
        self._max_activated_skills = max_activated_skills
        self._max_instruction_characters = max_instruction_characters
        self._max_total_instruction_characters = max_total_instruction_characters

    def list_mentions(self, query: str, *, limit: int = 8) -> tuple[SkillMention, ...]:
        """只使用 Skill 元数据生成候选，不在输入阶段读取完整 SKILL.md。"""

        normalized_query = query.strip().casefold()
        ranked: list[tuple[int, SkillSummary]] = []
        for skill in self._skill_library.list_skills():
            name = skill.name.casefold()
            searchable = " ".join(
                (skill.name, skill.description, skill.description_zh),
            ).casefold()
            if normalized_query and normalized_query not in searchable:
                continue
            score = 0
            if normalized_query:
                if name == normalized_query:
                    score = 3
                elif name.startswith(normalized_query):
                    score = 2
                else:
                    score = 1
            ranked.append((score, skill))

        ranked.sort(key=lambda item: (-item[0], item[1].name.casefold()))
        return tuple(
            SkillMention(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                description_zh=skill.description_zh,
                source_label=skill.source_label,
            )
            for _, skill in ranked[:limit]
        )

    def activate(
        self,
        selected_skill_ids: Sequence[str],
        user_text: str,
    ) -> tuple[ActivatedSkill, ...]:
        """兼容旧调用方；新代码应使用 :meth:`resolve` 取得任务正文。"""

        return self.resolve(selected_skill_ids, user_text).skills

    def resolve(
        self,
        selected_skill_ids: Sequence[str],
        user_text: str,
    ) -> SkillActivationResult:
        """仅激活真实出现在文本中的调用，消除前端残留选择造成的暗中注入。"""

        summaries = self._skill_library.list_skills()
        by_name = {skill.name.casefold(): skill for skill in summaries}
        # Skill ID 只用于兼容前端请求；文本中的显式 token 才是激活事实来源。
        # 这样输入框删除 token 后，即使浏览器残留旧 ID 也不会暗中注入 Skill。
        _ = selected_skill_ids
        requested: list[tuple[SkillSummary, str, bool]] = []
        seen_ids: set[str] = set()
        matches = list(self._explicit_token_pattern.finditer(user_text))
        leading_slash = self._leading_slash_pattern.match(user_text)
        for match in matches:
            skill = by_name.get(match.group("name").casefold())
            if skill is not None and skill.id not in seen_ids:
                # selected_skill_ids 只是 UI 一致性提示；文本中的 /name 才是服务端事实来源。
                source = "slash"
                primary = bool(
                    leading_slash
                    and match.start() == leading_slash.start()
                    and match.group("name").casefold()
                    == leading_slash.group("name").casefold()
                )
                requested.append((skill, source, primary))
                seen_ids.add(skill.id)

        if leading_slash and leading_slash.group("name").casefold() not in by_name:
            raise LookupError(
                f"未找到 Skill：{leading_slash.group('name')}，请从 / 候选列表中选择已安装 Skill",
            )

        if len(requested) > self._max_activated_skills:
            raise ValueError(f"单轮最多激活 {self._max_activated_skills} 个 Skill")

        activated: list[ActivatedSkill] = []
        total_characters = 0
        user_task_text = self._strip_invocation_tokens(user_text, by_name)
        for summary, source, primary in requested:
            detail = self._skill_library.get_skill(summary.id)
            instructions = self._skill_body(detail.markdown)
            if not instructions:
                raise ValueError(f"Skill {summary.name} 的 SKILL.md 没有正文指令")
            skill_directory = self._skill_directory(detail.skill_path, summary.path_hint)
            instructions = self._render_instructions(
                instructions,
                arguments=user_task_text,
                skill_directory=skill_directory,
            )
            if len(instructions) > self._max_instruction_characters:
                raise ValueError(
                    f"Skill {summary.name} 的完整指令超过 "
                    f"{self._max_instruction_characters} 个字符，未进行截断挂载",
                )
            if total_characters + len(instructions) > self._max_total_instruction_characters:
                raise ValueError(
                    f"激活的 Skill 总指令超过 {self._max_total_instruction_characters} 个字符，"
                    "请减少本轮 Skill 数量",
                )
            total_characters += len(instructions)
            activated.append(
                ActivatedSkill(
                    skill_id=summary.id,
                    name=summary.name,
                    description=summary.description,
                    instructions=instructions,
                    invocation_source=source,
                    arguments=user_task_text,
                    primary=primary,
                ),
            )
        return SkillActivationResult(tuple(activated), user_task_text)

    def resolve_for_conversation(
        self,
        selected_skill_ids: Sequence[str],
        user_text: str,
        previous_user_texts: Sequence[str],
    ) -> SkillActivationResult:
        """解析当前输入；未显式调用时继承会话最近一次 Skill 挂载。

        Skill 的正文会针对当前任务重新读取和渲染，避免把上一轮的
        ``$ARGUMENTS`` 固化到后续追问中。当前轮显式调用始终覆盖会话继承。
        """

        current = self.resolve(selected_skill_ids, user_text)
        if current.skills:
            return current

        summaries = self._skill_library.list_skills()
        by_name = {skill.name.casefold(): skill for skill in summaries}
        for previous_text in reversed(previous_user_texts):
            inherited_names: list[str] = []
            seen_names: set[str] = set()
            for match in self._explicit_token_pattern.finditer(previous_text):
                normalized_name = match.group("name").casefold()
                summary = by_name.get(normalized_name)
                if summary is None or normalized_name in seen_names:
                    continue
                inherited_names.append(summary.name)
                seen_names.add(normalized_name)

            if not inherited_names:
                continue
            if len(inherited_names) > self._max_activated_skills:
                inherited_names = inherited_names[: self._max_activated_skills]

            inherited_prompt = " ".join(
                [f"/{inherited_names[0]}"]
                + [f"/{name}" for name in inherited_names[1:]]
            )
            inherited = self.resolve(
                (),
                f"{inherited_prompt} {user_text}".strip(),
            )
            return SkillActivationResult(
                skills=tuple(
                    replace(skill, invocation_source="session")
                    for skill in inherited.skills
                ),
                user_task_text=current.user_task_text,
            )

        return current

    @classmethod
    def _strip_invocation_tokens(
        cls,
        user_text: str,
        by_name: dict[str, SkillSummary],
    ) -> str:
        """只删除已安装 Skill 的 / 调用标记，普通 @面经文本不受影响。"""

        def replace(match: re.Match[str]) -> str:
            return "" if match.group("name").casefold() in by_name else match.group(0)

        stripped = cls._explicit_token_pattern.sub(replace, user_text)
        return re.sub(r"[ \t]+", " ", stripped).strip()

    @classmethod
    def _skill_body(cls, markdown: str) -> str:
        """移除 YAML frontmatter，只把激活后的正文工作流交给模型。"""

        return cls._frontmatter_pattern.sub("", markdown, count=1).strip()

    @staticmethod
    def _skill_directory(skill_path: Path | None, path_hint: str) -> str:
        """返回 Skill 的真实目录；旧适配器没有路径时使用不泄露宿主结构的逻辑目录。"""

        if skill_path is not None:
            return str(skill_path.resolve().parent)
        normalized_hint = path_hint.replace("\\", "/").strip()
        parent = normalized_hint.rsplit("/", 1)[0] if "/" in normalized_hint else "."
        return parent or "."

    @staticmethod
    def _render_instructions(
        instructions: str,
        *,
        arguments: str,
        skill_directory: str,
    ) -> str:
        """展开主流 Agent Skill 参数变量，使挂载后的正文保持原 Skill 语义。"""

        rendered = instructions
        for token in ("${CLAUDE_SKILL_DIR}", "${SKILL_DIR}"):
            rendered = rendered.replace(token, skill_directory)
        for token in ("${ARGUMENTS}", "$ARGUMENTS"):
            rendered = rendered.replace(token, arguments)
        return rendered
