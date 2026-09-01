# Responsive Layout Gate One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让全局壳层、登录页、求职助手、岗位检索和简历助手在约 900×420 CSS viewport 以上无需浏览器缩放即可完成核心任务，并建立可重复运行的多视口回归检查。

**Architecture:** 全局壳层用 viewport media query 管理导航宽度和页面入口；业务组件用 container query 根据主内容区宽度切换布局。窄容器下保留唯一主任务，将历史、上下文和详情改为抽屉或标签页；业务状态常驻，响应式切换只改变呈现方式，不重建组件。

**Tech Stack:** Vue 3、Vite、原生 CSS media/container queries、Node.js test runner、Playwright（开发依赖）

**Spec:** `docs/responsive_layout_system.md`

## Global Constraints

- 仅本地修改、测试和构建，不部署生产环境。
- 以当前工作树为基线；`App.vue`、`LoginPage.vue`、`CareerAssistantPage.vue`、`styles.css` 中已有改动属于用户，必须逐块保留并复核。
- 页面级禁止水平滚动；仅表格、代码块等数据组件允许局部横向滚动。
- PC 优先；本计划覆盖桌面/缩放笔记本，不做 iPad 专项适配。
- Chrome/Edge 最近两个大版本为主，Firefox 保证核心流程可用。
- 每个响应式控制按钮保持可见焦点、`aria-expanded`/`aria-controls`，点击区域不小于 40×40 px；抽屉支持 Escape 关闭并恢复焦点。
- 同一业务组件只保留一份 DOM 和状态；禁止为窄屏复制第二套业务页面。
- 断点统一记录为：壳层 `1280/1024`；组件断点由本计划定义，并在规格文档的断点表同步更新。
- 当前存在 3 个登录页契约测试失败；先把它们作为已知基线完成，不用删除或放宽断言绕过。
- 提交前只暂存本任务新增文件和可确认的新修改；若已有与新修改无法安全拆分，则保留未提交并在交付中列明，不覆盖或代提交用户改动。

---

## Task 1: 修复全局 viewport 与应用壳层

**Files:**

- Create: `web-ui/src/responsive-layout-contract.test.js`
- Modify: `web-ui/src/styles.css`
- Modify: `web-ui/src/App.vue`
- Modify: `docs/responsive_layout_system.md`

### Interface contract

```css
:root {
  --app-sidebar-width: 248px;
  --app-page-gutter: clamp(14px, 2vw, 28px);
}

.main {
  container: app-main / inline-size;
}
```

```js
window.matchMedia('(max-width: 1023px)')
```

- `>=1280px`：248px 完整导航。
- `1024–1279px`：216px 紧凑导航。
- `<1024px`：导航抽屉，主内容占满可用宽度。
- `body` 不再设置 `min-width: 1280px`，应用高度优先使用 `100dvh`，以 `100vh` 回退。

### Steps

- [ ] 在 `responsive-layout-contract.test.js` 读取 `styles.css` 和 `App.vue`，先断言：不存在 `body { min-width: 1280px }`、壳层含 1024/1280 两档规则、主区声明 `container: app-main / inline-size`、JS 与 CSS 都使用 1023px 抽屉边界。
- [ ] 运行 `npm test -- --test-name-pattern="响应式壳层"`，确认测试因现状 `min-width: 1280px` 和 900px 边界失败。
- [ ] 在 `styles.css` 删除全局最小宽度，保留 `min-width: 0`；将 `.shell`/`.main` 的 `100vh` 改为 `100vh` 回退加 `100dvh`，并让页面纵向滚动发生在 `.main`。
- [ ] 新增根级尺寸 token；把 `.shell` 默认列宽改为 token，在 `1024–1279px` 设置 216px，在 `<1024px` 切换现有移动导航抽屉。
- [ ] 将 `.main` 设为命名 inline-size 容器，补充 `min-width: 0`、`overflow-x: clip`；不使用 `transform: scale()` 或 `zoom`。
- [ ] 在 `App.vue` 把 `matchMedia('(max-width: 900px)')` 改为 1023px，并确保监听器卸载逻辑保持不变。
- [ ] 更新规格文档中的断点表和全局滚动归属。
- [ ] 运行定向测试，再运行 `npm test`；期望除登录页既有 3 项外全部通过。
- [ ] 检查 `git diff -- web-ui/src/styles.css web-ui/src/App.vue`，确认没有移除现有登录页和主题相关改动。
- [ ] 条件允许时提交：`fix(ui): make application shell viewport responsive`。

