# BOSS 智能招呼语与左侧多选 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将一键打招呼从前端硬编码模板替换为固定 `deepseek-v4-pro` 的简历/JD 证据约束生成，并让用户在左侧岗位列表直接异步多选。

**Architecture:** 后端新增单岗位 `CareerGreetingService`，精确解析当前组织中 ready 的 `deepseek-v4-pro`，通过现有 Chat Completions JSON Output 生成并确定性校验文案。前端以最多 3 个请求并发生成批次；岗位卡复选框负责获取完整 JD 和维护选择状态，卡片主体继续负责详情预览。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、现有 PostgreSQL repositories、httpx/OpenAI-compatible Chat Completions、Vue 3 Composition API、Node test runner、pytest。

**Spec:** `docs/superpowers/specs/2026-08-25-smart-boss-greeting-generation-design.md`

## Global Constraints

- 固定 `provider_key=deepseek`、`model_id=deepseek-v4-pro`，不可回退到其他模型。
- 招呼语请求固定 `temperature=0.2`、thinking disabled、JSON Output、`max_tokens=800`。
- 单个模型请求只处理一个岗位；前端生成并发上限为 3，批次上限为 10。
- 简历正文只由后端依据当前 actor 和 `candidate_profile_id` 读取。
- 完整简历与 JD 只作为模型输入，不写入 trace 或错误响应。
- 左侧复选框改变批次选择；岗位卡主体点击只改变右侧预览。
- 首版仅完成 PC 端，不进行手机端或平板端专项适配。
- 不修改既有 BOSS 串行发送、验证码、限流与风险确认边界。
- 使用 TDD；保留工作区中所有无关和用户已有改动。

---

## File Structure

- Create `src/career_assistant/greetings.py`: 招呼语领域契约、证据编号、Prompt、固定模型解析、结果校验和一次纠正。
- Modify `src/career_assistant/model_clients.py`: 为单次 JSON 请求增加不影响默认值的 `CompletionRequestOptions` 与领域 trace 名称。
- Modify `src/career_assistant/web/router.py`: 请求 Schema、服务容器注入和 `POST /api/career/greetings/generate`。
- Create `tests/test_career_greetings.py`: 服务层、模型选择、Prompt、校验与纠正测试。
- Create `tests/test_career_greeting_api.py`: actor 隔离、成功响应和稳定错误映射测试。
- Create `web-ui/src/career-greeting-client.js`: 单岗位请求、响应归一化和最多 3 并发队列。
- Create `web-ui/src/career-greeting-client.test.js`: 请求体、并发、独立失败和重新生成测试。
- Modify `web-ui/src/career-greeting-preview.js`: 删除固定模板，只保留批次项目和发送状态纯函数。
- Modify `web-ui/src/career-greeting-preview.test.js`: 改为测试待生成、生成成功/失败和版本更新。
- Modify `web-ui/src/components/JobSearchWorkspace.vue`: 左侧复选框、异步详情获取、取消竞态和右侧按钮移除。
- Create `web-ui/src/job-search-multi-selection.test.js`: 静态交互契约与纯状态辅助测试。
- Modify `web-ui/src/components/CareerGreetingDialog.vue`: 调用真实 API、批次并发、单条重生成、失败重试和真实证据展示。
- Modify `web-ui/src/career-greeting-dialog-layout.test.js`: 校验真实生成状态和移除虚假 humanizer 文案。
- Modify `docs/boss_greeting_ui.md`: 记录实际调用链、Prompt、参数和 UI。
- Modify `docs/career_assistant_module.md`: 记录服务边界、接口、依赖、验证结果和后续边界。

### Task 1: 模型客户端单次调用参数

**Files:**
- Modify: `src/career_assistant/model_clients.py`
- Create: `tests/test_career_greeting_model_options.py`

**Interfaces:**
- Produces: `CompletionRequestOptions(temperature: float = 0.25, max_tokens: int | None = None, thinking: bool | None = None)`。
- Produces: `OpenAICompatibleChatClient.complete_json(..., options: CompletionRequestOptions | None = None, operation: str = "job_assessment") -> str`。
- Preserves: 所有既有调用在不传 `options` 时仍得到 `temperature=0.25` 和原最大 token。

- [ ] **Step 1: 写失败测试，锁定招呼语覆盖值和默认回归**

