# 求职助手职位库导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在求职助手中通过职位库弹窗选择真实岗位，并将其直接接入现有 JD 解析、会话绑定、职位信息与指标分析链路。

**Architecture:** `JobSearchWorkspace` 增加不改变独立职位库页面的选岗事件；新弹窗负责复用该工作区和确认交互；纯函数适配器把职位详情转换为现有 target role payload；`CareerAssistantPage` 继续调用已有两个 API 完成岗位创建与上下文绑定。

**Tech Stack:** Vue 3、JavaScript、Node Test Runner、Python unittest、Vite

**Spec:** `docs/superpowers/specs/2026-08-21-career-job-library-import-design.md`

## Global Constraints

- 现有手动输入和文件导入 JD 的组件与 API 行为不得改变。
- 职位弹窗主体必须复用 `JobSearchWorkspace`，不得复制职位库模板。
- 不新增数据库迁移、指标公式或 target role 数据模型。
- 只有完整职位详情成功加载后才能确认岗位。
- PC 端优先完成并验证；手机端保证可用，不做平板专项适配。
- 当前工作树包含用户未提交改动，本计划不自动创建 Git 提交，避免把共享文件中的既有改动一并提交。

---

### Task 1: 职位详情到目标岗位的纯映射

**Files:**
- Create: `web-ui/src/job-library-target-role.js`
- Create: `web-ui/src/job-library-target-role.test.js`

**Interfaces:**
- Consumes: 职位库归一化岗位对象。
- Produces: `toTargetRolePayload(job)`，返回现有 `/api/career/target-role-profiles` 接受的 JSON。

- [x] **Step 1: 写失败测试**

```javascript
import test from 'node:test'
import assert from 'node:assert/strict'
import { toTargetRolePayload } from './job-library-target-role.js'

test('将完整职位详情映射为现有目标岗位 payload', () => {
  const payload = toTargetRolePayload({
    title: '全栈开发工程师',
    company: '元响科技有限公司',
    salary: '15-30K·14薪',
    city: '上海',
    district: '杨浦区',
    experience: '经验不限',
    degree: '本科',
    description: '负责全栈产品研发。\n熟练使用 Vue 和 FastAPI。',
    skills: ['Vue', 'FastAPI'],
    welfare: ['五险一金'],
    address: '上海市杨浦区',
    sourceUrl: 'https://www.zhipin.com/job_detail/example.html'
  })

  assert.equal(payload.company_name, '元响科技有限公司')
  assert.equal(payload.role_name, '全栈开发工程师')
  assert.equal(payload.source_kind, 'job_library')
  assert.match(payload.job_text, /## 职位描述/)
  assert.match(payload.job_text, /熟练使用 Vue 和 FastAPI/)
})
```

- [x] **Step 2: 运行测试并确认模块缺失**

Run: `npm test --prefix web-ui`

Expected: FAIL，找不到 `job-library-target-role.js`。

- [x] **Step 3: 实现确定性映射与输入校验**

```javascript
export function toTargetRolePayload(job) {
  const title = String(job?.title ?? '').trim()
  const company = String(job?.company ?? job?.companyShort ?? '').trim()
  const description = String(job?.description ?? '').trim()
  if (!title || !company || !description) throw new Error('所选岗位缺少完整职位详情')
  return {
    company_name: company,
    role_name: title,
    source_kind: 'job_library',
    source_label: String(job?.sourceUrl ?? '').trim() || 'BOSS 直聘职位库',
    job_text: buildCanonicalJobText(job)
  }
}
```

同时按设计文档生成固定章节，跳过空字段，并保证不修改输入对象。

- [x] **Step 4: 运行前端单元测试**

Run: `npm test --prefix web-ui`

Expected: PASS。

### Task 2: 职位库选岗事件与弹窗

**Files:**
- Modify: `web-ui/src/components/JobSearchWorkspace.vue`
- Create: `web-ui/src/components/CareerJobSearchDialog.vue`

**Interfaces:**
- Consumes: `JobSearchWorkspace` 的完整岗位详情。
- Produces: `CareerJobSearchDialog` 事件 `confirm(job)`、`cancel`；`JobSearchWorkspace` 事件 `selection-change(job | null)`。

