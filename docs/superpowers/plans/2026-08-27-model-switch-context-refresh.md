# Model Switch Context Refresh Implementation Plan

> **For agentic workers:** 本项目禁止自动启用 Superpowers；本计划在当前会话内按步骤执行。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对话切换或更新模型时立即按当前模型重新估算上下文限制与占用，并避免并发响应串写。

**Architecture:** 前端以“会话 ID + 解析后的模型档案 ID + 模型连接修订号”为刷新键；任一键变化都先失效旧展示，再请求后端。后端继续通过显式 `model_profile_id` 构造 `SPECIFIC_PROFILE` 选择并生成上下文快照，新增路由测试固定这一契约。

**Tech Stack:** Vue 3、Node.js test runner、FastAPI、unittest/pytest

**Spec:** `docs/superpowers/specs/2026-08-27-model-switch-context-refresh-design.md`

## Global Constraints

- 始终使用中文界面文案和中文代码注释。
- 仅本地修改、测试和构建，不部署生产。
- 保留普通用户只能看到近似上下文占用的现有权限边界。
- 工作区已有未提交改动，所有补丁必须限定到本功能相关行，不创建提交。

---

### Task 1: 固定前端刷新契约

**Files:**
- Modify: `web-ui/src/career-context-meter-layout.test.js`
- Modify: `web-ui/src/components/CareerAssistantPage.vue`

**Interfaces:**
- Consumes: `selectedConversation.value?.id`、`resolvedSelectedProfileId.value`、`contextUsageRequestId`
- Produces: `contextUsageRevision: Ref<number>` 和统一的上下文重新加载 watcher

- [x] **Step 1: 写入失败测试**

```js
assert.match(page, /contextUsageRevision/)
assert.match(page, /contextUsage\.value = null[\s\S]*loadContextUsage\(\)/)
assert.match(page, /contextUsageRevision\.value \+= 1/)
```

- [x] **Step 2: 运行测试确认失败**

Run: `npm test -- --test-name-pattern="页面切换会话与模型"`

Expected: FAIL，页面尚未包含模型连接修订号和统一失效逻辑。

- [x] **Step 3: 实现最小刷新逻辑**

```js
const contextUsageRevision = ref(0)

watch(
  () => [selectedConversation.value?.id, resolvedSelectedProfileId.value, contextUsageRevision.value],
  () => {
    contextUsage.value = null
    void loadContextUsage()
  },
)
```

保存可用模型连接后执行 `contextUsageRevision.value += 1`，让同一档案 ID 的容量更新也能触发刷新。`loadContextUsage()` 继续使用 `contextUsageRequestId` 丢弃过期响应。

- [x] **Step 4: 运行定向测试确认通过**

Run: `npm test -- --test-name-pattern="页面切换会话与模型"`

Expected: PASS。

### Task 2: 固定后端指定模型契约

**Files:**
- Modify: `tests/test_career_turn_api.py`
- Read-only verification: `src/career_assistant/web/turn_router.py`

**Interfaces:**
- Consumes: `GET /api/career/conversations/{conversation_id}/context-usage?model_profile_id={uuid}`
- Produces: 路由测试，保证 `ModelSelectionMode.SPECIFIC_PROFILE` 与请求中的档案 ID 原样传给 `model_gateway.resolve()`

- [x] **Step 1: 新增路由回归测试**

```python
response = client.get(
    f"/api/career/conversations/{conversation_id}/context-usage",
    params={"model_profile_id": str(profile_id)},
)
self.assertEqual(gateway.selection.profile_id, profile_id)
self.assertEqual(gateway.selection.mode, ModelSelectionMode.SPECIFIC_PROFILE)
```

- [x] **Step 2: 运行后端定向测试**

Run: `pytest tests/test_career_turn_api.py -q`

Expected: PASS；若失败则仅修正 `get_context_usage()` 的指定模型选择链路。

### Task 3: 文档与全量验证

**Files:**
- Modify: `docs/career_model_connections.md`

**Interfaces:**
- Consumes: Task 1 的刷新键、Task 2 的后端契约
- Produces: 设计目标、调用链、竞态保护、验证结果记录

- [x] **Step 1: 更新模块文档**

记录调用链：`模型下拉框/历史对话/连接保存 → 失效旧占用 → 携带 model_profile_id 请求 → ModelGateway → ContextBudgetService → 仅接收最新响应`。

- [x] **Step 2: 运行 Web UI 全量测试与构建**

Run: `npm test && npm run build`

Expected: 全部测试通过，Vite production build 成功。

- [x] **Step 3: 运行后端相关测试**

Run: `pytest tests/test_career_turn_api.py tests/test_model_context_policy.py tests/test_model_context_policy_migration.py -q`

Expected: 全部通过。

- [x] **Step 4: 检查限定差异**

Run: `git diff -- web-ui/src/components/CareerAssistantPage.vue web-ui/src/career-context-meter-layout.test.js tests/test_career_turn_api.py docs/career_model_connections.md`

Expected: 只包含上下文刷新和对应测试、文档变更；保留文件中用户已有的其他改动。