```python
def test_completion_options_override_only_one_request():
    target = ModelConnectionTarget("deepseek", "deepseek-v4-pro", "https://api.deepseek.com")
    payload = OpenAICompatibleChatClient._build_request_payload(
        target,
        [ChatMessage(role="user", content="hello")],
        max_tokens=12288,
        response_format={"type": "json_object"},
        options=CompletionRequestOptions(
            temperature=0.2,
            max_tokens=800,
            thinking=False,
        ),
    )
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 800
    assert payload["thinking"] == {"type": "disabled"}


def test_completion_options_keep_existing_defaults():
    target = ModelConnectionTarget("openai", "gpt-test", "https://example.test")
    payload = OpenAICompatibleChatClient._build_request_payload(
        target,
        [ChatMessage(role="user", content="hello")],
        max_tokens=12288,
    )
    assert payload["temperature"] == 0.25
    assert payload["max_tokens"] == 12288
```

- [ ] **Step 2: 运行测试并确认因接口缺失失败**

Run: `pytest tests/test_career_greeting_model_options.py -q`

Expected: FAIL，提示 `CompletionRequestOptions` 或 `options` 不存在。

- [ ] **Step 3: 实现不可变请求选项并穿透 JSON 调用**

```python
@dataclass(frozen=True)
class CompletionRequestOptions:
    temperature: float = 0.25
    max_tokens: int | None = None
    thinking: bool | None = None


def complete_json(
    self,
    profile: ModelProfileRecord,
    credential_env_name: str | None,
    messages: list[ChatMessage],
    *,
    api_key: str | None = None,
    options: CompletionRequestOptions | None = None,
    operation: str = "job_assessment",
) -> str:
    request_options = options or CompletionRequestOptions()
    return trace_operation(
        run_name=f"career.{operation}.completion",
        run_type="llm",
        execute=lambda: self._request_completion(
            target,
            credential,
            messages,
            max_tokens=request_options.max_tokens or self._completion_max_tokens,
            response_format={"type": "json_object"},
            options=request_options,
        ),
        metadata={**metadata, "operation": operation},
        tags=("career", operation.replace("_", "-"), "privacy:metadata-only"),
    )
```

`_build_request_payload` 从 `options.temperature` 设置温度；`thinking=False` 明确关闭，`thinking=True` 明确开启，`None` 保留现有 DeepSeek V4 默认关闭行为。

- [ ] **Step 4: 运行模型客户端与既有岗位评估测试**

Run: `pytest tests/test_career_greeting_model_options.py tests/test_career_job_assessment.py tests/test_career_skill_runtime.py tests/test_career_skill_tools.py -q`

Expected: PASS。

- [ ] **Step 5: 提交独立模型客户端改动**

```bash
git add src/career_assistant/model_clients.py tests/test_career_greeting_model_options.py
git commit -m "feat: 支持招呼语单次模型参数"
```

### Task 2: 招呼语领域服务

**Files:**
- Create: `src/career_assistant/greetings.py`
- Create: `tests/test_career_greetings.py`

**Interfaces:**
- Consumes: `CompletionRequestOptions` 与 `OpenAICompatibleChatClient.complete_json`。
- Produces: `GreetingJobInput`、`GreetingEvidence`、`GreetingGenerationResult`、`GreetingGenerationError`、`GreetingModelUnavailableError`。
- Produces: `CareerGreetingService.generate(organization_id: UUID, actor_id: UUID, candidate_profile_id: UUID, job: GreetingJobInput, previous_message: str = "") -> GreetingGenerationResult`。

- [ ] **Step 1: 写证据编号、固定模型与 Prompt 测试**

```python
def test_generate_uses_actor_resume_and_exact_deepseek_profile():
    service, client, gateway = build_service(
        resume_outline="[教育] 211 本科\n[经历] 参与微服务项目投产",
        model_profiles=[ready_profile("deepseek", "deepseek-v4-pro")],
        model_output=json.dumps({
            "message": "彭女士您好，我有微服务项目投产经验，和岗位的服务交付方向较贴近。如果方便，想进一步了解团队当前的项目。",
            "resume_evidence_ids": ["CV-002"],
            "jd_evidence_ids": ["JD-001"],
            "warnings": [],
        }, ensure_ascii=False),
    )
    result = service.generate(ORG_ID, ACTOR_ID, PROFILE_ID, job_input())
    assert result.model_id == "deepseek-v4-pro"
    assert result.resume_evidence[0].id == "CV-002"
    assert "<candidate_resume>" in client.messages[-1].content
    assert "<job_description>" in client.messages[-1].content
    assert gateway.requested_profile_id == client.profile.id
```

