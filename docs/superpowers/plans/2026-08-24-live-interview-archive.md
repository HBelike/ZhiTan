# 实时面试归档到面经库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在浏览器面试结束后提供可选归档弹窗，把本场全部会话中的问题确定性合并到现有面经库。

**Architecture:** 前端在停止声音与实时连接后提交本场 `session_ids` 和手填元数据。新增归档服务在后端校验归属、聚合问题、生成或合并 Markdown，再复用现有 `InterviewLibraryService` 完成持久化、切片与索引。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy Core、PostgreSQL、Vue 3、原生 `fetch`、Node test、pytest。

**Spec:** `docs/superpowers/specs/2026-08-24-live-interview-archive-design.md`

## Global Constraints

- 只归档面试问题，不保存模型答案、候选人转写或原始音频。
- 公司和职位每次手填；日期默认首次会话开始日期。
- 点击结束必须先停止采集，归档弹窗不能阻塞结束动作。
- 同公司、职位和日期执行追加合并与确定性去重，不调用 LLM。
- “本次不保存”不二次确认且不提供后续归档入口。
- 只做 PC 端和本地验证，不部署生产。

---

### Task 1: 归档领域服务与问题文档生成

**Files:**
- Create: `src/career_assistant/live_interview/archive.py`
- Modify: `src/career_assistant/live_interview/persistence.py`
- Modify: `src/career_assistant/interview_library/repository.py`
- Test: `tests/test_live_interview_archive.py`

**Interfaces:**
- Consumes: `LiveInterviewRepository.history(...)`、`InterviewLibraryRepository`、`InterviewLibraryService.ingest(...)` 与 `update_markdown(...)`。
- Produces: `LiveInterviewArchiveService.preview(...)` 和 `archive(...)`，以及 `LiveInterviewArchiveResult`。

- [ ] **Step 1: 编写问题聚合、时间范围、Markdown 生成和去重的失败测试**

覆盖多个 session、取消/失败回答、重复问题、零问题、无权会话和已有面经合并。

- [ ] **Step 2: 运行归档单测并确认失败**

Run: `pytest tests/test_live_interview_archive.py -q`

Expected: FAIL，归档模块与接口尚不存在。

- [ ] **Step 3: 实现最小归档领域逻辑**

实现不可变请求/结果数据类型、规范化去重键、已有问题解析、标准 Markdown 生成和多会话聚合；所有会话必须同时匹配 `organization_id` 与 `actor_id`。

- [ ] **Step 4: 增加按公司与面经名称读取节点的仓储方法**

新增 `get_experience_by_company_and_job_name(organization_id, company_name, job_name)`，使用公司规范化名称和完整 `job_name` 查找，供合并路径读取旧正文。

- [ ] **Step 5: 运行归档单测并确认通过**

Run: `pytest tests/test_live_interview_archive.py -q`

Expected: PASS。

### Task 2: 归档 REST 契约

**Files:**
- Modify: `src/career_assistant/live_interview/web.py`
- Modify: `src/career_assistant/web/router.py`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Consumes: `LiveInterviewArchiveService.archive(...)`。
- Produces: `POST /api/career/live-interviews/archive`，返回 `experience`、`question_count` 和前 5 个 `question_preview`。

- [ ] **Step 1: 编写归档端点失败测试**

验证合法请求、字段校验、无权会话、零问题与服务错误映射，不在响应中泄露答案或音频字段。

- [ ] **Step 2: 运行端点测试并确认失败**

Run: `pytest tests/test_live_interview_web.py -q`

Expected: FAIL，`/archive` 尚不存在。

- [ ] **Step 3: 实现 Pydantic 请求和端点依赖装配**

请求包含 `session_ids: list[UUID]`、`company_name`、`role_name`、`interview_date`；从完整求职服务容器取得面经服务与仓储，从请求数据库创建实时会话仓储。

- [ ] **Step 4: 映射明确错误与成功响应**