---

## Task 2: 完成登录页紧凑桌面与短屏布局

**Files:**

- Modify: `web-ui/src/components/LoginPage.vue`
- Modify: `web-ui/src/styles.css`
- Modify: `web-ui/src/login-career-home.test.js`

### Interface contract

```html
<div class="login-agent-rail" aria-label="求职 Agent 执行流程">
  <div class="login-agent-track" aria-hidden="true">
    <span class="login-agent-orbit"></span>
  </div>
  <ol class="login-agent-stations">
    <li v-for="item in careerCapabilities" :key="item.title">
      <span class="login-agent-station-icon" aria-hidden="true">{{ item.icon }}</span>
      <strong>{{ item.title }}</strong>
      <span>{{ item.description }}</span>
    </li>
  </ol>
</div>
```

- `>=1280px`：左右同屏，职业能力区完整展示。
- `1024–1279px`：仍保持左右同屏，但减少间距、字号和装饰尺寸。
- `width <1024px`：隐藏右侧装饰/能力区，登录表单保持主任务。
- `height <=720px && width >=1024px`：压缩垂直留白，不截断提交按钮。

### Steps

- [ ] 运行 `npm test -- --test-name-pattern="求职 Agent|PC 登录页"`，记录现有 3 项失败，作为红灯。
- [ ] 把当前曲线路径和可点击步骤改为语义化 `ol/li` 连续执行轨；保留现有五项中文能力内容和求职助手视觉方向。
- [ ] 删除只服务旧交互的 `activeCareerCapability`、详情面板和按钮选择逻辑，避免窄屏隐藏内容仍占布局高度。
- [ ] 在 `styles.css` 实现五等分站点、10%–90% 连续轨道、克制的 orbit 动效，并在 `prefers-reduced-motion: reduce` 停止该动效。
- [ ] 添加 `1024–1279px` 和短屏媒体规则；将旧 `max-width: 900px` 登录页切换点调整到 1023px，保持表单列宽和焦点样式。
- [ ] 运行定向测试，预期 3 项全部通过。
- [ ] 以 1024×720、1093×614、1280×720 手工检查：标题、输入框、验证码、提交按钮均在纵向滚动后可达，页面无水平滚动。
- [ ] 运行 `npm test` 和 `npm run build`。
- [ ] 对比现有登录页 diff，确认没有回退用户已加入的“从一份简历”与五项能力内容。
- [ ] 条件允许时提交：`fix(ui): adapt login page for compact desktop viewports`。

---

## Task 3: 将求职助手三栏布局改为主任务加响应式抽屉

**Files:**

- Create: `web-ui/src/career-responsive-layout.js`
- Create: `web-ui/src/career-responsive-layout.test.js`
- Modify: `web-ui/src/components/CareerAssistantPage.vue`
- Modify: `web-ui/src/styles.css`

### Interface contract

```js
export function getCareerLayoutMode(width) {
  if (width < 820) return 'single'
  if (width < 1240) return 'compact'
  return 'wide'
}

export function getPanelAfterLayoutChange(previousMode, nextMode, panel) {
  return previousMode === nextMode ? panel : ''
}
```

```js
const compactPanel = ref('') // '' | 'history' | 'context'
```

- `wide`：历史、聊天、上下文三栏。
- `compact`：历史 + 聊天；上下文是右侧抽屉。
- `single`：聊天独占；历史和上下文分别是抽屉。
- ResizeObserver 只观察布局模式切换并关闭临时抽屉，不清空会话、草稿、滚动内容或上下文数据。

### Steps

