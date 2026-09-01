# Career LLM Job Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the career assistant's technology-specific lexical score cards with an asynchronous, evidence-bound LLM Judge that creates job-specific dimensions and falls back to the existing lexical algorithm after at most three failed attempts.

**Architecture:** A new assessment service numbers JD and resume paragraphs, asks the administrator-selected model for one structured tool call, validates every source reference, and computes percentages server-side. One PostgreSQL table stores version-keyed status and validated results; the conversation context API reads that record while a FastAPI background task performs analysis.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy Core, PostgreSQL JSONB, OpenAI-compatible Chat Completions, Vue 3, Node test runner, Vite.

**Spec:** `docs/superpowers/specs/2026-08-21-career-llm-job-assessment-design.md`

**Implementation result (2026-08-21):** Completed inline because the optional executing-plans/subagent skills were unavailable and the user requested immediate implementation. The final implementation accepts a configured text-only profile through JSON Object mode, stores validated section text directly in `job_sections[].items`, and reschedules any still-queued record on conversation reads. Verification used Python `unittest` because this workspace environment does not include `pytest`.

## Global Constraints

- The Judge model is selected by administrator-owned `profile_key`; it never follows the chat model selector.
- One structured model call performs decomposition and evidence judging; do not introduce a second Judge stage.
- Retry retryable transport/model/schema failures at most three total attempts, then run `lexical-evidence-v2`.
- Model-produced aggregate scores are ignored; percentages are recomputed from validated item verdicts.
- Only exact `JD-nnn` and `CV-nnn` references supplied by the server may enter a stored result.
- Do not introduce a queue, cache server, embedding service, or new LLM framework.
- Preserve all unrelated dirty-worktree changes and implement PC behavior before any mobile-specific work.

---

### Task 1: Persistence and configuration contract

**Files:**
- Create: `migrations/versions/20260821_16_job_match_assessments.py`
- Create: `src/career_assistant/persistence/job_assessment_repository.py`
- Modify: `src/career_assistant/persistence/__init__.py`
- Modify: `src/career_assistant/settings.py`
- Modify: `config/career_assistant.yaml`
- Test: `tests/test_career_job_assessment_repository.py`
- Test: `tests/test_career_settings.py`

**Interfaces:**
- Produces: `JobAssessmentStatus`, `JobAssessmentRecord`, and `CareerJobAssessmentRepository`.
- Produces: `JobAssessmentSettings(judge_profile_key: str, prompt_version: str, max_attempts: int)` and `load_job_assessment_settings()`.

- [ ] **Step 1: Write failing repository and settings tests**

```python
def test_job_assessment_settings_select_fixed_profile() -> None:
    settings = load_job_assessment_settings(TEST_CONFIG_PATH)
    assert settings.judge_profile_key == "deepseek-v4-flash"
    assert settings.prompt_version == "career-job-judge-v1"
    assert settings.max_attempts == 3

def test_create_or_get_queued_reuses_version_key(repository, profiles) -> None:
    first, created = repository.create_or_get_queued(**profiles)
    second, created_again = repository.create_or_get_queued(**profiles)
    assert created is True
    assert created_again is False
    assert first.id == second.id
```

- [ ] **Step 2: Run the focused tests and verify missing imports/schema fail**

Run: `pytest tests/test_career_job_assessment_repository.py tests/test_career_settings.py -q`

Expected: FAIL because the repository, migration-backed table, and settings loader do not exist.

- [ ] **Step 3: Add the migration and repository state transitions**

Create `career_assistant.job_match_assessments` with the columns and unique key from the spec. Implement these exact methods:

