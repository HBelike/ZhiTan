# BOSS 招呼失败重试能力门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 阻止旧版浏览器助手进入真实发送流程，并保证任意岗位被选中时都能看到和操作可安全重试的失败项。

**Architecture:** 浏览器助手通过 `ping` 明确声明支持的能力，Web UI 将“已连接”和“满足真实发送能力”分开判断。发送弹窗在开始真实发送和执行重试前均重新检查能力；失败操作条从当前选中岗位中解耦，始终指向本批第一个可处理失败项。

**Tech Stack:** Vue 3、Vite、Node.js `node:test`、Chrome/Edge Manifest V3 浏览器扩展

**Spec:** `docs/superpowers/specs/2026-08-23-career-boss-batch-greeting-design.md`

## Global Constraints

- 仅本地修改、测试和构建，不部署生产环境。
- 不绕过 BOSS 验证码、安全验证或平台限制。
- 只有扩展声明 `retry_greeting_message` 和 `greeting_submission_state` 能力时，才允许开始真实批量发送。
- 发送失败后必须停止后续岗位，并保留失败状态、尝试次数和最近处理时间。
- 重试入口不能依赖当前选中的岗位。

---

### Task 1: 扩展能力声明与连接门禁

**Files:**
- Modify: `browser-extension/job-library/service-worker.js`
- Modify: `browser-extension/job-library/manifest.json`
- Modify: `web-ui/src/boss-extension-onboarding.js`
- Test: `web-ui/src/boss-extension-onboarding.test.js`
- Test: `browser-extension/job-library/tests/boss-greeting.test.mjs`

**Interfaces:**
- Produces: `ping().capabilities: string[]`
- Produces: `normalizeBossExtensionConnection(payload).status: 'ready' | 'outdated' | 'missing'`

- [ ] **Step 1: 写入会失败的能力门禁测试**

