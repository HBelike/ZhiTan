# 线上笔试助手实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有求职助手中增加一个 PC 端线上笔试助手，可从浏览器当前题目页捕获题面，自动分析、生成代码、执行公开与生成测试，并由用户确认后归档到面经库。

**Architecture:** 复用现有 Manifest V3 扩展，以 `activeTab + scripting + captureVisibleTab + storage.session` 完成一次性捕获；Vue 独立路由通过现有页面桥接领取捕获。FastAPI 新模块负责稳定契约、模型结构化输出、Piston Provider、最多两轮修复和面经归档，不向题目网站写入或提交任何内容。

**Tech Stack:** Chrome Extension Manifest V3、Vue 3/Vite、FastAPI/Pydantic、现有 ModelGateway/OpenAI-compatible Client、Piston HTTP API、PostgreSQL 面经库、Node test、pytest。

**Spec:** `docs/superpowers/specs/2026-09-01-online-assessment-assistant-design.md`

## Global Constraints

- PC 端优先；本轮不做手机或平板专项适配。
- 页面路由固定为 `/career/online-assessment`，页面标题与操作标题使用中文。
- 扩展只在用户点击图标或页面点击“重新识别/补充题面”时捕获，不持续监听题目页。
- 不申请 `<all_urls>`，使用 `activeTab` 临时授权；不自动填入代码、不自动点击运行、不自动提交。
- DOM 文本最多 25,000 字符；截图 PNG 最大 5 MiB，长边在进入后端前缩放至不超过 1,920 像素。
- 支持 Python、JavaScript、Java、C++；同时支持函数题和标准输入题。
- 一次最多执行 20 个测试；公开样例与 AI 生成测试必须明确区分。
- Piston 编译 10 秒、运行 3 秒；编译内存 512 MiB、运行内存 256 MiB；stdout/stderr 各截断到 64 KiB。
- 自动修复最多两轮；`run_version` 变化后丢弃旧结果。
- 截图、原始页面、模型草稿与完整运行日志不写入面经库。
- 只做本地修改和验证，不部署生产环境。

---

### Task 1: 领域契约、配置和纯函数

**Files:**
- Create: `src/career_assistant/online_assessment/__init__.py`
- Create: `src/career_assistant/online_assessment/contracts.py`
- Create: `src/career_assistant/online_assessment/problem_extractor.py`
- Modify: `src/career_assistant/settings.py`
- Modify: `config/career_assistant.yaml`
- Test: `tests/test_online_assessment_contracts.py`

**Interfaces:**
- Consumes: 捕获端提交的 URL、页面标题、DOM 文本、代码、语言与公开样例。
- Produces: `AssessmentProblem`、`AssessmentSolution`、`AssessmentTestCase`、`AssessmentExecutionReport`、`OnlineAssessmentSettings`、`normalize_capture()`。

- [ ] **Step 1: 写失败测试**

```python
def test_capture_normalization_caps_text_and_blocks_low_confidence():
    problem = normalize_capture(CapturedProblemInput(page_text="x" * 30_000))
    assert len(problem.statement) == 25_000
    assert problem.needs_confirmation is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_online_assessment_contracts.py -q`
Expected: FAIL，因为模块尚不存在。

- [ ] **Step 3: 实现不可变契约、校验与配置读取**

```python
@dataclass(frozen=True)
class AssessmentProblem:
    title: str
    statement: str
    language: ProgrammingLanguage
    interface_mode: InterfaceMode
    public_tests: tuple[AssessmentTestCase, ...]
    confidence: float
    needs_confirmation: bool
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_online_assessment_contracts.py -q`
Expected: PASS。

- [ ] **Step 5: 提交独立变更**

```bash
git add src/career_assistant/online_assessment src/career_assistant/settings.py config/career_assistant.yaml tests/test_online_assessment_contracts.py
git commit -m "feat: add online assessment contracts"
```

### Task 2: Piston 执行 Provider 与报告归一化

**Files:**
- Create: `src/career_assistant/online_assessment/execution.py`
- Test: `tests/test_online_assessment_execution.py`
- Modify: `docker-compose.career-assistant.yml`

**Interfaces:**
- Consumes: `AssessmentSolution` 与最多 20 个 `AssessmentTestCase`。
- Produces: `ExecutionProvider.execute(solution, tests) -> AssessmentExecutionReport`，默认实现 `PistonExecutionProvider`。

- [ ] **Step 1: 写失败测试覆盖语言映射、超限拒绝和输出截断**

```python
def test_piston_maps_python_and_marks_sample_result(fake_http):
    report = provider.execute(solution, (sample_case,))
    assert fake_http.last_json["language"] == "python"
    assert report.tests[0].passed is True
    assert report.tests[0].origin == "public"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_online_assessment_execution.py -q`
Expected: FAIL，因为执行 Provider 尚不存在。

- [ ] **Step 3: 实现 Provider、请求限制与安全错误**

```python
class PistonExecutionProvider:
    def execute(self, solution, tests):
        if len(tests) > 20:
            raise ValueError("一次最多执行 20 个测试")
        return self._execute_each_case(solution, tests)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_online_assessment_execution.py -q`
