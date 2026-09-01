# ZhiTan Clean-Room 开源安装与验收设计

> 状态：已通过 `grill-me` 决策访谈确认
>
> 日期：2026-09-02
>
> 目标分支：`master`

## 1. 背景

ZhiTan 的第一版开源文档是在已有开发环境中验证的，因此遗漏了干净克隆才会暴露的问题：

- 全量测试读取被 `.gitignore` 排除的第三方 `grill-me/SKILL.md`，GitHub clone 必然失败；
- 本地 Compose 固定旧容器名和数据卷名，可能碰撞或复用另一套项目的数据；
- README 创建虚拟环境后仍使用裸 `python`，依赖可能安装到系统解释器；
- 数据库启动、迁移、API、Worker 和 Web 缺少完整 readiness 链；
- 端口、环境变量和启动命令分散在多个文件中，无法安全并行运行两个 clone；
- 活跃文档中混有旧分支、旧 Skill 数量、固定管理员邮箱和历史生产状态。

开源发布不能以“维护者机器能运行”为标准。此设计把 clean-room 安装变成项目接口和 CI 发布门禁。

## 2. 目标

1. 新用户只需 Docker Compose 即可在 localhost 启动可登录的 ZhiTan 核心产品。
2. 无模型、邮件、Firecrawl、LangSmith 或媒体服务 Key 时，核心栈仍保持 ready。
3. 不同目录中的 ZhiTan clone 默认拥有独立容器、数据卷和可覆盖端口。
4. README、安装脚本、Compose 和 CI 使用同一套命令与配置契约。
5. GitHub tracked 文件足以通过测试、构建、迁移和真实服务 smoke；禁止依赖 ignored 或本机缓存。
6. Windows、Linux 和 macOS 都有明确且可执行的安装路径。
7. 原项目 `D:\MyPro\WechaOffiicialAccount` 及其运行中服务在实施和验收期间完全不受影响。

## 3. 非目标

- 不删除公众号内容流水线、Docling、Gotenberg、Caddy 或历史业务代码。
- 不在 Quickstart 中启用公网域名、自动 TLS、生产备份或全部媒体能力。
- 不捆绑许可证不允许再分发的第三方 Skill。
- 不提供公开默认管理员密码，也不通过命令行参数接收密码。
- 不承诺无 Key 时 AI、邮件、网页采集和云端文档能力可用。

## 4. 用户路径与文档分层

### 4.1 根 README

根 README 只承担：

- 项目定位与核心能力；
- 5 分钟 localhost Docker Quickstart；
- 首个管理员初始化；
- 可访问地址和 readiness 验证；
- 指向详细开发、生产、排障、贡献和安全文档的链接。

### 4.2 活跃文档

- `docs/getting-started.md`：完整首次启动、停止、保留数据与删除数据；
- `docs/local-development.md`：Windows PowerShell 与 Linux/macOS POSIX 两套源码开发流程；
- `docs/production-deployment.md`：域名、TLS、Secret、生产 Compose 和部署者责任；
- `docs/troubleshooting.md`：端口、Docker、迁移、虚拟环境、日志和 Provider 配置；
- `CONTRIBUTING.md`：复用本地开发脚本和完整验证命令。

历史设计、旧生产状态和内部交接材料保留，但移动到或明确标记为 archive，不进入当前安装导航。

## 5. localhost Docker Quickstart

### 5.1 服务拓扑

默认 Quickstart 只包含五类服务：

```text
Browser -> career-web -> career-api -> career-postgres
                           |
                           +-> shared application_data

career-postgres (healthy)
  -> career-migrate (completed successfully)
       -> career-api (ready)
       -> career-agent-worker (healthy)
            -> career-web (proxy readiness)
```

服务职责：

