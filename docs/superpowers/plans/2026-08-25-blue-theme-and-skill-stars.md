# 蓝白主题与 Skill Star 链路修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清除面经库与技能库在蓝白主题下残留的绿色，移除重复导入入口，并让可关联 GitHub 仓库的 Skill 自动产生 Star 快照。

**Architecture:** UI 继续通过 `theme.css` 的 `html[data-ui-theme="blue"] body` 覆盖 Teleport 浮层，不改变浅绿主题；面经库只删除重复按钮，不改变现有导入流程。Star 数据层优先读取 SKILL.md，再读取安装锁文件中的 GitHub 来源；生产 Scheduler 启动时和每周内容任务中复用现有刷新方法，并挂载同一 Skill 持久卷。

**Tech Stack:** Vue 3、Vite、CSS theme tokens、FastAPI/Python、Docker Compose、Node test、unittest/pytest

**Spec:** `docs/ui_theme_system.md`、`docs/skill_library_module.md`、用户在 2026-08-25 确认的四项修改

## Global Constraints

- 始终使用中文页面标题和操作文案，不新增装饰性英文眉题。
- 仅完成 PC 端现有页面修复，不做平板专项适配。
- 只在本地修改、测试和构建，不部署生产。
- 警告与失败状态继续保留黄色和红色语义色。
- 不覆盖或回退工作区内与本任务无关的用户修改。

---

### Task 1: 面经导入入口与 Teleport 弹窗主题

**Files:**
- Modify: `web-ui/src/components/InterviewLibraryPage.vue`
- Modify: `web-ui/src/theme.css`
- Modify: `web-ui/src/interview-library-theme.test.js`
- Modify: `docs/ui_theme_system.md`

**Interfaces:**
- Consumes: 现有 `openImport('file')` 导入流程和 `--ui-*` 主题 token。
- Produces: 仅保留空态“导入第一份材料”入口；文件上传、解析进度及完成态全部使用蓝白 token。

- [ ] **Step 1: 扩展静态回归测试**

  在 `interview-library-theme.test.js` 中断言 `.import-backdrop`、`.file-intake-intro`、`.file-picker`、`.file-import-strategy`、`.recognition-card`、`.file-parse-progress-card` 等 Teleport 选择器使用蓝色 token，并断言右上角不存在文本为“导入材料”的按钮。

- [ ] **Step 2: 运行测试确认新断言失败**

  Run: `node --test src/interview-library-theme.test.js`
  Expected: FAIL，提示文件导入主题块或重复按钮断言尚未满足。

- [ ] **Step 3: 实现最小 UI 修复**

  删除页头 `@click="openImport('file')"` 的“导入材料”按钮；在 `theme.css` 增加 `html[data-ui-theme="blue"] body .import-*` 与 `.file-*` 覆盖，表单、单选、进度条和成功态使用 `--ui-accent`、`--ui-surface-active`、`--ui-line`。

- [ ] **Step 4: 运行聚焦测试**

  Run: `node --test src/interview-library-theme.test.js`
  Expected: PASS。

### Task 2: Skill Star 浮层蓝白主题与本地 Skill 表达

**Files:**
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/theme.css`
- Create: `web-ui/src/skill-star-theme.test.js`

**Interfaces:**
- Consumes: `SkillSummary.repository_full_name` 和现有 Star popover。
- Produces: 蓝白 Star 圆环/浮层；无 GitHub 仓库的 Skill 显示“本地 Skill”且不展示空 Star 统计入口。

- [ ] **Step 1: 新增失败测试**

  测试 `theme.css` 覆盖 `.star-stat-trigger`、`.star-ring`、`.skill-star-popover`、标题、圆环和注释区域；测试 `App.vue` 仅在 `repository_full_name` 存在时渲染 Star 行与触发器。

- [ ] **Step 2: 运行测试确认失败**

  Run: `node --test src/skill-star-theme.test.js`
  Expected: FAIL。

- [ ] **Step 3: 实现主题与条件展示**

  在 `App.vue` 对 Star 行、按钮增加 `v-if="skill.repository_full_name"`，否则显示“项目本地 Skill”；在 `theme.css` 使用蓝色 token 覆盖 Teleport 浮层及小圆环。

- [ ] **Step 4: 运行聚焦测试**

  Run: `node --test src/skill-star-theme.test.js`
  Expected: PASS。

### Task 3: 仓库来源识别与自动 Star 刷新

**Files:**
- Modify: `src/services/skill_library_service.py`
- Modify: `src/app/application.py`
- Modify: `docker-compose.production.yml`
- Create: `tests/test_skill_star_refresh.py`

**Interfaces:**
- Consumes: `~/.agents/.skill-lock.json` 的 `skills.<name>.source/sourceUrl/sourceType`，以及现有 `refresh_stale_star_snapshots() -> dict[str, int]`。
- Produces: `_repository_full_name_for_skill(name, homepage_url, path_hint, markdown) -> str | None`；Scheduler 启动和每周任务自动刷新过期快照。

- [ ] **Step 1: 新增服务层失败测试**

  临时创建 `.agents/.skill-lock.json` 和无 repository frontmatter 的 SKILL.md，断言 `list_skills()` 能解析 `owner/repo`；模拟 `_fetch_repository_stars`，断言首次刷新写入 Star，第二次七天内不重复请求。

- [ ] **Step 2: 新增 Scheduler/Compose 失败测试**

  静态断言 Scheduler 调用 Skill Star 刷新，并断言 `pipeline-scheduler` 挂载 `application_skills:/app/.agents`。

- [ ] **Step 3: 运行聚焦后端测试确认失败**

  Run: `python -m pytest tests/test_skill_star_refresh.py -q`
  Expected: FAIL。

- [ ] **Step 4: 实现来源回退和 Scheduler 接线**

  `SkillLibraryService` 缓存读取 `.agents/.skill-lock.json`，仅接受 `sourceType=github` 或可解析的 GitHub URL/owner-repo；`Application` 在 Scheduler 进入循环前和每周内容任务中调用现有刷新方法并记录摘要；Compose 为 Scheduler 增加 Skill 卷。

- [ ] **Step 5: 运行聚焦后端测试**

  Run: `python -m pytest tests/test_skill_star_refresh.py -q`
  Expected: PASS。

### Task 4: 文档与整体回归

**Files:**
- Modify: `docs/skill_library_module.md`
- Modify: `docs/ui_theme_system.md`

**Interfaces:**
- Consumes: Tasks 1-3 的最终行为。
- Produces: 记录主题覆盖、仓库识别优先级、七天快照、Scheduler 调用链和无仓库 Skill 的展示边界。

- [ ] **Step 1: 更新模块文档**

  补充 Star 数据源为 SKILL.md → `.skill-lock.json`，生产由 Scheduler 启动/每周刷新，列表请求只读缓存；本地 Skill 不显示伪 Star。

- [ ] **Step 2: 运行前端测试与构建**

  Run: `npm test`
  Expected: 新增主题测试通过；若存在无关既有失败，单独记录。

  Run: `npm run build`
  Expected: PASS。

- [ ] **Step 3: 运行后端聚焦测试**

  Run: `python -m pytest tests/test_skill_star_refresh.py tests/test_career_skill_tools.py -q`
  Expected: PASS。

- [ ] **Step 4: 检查差异边界**

  Run: `git diff --check`
  Expected: 本任务文件无空白错误，且未回退任何无关修改。
