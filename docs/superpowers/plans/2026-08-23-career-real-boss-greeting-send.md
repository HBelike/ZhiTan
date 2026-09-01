# BOSS 真实批量招呼发送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不部署线上服务的前提下，让求职助手通过本地 Chrome 扩展把用户逐条审核后的 1–10 条招呼语真实、串行发送到对应 BOSS 岗位。

**Architecture:** WebUI 只负责选择、审核和串行调度；MV3 扩展复用用户现有 BOSS 登录态，单条执行预检、建立沟通关系、打开聊天页、填入消息、点击发送并识别回执。任何验证码、限流、登录失效或未知结果都会停止整个批次，POST 和发送点击都不自动重试。

**Tech Stack:** Vue 3、Vite、Chrome MV3、`chrome.tabs`、`chrome.scripting`、Node test runner。

**Spec:** `docs/superpowers/specs/2026-08-23-career-boss-batch-greeting-design.md`

## Global Constraints

- PC 端优先；本轮不做手机端和平板端专项适配。
- 用户可配置批次上限为 1–10；6–10 条必须确认账号风险。
- 扩展一次只接收一个岗位和一条最终文案；WebUI 严格串行调用。
- 不保存、返回或记录 BOSS Cookie、Token、密码和完整聊天记录。
- 不绕过验证码、安全验证、限流或沟通额度提示。
- `friend/add`、页面“发送”点击均不盲目重试；结果未知时停止批次。
- BOSS 默认招呼语已自动发送时继续发送定制文案，定制文案作为第二条消息。
- 只在本地更新，不部署、不自动执行真人账号验收发送。

---

### Task 1: 发送协议纯函数

**Files:**
- Create: `browser-extension/job-library/boss-greeting.js`
- Create: `browser-extension/job-library/tests/boss-greeting.test.mjs`

**Interfaces:**
- Produces: `normalizeGreetingPayload(payload)`、`buildBossChatUrl(job)`、`classifyFriendAddResponse(response)`、`shouldStopGreetingBatch(errorCode)`。

- [ ] **Step 1: Write the failing tests**

覆盖缺失 `securityId/jobId/bossId/lid/message`、聊天 URL 编码、默认招呼语、验证码、限流、岗位失效和未知结果。

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern greeting`
Expected: FAIL because `boss-greeting.js` does not exist.

- [ ] **Step 3: Implement pure protocol helpers**

返回统一错误结构：

```js
{ code: 'verification_required', message: 'BOSS 要求安全验证。', stopBatch: true }
```

聊天 URL 只允许 `https://www.zhipin.com/web/geek/chat`，查询字段来自归一化岗位对象。

- [ ] **Step 4: Run tests**

Run: `npm test`
Expected: all extension tests pass.

- [ ] **Step 5: Commit**

```bash
git add browser-extension/job-library/boss-greeting.js browser-extension/job-library/tests/boss-greeting.test.mjs
git commit -m "feat: define BOSS greeting send protocol"
```

### Task 2: 扩展单条真实发送执行器

**Files:**
- Modify: `browser-extension/job-library/content-script.js`
- Modify: `browser-extension/job-library/service-worker.js`
- Modify: `browser-extension/job-library/boss-data.js`
- Modify: `browser-extension/job-library/manifest.json`
- Modify: `browser-extension/job-library/README.md`
- Test: `browser-extension/job-library/tests/boss-data.test.mjs`

**Interfaces:**
- Consumes: Task 1 protocol helpers.
- Produces: extension actions `preflight_greeting` and `send_greeting`.

- [ ] **Step 1: Extend job normalization tests**

断言搜索和详情结果保留 `bossId`、`jobId`、`securityId`、`lid`，但不包含 Cookie 或 Token。

- [ ] **Step 2: Add action whitelist**

Content Script 和 Service Worker 只新增：

```text
preflight_greeting
send_greeting
```

- [ ] **Step 3: Implement preflight**

检查登录、安全验证、岗位详情可用性和四个聊天标识。预检不执行 `friend/add`，不产生外部沟通。

- [ ] **Step 4: Implement one-item send**

执行顺序固定为：获取页面 Token → 单次 POST `friend/add` → 检查默认招呼语与平台提示 → 导航后台专用聊天标签页 → 等待 `#chat-input` 和发送按钮 → 写入消息并触发 input 事件 → 单次点击发送 → 检查自己的消息节点及送达状态。