- `career-postgres`：`pgvector/pgvector:pg16`，仅在 Compose 私网中暴露 5432；
- `career-migrate`：复用 API 镜像执行 `alembic upgrade head`，成功后退出；
- `career-api`：FastAPI/Uvicorn，仅供内部 Nginx 访问；
- `career-agent-worker`：持久化 Turn Worker，与 API 共享数据库和凭据主密钥卷；
- `career-web`：Vue 静态站点与 Nginx 同源 API 代理，仅映射 localhost HTTP 端口。

默认不启动 Caddy、公众号 Scheduler、Gotenberg、Docling 和 Skill seed。它们由生产覆盖文件或可选 profile 启用。

### 5.2 Compose 分层

- `compose.yaml`：localhost 核心栈，是 Quickstart 的唯一基础事实来源；
- `compose.production.yaml`：Caddy、域名、TLS、生产策略与可选内容流水线覆盖；
- `compose.document.yaml`：Docling、Gotenberg 等重型文档能力。

不设置顶层固定项目名，不使用 `container_name`，不为数据卷指定全局 `name`。Compose 根据当前目录或显式 `COMPOSE_PROJECT_NAME` 隔离资源。

### 5.3 端口

Quickstart 默认只映射：

```dotenv
ZHITAN_HTTP_PORT=18081
```

PostgreSQL、API 和 Worker 不映射宿主端口。源码开发另行支持：

```dotenv
CAREER_POSTGRES_PORT=54329
PREVIEW_SERVER_PORT=18080
VITE_PORT=5173
VITE_API_PROXY_TARGET=http://127.0.0.1:18080
```

开发脚本检测占用并输出最终端口；并行实例必须同时更新数据库端口与 `CAREER_DATABASE_URL`，前端代理必须指向本实例 API。

### 5.4 Secret

Quickstart/setup 脚本从公开 example 生成被 Git 忽略的实例环境文件，并创建 URL-safe 随机值：

- PostgreSQL 密码；
- 邮箱验证码 HMAC Secret；
- 凭据主密钥或其自动托管卷。

生产 Secret 由部署者通过 Secret 管理系统显式提供。示例值不得被描述为生产可用。

## 6. 无外部 Key 启动契约

未配置外部 Key 时：

- PostgreSQL、migration、API、Worker 和 Web 必须 ready；
- 管理员初始化、密码登录、导航和配置页面可用；
- 模型、邮件注册、密码找回、Firecrawl、云端视觉和观测入口显示“尚未配置”；
- 真正调用缺失 Provider 时返回可操作错误；
- 不伪造成功、不自动选择未知 Provider、不让 Worker 退出。

localhost 默认关闭公开注册和邮件验证码入口，使用 CLI bootstrap 创建首个管理员。

## 7. 首次管理员体验

当系统没有管理员时：

1. 前端读取 bootstrap status；
2. 显示独立的“初始化管理员”引导状态；
3. 根据 Docker、PowerShell 或 POSIX 环境展示交互式命令；
4. 隐藏当前必然失败的注册和找回密码入口；
5. 管理员在终端输入密码，脚本不接受密码命令行参数；
6. 初始化完成后用 `PLATFORM_ADMIN_EMAIL` 与密码登录。

CI 使用专门的 ephemeral integration helper，通过 stdin 接收随机密码，完成真实 HTTP 登录后销毁整个 Compose project 和数据卷。该 helper 不作为生产入口。

## 8. Health 与 readiness

- `/api/health`：只表示 API 进程存活；
- `/api/ready`：验证数据库连接、迁移处于 head、关键持久目录可写；
- Worker healthcheck：验证主进程存活和数据库 `SELECT 1`；
- Web healthcheck：通过 Nginx 请求 `/api/ready`，覆盖 Web、代理、API 和数据库链路；
- PostgreSQL healthcheck：`pg_isready`；
- migration：一次性成功状态是 API/Worker 的启动前置条件。

CI 不能只依赖 HTTP 200；还要确认 migration exit 0、Worker restart count 为 0、日志无 Traceback。

## 9. 本地开发脚本

提供并由 CI 直接执行：