另写测试覆盖：只有 `deepseek-v4-flash`、模型 disabled、凭证 missing、候选人属于其他 actor、JD 为空、previous message 写入 Prompt。

- [ ] **Step 2: 运行服务测试确认失败**

Run: `pytest tests/test_career_greetings.py -q`

Expected: FAIL，模块 `src.career_assistant.greetings` 不存在。

- [ ] **Step 3: 实现领域契约和证据编号**

```python
@dataclass(frozen=True)
class GreetingJobInput:
    id: str
    title: str
    company: str
    recruiter: str
    description: str
    skills: tuple[str, ...] = ()
    source_url: str = ""


@dataclass(frozen=True)
class GreetingEvidence:
    id: str
    summary: str


@dataclass(frozen=True)
class GreetingGenerationResult:
    job_key: str
    message: str
    resume_evidence: tuple[GreetingEvidence, ...]
    jd_highlights: tuple[GreetingEvidence, ...]
    warnings: tuple[str, ...]
    provider_key: str
    model_id: str
```

证据切分按非空段落和行执行，移除纯标题装饰行，每段最大 800 字；CV 编号为 `CV-001`，JD 编号为 `JD-001`。总输入分别限制为简历 30,000 字、JD 50,000 字。

- [ ] **Step 4: 实现固定模型解析与完整 Prompt**

```python
def _resolve_greeting_profile(self, organization_id: UUID) -> ModelResolution:
    candidates = [
        item for item in self._model_gateway.list_availability(organization_id)
        if item.profile.provider_key == "deepseek"
        and item.profile.model_id == "deepseek-v4-pro"
        and item.readiness is ModelReadiness.READY
        and ModelCapability.TEXT in item.profile.capabilities
    ]
    if not candidates:
        raise GreetingModelUnavailableError("DeepSeek V4 Pro 尚未配置或不可用")
    profile = min(candidates, key=lambda item: (item.profile.priority, item.profile.profile_key)).profile
    return self._model_gateway.resolve(
        organization_id,
        ModelSelectionRequest(
            mode=ModelSelectionMode.SPECIFIC_PROFILE,
            profile_id=profile.id,
            required_capabilities=frozenset({ModelCapability.TEXT}),
        ),
    )
```

System Prompt 与 User Prompt 逐字采用设计规格“最终 System Prompt”章节；User Prompt 必须包含 JSON 示例、编号证据、招聘者和 previous message。

- [ ] **Step 5: 实现解析、校验和一次纠正**

```python
for attempt in range(2):
    raw = self._model_client.complete_json(
        resolution.profile,
        resolution.credential_env_name,
        messages,
        api_key=resolution.credential,
        options=CompletionRequestOptions(temperature=0.2, max_tokens=800, thinking=False),
        operation="greeting",
    )
    try:
        return self._validate_result(raw, cv_evidence, jd_evidence, job)
    except GreetingGenerationError as exc:
        if attempt == 1:
            raise
        messages.append(ChatMessage(role="assistant", content=raw[:4000]))
        messages.append(ChatMessage(role="user", content=self._correction_prompt(exc)))
```

校验字段类型、CV/JD 编号、60 至 180 字硬范围、单段文本、数字、URL、邮箱与明显英文技术标识来源。重新生成时拒绝相同文本和仅结尾变化。

- [ ] **Step 6: 运行全部服务测试**

Run: `pytest tests/test_career_greetings.py tests/test_career_greeting_model_options.py -q`

Expected: PASS。

- [ ] **Step 7: 提交领域服务**

```bash
git add src/career_assistant/greetings.py tests/test_career_greetings.py
git commit -m "feat: 新增证据约束招呼语服务"
```

### Task 3: 招呼语 API 与服务容器

**Files:**
- Modify: `src/career_assistant/web/router.py`
- Create: `tests/test_career_greeting_api.py`

**Interfaces:**
- Consumes: `CareerGreetingService.generate`。
- Produces: `POST /api/career/greetings/generate`。
- Request: `GenerateGreetingRequest(candidate_profile_id: UUID, job: GreetingJobRequest, previous_message: str = "")`。

- [ ] **Step 1: 写 API 成功与错误映射测试**

