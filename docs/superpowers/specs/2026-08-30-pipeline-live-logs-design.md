# 工作流实时日志设计

## 目标

在工作台的每条手动工作流运行记录中提供“查看日志”入口。管理员打开弹窗后能先看到已经产生的链路日志，再持续收到当前运行的新日志；运行结束后日志仍可回看。

## 设计决策

- PC 端先行，不做平板专项适配。
- 使用 PostgreSQL 持久化结构化事件，不解析全局 `logs/app.log`。
- 使用 SSE 单向推送，复用项目已有的持久事件、游标续传和心跳模式，不引入 WebSocket 或新依赖。
- 只采集当前手动工作流线程的 `INFO`、`WARNING`、`ERROR`、`CRITICAL` 日志，并额外记录工作流与 Task 生命周期事件。
- 日志接口继续使用现有管理员会话校验；查询必须同时校验运行记录所属 organization。
- 浏览器不接收 Prompt、模型完整响应、API Key 或堆栈原文。现有业务日志原样展示，顶层失败仅保存既有安全摘要。

## 数据模型

新增 `career_assistant.pipeline_execution_events`：

- `id BIGSERIAL PRIMARY KEY`：同时充当稳定 SSE 游标。
- `execution_request_id UUID NOT NULL`：关联 `pipeline_execution_requests(id)`，删除运行记录时级联删除。
- `event_type TEXT NOT NULL`：`run_started`、`task_started`、`log`、`task_succeeded`、`task_failed`、`run_succeeded`、`run_failed`。
- `level TEXT NOT NULL`：日志级别。
- `task_name TEXT`、`task_run_id TEXT`：可为空的 Task 关联。
- `message TEXT NOT NULL`：面向管理员的日志正文。
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`。

按 `(execution_request_id, id)` 建索引，所有增量查询使用 `id > after_id ORDER BY id ASC`。

## 后台采集

`ManualPipelineRunner` 在后台线程启动后创建本次运行的事件写入器，并将一个专属 `logging.Handler` 交给 `Application`。`LoggerManager` 初始化控制台和文件 Handler 后附加该 Handler。Handler 只接受创建它的线程产生的 `INFO` 及以上记录，避免把 API 请求或其他后台任务混入本次日志。

`BaseTask` 的创建、开始、成功和失败日志增加结构化 `extra` 字段，Handler 据此生成 Task 生命周期事件；普通业务日志生成 `log` 事件。工作流开始、成功和失败由 Runner 显式写入，Runner 在写入终态事件后再更新运行请求状态。

## API

- `GET /api/admin/pipeline-runs/{run_id}/logs?after_id=0&limit=500`：返回按事件 ID 正序排列的历史/增量事件，以及当前运行状态。
- `GET /api/admin/pipeline-runs/{run_id}/logs/stream?after_id=0`：返回 SSE。服务端轮询持久事件，发送 `id`、`event` 和 JSON `data`；无事件时发送心跳；运行进入终态且已无待发送事件后关闭。

浏览器先调用历史接口，再以最后一个事件 ID 建立 SSE。断线时使用最新游标重连，事件 ID 去重。

## 前端交互

每条运行记录增加“查看日志”按钮。弹窗标题显示短运行 ID 和实时状态，主体包含 Task 状态条、连接状态和日志列表。打开后读取历史并建立 SSE；用户位于底部时自动滚动，向上浏览时不抢动滚动位置；关闭弹窗时释放连接和重连定时器。

## 验证

- 仓储测试覆盖 organization 隔离、事件顺序、游标和数量限制。
- Handler 测试覆盖线程过滤、日志级别和结构化 Task 字段。
- API 测试覆盖管理员鉴权、历史读取、SSE 心跳/终止与非法游标。
- 前端测试覆盖按钮、弹窗、历史加载、EventSource、去重、自动滚动和卸载清理。
- 运行后端定向测试、前端全部 Node 测试及 Vite 构建。