```python
def create_or_get_queued(self, *, organization_id: UUID, actor_id: UUID,
                         candidate_profile_id: UUID, target_role_profile_id: UUID,
                         judge_model_profile_id: UUID, judge_provider_key: str,
                         judge_model_id: str, prompt_version: str) -> tuple[JobAssessmentRecord, bool]: ...
def get_current(self, actor_id: UUID, candidate_profile_id: UUID,
                target_role_profile_id: UUID) -> JobAssessmentRecord | None: ...
def claim(self, assessment_id: UUID) -> JobAssessmentRecord | None: ...
def save_ready(self, assessment_id: UUID, *, result: dict[str, object],
               attempt_count: int, fallback: bool = False) -> JobAssessmentRecord: ...
def save_failed(self, assessment_id: UUID, *, error_code: str,
                attempt_count: int) -> JobAssessmentRecord: ...
def reset_for_retry(self, assessment_id: UUID) -> JobAssessmentRecord: ...
```

`claim()` must atomically update only `queued` rows to `analyzing`; this prevents duplicate background tasks from calling the Judge concurrently.

- [ ] **Step 4: Add validated assessment settings**

Add the following YAML and reject empty profile/prompt values or `max_attempts` outside `1..3`:

```yaml
job_assessment:
  judge_profile_key: deepseek-v4-flash
  prompt_version: career-job-judge-v1
  max_attempts: 3
```

- [ ] **Step 5: Run persistence and settings tests**

Run: `pytest tests/test_career_job_assessment_repository.py tests/test_career_settings.py -q`

Expected: PASS.

### Task 2: Evidence-bound Judge domain service

**Files:**
- Create: `src/career_assistant/job_assessment.py`
- Modify: `src/career_assistant/model_clients.py`
- Test: `tests/test_career_job_assessment.py`
- Test: `tests/test_career_model_clients.py`

**Interfaces:**
- Consumes: `OpenAICompatibleChatClient`, `ModelGateway`, `CareerModelProfileRepository`, `CareerJobAssessmentRepository`, and `JobAssessmentSettings`.
- Produces: `CareerJobAssessmentService.enqueue_context()`, `run_assessment()`, and `retry_context()`.
- Produces validated result payload keys `algorithm_version`, `status`, `dimensions`, `items`, `job_sections`, and `disclaimer`; cited source text is materialized only after server-side ID validation.

- [ ] **Step 1: Write failing source-numbering, validation, scoring, retry, and fallback tests**

```python
def test_supported_item_requires_real_cv_reference() -> None:
    raw = judge_payload(verdict="supported", cv_source_ids=["CV-999"])
    with pytest.raises(JobAssessmentValidationError):
        validate_and_score(raw, jd_lines={"JD-001": "需要会唱歌"}, cv_lines={"CV-001": "校园主持"})

def test_scores_are_recomputed_from_items() -> None:
    result = validate_and_score(mixed_payload(), jd_lines=JD_LINES, cv_lines=CV_LINES)
    assert result["dimensions"][0]["score"] == 75.0

def test_three_invalid_outputs_then_legacy_fallback() -> None:
    service = service_with_model_outputs(["{}", "{}", "{}"])
    result = service.run_assessment(ASSESSMENT_ID)
    assert result.status is JobAssessmentStatus.FALLBACK_READY
    assert service.model_call_count == 3
```

- [ ] **Step 2: Run domain tests and verify they fail**

Run: `pytest tests/test_career_job_assessment.py tests/test_career_model_clients.py -q`

Expected: FAIL because the Judge service and JSON completion path are missing.

- [ ] **Step 3: Implement paragraph numbering and the one-call schema**

Expose:

```python
def number_source_lines(text: str, prefix: str) -> dict[str, str]: ...
def build_judge_tool() -> FunctionToolDefinition: ...
def validate_and_score(raw: dict[str, object], *, jd_lines: dict[str, str],
                       cv_lines: dict[str, str]) -> dict[str, object]: ...
```

The tool schema must constrain section categories, `requirement_type`, `verdict`, reference arrays, reason length, and 2-5 dynamic dimensions. Validation must reject unknown references, duplicate IDs, missing dimensions, orphaned items, and matched verdicts without CV evidence.

- [ ] **Step 4: Implement the service and retry classification**