```python
def test_generate_greeting_returns_evidence_and_model():
    app = FastAPI()
    app.include_router(career_router.router)
    fake_service = Mock()
    fake_service.generate.return_value = greeting_result()
    with patch.object(career_router, "get_career_services", return_value=services(fake_service)):
        response = TestClient(app).post("/api/career/greetings/generate", json=request_payload())
    assert response.status_code == 200
    assert response.json()["model"]["model_id"] == "deepseek-v4-pro"
    assert response.json()["resume_evidence"][0]["id"] == "CV-002"


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (GreetingCandidateNotFoundError("missing"), 404, "candidate_profile_not_found"),
        (GreetingModelUnavailableError("missing"), 409, "greeting_model_unavailable"),
        (GreetingJobValidationError("missing"), 422, "incomplete_job_detail"),
        (GreetingGenerationError("bad output"), 502, "greeting_generation_failed"),
    ],
)
def test_generate_greeting_maps_domain_errors(error, status_code, code):
    app = FastAPI()
    app.include_router(career_router.router)
    fake_service = Mock()
    fake_service.generate.side_effect = error
    with patch.object(career_router, "get_career_services", return_value=services(fake_service)):
        response = TestClient(app).post(
            "/api/career/greetings/generate",
            json=request_payload(),
        )
    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code
```

- [ ] **Step 2: 运行 API 测试确认路由不存在**

Run: `pytest tests/test_career_greeting_api.py -q`

Expected: FAIL 或 404。

- [ ] **Step 3: 增加 Pydantic Schema 和路由**

```python
class GreetingJobRequest(BaseModel):
    id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=240)
    company: str = Field(default="", max_length=240)
    recruiter: str = Field(default="", max_length=160)
    description: str = Field(min_length=1, max_length=50_000)
    skills: list[str] = Field(default_factory=list, max_length=50)
    source_url: str = Field(default="", max_length=2_000)


class GenerateGreetingRequest(BaseModel):
    candidate_profile_id: UUID
    job: GreetingJobRequest
    previous_message: str = Field(default="", max_length=2_000)


@router.post("/greetings/generate")
def generate_greeting(request_body: GenerateGreetingRequest, request: Request) -> dict[str, object]:
    actor = get_request_actor()
    result = get_career_services(request).greeting_service.generate(
        actor.organization_id,
        actor.actor_id,
        request_body.candidate_profile_id,
        request_body.job.to_domain(),
        request_body.previous_message,
    )
    return _greeting_payload(result)
```

错误响应统一为 `{"detail": {"code": code, "message": message}}`，不回传原始模型文本。

- [ ] **Step 4: 注入共享服务容器**

在 `CareerAssistantServices` 增加 `greeting_service: CareerGreetingService`，并在 `get_career_services` 中复用 `context_repository`、`model_profile_repository`、`model_gateway`、`model_connection_client` 构造。

- [ ] **Step 5: 运行 API 与服务容器回归**

Run: `pytest tests/test_career_greeting_api.py tests/test_career_history_fast_path.py tests/test_career_turn_api.py -q`

Expected: PASS。

- [ ] **Step 6: 提交 API**

```bash
git add src/career_assistant/web/router.py tests/test_career_greeting_api.py
git commit -m "feat: 提供智能招呼语生成接口"
```

### Task 4: 前端招呼语请求队列与状态纯函数

**Files:**
- Create: `web-ui/src/career-greeting-client.js`
- Create: `web-ui/src/career-greeting-client.test.js`
- Modify: `web-ui/src/career-greeting-preview.js`
- Modify: `web-ui/src/career-greeting-preview.test.js`

**Interfaces:**
- Produces: `requestGreeting({ candidateProfileId, job, previousMessage }, requestImpl = fetch) -> Promise<GreetingPayload>`。
- Produces: `generateGreetingBatch(jobs, worker, concurrency = 3, onSettled = () => {}) -> Promise<void>`。
- Produces: `createGreetingItems(jobs)` 生成 `status="generating"`、空 message 的批次项目。
- Produces: `applyGreetingResult(items, id, payload, previousMessage = "")` 和 `applyGreetingFailure(items, id, error)`。

- [ ] **Step 1: 写请求体和并发上限失败测试**

