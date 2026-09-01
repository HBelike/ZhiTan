# BOSS 一键打招呼 PC UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在求职助手当前会话中增加可本地演示的“一键打招呼”PC 流程，覆盖职位库多选、单条文案审核、超过五条风险告警和串行发送进度，不调用真实发送接口。

**Architecture:** 复用 `JobSearchWorkspace` 的真实职位检索能力，增加最多十条的多选模式；新建独立 `CareerGreetingDialog` 承载选岗、审稿和本地发送演示。纯状态规则放在 `career-greeting-preview.js` 并用 Node Test 验证，`CareerAssistantPage` 只负责入口、开关和当前简历传递。

**Tech Stack:** Vue 3、Vite 7、原生 CSS、Node Test、现有职位库浏览器桥接

**Spec:** `docs/superpowers/specs/2026-08-23-career-boss-batch-greeting-design.md`

## Global Constraints

- 只更新本地代码，不部署、不发布、不调用真实 BOSS 发送动作。
- 首版只实现 PC 端；小屏仅保证弹窗不溢出，不做手机端专项交互。
- 单批用户可配置上限为 1 至 10，默认 5。
- 每个岗位只展示一条招呼语，支持编辑、重新生成和取消。
- 选择 6 至 10 条发送时必须经过账号风险告警。
- 页面标题、分区标题、导航名称和操作标题使用中文。
- 不修改现有 `CareerContextSetupDialog.vue` 及其他用户未提交改动。
- 所有发送进度均为本地模拟，界面持续显示“本地预览，不会发送到 BOSS”。

## 视觉方向

- 雪原蓝 `#F3F7FC`：弹窗画布。
- 纸面白 `#FFFFFF`：职位检索与审稿面板。
- 墨蓝 `#14213D`：标题、岗位名和主要文案。
- 投递蓝 `#0869D8`：主操作、轨道和当前节点。
- 信号青 `#04A6A6`：已就绪、已完成和浏览器连接。
- 风险红 `#C94955`：超过五条警告和停止状态。
- 标题、正文和状态分别复用 `--ui-font-display`、`--ui-font-body`、`--ui-font-utility`，不引入外部字体。
- 标志性元素是左侧纵向“投递轨道”。岗位按真实发送顺序形成节点，节点本身承担导航和状态表达，不增加无业务意义的统计卡片。

```text
┌ 一键打招呼 / 本地预览 ────────────── 简历 v1 ─── × ┐
│  选岗位  ─────────  审核招呼语  ─────────  发送进度 │
├─────────────────────────────────────────────────────┤
│ 选择阶段：复用职位库双栏检索                已选 3/5 │
│                                                     │
│ 审核阶段：                                          │
│ ┌ 投递轨道 ┐ ┌ 当前岗位招呼语编辑 ┐ ┌ 事实依据 ┐    │
│ │ ● 岗位一 │ │ textarea           │ │ CV 证据  │    │
│ │ ● 岗位二 │ │ 重新生成 / 取消    │ │ JD 重点  │    │
│ │ ○ 岗位三 │ └────────────────────┘ └──────────┘    │
├─────────────────────────────────────────────────────┤
│ 本地预览，不会发送到 BOSS        取消   模拟发送     │
└─────────────────────────────────────────────────────┘
```

设计自检：常见方案会使用三张统计卡和横向步骤条，容易像通用后台。本方案删除统计卡，步骤只保留在弹窗顶部；用“投递轨道”同时承担岗位顺序、审稿导航和发送状态，使视觉选择来自海投任务本身。

---

### Task 1: 本地预览状态模型

**Files:**
- Create: `web-ui/src/career-greeting-preview.js`
- Create: `web-ui/src/career-greeting-preview.test.js`

**Interfaces:**
- Consumes: 职位库返回的岗位对象 `{ id, jobId, sourceUrl, company, companyShort, title, skills, description }`
- Produces: `normalizeGreetingLimit(value) -> number`、`toggleGreetingJob(jobs, job, limit) -> { jobs, error }`、`createGreetingItems(jobs) -> GreetingItem[]`、`regenerateGreetingItem(item) -> GreetingItem`、`needsGreetingRiskWarning(count) -> boolean`