- [ ] 为纯函数写测试：819/820/1239/1240 边界；同模式保持面板，跨模式清空临时面板。
- [ ] 为组件源代码增加契约断言：存在历史/上下文触发器、`aria-expanded`、Escape 处理、scrim、命名 container query；确认旧 `100vw` 计算不再作为列宽来源。
- [ ] 运行 `npm test -- --test-name-pattern="求职助手响应式"`，确认新增测试失败。
- [ ] 新建 `career-responsive-layout.js` 并实现上述纯函数。
- [ ] 在 `CareerAssistantPage.vue` 增加 `careerWorkspaceRef`、`layoutMode`、`compactPanel`、触发器引用和 ResizeObserver；组件卸载时断开 observer。
- [ ] 增加 `openCompactPanel`/`closeCompactPanel`：记录触发器、设置 `aria-expanded`，Escape 关闭后恢复触发器焦点。
- [ ] 保留现有 `historyCollapsed` 和 `contextRailWidth`，让它们只影响 wide/compact 模式；不要移动或回退当前输入区旁的 `CareerContextMeter`。
- [ ] 在模板中给历史、聊天、上下文设置稳定区域 class；添加窄容器触发器和一个共享 scrim，不复制 `CareerContextRail`。
- [ ] 用 `@container app-main (max-width: 1239px)` 把上下文栏变为 fixed/absolute 抽屉；用 `max-width: 819px` 把历史栏也变为抽屉，聊天列保持 `minmax(0, 1fr)`。
- [ ] 删除 `@media (max-width: 1500px) and (min-width: 641px)` 中基于 `calc(100vw - var(--history-panel-width) - var(--chat-min-width) - 50px)` 的业务列宽推导，抽屉宽度使用 `min(420px, calc(100% - 24px))`。
- [ ] 运行定向测试和全量单测；以 900×420、1024×576、1093×614、1280×720 检查发送输入框始终可见、两类抽屉可开关、页面无横向溢出。
- [ ] 运行 `npm run build`，检查控制台无 ResizeObserver 或焦点错误。
- [ ] 条件允许时提交：`feat(ui): make career workspace responsive`。

---

## Task 4: 将岗位检索改为紧凑主从视图

**Files:**

- Create: `web-ui/src/job-search-responsive-layout.test.js`
- Modify: `web-ui/src/components/JobSearchWorkspace.vue`
- Modify: `web-ui/src/styles.css`

### Interface contract

```js
const compactView = ref('list') // 'list' | 'detail'

function selectJob(job) {
  selectedJob.value = job
  compactView.value = 'detail'
}

function returnToJobList() {
  compactView.value = 'list'
}
```

- 宽容器保持筛选/列表/详情协同布局。
- 中等容器压缩筛选区，列表与详情仍并排。
- 窄容器一次只显示列表或详情；返回列表不清除查询条件、列表、选中岗位。

### Steps

- [ ] 新建源代码契约测试，断言 `compactView`、返回按钮、根节点状态 class、`@container app-main`、局部滚动规则存在。
- [ ] 运行定向测试，确认红灯。
- [ ] 在组件中加入 `compactView`，将现有岗位选中入口统一经过 `selectJob`；返回动作只改视图，不清空 `selectedJob`。
- [ ] 在详情标题区添加“返回岗位列表”按钮，配置 `aria-label`，仅窄容器显示。
- [ ] 根节点添加 `show-compact-detail` class；列表和详情保持挂载，使用 CSS 控制可见区域，避免来回切换丢失滚动和选中状态。
- [ ] 将岗位工作区设为 inline-size 容器；在约 1000px 容器宽度压缩筛选区，在约 760px 进入单页主从模式。
- [ ] 给长岗位名、标签和详情正文添加 `min-width: 0`、换行/截断规则；数据列表内部可纵向滚动，页面级不得横向滚动。
- [ ] 运行定向测试和全量单测；验证“筛选→选择→详情→返回”后筛选条件、列表和选中高亮仍存在。
- [ ] 在既有 `career-job-dialog-preview.html` 检查 900×420、1024×576、1280×720。
- [ ] 运行 `npm run build`。
- [ ] 条件允许时提交：`feat(ui): add responsive job list detail flow`。

---

## Task 5: 将简历助手改为自适应表单与标签工作区

**Files:**

- Create: `web-ui/src/resume-responsive-layout.test.js`
- Modify: `web-ui/src/components/ResumeAssistantPage.vue`
- Modify: `web-ui/src/styles.css`

### Interface contract

```js
const workspacePane = ref('assistant') // 'document' | 'assistant'
const detailPane = ref('optimized') // 'optimized' | 'analysis'
```

- 宽容器保持文档/助手双栏和结果详情双栏。
- 中等容器让表单降为两列，卡片使用 `repeat(auto-fit, minmax(220px, 1fr))`。
- 窄容器表单单列；文档/助手、优化稿/分析分别通过标签切换，所有 pane 保持挂载。

### Steps

