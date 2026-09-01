# 线上笔试 LeetCode 兼容与助手展示开关 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 LeetCode 中文站题面被识别为 generic、缺少函数接口而无法生成答案的问题，并让管理员用一个开关控制面试大师与线上笔试助手对普通用户的展示。

**Architecture:** 扩展采集层增加 LeetCode 主域名识别、编辑器代码候选与函数签名提取；后端确定性清洗层从 starter code 补全函数签名，模型仍负责题意复核。现有 `route_module_settings` 增加一个 feature scope 配置，前端同时用它控制求职助手入口和两个直达路由；管理员保持可见可访问。

**Tech Stack:** Chrome Extension Manifest V3、Vue 3、FastAPI/Pydantic、PostgreSQL JSONB 配置、Node test、pytest

**Spec:** `docs/superpowers/specs/2026-09-01-online-assessment-assistant-design.md`

## Global Constraints

- 只读识别和分析，不向笔试平台自动填写或提交答案。
- PC 端优先，不做平板专项适配。
- “面试大师与笔试助手”使用一个共享开关，默认开启。
- 普通用户关闭后隐藏入口并禁止直达，管理员不受展示开关限制。
- 复用现有 `route_module_settings`、导航 API 和管理台样式，不新增表。
- 本次只做本地修改、测试和构建，不部署生产。

---

### Task 1: LeetCode 题面与函数签名采集

**Files:**
- Modify: `browser-extension/job-library/assessment-capture.js`
- Test: `browser-extension/job-library/tests/assessment-capture.test.mjs`
- Modify: `src/career_assistant/online_assessment/problem_extractor.py`
- Test: `tests/test_online_assessment_contracts.py`

**Interfaces:**
- Consumes: 页面 `location.hostname`、可见编辑器 DOM、`CapturedProblemInput.starter_code`。
- Produces: `detectAssessmentPlatform(hostname): string`、`extractFunctionSignature(starterCode): string`，以及带 `interface_kind=function` 的 `AssessmentProblem`。

- [x] **Step 1: 写出失败测试**

```javascript
assert.equal(detectAssessmentPlatform('leetcode.cn'), 'leetcode')
assert.match(extractFunctionSignature('class Solution:\n    def groupAnagrams(self, strs):'), /groupAnagrams/)
```

```python
problem = normalize_capture(CapturedProblemInput(
    source_platform="leetcode",
    visible_text="给定字符串数组，将字母异位词组合在一起。示例输入与输出完整。",
    starter_code="class Solution:\n    def groupAnagrams(self, strs):",
))
assert problem.interface_kind is InterfaceKind.FUNCTION
assert "groupAnagrams" in problem.function_signature
```

- [x] **Step 2: 运行定向测试并确认失败**

Run: `node --test browser-extension/job-library/tests/assessment-capture.test.mjs`

Run: `python -m pytest tests/test_online_assessment_contracts.py -q`

Expected: 主域名识别或 starter code 签名补全断言失败。

- [x] **Step 3: 实现纯函数和采集回退**

```javascript
export function detectAssessmentPlatform(hostname = '') {
  const host = String(hostname).trim().toLowerCase()
  if (host === 'leetcode.com' || host.endsWith('.leetcode.com') || host === 'leetcode.cn' || host.endsWith('.leetcode.cn')) return 'leetcode'
  if (host === 'hackerrank.com' || host.endsWith('.hackerrank.com')) return 'hackerrank'
  return 'generic'
}
```

编辑器采集依次读取可见 textarea、Monaco view lines 和 CodeMirror；函数签名统一由 `extractFunctionSignature` 返回。后端在 `capture.function_signature` 为空时从 `starter_code` 提取 Python/JavaScript/Java/C++ 方法签名，并据此移除“无法判断接口/缺少函数签名”的硬阻断。

- [x] **Step 4: 运行定向测试并确认通过**

Run: `node --test browser-extension/job-library/tests/assessment-capture.test.mjs`

Run: `python -m pytest tests/test_online_assessment_contracts.py -q`

Expected: PASS。

### Task 2: 旧扩展恢复和重新检测入口

**Files:**
- Modify: `browser-extension/job-library/service-worker.js`
- Modify: `web-ui/src/components/OnlineAssessmentAssistantPage.vue`
- Modify: `web-ui/src/online-assessment/bridge.js`
- Test: `browser-extension/job-library/tests/assessment-capture.test.mjs`
- Test: `web-ui/src/online-assessment-view.test.js`

**Interfaces:**
- Consumes: `assessmentBridge.ping()` 和固定下载地址 `/downloads/find-job-boss-helper-v0.3.1.zip`。
- Produces: `retryExtensionDetection()`，以及安装、更新、浏览器启动后的幂等 bridge 注入。