`LookupError` 返回 404，空问题和字段业务校验返回 422，短暂未结束状态返回 409；成功返回可跳转的面经 ID。

- [ ] **Step 5: 运行后端相关测试**

Run: `pytest tests/test_live_interview_archive.py tests/test_live_interview_web.py -q`

Expected: PASS。

### Task 3: 弹窗状态辅助函数

**Files:**
- Modify: `web-ui/src/browser-live-interview/view.js`
- Modify: `web-ui/src/browser-live-interview-view.test.js`

**Interfaces:**
- Produces: `buildArchivePreview(history, limit)`、`defaultInterviewDate(startedAt)` 和归档状态所需的纯函数。

- [ ] **Step 1: 编写前端纯函数失败测试**

覆盖只取问题、最多 5 条预览、日期本地格式化、剩余数量与零问题状态。

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test web-ui/src/browser-live-interview-view.test.js`

Expected: FAIL，新函数尚未导出。

- [ ] **Step 3: 实现纯函数并运行测试**

Run: `node --test web-ui/src/browser-live-interview-view.test.js`

Expected: PASS。

### Task 4: 结束面试归档弹窗

**Files:**
- Modify: `web-ui/src/components/BrowserInterviewMasterPage.vue`
- Modify: `web-ui/src/browser-live-interview-view.test.js`

**Interfaces:**
- Consumes: `sessionIds`、`completedHistory`、首次开始时间和 `POST /api/career/live-interviews/archive`。
- Produces: 结束后的归档弹窗、保存状态、失败重试、成功跳转与不保存关闭行为。

- [ ] **Step 1: 增加页面静态合同测试**

断言页面包含“保存本次面经？”、“公司名称”、“面试职位”、“面试日期”、“本次不保存”、“保存到面经库”，且结束函数先停止采集再打开弹窗。

- [ ] **Step 2: 运行前端测试并确认失败**

Run: `npm --prefix web-ui test -- --runInBand`

Expected: FAIL，弹窗尚不存在。

- [ ] **Step 3: 实现弹窗状态与提交逻辑**

结束时保存首次会话时间，停止媒体资源后打开弹窗；提交全部 `sessionIds`。保存校验只约束主操作，关闭和“不保存”始终可用。

- [ ] **Step 4: 实现视觉、可访问性与状态反馈**

使用现有蓝色变量与 640px PC 弹窗；字段使用显式 label；关闭按钮具备可访问名称；保存中禁用重复提交；错误保留表单；成功提供查看面经入口。

- [ ] **Step 5: 运行前端单测**

Run: `npm --prefix web-ui test -- --runInBand`

Expected: PASS。

### Task 5: 文档同步与本地验收

**Files:**
- Modify: `docs/windows-live-interview-assistant.md`
- Modify: `docs/interview_library_rag_module.md`

**Interfaces:**
- Consumes: 已完成的端点、归档逻辑与弹窗行为。
- Produces: 模块目标、调用链、依赖、验证结果和边界说明。

- [ ] **Step 1: 更新调用链与数据边界文档**

记录结束面试、聚合多会话、问题归并、面经重索引、无 LLM 与不保存行为。

- [ ] **Step 2: 运行后端测试集合**

Run: `pytest tests/test_live_interview_archive.py tests/test_live_interview_web.py tests/test_live_interview_services.py tests/test_live_interview_core.py -q`

Expected: PASS。

- [ ] **Step 3: 运行前端测试与生产构建**

Run: `npm --prefix web-ui test -- --runInBand`

Run: `npm --prefix web-ui run build`

Expected: tests PASS，Vite build PASS。

- [ ] **Step 4: 检查未提交差异只包含本功能文件**

Run: `git status --short`

Expected: 保留用户原有无关改动，不纳入本功能提交。

- [ ] **Step 5: 创建本地功能提交**

仅暂存本计划列出的文件，提交信息使用：`feat: archive live interview questions`
