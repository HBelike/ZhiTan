# 职位库一级导航 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已有职位库从面经库内按钮入口调整为独立的左侧一级菜单，同时保留 `/interviews/jobs` 路由和全部职位检索能力。

**Architecture:** 后端顶级模块目录新增 `job_library`，使管理台开关与角色访问判定独立；前端导航用同一模块键渲染菜单并精确映射路由。面经库与职位库组件删除彼此跳转入口，不改浏览器助手、搜索和详情数据链路。

**Tech Stack:** Vue 3、JavaScript、Python、unittest

**Spec:** `docs/job_library_module.md`

## Global Constraints

- 左侧一级菜单名称必须为“职位库”，位于“面经库”之后。
- 页面标题、导航名称和操作标题使用中文。
- 保留现有 `/interviews/jobs` 路由，避免历史链接失效。
- 不修改职位搜索、浏览器助手或 BOSS 数据处理逻辑。
- 仅验证 PC 端，本次不做平板专项适配。

---

### Task 1: 独立模块目录与访问归属

**Files:**
- Modify: `tests/test_navigation_config.py`
- Modify: `src/platform_access/navigation_config.py`

**Interfaces:**
- Consumes: `route_modules_for_ui(value, role) -> list[dict[str, object]]`
- Produces: 模块键 `job_library`，路径 `/interviews/jobs`，默认启用且非管理员专属。

- [x] **Step 1: 写入失败测试**

```python
def test_default_catalog_contains_job_library_as_top_level_module(self) -> None:
    modules = route_modules_for_ui(None, PlatformRole.ADMIN)
    by_key = {item["key"]: item for item in modules}
    self.assertEqual(len(modules), 9)
    self.assertEqual(by_key["job_library"]["path"], "/interviews/jobs")
    self.assertFalse(by_key["job_library"]["admin_only"])
```

- [x] **Step 2: 运行测试并确认失败**

Run: `python -m unittest tests.test_navigation_config.NavigationConfigTests.test_default_catalog_contains_job_library_as_top_level_module -v`

Expected: FAIL，目录中尚无 `job_library`。

- [x] **Step 3: 添加最小模块定义**

在 `interview_library` 后添加 `job_library`，字段固定为：`label="职位库"`、`path="/interviews/jobs"`、`admin_only=False`、`locked=False`。

- [x] **Step 4: 运行导航配置测试**

Run: `python -m unittest tests.test_navigation_config -v`

Expected: PASS。

### Task 2: 前端一级菜单与精确路由

**Files:**
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/components/InterviewLibraryPage.vue`
- Modify: `web-ui/src/components/JobSearchWorkspace.vue`

**Interfaces:**
- Consumes: 后端模块键 `job_library`。
- Produces: 独立“职位库”菜单；`/interviews` 与 `/interviews/jobs` 分别归属不同模块。

- [x] **Step 1: 新增职位库导航项**

在 `appNavItems` 的面经库之后添加 `{ moduleKey: 'job_library', label: '职位库', icon: '⌕', path: '/interviews/jobs', enabled: true }`。

- [x] **Step 2: 拆分高亮与权限映射**

`isAppNavActive` 对两个模块使用路径精确匹配；`navItemForRoute` 先匹配 `/interviews/jobs`，再匹配 `/interviews`。

- [x] **Step 3: 移除页面间按钮事件**

删除 `InterviewLibraryPage` 的 `open-job-search` emit 和“URL解析职位”按钮；删除 `JobSearchWorkspace` 的 `back` emit、“返回面经库”按钮及其样式；App 不再监听两个事件。

- [x] **Step 4: 构建前端**

Run: `npm run build --prefix web-ui`

Expected: PASS，Vue 模板与模块打包无错误。

### Task 3: 文档与 PC 验证

**Files:**
- Modify: `docs/job_library_module.md`
- Modify: `docs/career_assistant_module.md`

**Interfaces:**
- Consumes: 已完成的一级菜单与独立模块配置。
- Produces: 与真实入口、调用链和验证结果一致的维护文档。

- [x] **Step 1: 更新模块边界**

将职位库描述改为独立一级模块，记录菜单位置、路由、独立管理台开关，以及不再从面经库进入。

- [x] **Step 2: 运行完整相关验证**

Run: `python -m unittest tests.test_navigation_config -v`

Run: `npm run build --prefix web-ui`

Expected: 全部 PASS。

- [x] **Step 3: 检查 PC 页面**

验证左侧“面经库”“职位库”可独立高亮；职位库页面无返回面经库按钮；面经库页面无 URL 解析职位按钮。