- [ ] 新建源代码契约测试，断言两个 pane 状态、中文标签按钮、`aria-selected`、auto-fit 卡片和两档 container query。
- [ ] 运行定向测试，确认红灯。
- [ ] 在 `ResumeAssistantPage.vue` 加入两组标签状态；按钮采用 `role="tab"`，面板采用 `role="tabpanel"`，ID 和 `aria-controls` 一一对应。
- [ ] 给现有左右 pane 添加区域 class 和根状态 class；只控制布局/可见性，不复制表单、文档编辑器或结果组件。
- [ ] 将固定四列表单改为 `repeat(auto-fit, minmax(220px, 1fr))`；在窄容器强制单列，字段与按钮 `min-width: 0`。
- [ ] 用 `@container app-main` 在约 1100px 压缩双栏，在约 760px 显示标签并切换单 pane；移除会造成短屏截断的固定高度，改为 `min-height` 加 pane 内部滚动。
- [ ] 确认右侧现有 `rightPane` 业务状态与新 `detailPane` 职责不冲突；若两者语义相同，复用 `rightPane`，不保留重复状态。
- [ ] 运行定向测试和全量单测；验证切换 pane 前后输入内容、生成结果和滚动位置不丢失。
- [ ] 以 900×420、1024×576、1093×614、1280×720 检查主操作始终可达、无页面级横向滚动。
- [ ] 运行 `npm run build`。
- [ ] 条件允许时提交：`feat(ui): adapt resume assistant workspace`。

---

## Task 6: 建立第一验收门的真实视口回归

**Files:**

- Create: `web-ui/playwright.config.js`
- Create: `web-ui/e2e/responsive-gate-one.spec.js`
- Create: `web-ui/e2e/support/api-fixtures.js`
- Create: `web-ui/responsive-shell-preview.html`
- Create: `web-ui/src/responsive-shell-preview-main.js`
- Modify: `web-ui/package.json`
- Modify: `web-ui/package-lock.json`
- Modify: `docs/responsive_layout_system.md`

### Interface contract

```js
const viewports = [
  { name: 'scaled-laptop', width: 900, height: 420 },
  { name: 'laptop-150', width: 911, height: 512 },
  { name: 'laptop-125', width: 1093, height: 614 },
  { name: 'compact-desktop', width: 1280, height: 720 },
  { name: 'desktop', width: 1440, height: 900 }
]
```

每个场景至少断言：

```js
expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
```

### Steps

- [ ] 安装 `@playwright/test` 为开发依赖，增加脚本 `test:e2e:responsive`；不引入新的 UI 框架。
- [ ] 写 `playwright.config.js`，让测试启动现有 Vite dev server；浏览器只安装 Chromium，避免扩大本地依赖范围。
- [ ] 创建最小壳层预览入口，复用正式 `styles.css` 和壳层 class，用于稳定验证 248/216/抽屉三档，不复制业务逻辑。
- [ ] 在 `api-fixtures.js` 为登录、求职助手和简历助手的只读初始化请求提供确定性本地响应；未识别的写请求直接失败，使测试不会误触真实后端。
- [ ] 为登录页、壳层、求职助手、岗位检索、简历助手建立视口矩阵；岗位检索复用现有 `career-job-dialog-preview.html`。
- [ ] 先写页面级无横向溢出、主操作可见、导航模式正确、抽屉/标签能切换、Escape 能关闭的结构断言。
- [ ] 只为登录、求职助手主界面、岗位列表、简历助手各保留一张稳定截图；对时间、滚动条和动态光点禁用动画后截图。
- [ ] 运行 `npm run test:e2e:responsive`，修复所有结构断言；若视觉快照变化，只在人工确认布局符合规格后更新基线。
- [ ] 运行完整验收：`npm test`、`npm run test:e2e:responsive`、`npm run build`。
- [ ] 在规格文档记录第一验收门结果、已覆盖 viewport、Firefox 手工基本检查结果以及第二验收门边界。
- [ ] 审查所有修改文件和未提交用户改动，确认无生产部署、无页面级横向滚动、无状态复制。
- [ ] 条件允许时提交：`test(ui): add responsive viewport regression gate`。

---

## Acceptance Gate One

- [ ] 900×420 下可以登录、发送求职助手消息、浏览岗位列表/详情、执行简历助手主操作。
- [ ] 1024–1279px 壳层使用紧凑导航，主内容不会被 248px 导航过度挤压。
- [ ] 任何目标 viewport 的 `documentElement.scrollWidth` 不大于 `clientWidth`。
- [ ] 求职助手抽屉、岗位主从视图、简历标签切换均不丢失业务状态。
- [ ] 键盘可操作全部新增控制，Escape 关闭抽屉并恢复焦点。
- [ ] `npm test`、`npm run test:e2e:responsive`、`npm run build` 全部通过。
- [ ] 现有用户改动被保留，设计规格已同步实现结果和第二验收门范围。

通过后再单独制定第二验收门计划：面试题库、实时面试、后台页面及其余路由。
