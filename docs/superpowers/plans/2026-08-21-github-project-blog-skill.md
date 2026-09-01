# GitHub 项目文章 Skill Implementation Plan

> 状态说明（2026-08-21）：这是首次接入时的历史实施计划。计划中保留的标签下限和最大长度校验随后均已取消；现行合同以 `docs/summary_writing_skill_research.md` 为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一个自包含的 `github-project-blog` Skill 接管 `SummaryTask` 的写作方法，并取消每个项目至少 500 个中文字符及其派生的全文最低字数。

**Architecture:** 保留现有动态 Top N、单次生成、质量修复重试、JSON 解析、`ContentBrief` 和入库链路。新增一个只负责按固定名称读取 Skill 正文的加载器，`SummaryTask` 将正文挂载到 system message；Skill 自身编译技术分析、博客写作和去 AI 化规则，不产生运行时 Skill 依赖。

**Tech Stack:** Python 3、`unittest`、现有 `DeepSeekProvider`、现有 `SkillLibraryService`、Markdown `SKILL.md`。

**Spec:** `docs/superpowers/specs/2026-08-21-github-project-blog-skill-design.md`

## Global Constraints

- 项目数量始终使用当期 `len(rankings)`，不得写死为 5。
- `SummaryTask` 继续一次性生成 `title`、`digest`、`article_markdown`，不增加模型调用。
- `github-project-blog` 必须自包含，不在运行时调用 `blog-post-writer`、`humanizer` 或 `technical-review`。
- 取消每项目 500 字最低限制及 `N × 500` 全文最低限制，保留结构、精确数字、标签内容下限和最大长度校验。
- 不扩展 GitHub 证据采集，不改数据库、审核、图片、视频、排版和公众号草稿接口。
- 页面和文档标题继续使用中文；代码变量、类型和 API 名称使用英文。

---

### Task 1: 固定名称的文章 Skill 加载器

**Files:**
- Create: `src/services/article_skill_prompt_loader.py`
- Create: `tests/test_article_skill_prompt_loader.py`

**Interfaces:**
- Consumes: `SkillLibraryService.list_skills() -> list[SkillSummary]` 与 `get_skill(skill_id) -> SkillDetail`
- Produces: `ArticleSkillPromptLoader.load(name: str = "github-project-blog") -> str`

- [ ] **Step 1: 写加载成功、缺失和空正文测试**

```python
class ArticleSkillPromptLoaderTests(unittest.TestCase):
    def test_load_returns_body_without_frontmatter(self) -> None:
        loader = ArticleSkillPromptLoader(_FakeSkillLibrary(markdown="---\nname: github-project-blog\ndescription: test\n---\n\n# 写作规则\n\n只根据证据写作。"))
        instructions = loader.load()
        self.assertNotIn("name: github-project-blog", instructions)
        self.assertIn("只根据证据写作", instructions)

    def test_load_rejects_missing_skill(self) -> None:
        with self.assertRaisesRegex(LookupError, "github-project-blog"):
            ArticleSkillPromptLoader(_FakeSkillLibrary(markdown=None)).load()

    def test_load_rejects_empty_body(self) -> None:
        with self.assertRaisesRegex(ValueError, "没有正文"):
            ArticleSkillPromptLoader(_FakeSkillLibrary(markdown="---\nname: github-project-blog\ndescription: test\n---\n")).load()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m unittest tests.test_article_skill_prompt_loader -v`

Expected: FAIL，提示 `src.services.article_skill_prompt_loader` 不存在。

- [ ] **Step 3: 实现最小加载器**