```javascript
test('请求只发送候选人 id、完整岗位和上一版文案', async () => {
  const calls = []
  await requestGreeting({
    candidateProfileId: 'candidate-1',
    job: fullJob('1'),
    previousMessage: '上一版'
  }, async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) })
    return jsonResponse(successPayload('1'))
  })
  assert.equal(calls[0].url, '/api/career/greetings/generate')
  assert.equal(calls[0].body.candidate_profile_id, 'candidate-1')
  assert.equal(calls[0].body.previous_message, '上一版')
  assert.equal(calls[0].body.job.description, fullJob('1').description)
  assert.equal('resume_outline' in calls[0].body, false)
})

test('批次生成最多同时运行三个岗位且单条失败不阻断', async () => {
  let active = 0
  let peak = 0
  const settled = []
  await generateGreetingBatch(Array.from({ length: 7 }, (_, i) => fullJob(String(i))), async (job) => {
    active += 1
    peak = Math.max(peak, active)
    await delay(10)
    active -= 1
    if (job.id === '3') throw new Error('模型失败')
    return successPayload(job.id)
  }, 3, (result) => settled.push(result))
  assert.equal(peak, 3)
  assert.equal(settled.length, 7)
  assert.equal(settled.filter((item) => item.status === 'rejected').length, 1)
})
```

- [ ] **Step 2: 运行前端测试确认模块缺失**

Run: `npm --prefix web-ui test -- --test-name-pattern="请求只发送|批次生成"`

Expected: FAIL。

- [ ] **Step 3: 实现请求、错误解析与并发 worker pool**

```javascript
export async function generateGreetingBatch(jobs, worker, concurrency = 3, onSettled = () => {}) {
  const queue = [...jobs]
  const run = async () => {
    while (queue.length) {
      const job = queue.shift()
      try {
        onSettled({ status: 'fulfilled', job, value: await worker(job) })
      } catch (reason) {
        onSettled({ status: 'rejected', job, reason })
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(Math.max(1, concurrency), queue.length) }, run))
}
```

`requestGreeting` 将非 2xx 的 `detail.message` 转成 `GreetingRequestError(code, message)`。

- [ ] **Step 4: 删除固定模板并实现批次状态转换**

`createGreetingItems` 不再包含 `PREVIEW_EVIDENCE`、`GREETING_ENDINGS` 或 `previewGreeting`。初始项目保留发送字段，新增：

```javascript
{
  message: '',
  revision: 0,
  status: 'generating',
  evidence: [],
  jdHighlights: [],
  generationError: '',
  generatedModel: ''
}
```

成功后 revision 加一、status 变为 ready；失败后 status 变为 generation_failed，保留岗位和可重试状态。

- [ ] **Step 5: 运行前端纯函数测试**

Run: `npm --prefix web-ui test -- --test-name-pattern="招呼语|批次生成|重新生成"`

Expected: PASS，且测试中不再断言“3年平安银行全栈开发经验”。

- [ ] **Step 6: 提交请求层与状态层**

```bash
git add web-ui/src/career-greeting-client.js web-ui/src/career-greeting-client.test.js web-ui/src/career-greeting-preview.js web-ui/src/career-greeting-preview.test.js
git commit -m "feat: 接入招呼语生成请求队列"
```

### Task 5: 左侧岗位卡异步多选

**Files:**
- Modify: `web-ui/src/components/JobSearchWorkspace.vue`
- Create: `web-ui/src/job-search-multi-selection.js`
- Create: `web-ui/src/job-search-multi-selection.test.js`

**Interfaces:**
- Consumes: `jobLibraryBridge.getJobDetail(job)` 与 `toggleGreetingJob`。
- Produces: `createSelectionIntentState()`、`beginSelectionIntent(state, key)`、`cancelSelectionIntent(state, key)`、`isCurrentSelectionIntent(state, key, token)` 纯竞态辅助函数。
- Preserves: `selection-list-change` 继续返回完整岗位对象数组。

- [ ] **Step 1: 写选择意图与静态 UI 失败测试**

```javascript
test('取消中的详情请求不能在返回后重新加入岗位', () => {
  const state = createSelectionIntentState()
  const token = beginSelectionIntent(state, 'job-1')
  cancelSelectionIntent(state, 'job-1')
  assert.equal(isCurrentSelectionIntent(state, 'job-1', token), false)
})

test('多选岗位卡包含独立复选框且不再依赖右侧加入按钮', () => {
  const source = readFileSync(new URL('./components/JobSearchWorkspace.vue', import.meta.url), 'utf8')
  assert.match(source, /class="job-selection-checkbox"/)
  assert.match(source, /@click\.stop="toggleJobFromCard\(job\)"/)
  assert.doesNotMatch(source, /加入本批|移出本批/)
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm --prefix web-ui test -- --test-name-pattern="取消中的详情请求|独立复选框"`

