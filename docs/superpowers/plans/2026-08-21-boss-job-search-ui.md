# BOSS 真实岗位检索 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在面经库新增一个只输入岗位名称即可评审的 PC 端真实岗位检索 UI 原型。

**Architecture:** 使用独立 Vue 单文件组件承载岗位检索工作台，通过 App.vue 现有手写路由接入 `/interviews/jobs`。原型数据只保存在组件内，模拟搜索、卡片选择和分页，后续真实浏览器助手通过相同的界面状态接口替换数据源。

**Tech Stack:** Vue 3 Composition API、Vite、Scoped CSS、现有主题 CSS Token。

**Spec:** `docs/superpowers/specs/2026-08-21-boss-job-search-ui-design.md`

## Global Constraints

- 本轮只交付 PC 端 UI 评审原型，不接入真实 BOSS 数据链路。
- 页面必须明确标记“界面预览数据”。
- 用户只填写岗位名称，默认城市为上海。
- 子路由为 `/interviews/jobs`，页面标题和操作均使用中文。
- 不添加第三方前端依赖，不修改现有面经数据接口。

---

### Task 1: 岗位检索工作台组件

**Files:**
- Create: `web-ui/src/components/JobSearchWorkspace.vue`

**Interfaces:**
- Consumes: `back` 事件用于返回面经库。
- Produces: 搜索、选择岗位、加载更多三种可评审交互。

- [x] **Step 1: 创建静态岗位数据与组件状态**

定义 `jobs`、`query`、`selectedJobId`、`searching`、`visibleCount`，默认岗位为“Agent评测基建开发工程师”。

- [x] **Step 2: 实现可交互模板**

添加单输入搜索带、岗位卡片列表、详情阅读区、“加载更多岗位”和“返回面经库”。

- [x] **Step 3: 实现主题化 Scoped CSS**

按设计文档实现实时检索轨道、双栏布局、选中态、焦点态、加载态和 reduced-motion。

- [x] **Step 4: 运行生产构建**

Run: `npm run build`

Expected: Vite build succeeds without Vue template or CSS errors.

### Task 2: 面经库入口与子路由

**Files:**
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/components/InterviewLibraryPage.vue`

**Interfaces:**
- Consumes: `InterviewLibraryPage` 的 `open-job-search` 事件与 `JobSearchWorkspace` 的 `back` 事件。
- Produces: `/interviews/jobs` 可直接打开，并保持面经库主导航高亮。

- [x] **Step 1: 为面经库添加入口事件**

在工具栏新增“URL解析职位”按钮，点击时触发 `open-job-search`。

- [x] **Step 2: 注册子路由**

让 `normalizeRoute`、`activeRoute`、权限映射、主导航选中态和页面布局识别 `/interviews/jobs`。

- [x] **Step 3: 挂载工作台**

在 App.vue 中渲染 `JobSearchWorkspace`，入口调用 `navigateTo('/interviews/jobs')`，返回调用 `navigateTo('/interviews')`。

- [x] **Step 4: 再次运行生产构建**

Run: `npm run build`

Expected: Vite build succeeds and `/interviews/jobs` remains inside authenticated app shell.

### Task 3: 浏览器视觉评审

**Files:**
- Modify if needed: `web-ui/src/components/JobSearchWorkspace.vue`

**Interfaces:**
- Consumes: 本地 Vite 页面与已有测试账户会话。
- Produces: 可供用户评审的 PC 端截图。

- [x] **Step 1: 打开 PC 视口**

访问 `/interviews/jobs`，确认顶部搜索、左侧至少 3 张卡片和右侧详情首屏同时可见。

- [x] **Step 2: 检查交互与视觉层级**

点击第二张卡片、重新搜索并加载更多，确认选中态与详情同步且页面不跳动。

- [x] **Step 3: 截图并根据视觉问题修订**

修正溢出、密度、对比度或按钮层级后，再运行 `npm run build`。