```python
class ArticleSkillPromptLoader:
    _frontmatter_pattern = re.compile(r"\A---\s*\r?\n.*?\r?\n---\s*(?:\r?\n)?", re.DOTALL)

    def __init__(self, skill_library: SkillLibraryService, *, max_characters: int = 32_000) -> None:
        self._skill_library = skill_library
        self._max_characters = max_characters

    def load(self, name: str = "github-project-blog") -> str:
        summary = next((item for item in self._skill_library.list_skills() if item.name == name), None)
        if summary is None:
            raise LookupError(f"缺少文章生成 Skill：{name}")
        instructions = self._frontmatter_pattern.sub("", self._skill_library.get_skill(summary.id).markdown, count=1).strip()
        if not instructions:
            raise ValueError(f"文章生成 Skill {name} 的 SKILL.md 没有正文")
        if len(instructions) > self._max_characters:
            raise ValueError(f"文章生成 Skill {name} 正文超过 {self._max_characters} 个字符")
        return instructions
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `.venv\Scripts\python.exe -m unittest tests.test_article_skill_prompt_loader -v`

Expected: 3 tests PASS。

- [ ] **Step 5: 提交加载器**

```powershell
git add src/services/article_skill_prompt_loader.py tests/test_article_skill_prompt_loader.py
git commit -m "feat: load article writing skill instructions"
```

### Task 2: 创建自包含 github-project-blog Skill

**Files:**
- Create: `.agents/skills/github-project-blog/SKILL.md`
- Create: `.agents/skills/github-project-blog/agents/openai.yaml`
- Create: `deploy/skill-seeds/github-project-blog/SKILL.md`
- Modify: `deploy/skill-seeds/catalog.manifest.json`

**Interfaces:**
- Consumes: 动态 Top N 周榜 JSON、`source_evidence`、管理员指令和重生成反馈
- Produces: 供 `SummaryTask` system message 挂载的完整写作规则；模型输出字段仍为 `title`、`digest`、`article_markdown`

- [ ] **Step 1: 使用初始化器创建项目 Skill 骨架**

Run:

```powershell
.venv\Scripts\python.exe <CODEX_SKILLS>\skill-creator\scripts\init_skill.py github-project-blog --path .agents\skills --interface 'display_name=GitHub 项目文章' --interface 'short_description=生成有技术深度的中文 GitHub 项目文章' --interface 'default_prompt=Use $github-project-blog to write an evidence-grounded Chinese article about the supplied GitHub rankings.'
```

Expected: 生成 `SKILL.md` 与 `agents/openai.yaml`，且不创建无用资源目录。

- [ ] **Step 2: 写入自包含 Skill 正文**

`SKILL.md` 必须包含：

```markdown
---
name: github-project-blog
description: 根据公开 GitHub 仓库证据生成中文技术教学文章。用于单仓库或动态 Top N 周榜文章，强调问题、机制、架构、模块、执行流程、可学习实现、运行方式、优缺点和适合人群；不用于没有仓库证据的泛主题写作。
license: MIT
metadata:
  source_influences: blog-post-writer, humanizer
---
```

正文定义以下不可省略规则：动态项目数量；以证据为边界；README 未说明时明确写依据有限；将 13 个分析问题映射到现有六个项目标签；用具体数字和机制替代形容词；开头两句提供真实信息差；结尾落到工程判断；删除营销词、假深刻句、机械三段式、戏剧化碎句、聊天式结尾和无根据反对意见；只输出当前业务 JSON，不调用其他 Skill。

- [ ] **Step 3: 校验 Skill 结构**

Run: `.venv\Scripts\python.exe <CODEX_SKILLS>\skill-creator\scripts\quick_validate.py .agents\skills\github-project-blog`

Expected: `Skill is valid!`

- [ ] **Step 4: 导出生产种子并刷新清单**

Run:

```powershell
.venv\Scripts\python.exe scripts\export_portable_skills.py --source-root .agents\skills --destination-root deploy\skill-seeds --overwrite
```

Expected: `deploy/skill-seeds/github-project-blog/SKILL.md` 与清单 SHA-256 已更新，不覆盖无关 Skill 内容。

- [ ] **Step 5: 验证可移植种子**

Run: `.venv\Scripts\python.exe scripts\verify_skill_portability.py`

Expected: 生产 Skill 种子验证通过。

- [ ] **Step 6: 提交 Skill 与生产种子**

```powershell
git add .agents/skills/github-project-blog deploy/skill-seeds/github-project-blog deploy/skill-seeds/catalog.manifest.json
git commit -m "feat: add github project article skill"
```

### Task 3: SummaryTask 挂载 Skill 并取消 500 字最低限制

**Files:**
- Modify: `src/tasks/summary_task.py`
- Modify: `scripts/verify_summary_depth_contract.py`

**Interfaces:**
- Consumes: `ArticleSkillPromptLoader.load() -> str`
- Produces: 原有 `GeneratedContentInput`、任务元数据和 `ContentBrief`；增加任务元数据 `article_skill="github-project-blog"`

- [ ] **Step 1: 先更新离线合同断言**

在 `scripts/verify_summary_depth_contract.py` 中：

```python
article_skill_instructions = "# GitHub 项目文章\n\n只根据输入证据写作，并解释技术机制。"
messages = task._build_messages(..., article_skill_instructions=article_skill_instructions)
assert "只根据输入证据写作" in messages[0].content
assert "不少于 500 个中文字符" not in messages[-1].content
assert "全文中文字符数不得超过" in messages[-1].content