- [x] **Step 1: 写出失败测试**

断言 Service Worker 同时监听 `onInstalled` 与 `onStartup`，页面包含“下载最新版扩展”和“重新检测”，并继续展示检测到的版本。

- [x] **Step 2: 运行扩展与页面测试并确认失败**

Run: `node --test browser-extension/job-library/tests/*.test.mjs`

Run: `npm test -- --test-name-pattern="online assessment"`（目录 `web-ui`）

Expected: 缺少启动重注入或恢复按钮断言失败。

- [x] **Step 3: 实现恢复流程**

在 `onInstalled` 和 `onStartup` 调用同一个 `injectBridgeIntoOpenAppTabs`。页面检测失败时呈现当前版本、下载链接、安装说明链接和“重新检测”；重新检测成功后保留 launch id 并继续领取题面。

- [x] **Step 4: 运行扩展与页面测试并确认通过**

Run: `node --test browser-extension/job-library/tests/*.test.mjs`

Run: `npm test -- --test-name-pattern="online assessment"`（目录 `web-ui`）

Expected: PASS。

### Task 3: 管理台共享展示开关

**Files:**
- Modify: `src/platform_access/navigation_config.py`
- Test: `tests/test_navigation_config.py`
- Modify: `web-ui/src/navigation-access.js`
- Test: `web-ui/src/navigation-access.test.js`
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/components/AdminConsolePage.vue`
- Modify: `web-ui/src/components/CareerAssistantPage.vue`
- Test: `web-ui/src/online-assessment-view.test.js`
- Modify: `docs/platform_access_operations_module.md`

**Interfaces:**
- Consumes: `GET /api/navigation/modules` 与 `PUT /api/admin/navigation-modules` 的现有 item 列表/布尔映射。
- Produces: key `career_interview_tools`，元数据 `scope=feature`、`parent_key=career_assistant`，前端 computed `careerInterviewToolsVisible`。

- [x] **Step 1: 写出失败测试**

```python
items = route_modules_for_ui({"career_interview_tools": False}, PlatformRole.MEMBER)
feature = next(item for item in items if item["key"] == "career_interview_tools")
assert feature["accessible"] is False
```

前端断言普通用户在 feature false 时不能访问两个直达路由，并断言管理台有“求职助手子功能”和“面试大师与笔试助手”。

- [x] **Step 2: 运行导航定向测试并确认失败**

Run: `python -m pytest tests/test_navigation_config.py -q`

Run: `npm test -- --test-name-pattern="navigation|online assessment"`（目录 `web-ui`）

Expected: 新 feature key 或展示控制断言失败。

- [x] **Step 3: 实现共享开关**

在后端目录增加默认开启的 feature definition。管理台把顶级路由与求职助手子功能分组，但仍一次保存完整布尔映射。`App.vue` 对两个子路由同时校验父模块和 feature；`CareerAssistantPage.vue` 通过布尔 prop 同时隐藏两个按钮。

- [x] **Step 4: 运行导航定向测试并确认通过**

Run: `python -m pytest tests/test_navigation_config.py -q`

Run: `npm test -- --test-name-pattern="navigation|online assessment"`（目录 `web-ui`）

Expected: PASS。

### Task 4: 文档与全量验证

**Files:**
- Modify: `docs/modules/online-assessment-assistant.md`
- Modify: `docs/platform_access_operations_module.md`

**Interfaces:**
- Consumes: Task 1-3 的最终调用链和测试结果。
- Produces: LeetCode 兼容边界、扩展恢复方式、共享开关行为和验证结果的项目记录。

- [x] **Step 1: 更新模块文档**

记录 `leetcode.cn` 主域名修复、starter code 签名回退、扩展启动重注入，以及 `career_interview_tools` 的默认值和管理员例外。

- [x] **Step 2: 运行后端全量测试**

Run: `python -m pytest -q`

Expected: 全部 PASS。

- [x] **Step 3: 运行 WebUI 全量测试和构建**

Run: `npm test`（目录 `web-ui`）

Run: `npm run build`（目录 `web-ui`）

Expected: 全部 PASS，Vite build 成功。

- [x] **Step 4: 运行扩展全量测试和重新打包**

Run: `node --test browser-extension/job-library/tests/*.test.mjs`

Run: `python scripts/package_boss_extension.py`

Expected: 扩展测试 PASS，生成 `web-ui/public/downloads/find-job-boss-helper-v0.3.1.zip`。

- [x] **Step 5: PC 页面验收**

在本地页面验证管理台开关保存、求职助手入口隐藏/显示、两个直达路由阻止/恢复，以及笔试页旧扩展恢复面板。不得连接或部署生产。
