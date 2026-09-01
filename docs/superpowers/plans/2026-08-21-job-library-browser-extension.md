# 职位库浏览器助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让职位库只通过用户已登录的 Chrome 会话获取 BOSS 真实岗位列表和每次点击时的最新岗位详情。

**Architecture:** 网页通过 Content Script 向 Chrome MV3 Service Worker 发送三个白名单动作；Service Worker 在 `www.zhipin.com` 页面 MAIN world 中执行同源只读请求，并通过独立归一化模块返回稳定字段。页面不保存登录凭据，不设置模拟岗位兜底。

**Tech Stack:** Vue 3、Chrome Manifest V3、Content Script、Service Worker、`chrome.tabs`、`chrome.scripting`、Node.js 内置测试运行器。

**Spec:** `docs/superpowers/specs/2026-08-21-boss-job-search-ui-design.md`

## Global Constraints

- 页面名称统一为“职位库”，子路由保持 `/interviews/jobs`。
- 用户唯一必填项是岗位名称，城市固定上海 `101020100`。
- 每次点击岗位卡片都必须执行新的详情请求。
- 只实现读取，不实现投递、沟通、批量触达或 Cookie 导出。
- 登录、验证码和风险控制必须停止当前调用并提示人工处理。
- 正式页面不展示模拟岗位。

---

### Task 1: 稳定字段与错误契约

**Files:**
- Create: `browser-extension/job-library/boss-data.js`
- Create: `browser-extension/job-library/tests/boss-data.test.mjs`
- Create: `browser-extension/job-library/package.json`

**Interfaces:**
- Produces: `buildSearchEndpoint(query, page, city)`、`buildDetailEndpoint(securityId)`、`normalizeSearchResponse(response, context)`、`normalizeDetailResponse(response, fallback)`、`classifyBossFailure(result)`。

- [x] **Step 1: 写列表、详情、端点和访问限制测试**

测试固定验证中文关键词编码、页码上限、`securityId`、列表字段、详情正文、公司信息、验证码和 `code=37`。

- [x] **Step 2: 运行测试确认契约成立**

Run: `cd browser-extension/job-library; npm test`

Expected: 4 tests pass.

### Task 2: Chrome MV3 只读通道

**Files:**
- Create: `browser-extension/job-library/manifest.json`
- Create: `browser-extension/job-library/content-script.js`
- Create: `browser-extension/job-library/service-worker.js`

**Interfaces:**
- Consumes: `boss-data.js` 稳定函数。
- Produces: `ping`、`search_jobs`、`get_job_detail` 三种消息动作。

- [x] **Step 1: 声明最小扩展权限与站点范围**

Manifest 只声明 `tabs`、`scripting`，Host 范围只覆盖项目本地地址、正式域名和 `www.zhipin.com`。

- [x] **Step 2: 实现网页消息桥**

Content Script 校验同窗口、同 Origin、Channel、Request ID 和动作白名单，再转发到 Service Worker。

- [x] **Step 3: 实现 BOSS 页面同源请求**

Service Worker 串行执行请求并保留 700ms 间隔；复用现有 BOSS Tab，没有时创建非激活 Tab；MAIN world 只允许列表和详情两个路径。

- [x] **Step 4: 运行静态验证**

Run: `node --check service-worker.js; node --check content-script.js`

Expected: both commands exit 0 and Manifest parses as JSON.

### Task 3: 职位库真实数据状态

**Files:**
- Create: `web-ui/src/job-library-bridge.js`
- Modify: `web-ui/src/components/JobSearchWorkspace.vue`
- Modify: `web-ui/src/App.vue`

**Interfaces:**
- Consumes: 扩展三个动作。
- Produces: 单输入搜索、分页、每次点击刷新详情、空状态、安装状态和访问限制状态。

- [x] **Step 1: 实现带超时的网页桥**

每次调用生成唯一 Request ID，只接受当前 Origin 且 ID 匹配的响应；扩展未安装时 1.8 秒内进入安装状态。

- [x] **Step 2: 移除所有预览岗位**

组件初始 `jobs=[]`；只有 `search_jobs` 成功响应能创建岗位卡片。

- [x] **Step 3: 实现详情强制刷新与竞态丢弃**

`selectJob(job)` 每次调用 `getJobDetail(job)`，递增 `detailSequence`，迟到响应不能覆盖当前岗位。

- [x] **Step 4: 验证构建**

Run: `cd web-ui; npm run build`

Expected: Vite build succeeds with 35 transformed modules.

### Task 4: 安装与维护文档

**Files:**
- Create: `browser-extension/job-library/README.md`
- Create: `docs/job_library_module.md`
- Modify: `docs/career_assistant_module.md`

**Interfaces:**
- Produces: Chrome 首次安装步骤、权限解释、调用链、接口漂移维护点和验证命令。

- [x] **Step 1: 记录一次性安装步骤**

用户在 `chrome://extensions` 加载 `browser-extension/job-library` 并登录 BOSS；日常页面不再要求扩展操作。

- [x] **Step 2: 记录实现边界与验证结果**

文档明确内部接口会漂移、当前不含模拟岗位、真实端到端需要已安装扩展和用户登录会话。