- [x] **Step 1: 扩展职位库组件的选岗接口**

```javascript
const props = defineProps({ selectionMode: { type: Boolean, default: false } })
const emit = defineEmits(['selection-change'])
```

开始搜索、开始切换岗位和详情失败时，在选岗模式发出 `null`；详情成功时发出完整 `detail`。独立职位库页面不传属性，现有显示与调用不变。

- [x] **Step 2: 创建职位检索弹窗**

弹窗使用 `Teleport`，结构固定为：面包屑与关闭按钮、`JobSearchWorkspace` 主体、底部确认轨道。确认按钮条件为：

```javascript
const canConfirm = computed(() => Boolean(
  selectedJob.value?.title &&
  (selectedJob.value?.company || selectedJob.value?.companyShort) &&
  selectedJob.value?.description &&
  !props.busy
))
```

- [x] **Step 3: 检查弹窗关闭与错误状态**

保存中禁止遮罩、关闭按钮和 `Esc` 关闭；`error` 显示在确认轨道上方；当前会话已有岗位时显示“确认后仅影响后续回答”。

- [x] **Step 4: 运行生产构建**

Run: `npm run build --prefix web-ui`

Expected: PASS。

### Task 3: 求职助手入口与现有上下文编排

**Files:**
- Modify: `web-ui/src/components/CareerAssistantPage.vue`

**Interfaces:**
- Consumes: `toTargetRolePayload(job)`、`CareerJobSearchDialog.confirm(job)`。
- Produces: 更新后的 `conversationContext`，供现有 `CareerContextRail` 和 `CareerJobCanvas` 消费。

- [x] **Step 1: 增加顶部资料入口**

在 `chat-header` 中增加右侧操作组：

```vue
<div v-if="selectedConversation" class="chat-material-actions">
  <button type="button" @click="openContextSetup">简历资料</button>
  <button ref="jobSearchButton" type="button" @click="openJobSearch">职位检索</button>
</div>
```

按钮用状态圆点表达已有简历/岗位，保持操作名不变。

- [x] **Step 2: 实现职位保存与绑定**

```javascript
async function confirmJobFromLibrary(job) {
  const conversationId = selectedConversation.value?.id
  if (!conversationId) return
  const payload = toTargetRolePayload(job)
  const targetRole = await requestJson('/api/career/target-role-profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  conversationContext.value = await requestJson(`/api/career/conversations/${conversationId}/context`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      candidate_profile_id: conversationContext.value?.candidate_profile?.id ?? null,
      target_role_profile_id: targetRole.id
    })
  })
}
```

创建岗位成功、绑定失败时缓存 `targetRole` 和岗位稳定 ID，重试只执行绑定。

- [x] **Step 3: 处理会话切换和反馈**

切换、归档或删除会话时关闭弹窗并清除待确认选择；保存成功显示“目标岗位已更新，后续回答将使用新岗位”；失败时弹窗保持打开且当前 context 不变。

- [x] **Step 4: 运行构建和现有上下文测试**

Run: `npm run build --prefix web-ui`

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_career_context_profiles_v2 -v`

Expected: 全部 PASS。

### Task 4: 文档与 PC 验收

**Files:**
- Modify: `docs/career_assistant_module.md`
- Modify: `docs/job_library_module.md`

**Interfaces:**
- Consumes: 完成的弹窗选岗和上下文绑定链路。
- Produces: 与真实实现一致的模块职责、调用链、异常边界和验证记录。

- [x] **Step 1: 更新维护文档**

记录职位库独立页面与求职助手弹窗共用 `JobSearchWorkspace`；记录 `job_library` 来源映射、现有 target role API 复用和四项指标边界。

- [x] **Step 2: 运行完整相关验证**

Run: `npm test --prefix web-ui`

Run: `npm run build --prefix web-ui`

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_career_context_profiles_v2 -v`

Expected: 全部 PASS。

- [x] **Step 3: PC 端可视验收**

使用本地页面验证：顶部两个入口、职位库弹窗与独立职位库一致、确认按钮状态、成功绑定后的右栏刷新、原手动 JD 流程仍可打开。