- [ ] **Step 1: 写入状态模型失败测试**

```javascript
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  createGreetingItems,
  needsGreetingRiskWarning,
  normalizeGreetingLimit,
  regenerateGreetingItem,
  toggleGreetingJob
} from './career-greeting-preview.js'

const job = (id) => ({
  id,
  title: `全栈工程师 ${id}`,
  company: `示例公司 ${id}`,
  companyShort: `示例 ${id}`,
  description: '负责微服务与分布式系统交付。',
  skills: ['微服务', '分布式']
})

test('批次上限固定在一到十之间', () => {
  assert.equal(normalizeGreetingLimit(0), 1)
  assert.equal(normalizeGreetingLimit(6), 6)
  assert.equal(normalizeGreetingLimit(99), 10)
})

test('职位多选去重并服从用户上限', () => {
  let state = toggleGreetingJob([], job('1'), 1)
  assert.deepEqual(state.jobs.map((item) => item.id), ['1'])
  state = toggleGreetingJob(state.jobs, job('2'), 1)
  assert.match(state.error, /最多选择 1 个岗位/)
  state = toggleGreetingJob(state.jobs, job('1'), 1)
  assert.deepEqual(state.jobs, [])
})

test('创建每岗一条招呼语并支持单条重新生成', () => {
  const items = createGreetingItems([job('1'), job('2')])
  assert.equal(items.length, 2)
  assert.equal(items[0].revision, 1)
  const regenerated = regenerateGreetingItem(items[0])
  assert.equal(regenerated.revision, 2)
  assert.notEqual(regenerated.message, items[0].message)
})

test('超过五条需要风险确认', () => {
  assert.equal(needsGreetingRiskWarning(5), false)
  assert.equal(needsGreetingRiskWarning(6), true)
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npm test -- --test-name-pattern="批次上限|职位多选|创建每岗|超过五条"`

Working directory: `web-ui`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `career-greeting-preview.js`.

- [ ] **Step 3: 实现纯状态函数**

```javascript
export function normalizeGreetingLimit(value) {
  return Math.max(1, Math.min(10, Math.round(Number(value) || 5)))
}

export function greetingJobKey(job) {
  return String(job?.id ?? job?.jobId ?? job?.sourceUrl ?? `${job?.company ?? ''}:${job?.title ?? ''}`)
}

export function toggleGreetingJob(jobs, job, limit) {
  const key = greetingJobKey(job)
  const exists = jobs.some((item) => greetingJobKey(item) === key)
  if (exists) return { jobs: jobs.filter((item) => greetingJobKey(item) !== key), error: '' }
  const max = normalizeGreetingLimit(limit)
  if (jobs.length >= max) return { jobs, error: `本批最多选择 ${max} 个岗位。` }
  return { jobs: [...jobs, job], error: '' }
}
```

`createGreetingItems` 使用用户已确认的履历案例生成本地演示文案，字段包含 `id`、`job`、`message`、`revision`、`included`、`status`、`evidence`。`regenerateGreetingItem` 只更新传入项并递增 `revision`。不得添加网络请求。

- [ ] **Step 4: 运行状态模型测试**

Run: `npm test -- --test-name-pattern="批次上限|职位多选|创建每岗|超过五条"`

Working directory: `web-ui`

Expected: 4 tests PASS.

- [ ] **Step 5: 提交状态模型**

```bash
git add web-ui/src/career-greeting-preview.js web-ui/src/career-greeting-preview.test.js
git commit -m "feat: add local greeting preview state"
```

### Task 2: 职位库多选能力

**Files:**
- Modify: `web-ui/src/components/JobSearchWorkspace.vue`
- Test: `web-ui/src/career-greeting-preview.test.js`

**Interfaces:**
- Consumes: `multiSelectionMode: Boolean`、`selectionLimit: Number`
- Produces: `selection-list-change` 事件，负载为完整岗位对象数组

- [ ] **Step 1: 增加多选状态测试**

在 `career-greeting-preview.test.js` 增加：

```javascript
test('选满十条后仍可移除已有岗位', () => {
  const jobs = Array.from({ length: 10 }, (_, index) => job(String(index + 1)))
  const removed = toggleGreetingJob(jobs, jobs[4], 10)
  assert.equal(removed.jobs.length, 9)
  assert.equal(removed.error, '')
})
```