```js
assert.equal(normalizeBossExtensionConnection({ connected: true, version: '0.2.3' }).status, 'outdated')
assert.equal(normalizeBossExtensionConnection({
  connected: true,
  version: '0.2.4',
  capabilities: ['retry_greeting_message', 'greeting_submission_state']
}).status, 'ready')
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `npm test -- --test-name-pattern="连接状态"`
Expected: FAIL，因为连接状态尚未检查能力。

- [ ] **Step 3: 最小实现能力声明与 0.2.4 版本升级**

```js
const GREETING_CAPABILITIES = ['retry_greeting_message', 'greeting_submission_state']
if (message.action === 'ping') {
  return { ok: true, data: { connected: true, version, capabilities: GREETING_CAPABILITIES } }
}
```

- [ ] **Step 4: 运行连接与扩展测试**

Run: `npm test -- --test-name-pattern="连接状态"`
Expected: PASS。

Run: `node --test tests/boss-greeting.test.mjs`
Expected: PASS。

### Task 2: 全局失败操作条与失败项自动聚焦

**Files:**
- Modify: `web-ui/src/career-greeting-preview.js`
- Modify: `web-ui/src/components/CareerGreetingDialog.vue`
- Test: `web-ui/src/career-greeting-preview.test.js`
- Test: `web-ui/src/career-greeting-dialog-layout.test.js`

**Interfaces:**
- Produces: `findGreetingFailureAction(items)`，返回 `retry`、`update_extension` 或空值。
- Consumes: `JobLibraryBridgeError.retryable`、`errorCode`、`submissionState`。

- [ ] **Step 1: 写入会失败的跨选中项重试测试**

```js
const action = findGreetingFailureAction([
  { id: 'failed', status: 'failed', retryable: true },
  { id: 'stopped', status: 'stopped', retryable: false }
])
assert.deepEqual(action, { itemId: 'failed', type: 'retry' })
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npm test -- --test-name-pattern="失败操作"`
Expected: FAIL，因为失败操作尚未从当前选中项解耦。

- [ ] **Step 3: 实现失败操作解析和持久操作条**

```js
const failureAction = computed(() => findGreetingFailureAction(items.value))
async function handleFailureAction() {
  const item = items.value.find((entry) => entry.id === failureAction.value?.itemId)
  activeItemId.value = item?.id ?? activeItemId.value
  if (failureAction.value?.type === 'retry') await retryFailedItem(item)
  else installDialogOpen.value = true
}
```

- [ ] **Step 4: 失败时保持失败项为当前项**

```js
activeItemId.value = nextItem.id
items.value = stopGreetingItems(items.value)
```

- [ ] **Step 5: 运行交互与布局测试**

Run: `npm test -- --test-name-pattern="失败操作|重试"`
Expected: PASS。

### Task 3: 发送前与重试前能力复检

**Files:**
- Modify: `web-ui/src/components/CareerGreetingDialog.vue`
- Modify: `web-ui/src/components/BossExtensionInstallDialog.vue`
- Test: `web-ui/src/career-greeting-dialog-layout.test.js`

**Interfaces:**
- Consumes: `normalizeBossExtensionConnection(await jobLibraryBridge.ping())`
- Produces: 不满足能力时打开扩展更新弹窗，且不进入发送队列。

- [ ] **Step 1: 添加发送前能力复检测试**

```js
assert.match(source, /ensureGreetingExtensionReady/)
assert.match(source, /installDialogOpen\.value = true/)
```

- [ ] **Step 2: 实现开始发送与重试前复检**

```js
async function ensureGreetingExtensionReady() {
  if (props.simulationMode) return true
  const connection = normalizeBossExtensionConnection(await jobLibraryBridge.ping())
  if (connection.status === 'ready') return true
  installDialogOpen.value = true
  sendError.value = connection.status === 'outdated' ? '浏览器助手版本过旧，请更新后重试。' : '未检测到浏览器助手。'
  return false
}
```

- [ ] **Step 3: 运行 Web UI 测试**

Run: `npm test`
Expected: PASS。

### Task 4: 扩展包、文档与本地验收

**Files:**
- Modify: `browser-extension/job-library/README.md`
- Modify: `docs/boss_greeting_ui.md`
- Modify: `docs/job_library_module.md`
- Create: `web-ui/public/downloads/find-job-boss-helper-v0.2.4.zip`
- Modify: `web-ui/src/greeting-retry-preview-main.js`

**Interfaces:**
- Produces: Chrome/Edge 可加载的 0.2.4 ZIP。
- Produces: 本地预览可复现“失败项未选中但重试入口仍可见”。

- [ ] **Step 1: 更新本地模拟桥接的能力声明**

```js
return { connected: true, version: '0.2.4', capabilities: ['retry_greeting_message', 'greeting_submission_state'] }
```

- [ ] **Step 2: 更新模块文档和版本说明**

在 `browser-extension/job-library/README.md`、`docs/boss_greeting_ui.md`、`docs/job_library_module.md` 写明：

```text
0.2.4 的 ping 声明 retry_greeting_message 与 greeting_submission_state 能力；
Web UI 在真实发送和重试前复检能力；失败操作条不依赖当前选中岗位；
旧版验证失败只引导升级，不执行无法确认提交阶段的盲目重发；本次未部署生产。
```

- [ ] **Step 3: 生成并核验扩展 ZIP**

Run: `Compress-Archive -LiteralPath manifest.json,content-script.js,service-worker.js,boss-data.js,boss-greeting.js -DestinationPath find-job-boss-helper-v0.2.4.zip`
Expected: ZIP 内 `manifest.json` 版本为 `0.2.4`。

- [ ] **Step 4: 运行完整验证**

Run: `npm test`
Expected: 所有 Web UI 测试通过。

Run: `npm run build`
Expected: Vite 构建成功。

Run: `node --test tests/boss-greeting.test.mjs`
Expected: 所有扩展测试通过。

Run: 本地浏览器打开 `http://127.0.0.1:5173/greeting-retry-preview.html`。
Expected: 当前选中“已停止”项时仍显示失败项重试按钮，点击后只重发失败项并成功。