Expected: PASS。

- [ ] **Step 5: 提交独立变更**

```bash
git add src/career_assistant/online_assessment/execution.py tests/test_online_assessment_execution.py docker-compose.career-assistant.yml
git commit -m "feat: add isolated assessment execution provider"
```

### Task 3: 模型分析、生成测试和修复编排

**Files:**
- Create: `src/career_assistant/online_assessment/model_output.py`
- Create: `src/career_assistant/online_assessment/solution_service.py`
- Create: `src/career_assistant/online_assessment/test_generator.py`
- Create: `src/career_assistant/online_assessment/repair_service.py`
- Test: `tests/test_online_assessment_services.py`

**Interfaces:**
- Consumes: `ModelGateway`、`OpenAICompatibleChatClient`、`OnlineAssessmentSettings`、`AssessmentProblem`。
- Produces: `OnlineAssessmentService.analyze()`、`solve()`、`execute_and_repair()`；所有 JSON 都经 Pydantic 契约验证，模型说明不能改变用户选择的语言。

- [ ] **Step 1: 写失败测试覆盖低置信度闸门、JSON 去围栏、测试来源与两轮上限**

```python
def test_low_confidence_stops_before_solution(model_client):
    result = service.solve(low_confidence_problem, run_version=3)
    assert result.status == "needs_confirmation"
    assert model_client.calls == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_online_assessment_services.py -q`
Expected: FAIL，因为服务尚不存在。

- [ ] **Step 3: 实现确定 Prompt、模型档案解析和最多两轮修复**

```python
for repair_round in range(settings.max_repair_rounds + 1):
    report = execution_provider.execute(solution, tests)
    if report.passed or repair_round == settings.max_repair_rounds:
        return solution, report
    solution = repair_service.repair(problem, solution, report)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_online_assessment_services.py -q`
Expected: PASS。

- [ ] **Step 5: 提交独立变更**

```bash
git add src/career_assistant/online_assessment tests/test_online_assessment_services.py
git commit -m "feat: generate and repair assessment solutions"
```

### Task 4: FastAPI 接口与面经归档

**Files:**
- Create: `src/career_assistant/online_assessment/archive.py`
- Create: `src/career_assistant/online_assessment/web.py`
- Modify: `src/career_assistant/web/router.py`
- Modify: `src/career_assistant/interview_library/models.py`
- Create: `migrations/versions/20260901_33_online_assessment_source.py`
- Test: `tests/test_online_assessment_web.py`
- Test: `tests/test_online_assessment_archive.py`

**Interfaces:**
- Consumes: 领域服务、当前 `CareerRequestActor`、`InterviewLibraryService`。
- Produces: `POST /api/career/online-assessments/analyze`、`solve/stream`、`execute`、`archive/preview`、`archive`。

- [ ] **Step 1: 写失败测试覆盖接口状态、NDJSON、旧 run_version 丢弃语义和归档 Markdown**

```python
def test_archive_uses_online_assessment_source(fake_library):
    response = client.post("/api/career/online-assessments/archive", json=payload)
    assert response.status_code == 201
    assert fake_library.last_draft.source_type.value == "online_assessment"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_online_assessment_web.py tests/test_online_assessment_archive.py -q`
Expected: FAIL，因为 Router 与来源枚举尚不存在。

- [ ] **Step 3: 实现接口、错误映射、归档预览和来源迁移**

```python
draft = InterviewExperienceDraft(
    company_name=request.company_name,
    role_name=request.role_name,
    interview_date=request.assessment_date,
    markdown_content=archive_markdown,
    source_type=InterviewSourceType.ONLINE_ASSESSMENT,
    tags=("线上笔试", problem.language.value),
)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_online_assessment_web.py tests/test_online_assessment_archive.py -q`
Expected: PASS。

- [ ] **Step 5: 提交独立变更**

```bash
git add src/career_assistant/online_assessment src/career_assistant/web/router.py src/career_assistant/interview_library/models.py migrations/versions/20260901_33_online_assessment_source.py tests/test_online_assessment_web.py tests/test_online_assessment_archive.py
git commit -m "feat: expose online assessment api and archive"
```

### Task 5: 浏览器题面提取器与一次性领取协议

**Files:**
- Create: `browser-extension/job-library/assessment-capture.js`
- Create: `browser-extension/job-library/tests/assessment-capture.test.mjs`
- Modify: `browser-extension/job-library/manifest.json`
- Modify: `browser-extension/job-library/content-script.js`
- Modify: `browser-extension/job-library/service-worker.js`
- Modify: `browser-extension/job-library/README.md`

**Interfaces:**
- Consumes: 当前激活标签页和用户点击动作。
- Produces: `online_assessment_capture_v1`、`claim_assessment_launch`、`refresh_assessment_problem`、`append_assessment_problem`。

- [ ] **Step 1: 写失败测试覆盖 LeetCode、HackerRank、通用选择器和可见视口回退**