- [ ] **Step 2: 运行新增测试**

Run: `npm test -- --test-name-pattern="选满十条"`

Working directory: `web-ui`

Expected: PASS after Task 1 helper implementation; this locks the behavior before Vue integration.

- [ ] **Step 3: 扩展 `JobSearchWorkspace` 接口**

增加 props：

```javascript
multiSelectionMode: { type: Boolean, default: false },
selectionLimit: { type: Number, default: 10 }
```

增加 emit 与状态：

```javascript
const emit = defineEmits(['selection-change', 'selection-list-change'])
const selectedJobs = ref([])
const multiSelectionError = ref('')
```

从 `career-greeting-preview.js` 引入 `greetingJobKey`、`toggleGreetingJob`。在岗位详情标题区增加“加入本批”按钮，只有完整 `description` 时可用。岗位卡片显示已加入标记，但点击卡片仍只负责加载详情。

- [ ] **Step 4: 验证原单选模式与多选模式构建**

Run: `npm run build`

Working directory: `web-ui`

Expected: Vite build succeeds; `CareerJobSearchDialog` 继续使用原 `selection-mode`，行为不变。

- [ ] **Step 5: 提交职位多选**

```bash
git add web-ui/src/components/JobSearchWorkspace.vue web-ui/src/career-greeting-preview.test.js
git commit -m "feat: add multi-select mode to job library"
```

### Task 3: 一键打招呼弹窗

**Files:**
- Create: `web-ui/src/components/CareerGreetingDialog.vue`
- Modify: `web-ui/src/career-greeting-preview.js`
- Test: `web-ui/src/career-greeting-preview.test.js`

**Interfaces:**
- Consumes: `open: Boolean`、`candidateProfile: Object | null`
- Produces: `cancel` 事件
- Uses: `JobSearchWorkspace` 的 `selection-list-change`

- [ ] **Step 1: 增加发送顺序状态测试**

```javascript
import { advanceGreetingSend } from './career-greeting-preview.js'

test('本地发送每次只推进一个待发送岗位', () => {
  const items = createGreetingItems([job('1'), job('2')])
  const first = advanceGreetingSend(items)
  assert.equal(first[0].status, 'sent')
  assert.equal(first[1].status, 'queued')
  const second = advanceGreetingSend(first)
  assert.equal(second[1].status, 'sent')
})
```

- [ ] **Step 2: 运行发送状态测试并确认失败**

Run: `npm test -- --test-name-pattern="本地发送"`

Working directory: `web-ui`

Expected: FAIL because `advanceGreetingSend` is not exported.

- [ ] **Step 3: 实现发送状态函数与弹窗结构**

`advanceGreetingSend(items)` 将第一个 `queued` 项改为 `sent`，其他项保持不变。弹窗包含三个阶段：

1. `select`：复用职位库，显示批次上限输入和已选数量。
2. `review`：左侧投递轨道、中间文案编辑、右侧事实依据。
3. `sending`：显示串行本地进度和“停止剩余发送”。

风险告警为弹窗内二级对话框。只有待发送数量超过 5 时出现，必须勾选“我已了解风险”才能执行本地模拟。

- [ ] **Step 4: 完成视觉样式与可访问性**

样式全部写在 `CareerGreetingDialog.vue` 的 scoped style 中。要求：

- 弹窗尺寸 `min(1560px, calc(100vw - 48px))`，高度 `calc(100dvh - 48px)`。
- 审核区三栏宽度约 `300px minmax(520px, 1fr) 280px`。
- 投递轨道节点显示 `ready`、`excluded`、`queued`、`sent`、`stopped`。
- 所有按钮有 `:focus-visible` 焦点环。
- `prefers-reduced-motion` 下关闭弹窗和进度动画。
- 本地预览提示固定出现在标题区和底部操作区。

- [ ] **Step 5: 运行测试与构建**

Run: `npm test && npm run build`

Working directory: `web-ui`

Expected: all Node tests PASS and Vite build succeeds.

- [ ] **Step 6: 提交弹窗**