Resolve the configured `profile_key` from the organization model list, require text plus tools when Tool Calling is declared and otherwise require text, then use the stored credential returned by `ModelGateway`. Call the forced `submit_job_assessment` tool when tools are supported; otherwise call a new `complete_json()` client method using `response_format={"type": "json_object"}`. Retry only retryable model failures and invalid JSON/Schema outputs up to `settings.max_attempts`. Non-retryable configuration errors enter legacy fallback immediately.

Legacy fallback is accepted only when at least one requirement exists and two existing dimensions have `status == "ready"`; otherwise save `failed` with a stable error code.

- [ ] **Step 5: Run domain and client tests**

Run: `pytest tests/test_career_job_assessment.py tests/test_career_model_clients.py -q`

Expected: PASS.

### Task 3: API orchestration and background execution

**Files:**
- Modify: `src/career_assistant/web/router.py`
- Modify: `src/app/application.py`
- Test: `tests/test_career_job_assessment_api.py`
- Test: `tests/test_career_context_profiles_v2.py`

**Interfaces:**
- Consumes: `CareerJobAssessmentService` and `CareerJobAssessmentRepository`.
- Produces: context payload field `assessment` with lifecycle status and validated result.
- Produces: `POST /api/career/conversations/{conversation_id}/assessment/retry`.

- [ ] **Step 1: Write failing API tests**

```python
def test_binding_complete_context_queues_assessment(client, complete_context_ids) -> None:
    response = client.post("/api/career/conversations/CONVERSATION/context", json=complete_context_ids)
    assert response.status_code == 200
    assert response.json()["assessment"]["status"] == "queued"

def test_retry_rejects_incomplete_context(client, resume_only_conversation) -> None:
    response = client.post(f"/api/career/conversations/{resume_only_conversation}/assessment/retry")
    assert response.status_code == 422
```

- [ ] **Step 2: Run API tests and verify they fail**

Run: `pytest tests/test_career_job_assessment_api.py tests/test_career_context_profiles_v2.py -q`

Expected: FAIL because context payloads still run lexical scoring synchronously and no retry route exists.

- [ ] **Step 3: Wire services without adding a second container**

Add `job_assessment_repository` and `job_assessment_service` fields to `CareerAssistantServices`. Construct them alongside existing full services; add only the repository to `CareerAssistantReadServices` so reads do not initialize an HTTP client.

- [ ] **Step 4: Queue on create/update and expose status**

Add `BackgroundTasks` to complete-context create/update routes. After binding, call `enqueue_context(context)` and schedule `run_assessment(record.id)` whenever the current row remains queued; atomic `claim()` prevents duplicates and allows an interrupted queued row to recover on the next read. Replace synchronous `build_match_assessment()` inside `_conversation_context_payload()` with repository lookup and serialized status/result.

- [ ] **Step 5: Implement manual retry**

The retry endpoint must verify actor ownership, require both profiles, reset only the current version-keyed assessment, schedule one background task, and return the queued context payload.

- [ ] **Step 6: Run API regression tests**

Run: `pytest tests/test_career_job_assessment_api.py tests/test_career_context_profiles.py tests/test_career_context_profiles_v2.py -q`

Expected: PASS with old deterministic unit tests preserved as fallback tests.

### Task 4: Dynamic PC score cards, evidence dialog, and polling

**Files:**
- Modify: `web-ui/src/components/CareerAssistantPage.vue`
- Modify: `web-ui/src/components/CareerContextRail.vue`
- Modify: `web-ui/src/components/CareerJobCanvas.vue`
- Modify: `web-ui/src/job-canvas-parser.js`
- Create: `web-ui/src/career-assessment-view.js`
- Create: `web-ui/src/career-assessment-view.test.js`
- Modify: `web-ui/src/job-canvas-parser.test.js`

**Interfaces:**
- Consumes: context `assessment.status`, `assessment.dimensions`, `assessment.items`, and validated `assessment.job_sections[].items`.
- Produces: `buildAssessmentCards(assessment)`, `itemsForDimension(assessment, dimensionId)`, and `isAssessmentPending(assessment)`.

- [ ] **Step 1: Write failing view-model and parser tests**

