# 工作流实时日志 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每次管理员手动工作流提供可持久回看、可断线续传的实时链路日志弹窗。

**Architecture:** PostgreSQL 保存结构化事件，工作流线程通过附加 logging Handler 写入事件，FastAPI 通过历史 JSON 接口和 SSE 增量接口输出，Vue 弹窗负责历史合并、实时追加和 Task 状态呈现。

**Tech Stack:** Python logging、FastAPI、SQLAlchemy/PostgreSQL、SSE、Vue 3、Node test、pytest。

**Spec:** `docs/superpowers/specs/2026-08-30-pipeline-live-logs-design.md`

## Global Constraints

- 页面标题与操作文案使用中文。
- 只实现 PC 端工作台，暂不做平板专项适配。
- 不引入新的运行时依赖。
- 保留工作区既有未提交修改。
- 日志接口仅允许当前 organization 的管理员访问。

---

### Task 1: 结构化事件存储

**Files:**
- Create: `migrations/versions/20260830_31_pipeline_execution_events.py`
- Modify: `src/platform_access/repository.py`
- Modify: `src/platform_access/service.py`
- Test: `tests/test_pipeline_execution_logs.py`

**Interfaces:**
- Produces: `append_pipeline_execution_event(...)`、`list_pipeline_execution_events(...)`、`get_manual_pipeline_request(...)`。

- [x] **Step 1: 写迁移与仓储失败测试**
- [x] **Step 2: 运行 `pytest tests/test_pipeline_execution_logs.py -q` 验证失败**
- [x] **Step 3: 实现迁移、事件写入、增量读取和归属查询**
- [x] **Step 4: 重跑测试并确认通过**

### Task 2: 工作流日志采集

**Files:**
- Create: `src/platform_access/pipeline_logs.py`
- Modify: `src/logging/logger_manager.py`
- Modify: `src/app/application.py`
- Modify: `src/tasks/base_task.py`
- Modify: `src/platform_access/pipeline_runner.py`
- Test: `tests/test_pipeline_execution_logs.py`

**Interfaces:**
- Consumes: Task 1 的事件写入接口。
- Produces: `PipelineExecutionLogHandler`，并为 Task 生命周期日志提供 `pipeline_event_type`、`task_name`、`task_run_id` 字段。

- [x] **Step 1: 添加线程隔离、级别过滤和生命周期映射测试**
- [x] **Step 2: 运行定向测试验证失败**
- [x] **Step 3: 实现 Handler 注入及 Runner 生命周期事件**
- [x] **Step 4: 重跑测试并确认通过**

### Task 3: 历史与 SSE 接口

**Files:**
- Modify: `src/platform_access/web.py`
- Test: `tests/test_pipeline_execution_logs.py`

**Interfaces:**
- Consumes: Task 1 的增量事件读取与运行归属查询。
- Produces: `/api/admin/pipeline-runs/{run_id}/logs` 和 `/logs/stream`。

- [x] **Step 1: 添加游标校验、历史返回和终态关闭测试**
- [x] **Step 2: 运行定向测试验证失败**
- [x] **Step 3: 实现历史接口、SSE 格式、心跳与终态退出**
- [x] **Step 4: 重跑测试并确认通过**

### Task 4: PC 日志弹窗

**Files:**
- Create: `web-ui/src/components/PipelineLogDialog.vue`
- Modify: `web-ui/src/components/ManualPipelinePanel.vue`
- Modify: `web-ui/src/styles.css`
- Modify: `web-ui/src/theme.css`
- Create: `web-ui/src/pipeline-live-logs.test.js`

**Interfaces:**
- Consumes: Task 3 的 JSON 与 SSE 接口。
- Produces: 每条运行记录的“查看日志”按钮和可释放连接的实时弹窗。

- [x] **Step 1: 添加按钮、接口路径、EventSource、去重和关闭清理契约测试**
- [x] **Step 2: 运行 `npm test -- --test-name-pattern=工作流实时日志` 验证失败**
- [x] **Step 3: 实现弹窗、任务链、日志列表和自动滚动**
- [x] **Step 4: 重跑前端测试并确认通过**

### Task 5: 文档与完整验证

**Files:**
- Modify: `docs/platform_access_operations_module.md`

**Interfaces:**
- Consumes: 前四项的最终接口和行为。
- Produces: 运维可查的事件表、接口和验证说明。

- [x] **Step 1: 更新模块设计目标、调用链、依赖和边界**
- [x] **Step 2: 运行 `pytest tests/test_pipeline_execution_logs.py -q`**
- [x] **Step 3: 运行 `npm test`**
- [x] **Step 4: 运行 `npm run build`**
- [x] **Step 5: 检查 `git diff --check` 和最终变更范围**