Expected: FAIL。

- [ ] **Step 3: 实现卡片复选框的完整详情加载**

```javascript
async function toggleJobFromCard(job) {
  const key = greetingJobKey(job)
  if (isJobInSelection(job)) {
    cancelSelectionIntent(selectionIntents, key)
    removeSelectedJob(job)
    return
  }
  if (selectedJobs.value.length >= normalizedSelectionLimit.value) {
    multiSelectionError.value = `本批最多选择 ${normalizedSelectionLimit.value} 个岗位。`
    return
  }
  const token = beginSelectionIntent(selectionIntents, key)
  selectionLoadingKeys.value.add(key)
  try {
    const detail = job.description ? job : await jobLibraryBridge.getJobDetail(job)
    if (!isCurrentSelectionIntent(selectionIntents, key, token)) return
    addSelectedJob(detail)
    replaceJobSummaryWithDetail(detail)
  } catch (error) {
    if (isCurrentSelectionIntent(selectionIntents, key, token)) {
      selectionErrors.value[key] = readableBridgeError(error, '岗位详情获取失败')
    }
  } finally {
    selectionLoadingKeys.value.delete(key)
  }
}
```

模板中复选框使用 `@click.stop`，并提供 `aria-label`、loading 文案和失败提示。卡片本身的 `@click="selectJob(job)"` 不变。

- [ ] **Step 4: 移除右侧批次按钮并增加局部样式**

只在 `JobSearchWorkspace.vue` 的 scoped 样式中增加复选框、loading 和错误样式，不修改全局主题。移除 `toggleSelectedJob()` 和右侧按钮模板。

- [ ] **Step 5: 运行职位库和前端相关测试**

Run: `npm --prefix web-ui test -- --test-name-pattern="岗位|职位|复选框|扩展"`

Expected: PASS。

- [ ] **Step 6: 提交左侧多选**

```bash
git add web-ui/src/components/JobSearchWorkspace.vue web-ui/src/job-search-multi-selection.js web-ui/src/job-search-multi-selection.test.js
git commit -m "feat: 支持左侧岗位直接多选"
```

### Task 6: 审核弹窗接入真实智能生成

**Files:**
- Modify: `web-ui/src/components/CareerGreetingDialog.vue`
- Modify: `web-ui/src/career-greeting-dialog-layout.test.js`

**Interfaces:**
- Consumes: `requestGreeting`、`generateGreetingBatch`、`applyGreetingResult`、`applyGreetingFailure`。
- Requires: `props.candidateProfile.id`。
- Preserves: 既有编辑、取消、风险确认、串行发送和失败重试流程。

- [ ] **Step 1: 写真实生成与标签失败测试**

```javascript
test('审核弹窗调用真实招呼语接口并移除虚假 humanizer 标签', () => {
  const source = readFileSync(new URL('./components/CareerGreetingDialog.vue', import.meta.url), 'utf8')
  assert.match(source, /requestGreeting/)
  assert.match(source, /generateGreetingBatch/)
  assert.match(source, /previousMessage:\s*item\.message/)
  assert.match(source, /事实证据已校验/)
  assert.doesNotMatch(source, /humanizer 已检查|<strong>humanizer<\/strong>/)
})
```

- [ ] **Step 2: 运行布局测试确认失败**

Run: `npm --prefix web-ui test -- --test-name-pattern="真实招呼语接口"`

Expected: FAIL。

- [ ] **Step 3: 将开始审核改为异步批次生成**

```javascript
async function startReview() {
  if (!selectedJobs.value.length || !props.candidateProfile?.id || generationRunning.value) return
  items.value = createGreetingItems(selectedJobs.value)
  activeItemId.value = items.value[0]?.id ?? ''
  stage.value = 'review'
  generationRunning.value = true
  await generateGreetingBatch(
    selectedJobs.value,
    (job) => requestGreeting({ candidateProfileId: props.candidateProfile.id, job }),
    3,
    settleGreetingResult
  )
  generationRunning.value = false
}
```