```javascript
test('generic extraction skips hidden content and caps text', () => {
  const result = normalizeAssessmentCapture(rawCapture)
  assert.equal(result.pageText.length, 25000)
  assert.equal(result.captureMode, 'generic')
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm --prefix browser-extension/job-library test`
Expected: FAIL，因为提取模块尚不存在。

- [ ] **Step 3: 实现适配器、截图、128-bit launch_id、5 分钟 TTL 与领取后删除**

```javascript
const launchId = crypto.randomUUID()
await chrome.storage.session.set({ [`assessment:${launchId}`]: capture })
await chrome.windows.create({ url: `${appOrigin}/career/online-assessment?launch_id=${launchId}`, type: 'popup' })
```

- [ ] **Step 4: 运行测试确认通过并检查 Manifest 权限**

Run: `npm --prefix browser-extension/job-library test`
Expected: PASS；Manifest 只增加 `activeTab` 与 `storage`，没有 `<all_urls>`。

- [ ] **Step 5: 提交独立变更**

```bash
git add browser-extension/job-library
git commit -m "feat: capture online assessment problems from active tab"
```

### Task 6: 前端桥接、状态机与独立 PC 页面

**Files:**
- Create: `web-ui/src/online-assessment/bridge.js`
- Create: `web-ui/src/online-assessment/state.js`
- Create: `web-ui/src/online-assessment/api.js`
- Create: `web-ui/src/components/OnlineAssessmentAssistantPage.vue`
- Create: `web-ui/src/online-assessment-bridge.test.js`
- Create: `web-ui/src/online-assessment-state.test.js`
- Create: `web-ui/src/online-assessment-view.test.js`
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/components/CareerAssistantPage.vue`

**Interfaces:**
- Consumes: 扩展桥接动作与 Task 4 API。
- Produces: 独立窗口页面、自动流水线、重新识别、补充题面、代码编辑/复制/重测和归档确认。

- [ ] **Step 1: 写失败测试覆盖路由、状态迁移、run_version、sessionStorage 和桥接错误文案**

```javascript
test('stale solution is ignored after recapture', () => {
  const state = createAssessmentState()
  const first = state.beginRun()
  state.beginRun()
  assert.equal(state.accept(first, { code: 'old' }), false)
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm --prefix web-ui test -- --test-name-pattern="online assessment"`
Expected: FAIL，因为页面与状态模块尚不存在。

- [ ] **Step 3: 实现清晰的三栏 PC 工作台与无扩展/低置信度/执行器未就绪状态**

```vue
<main class="assessment-shell">
  <ProblemPanel />
  <SolutionEditor />
  <TestReportPanel />
</main>
```

- [ ] **Step 4: 运行测试和生产构建**

Run: `npm --prefix web-ui test`
Expected: PASS。

Run: `npm --prefix web-ui run build`
Expected: Vite build 成功。

- [ ] **Step 5: 提交独立变更**

```bash
git add web-ui/src
git commit -m "feat: add online assessment assistant workspace"
```

### Task 7: 联调、许可证与模块文档

**Files:**
- Create: `docs/modules/online-assessment-assistant.md`
- Create: `docs/third-party/online-assessment-open-source-notices.md`
- Modify: `docs/career_assistant_module.md`
- Modify: `browser-extension/job-library/README.md`

**Interfaces:**
- Consumes: Tasks 1–6 的稳定接口。
- Produces: 可复现实测步骤、开源借鉴边界、调用链与已知限制。

- [ ] **Step 1: 运行后端定向测试**

Run: `python -m pytest tests/test_online_assessment_contracts.py tests/test_online_assessment_execution.py tests/test_online_assessment_services.py tests/test_online_assessment_web.py tests/test_online_assessment_archive.py -q`
Expected: PASS。

- [ ] **Step 2: 运行扩展和前端测试**

Run: `npm --prefix browser-extension/job-library test`
Expected: PASS。

Run: `npm --prefix web-ui test && npm --prefix web-ui run build`
Expected: PASS。

- [ ] **Step 3: 运行静态检查并验证未引入自动提交**

Run: `rg -n "submit|fill|executeScript" browser-extension/job-library/assessment-capture.js web-ui/src/online-assessment web-ui/src/components/OnlineAssessmentAssistantPage.vue`
Expected: 仅出现本产品表单提交或题面只读提取，不出现对第三方题目站点写入/点击代码。

- [ ] **Step 4: 更新模块文档与第三方声明**

```markdown
- LeetCode-AI-Assistant（MIT）：借鉴平台题面、语言和公开测试提取思路。
- Open Interview Assistant（Apache-2.0）：借鉴选择器回退与 activeTab 连接思路。
- LeetBuddy（MIT）：借鉴题面分析和边界测试交互思路。
- Piston（MIT）：作为隔离代码执行 API；不把其源码复制进当前仓库。
```

- [ ] **Step 5: 提交文档和最终验证记录**

```bash
git add docs/modules/online-assessment-assistant.md docs/third-party/online-assessment-open-source-notices.md docs/career_assistant_module.md browser-extension/job-library/README.md
git commit -m "docs: document online assessment assistant"
```
