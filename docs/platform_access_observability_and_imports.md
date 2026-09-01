# 平台访问控制、可观测性与多文件面经导入

## 目标

本模块在不改动公众号内容流水线与技能库既有职责的前提下，为个人平台补齐三项上线基础能力：账号登录与管理员路由控制、隐私优先的 LangSmith 可观测性、以及可选择归并或拆分的多文件面经导入。

## 技术取舍

- 使用 PostgreSQL 保存账号、会话、运行配置版本和手动运行记录；浏览器仅持有 HttpOnly 会话 Cookie，不保存密码或 API Key。
- 管理员可修改内容流水线的项目数量、检索关键词与生成提示词；手动执行经由单例后台执行器启动，避免 HTTP 请求被外部模型调用长期占用。
- LangSmith 仅写入 Provider、模型名、请求数量、字符数、Token 用量和调用结果长度。简历、面经正文、附件、完整对话和密钥均不上传。
- LangSmith 控制台不嵌入工作台。LangSmith 的 CSP/X-Frame-Options 会拒绝第三方 iframe，因此可观测性页仅展示连接状态，并在新标签页打开官方控制台。
- 面经文件原件只在解析任务内短暂使用；持久化内容是解析后的 Markdown、来源、标签和索引。多文件模式可选“归并为一份”或“每份独立入库”。

## 调用链

```text
LoginPage
  -> /api/auth/bootstrap | /api/auth/login
  -> platform_session Cookie
  -> App.vue 根据角色显示路由

AdminConsolePage
  -> /api/admin/pipeline-config
  -> PipelineConfigVersion

ManualPipelinePanel（首页，仅管理员）
  -> /api/admin/pipeline-runs
  -> ManualPipelineRunner
  -> Application.run_manual_pipeline()

DeepSeekProvider.chat()
  -> trace_llm_call(metadata_only)
  -> LangSmith（可选）
  -> OpenAI-compatible Chat Completions

InterviewLibraryPage
  -> parse-file-stream（逐文件临时解析）
  -> 合并 Markdown / 独立 Markdown
  -> /experiences（1 次或 N 次）
  -> PostgreSQL + pgvector 索引
```

## 依赖与配置

- Python：`langsmith>=0.6.0,<1.0.0`。
- 开启观测：服务端配置 `LANGSMITH_API_KEY`、`LANGSMITH_TRACING=true`，可选 `LANGSMITH_PROJECT` 和 `LANGSMITH_UI_URL`。
- 开发后端唯一入口：`scripts\start_dev_backend.ps1`，固定端口 `18080`；前端 Vite 代理同样指向该端口。

## 验证

- `\.venv\Scripts\python.exe -m compileall -q src`：验证 Python 源码可编译。
- `cd web-ui; npm run build`：验证 Vue 登录页、管理员台、可观测性页和多文件导入页面可打包。
- `http://127.0.0.1:18080/api/health`：确认浏览器没有连接旧端口。

## 当前边界与后续

- 当前 Career/面经 API 仍沿用既有请求身份策略；下一阶段应将其映射到 `platform_session`，把管理员、操作员、只读角色落实到每一条业务接口。
- 多文件“独立入库”时，当前表单展示第一份解析草稿供核验；后续可增加逐文件切换页签，支持分别修订元数据。
- 外部平台网页、职位信息与登录态采集必须遵循平台条款；不通过伪装或绕过反爬策略获得受限内容。