没有候选人资料时禁用生成按钮并显示“请先选择简历资料”。批次中的失败项显示错误和“重新生成这一条”，发送按钮只统计 `status=ready` 且 included 的项目。

- [ ] **Step 4: 将重新生成改为真实 API**

```javascript
async function regenerateActive() {
  const item = activeItem.value
  if (!item || regeneratingItemId.value) return
  regeneratingItemId.value = item.id
  const previousMessage = item.message
  markGenerating(item.id)
  try {
    const payload = await requestGreeting({
      candidateProfileId: props.candidateProfile.id,
      job: item.job,
      previousMessage
    })
    items.value = applyGreetingResult(items.value, item.id, payload, previousMessage)
  } catch (error) {
    items.value = applyGreetingFailure(items.value, item.id, error, { preserveMessage: true })
  } finally {
    regeneratingItemId.value = ''
  }
}
```

删除 520ms 定时器和 `regenerateGreetingItem`。对话框关闭后通过 generation session token 忽略晚到结果。

- [ ] **Step 5: 更新证据与状态展示**

成功显示“事实证据已校验”“DeepSeek V4 Pro”“第 N 版”；失败显示服务端错误。证据列表从响应 `resume_evidence[].summary` 读取，JD 标签从 `jd_highlights[].summary` 读取。生成中 textarea 禁用，已有上一版的重新生成失败仍保留上一版。

- [ ] **Step 6: 运行招呼语和布局测试**

Run: `npm --prefix web-ui test -- --test-name-pattern="招呼语|一键打招呼|真实招呼语接口"`

Expected: PASS。

- [ ] **Step 7: 提交弹窗集成**

```bash
git add web-ui/src/components/CareerGreetingDialog.vue web-ui/src/career-greeting-dialog-layout.test.js
git commit -m "feat: 使用 DeepSeek 生成岗位招呼语"
```

### Task 7: 文档同步与全量验收

**Files:**
- Modify: `docs/boss_greeting_ui.md`
- Modify: `docs/career_assistant_module.md`

**Interfaces:**
- Documents: 真实调用链、固定模型、`temperature=0.2`、Prompt、错误边界、测试命令与结果。

- [ ] **Step 1: 更新模块文档并删除失真实现描述**

在 `docs/boss_greeting_ui.md` 明确记录：当前已实现单次证据约束生成，不存在第二阶段 humanizer；左侧复选框会先抓取完整 JD；模型不可用时不回退。保留完整 System Prompt 或链接到设计规格中的唯一版本。

在 `docs/career_assistant_module.md` 增加 `CareerGreetingService`、API、模型客户端单次参数、前端并发 3 和不持久化批次的边界。

- [ ] **Step 2: 运行后端目标测试**

Run: `pytest tests/test_career_greeting_model_options.py tests/test_career_greetings.py tests/test_career_greeting_api.py -q`

Expected: PASS。

- [ ] **Step 3: 运行完整前端测试**

Run: `npm --prefix web-ui test`

Expected: 全部 PASS。

- [ ] **Step 4: 运行前端生产构建**

Run: `npm --prefix web-ui run build`

Expected: 构建成功，无 Vue 编译错误。

- [ ] **Step 5: 运行受影响的后端回归**

Run: `pytest tests/test_career_job_assessment.py tests/test_career_history_fast_path.py tests/test_career_turn_api.py tests/test_career_skill_runtime.py tests/test_career_skill_tools.py -q`

Expected: PASS。

- [ ] **Step 6: PC 端本地验收**

依次验证：搜索岗位；左侧连续勾选 3 个；取消一个正在加载的岗位；选满上限；点击卡片查看详情但不改变选择；生成成功和单条失败；修改文案；重新生成产生不同表达；确认发送按钮只包含 ready 项。不得连接生产服务器或执行生产部署。

- [ ] **Step 7: 检查差异与工作区归属**

Run: `git diff --check`

Expected: 无空白错误。使用 `git status --short` 确认没有覆盖 `.agents/skills/wps-ppt/`、`data/`、`src/test.py`、`CareerJobCanvas.vue` 等无关用户改动。

- [ ] **Step 8: 提交文档与最终验证记录**

只暂存本任务实际新增或修改的文档块；如果 `docs/career_assistant_module.md` 含有用户的既有未提交改动，则保留工作区、不将整文件纳入提交，并在交付说明中注明。

```bash
git add docs/boss_greeting_ui.md
git commit -m "docs: 更新智能招呼语调用链"
```