short_article = build_article(rankings, pad_to_minimum=False)
project_sections = task._validate_article_depth(...)
assert all(task._count_chinese_characters(section) < 500 for section in project_sections.values())
```

保留缺少固定标签、项目标题和精确 stars/增长数字的失败用例。

- [ ] **Step 2: 运行离线验证并确认失败**

Run: `.venv\Scripts\python.exe scripts\verify_summary_depth_contract.py`

Expected: FAIL，原因是 `_build_messages` 尚不接受 Skill 指令，且旧 500 字合同仍生效。

- [ ] **Step 3: 接入加载器并替换写作准则**

在 `SummaryTask.execute()` 中构造：

```python
article_skill_instructions = ArticleSkillPromptLoader(
    SkillLibraryService(context.config),
).load()
```

将其传给 `_generate_normalized_content()`、`_build_messages()` 和 `_build_retry_messages()`。两个 system message 都通过 `_build_article_skill_section()` 挂载同一正文。删除 `_build_writing_playbook_prompt()` 及 user prompt 中重复的“写作方法”。

- [ ] **Step 4: 删除最低字数合同但保留最大值**

实施以下行为：

```python
def _article_chinese_character_maximum(self, project_count: int) -> int:
    return max(1, project_count) * self.max_project_section_chinese_characters + 900
```

删除 `min_project_section_chinese_characters`、每项目最小值判断和全文最小值判断；Prompt 只声明项目与全文最大值。保留每标签至少 45 个中文字符、每项目最多 800 个中文字符、固定章节、固定项目标题、真实数字和证据状态检查。

- [ ] **Step 5: 运行离线合同与加载器测试**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_article_skill_prompt_loader -v
.venv\Scripts\python.exe scripts\verify_summary_depth_contract.py
```

Expected: 全部通过，输出不再含 500 字最低合同。

- [ ] **Step 6: 提交 SummaryTask 改造**

```powershell
git add src/tasks/summary_task.py scripts/verify_summary_depth_contract.py
git commit -m "feat: drive article generation with project skill"
```

### Task 4: 文档同步与完整验证

**Files:**
- Modify: `docs/weekly_media_pipeline.md`
- Modify: `docs/summary_writing_skill_research.md`
- Modify: `docs/skill_library_module.md`

**Interfaces:**
- Consumes: 已实现的 Skill 加载和 SummaryTask 质量合同
- Produces: 设计目标、技术取舍、调用链、依赖、验证结果和后续证据边界的维护记录

- [ ] **Step 1: 更新模块文档**

写明：`SummaryTask` 仍单次生成动态 Top N；写作方法来自自包含 `github-project-blog`；该 Skill 只借鉴两个 MIT Skill，不构成运行时依赖；500 字最低限制已取消；当前技术深度仍受 README 摘录范围限制。

- [ ] **Step 2: 运行定向验证**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_article_skill_prompt_loader tests.test_career_skill_runtime -v
.venv\Scripts\python.exe scripts\verify_summary_depth_contract.py
.venv\Scripts\python.exe scripts\verify_skill_portability.py
```

Expected: 所有测试和脚本通过。

- [ ] **Step 3: 执行静态检查**

Run:

```powershell
.venv\Scripts\python.exe -m compileall -q src\services\article_skill_prompt_loader.py src\tasks\summary_task.py tests\test_article_skill_prompt_loader.py
git diff --check
```

Expected: 退出码 0；无空白错误。

- [ ] **Step 4: 提交文档与最终修正**

```powershell
git add docs/weekly_media_pipeline.md docs/summary_writing_skill_research.md docs/skill_library_module.md
git commit -m "docs: record article skill generation flow"
```

- [ ] **Step 5: 最终审计**

Run:

```powershell
git status --short
git log -5 --oneline
```

Expected: 只报告用户原有的无关修改；本任务文件均已提交，测试结果可复述。