- [ ] **Step 5: Fail closed**

验证码、限流、登录失效、DOM 不完整、回执超时均返回 `stopBatch: true`。默认招呼语已发送时继续发送定制文案。不自动确认额度弹窗，不重试 POST 或发送点击。

- [ ] **Step 6: Run extension tests**

Run: `npm test`
Expected: all extension tests pass.

- [ ] **Step 7: Commit**

```bash
git add browser-extension/job-library
git commit -m "feat: send one BOSS greeting through local extension"
```

### Task 3: WebUI 扩展桥接

**Files:**
- Modify: `web-ui/src/job-library-bridge.js`
- Modify: `web-ui/src/job-library-bridge.test.js`

**Interfaces:**
- Produces: `jobLibraryBridge.preflightGreeting(job, options)` and `jobLibraryBridge.sendGreeting(job, message)`.

- [ ] **Step 1: Add bridge tests**

断言桥接只传输岗位标识、最终文案和布尔确认，不传输简历正文、浏览器凭证或整批任务。

- [ ] **Step 2: Implement bridge methods**

```js
preflightGreeting(job, message)
sendGreeting(job, message)
```

- [ ] **Step 3: Run WebUI tests**

Run: `npm test`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add web-ui/src/job-library-bridge.js web-ui/src/job-library-bridge.test.js
git commit -m "feat: bridge BOSS greeting send actions"
```

### Task 4: 前端严格串行调度

**Files:**
- Modify: `web-ui/src/career-greeting-preview.js`
- Modify: `web-ui/src/career-greeting-preview.test.js`
- Modify: `web-ui/src/components/CareerGreetingDialog.vue`
- Modify: `web-ui/src/components/CareerGreetingPreviewPage.vue`

**Interfaces:**
- Consumes: Task 3 bridge methods.
- Produces: 1–10 条真实串行发送 UI、停止控制和逐条错误状态。

- [ ] **Step 1: Extend state tests**

覆盖 `preflighting → sending → sent`，上一条完成前下一条不能开始；取消项跳过；任一终止错误把后续队列标为已停止。

- [ ] **Step 2: Replace timer simulation**

将 `setTimeout` 模拟替换为单一 `for...of` 异步循环。每次只调用一次预检和一次发送，收到结果后才推进下一条。

- [ ] **Step 3: Handle default greeting**

默认招呼语不作为发送门槛；平台先发送默认招呼语时，继续发送本次定制文案。

- [ ] **Step 4: Update real-send copy**

删除“本地模拟”“不会发送”等文案，改为“将通过本地浏览器真实发送到 BOSS”；预览专页显式传入 `simulationMode`，继续保持无外部副作用。

- [ ] **Step 5: Verify stop behavior**

用户点击停止后不启动下一项；当前已提交的发送等待明确结果，不能伪装成已取消。

- [ ] **Step 6: Run tests and build**

Run: `npm test && npm run build`
Expected: tests and build pass.

- [ ] **Step 7: Commit**

```bash
git add web-ui/src
git commit -m "feat: run real BOSS greetings serially"
```

### Task 5: 本地联调与交付文档

**Files:**
- Modify: `docs/boss_greeting_ui.md`
- Modify: `docs/superpowers/specs/2026-08-23-career-boss-batch-greeting-design.md`

**Interfaces:**
- Consumes: Tasks 1–4 completed behavior.

- [ ] **Step 1: Run automated suites**

Run extension and WebUI test/build commands. Record exact counts.

- [ ] **Step 2: Reload local extension**

只说明如何在 `chrome://extensions` 重新加载开发目录；不代替用户安装或修改浏览器权限。

- [ ] **Step 3: Perform read-only preflight validation**

验证扩展连接、字段完整、登录/验证状态识别和预览模式，不点击真实“发送”。

- [ ] **Step 4: Stop before live acceptance send**

真人账号单条及多条发送属于代表用户对外沟通，必须在实际执行前说明具体岗位和文案并取得确认。

- [ ] **Step 5: Commit docs**

```bash
git add docs/boss_greeting_ui.md docs/superpowers/specs/2026-08-23-career-boss-batch-greeting-design.md
git commit -m "docs: document real BOSS greeting sender"
```