- `scripts/setup_dev.ps1`；
- `scripts/setup_dev.sh`；
- clean-room/Quickstart smoke 脚本。

脚本负责：

- 检查 Python、Node、npm、Docker Compose 版本；
- 创建并始终使用项目内 `.venv`；
- 安装锁定依赖；
- 生成本地实例配置；
- 检查端口冲突；
- 等待 PostgreSQL healthy 后执行迁移；
- 输出 API、Worker 和 Web 的启动命令与访问地址。

README 同时提供等价手动步骤，但推荐路径是受 CI 测试的脚本。

## 10. 依赖可复现性

- `requirements*.txt` 保留维护者声明的兼容范围；
- 增加跨支持 Python 版本验证的锁定依赖文件，普通用户、Docker 和 CI 默认安装锁定版本；
- `package-lock.json` 继续作为前端依赖事实来源；
- 更新依赖必须由单独 PR/提交完成并触发完整 clean-room CI。

## 11. Skill 许可证边界

- 公开 `deploy/skill-seeds/catalog.manifest.json` 默认 `total: 0`；
- 测试不得读取 ignored 或本机插件缓存；
- Skill runtime 测试使用仓库自有 inline fixture 或明确允许分发的项目 fixture；
- 只有许可证明确、完整保留许可证文本的 Skill 才能随仓库发布；
- 用户私有导出只用于本地/私有部署，不进入 Git。

## 12. CI 与发布门禁

`master` 的必过检查：

1. Python 全量测试；
2. Web 全量测试；
3. 浏览器扩展测试；
4. 前端生产构建；
5. Compose 配置展开；
6. 核心镜像构建；
7. 唯一 Compose project 的 clean-room 启动；
8. migration/head 校验；
9. `/api/health`、`/api/ready` 和 Web 代理验证；
10. bootstrap pending、认证 401、临时管理员真实登录；
11. Worker 状态、重启次数和错误日志检查；
12. ignored/untracked 依赖检测与敏感信息扫描；
13. 只清理本次 CI project 和数据卷。

真实 Provider、DNS、TLS 和邮件发送放入手动 workflow，不作为无凭据 PR 的强制门禁。

## 13. Clean-room 最终验收

实施完成并推送 `master` 后：

1. 记录原项目容器、数据卷、端口与进程状态；
2. 严格验证目标为 `D:\MyPro\ZHITANTEST`；
3. 清除该目录中的 clone、`.venv`、`node_modules`、`.env` 和构建产物；
4. 从 GitHub `master` 重新 clone；
5. 逐条执行新 README Quickstart；
6. 验证新 Compose labels、容器、卷和端口只属于 ZHITANTEST；
7. 验证迁移、readiness、Worker、Web 代理和管理员登录；
8. 执行所有文档列出的测试与构建命令；
9. 再次确认原项目状态未改变；
10. 默认停止测试实例但保留数据，除非用户明确选择 `down -v`。

## 14. 安全与破坏性操作边界

- 不停止、重建、重命名或删除原项目容器；
- 不连接原项目 PostgreSQL 执行迁移；
- 不复用原项目数据卷、`.env`、虚拟环境或 `node_modules`；
- 清理命令必须指定经过解析验证的 ZHITANTEST 绝对路径或唯一 Compose project；
- README 区分 `down`（保留数据）与 `down -v`（永久删除当前实例数据）；
- 任何真实 Key 只进入被忽略的实例环境或 Secret 管理系统。

## 15. 完成标准

以下条件全部满足才算完成：

- 设计、实现、文档和 CI 已提交并推送 `ZhiTan/master`；
- GitHub clean checkout 不依赖本机或 ignored 文件；
- Quickstart 五服务栈在独立 Compose project 中 ready；
- 无外部 Key 时可初始化管理员并登录；
- 全量测试、构建、迁移和 smoke 全绿；
- README 和详细文档命令经过实际执行；
- 原项目文件、容器、卷、端口和数据未被修改；
- 最终结果与已知非阻断限制被记录。