```bash
git add web-ui/src/components/CareerGreetingDialog.vue web-ui/src/career-greeting-preview.js web-ui/src/career-greeting-preview.test.js
git commit -m "feat: build local batch greeting dialog"
```

### Task 4: 求职助手入口集成

**Files:**
- Modify: `web-ui/src/components/CareerAssistantPage.vue`

**Interfaces:**
- Consumes: `conversationContext.candidate_profile`
- Produces: 顶部“一键打招呼”入口和 `CareerGreetingDialog` 开关

- [ ] **Step 1: 导入弹窗并增加页面状态**

```javascript
import CareerGreetingDialog from './CareerGreetingDialog.vue'

const showGreetingDialog = ref(false)
const greetingButton = ref(null)
```

增加 `openGreetingDialog` 和 `closeGreetingDialog`。没有当前会话时沿用“请先开启一个对话”；没有简历时提示“请先添加简历资料，再生成岗位招呼语”。

- [ ] **Step 2: 增加顶部入口**

在“简历资料”按钮之前加入“一键打招呼”。按钮使用现有 `chat-material-button` 基础样式和新的 `greeting` 修饰类，保持当前头部操作顺序：

```text
一键打招呼 → 简历资料 → 职位检索
```

按钮图标使用内联 SVG 对话气泡，不引入图标依赖。

- [ ] **Step 3: 挂载弹窗**

在 `CareerJobSearchDialog` 之前加入：

```vue
<CareerGreetingDialog
  :open="showGreetingDialog"
  :candidate-profile="conversationContext?.candidate_profile ?? null"
  @cancel="closeGreetingDialog"
/>
```

- [ ] **Step 4: 运行完整前端验证**

Run: `npm test && npm run build`

Working directory: `web-ui`

Expected: all tests PASS and Vite production build succeeds.

- [ ] **Step 5: 提交入口集成**

```bash
git add web-ui/src/components/CareerAssistantPage.vue
git commit -m "feat: add greeting workflow entry"
```

### Task 5: 本地视觉验收与文档同步

**Files:**
- Create: `docs/boss_greeting_ui.md`
- Create: `docs/boss-greeting-ui-preview.png`

**Interfaces:**
- Consumes: 本地 Vite 页面和已实现 UI
- Produces: PC 端截图及模块文档中的设计目标、调用链、依赖、验证结果和后续边界

- [ ] **Step 1: 启动本地页面**

Run: `npm run dev`

Working directory: `web-ui`

Expected: Vite listens on `http://127.0.0.1:5173`.

- [ ] **Step 2: 完成 PC 端视觉检查**

在 1440px 及以上宽度检查：

- 顶部入口不挤压会话标题。
- 职位选择阶段保留职位库双栏阅读能力。
- 审核阶段首屏同时看见投递轨道、完整文案编辑器和事实依据。
- 6 条以上风险告警不会被弹窗底部遮挡。
- 发送阶段状态变化不会造成布局跳动。
- 关闭后焦点返回“一键打招呼”按钮。

- [ ] **Step 3: 截图并根据画面完成一次删减**

保存截图到 `docs/boss-greeting-ui-preview.png`。检查是否存在无业务意义的装饰、重复说明或过多边框，至少删除一项不必要视觉元素，再重新截图确认。

- [ ] **Step 4: 更新模块文档**

创建 `docs/boss_greeting_ui.md`，记录：

- 设计目标：职位多选、逐条审稿、风险告警和串行进度。
- 调用链：`CareerAssistantPage → CareerGreetingDialog → JobSearchWorkspace → career-greeting-preview.js`。
- 依赖：当前简历版本和职位库浏览器助手。
- 验证：Node tests、Vite build、PC 截图。
- 边界：本地模拟，不调用真实发送，不部署，真实 BOSS 发送等待单条链路验证。

- [ ] **Step 5: 运行最终验证**

Run: `npm test && npm run build`

Working directory: `web-ui`

Expected: all tests PASS and Vite build succeeds.

- [ ] **Step 6: 提交视觉验收与文档**

```bash
git add docs/boss_greeting_ui.md docs/boss-greeting-ui-preview.png
git commit -m "docs: record greeting UI preview"
```