```javascript
test('主播岗位显示模型维度且不出现技术模板', () => {
  const cards = buildAssessmentCards(VOICE_HOST_ASSESSMENT)
  assert.deepEqual(cards.map((item) => item.label), ['才艺与表达', '互动能力', '关键要求缺口率'])
  assert.equal(cards.some((item) => item.label.includes('技能证据')), false)
})

test('分析终态停止轮询', () => {
  assert.equal(shouldPollAssessment({ status: 'analyzing' }), true)
  assert.equal(shouldPollAssessment({ status: 'ready' }), false)
})
```

- [ ] **Step 2: Run frontend unit tests and verify they fail**

Run: `node --test src/career-assessment-view.test.js src/job-canvas-parser.test.js`

Working directory: `web-ui`

Expected: FAIL because the dynamic assessment view model does not exist.

- [ ] **Step 3: Implement dynamic cards and evidence lookup**

Remove the hard-coded `skill_coverage`, `experience_coverage`, and `project_relevance` maps. Use dimension IDs from the API, render only scored dimensions, map verdicts to “已有证据 / 部分证据 / 待补证 / 需要确认”, and display JD/CV source text by validated IDs.

- [ ] **Step 4: Implement lifecycle UI and retry action**

Show one of “排队分析中”“正在分析岗位与简历”“已使用基础规则分析”“岗位分析失败”. Emit `retry-assessment` from `CareerContextRail`, call the retry endpoint in `CareerAssistantPage`, and disable duplicate retries while queued/analyzing.

- [ ] **Step 5: Implement bounded polling**

While the selected context status is `queued` or `analyzing`, refresh that conversation every two seconds. Clear the timer when status becomes terminal, the conversation changes, or the component unmounts. Reuse the existing conversation GET path and do not add a second polling endpoint.

- [ ] **Step 6: Use validated Judge sections for the lower canvas**

Pass `assessment.job_sections` into `CareerJobCanvas`. Render the source text materialized by the server after reference validation; if absent, keep the current deterministic parser and full-original fallback.

- [ ] **Step 7: Run frontend tests and production build**

Run: `node --test src/career-assessment-view.test.js src/job-canvas-parser.test.js && npm run build`

Working directory: `web-ui`

Expected: all tests PASS and Vite finishes without errors.

### Task 5: Cross-role integration verification and module documentation

**Files:**
- Modify: `tests/test_career_job_assessment.py`
- Modify: `tests/test_career_job_assessment_api.py`
- Modify: `docs/career_assistant_module.md`

**Interfaces:**
- Consumes: the completed backend and frontend contracts.
- Produces: regression evidence for technical, voice-host, and sales/operations jobs and an updated module call chain.

- [ ] **Step 1: Add three fixture-based role scenarios**

Each fake Judge result must cite numbered inputs and assert that the server, not the fake model, computes the returned score. The voice-host fixture includes “公司 10 年行业经验” as `company_information` and proves it does not become an experience requirement.

- [ ] **Step 2: Run all focused career assessment tests**

Run: `pytest tests/test_career_job_assessment.py tests/test_career_job_assessment_repository.py tests/test_career_job_assessment_api.py tests/test_career_context_profiles.py tests/test_career_context_profiles_v2.py -q`

Expected: PASS.

- [ ] **Step 3: Update the module record**

Document the design goal, fixed Judge model, one-call flow, cache key, retry/fallback chain, dynamic PC rendering, source-reference integrity, dependencies, validation commands, and the boundary that mobile has not received special adaptation.

- [ ] **Step 4: Run final verification**

Run: `python -m compileall src/career_assistant src/app && pytest tests/test_career_job_assessment.py tests/test_career_job_assessment_repository.py tests/test_career_job_assessment_api.py tests/test_career_context_profiles.py tests/test_career_context_profiles_v2.py -q`

Run: `node --test src/career-assessment-view.test.js src/job-canvas-parser.test.js && npm run build`

Working directory for the second command: `web-ui`

Expected: Python compilation, all focused tests, and the production build PASS.
