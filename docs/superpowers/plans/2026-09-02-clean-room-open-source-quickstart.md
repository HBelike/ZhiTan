# ZhiTan Clean-Room Open-Source Quickstart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让公开仓库的新 clone 在不依赖维护者机器、ignored 文件或外部 Provider Key 的前提下，通过一套可复制的 Docker Quickstart 完成启动、迁移、首位管理员初始化、真实登录和全量验收。

**Architecture:** `compose.yaml` 定义 Postgres → migration → API/Worker → Web 的五服务核心栈，生产和文档处理能力使用覆盖文件扩展；应用以 `/api/health` 表示进程存活、以 `/api/ready` 表示数据库/迁移/持久目录就绪；脚本、README 和 CI 复用同一个 `.env.quickstart` 与验证入口。

**Tech Stack:** Python 3.12/3.13、FastAPI、SQLAlchemy、Alembic、PostgreSQL/pgvector、Vue 3、Vite、Nginx、Docker Compose、GitHub Actions、pytest、Node test runner。

**Spec:** [`docs/superpowers/specs/2026-09-02-clean-room-open-source-quickstart-design.md`](../specs/2026-09-02-clean-room-open-source-quickstart-design.md)

## Global Constraints

- 只在 `D:\MyPro\ZhiTan` 的 `master` 分支修改并提交；不修改 `D:\MyPro\WechaOffiicialAccount`。
- 在最终验收前，不停止、重建、重命名或删除原项目的容器、卷和进程。
- 清理只允许针对已解析并二次确认的 `D:\MyPro\ZHITANTEST`，或显式 `COMPOSE_PROJECT_NAME=zhitantest` 的 Compose 资源。
- 默认 Quickstart 端口固定为 `ZHITAN_HTTP_PORT=18081`；PostgreSQL、API、Worker 不映射宿主端口。
- 默认无 DeepSeek、OpenAI、Firecrawl、邮件、LangSmith、Docling、Gotenberg 等 Key 也必须 ready。
- 所有行为改动先写失败测试，再写最小实现，再运行聚焦测试和相关回归。
- 每个任务完成后同步更新设计目标、调用链、验证结果和边界；提交只包含当前任务相关文件。

---

## Task 1: 固化公开仓库文件边界，消除本机 Skill 依赖

**Files:**

- Modify: `tests/test_career_skill_runtime.py`
- Modify: `deploy/skill-seeds/catalog.manifest.json`
- Modify: `.gitignore`
- Create: `tests/fixtures/skills/minimal-public-skill/SKILL.md`
- Create: `tests/test_public_checkout_contract.py`

- [ ] **Step 1: 写出 clean checkout 契约测试**

  在 `tests/test_public_checkout_contract.py` 中读取 `git ls-files`，断言所有测试读取的 fixture 都是 tracked 文件，并断言公开 Skill manifest 的 `total` 与实际 tracked `deploy/skill-seeds/*/SKILL.md` 数量一致。核心接口：

  ```python
  def tracked_paths(project_root: Path) -> set[str]:
      completed = subprocess.run(
          ["git", "ls-files", "-z"],
          cwd=project_root,
          check=True,
          capture_output=True,
      )
      return {item for item in completed.stdout.decode().split("\0") if item}
  ```

- [ ] **Step 2: 运行测试并确认它暴露 `grill-me` 依赖**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_career_skill_runtime.py tests/test_public_checkout_contract.py -q`

  Expected: FAIL，错误只指向 ignored/untracked `deploy/skill-seeds/grill-me/SKILL.md` 或 manifest 数量不一致。

- [ ] **Step 3: 改用仓库自有最小 fixture**

  将 runtime 测试的输入改为 `tests/fixtures/skills/minimal-public-skill/SKILL.md`。fixture 只包含测试 parser/runtime 所需的公开原创内容，不复制第三方 Skill 文本。将公开 manifest 设置为：

  ```json
  {
    "version": 1,
    "total": 0,
    "skills": []
  }
  ```

  `.gitignore` 继续忽略本地导出的 `deploy/skill-seeds/*`，但显式允许 manifest 和测试 fixture。

- [ ] **Step 4: 验证公开文件契约与 Python 全量测试**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_career_skill_runtime.py tests/test_public_checkout_contract.py -q`

  Expected: PASS。

  Run: `.\.venv\Scripts\python.exe -m pytest -q`

  Expected: PASS，且没有读取任何用户级 `.codex`、`.agents` 或 ignored Skill。

- [ ] **Step 5: 提交**

  ```powershell
  git add .gitignore deploy/skill-seeds/catalog.manifest.json tests/fixtures/skills/minimal-public-skill/SKILL.md tests/test_career_skill_runtime.py tests/test_public_checkout_contract.py
  git commit -m "test: enforce public checkout boundary"
  ```

---

## Task 2: 建立 liveness/readiness 契约

**Files:**

- Create: `src/web/readiness.py`
- Modify: `src/web/api.py`
- Create: `tests/test_web_readiness.py`

- [ ] **Step 1: 写 readiness 单元测试**

  测试三种结果：全部正常返回 200；数据库不可达返回 503；migration 不在 Alembic head 或目录不可写返回 503。固定数据结构：

  ```python
  @dataclass(frozen=True, slots=True)
  class ReadinessCheck:
      name: str
      ok: bool
      detail: str | None = None

  @dataclass(frozen=True, slots=True)
  class ReadinessReport:
      ready: bool
      checks: tuple[ReadinessCheck, ...]
  ```

  对外函数：

  ```python
  def check_application_readiness(
      *,
      database_url: str,
      alembic_config_path: Path,
      writable_paths: Sequence[Path],
  ) -> ReadinessReport:
  ```

  该函数必须返回 `ReadinessReport`，不得抛出含连接串的异常；每个失败项通过 `ReadinessCheck.detail` 返回脱敏提示。

- [ ] **Step 2: 先运行聚焦测试**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_web_readiness.py -q`

  Expected: FAIL，因为 readiness 模块尚不存在。

- [ ] **Step 3: 实现数据库、迁移和目录检查**

  使用 SQLAlchemy `SELECT 1` 验证数据库；用 Alembic `MigrationContext.get_current_heads()` 与 `ScriptDirectory.get_heads()` 比较 revision；使用同目录临时文件的创建与删除验证 `CAREER_APPLICATION_DATA_DIR` 可写。错误 detail 只输出异常类型和可操作提示，不回显连接串或 Secret。

- [ ] **Step 4: 暴露公开 `/api/ready`**

  保留 `/api/health` 为无依赖 liveness；在 `_PUBLIC_API_PATHS` 中加入 `/api/ready`；新增接口：

  ```python
  @app.get("/api/ready")
  def readiness() -> JSONResponse:
      report = check_application_readiness(
          database_url=os.environ["CAREER_DATABASE_URL"],
          alembic_config_path=project_root / "alembic.ini",
          writable_paths=(application_data_dir,),
      )
      status_code = 200 if report.ready else 503
      return JSONResponse(asdict(report), status_code=status_code)
  ```

- [ ] **Step 5: 验证聚焦回归**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_web_readiness.py tests/test_platform_access_web.py -q`

  Expected: PASS；匿名 `/api/ready` 可访问，其他受保护 API 仍返回 401。

- [ ] **Step 6: 提交**

  ```powershell
  git add src/web/api.py src/web/readiness.py tests/test_web_readiness.py
  git commit -m "feat: add application readiness endpoint"
  ```

---

## Task 3: 增加 Worker 健康检查和可观察退出语义

**Files:**

- Create: `scripts/check_career_worker_health.py`
- Modify: `scripts/run_career_agent_worker.py`
- Create: `tests/test_career_worker_health.py`

- [ ] **Step 1: 写 Worker health 测试**

  断言：缺少数据库 URL 返回非零；`SELECT 1` 成功返回 0；数据库异常返回非零且输出不包含密码；Worker 进程收到终止信号时正常关闭持久化连接。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_career_worker_health.py -q`

  Expected: FAIL，因为 health 脚本尚不存在。

- [ ] **Step 3: 实现独立健康探针**

  ```python
  def check_worker_database(database_url: str) -> None:
      engine = create_engine(database_url, pool_pre_ping=True)
      try:
          with engine.connect() as connection:
              connection.execute(text("SELECT 1"))
      finally:
          engine.dispose()

  def main() -> int:
      database_url = os.environ.get("CAREER_DATABASE_URL", "").strip()
      if not database_url:
          print("CAREER_DATABASE_URL 未配置", file=sys.stderr)
          return 2
      try:
          check_worker_database(database_url)
      except Exception as exc:
          print(f"Worker 数据库检查失败：{type(exc).__name__}", file=sys.stderr)
          return 1
      return 0
  ```

- [ ] **Step 4: 验证**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_career_worker_health.py tests/test_career_agent_worker.py -q`

  Expected: PASS。

- [ ] **Step 5: 提交**

  ```powershell
  git add scripts/check_career_worker_health.py scripts/run_career_agent_worker.py tests/test_career_worker_health.py
  git commit -m "feat: add worker health probe"
  ```

---

## Task 4: 重建隔离的 Compose 核心栈及覆盖层

**Files:**

- Create: `compose.yaml`
- Create: `compose.production.yaml`
- Create: `compose.document.yaml`
- Modify: `docker/nginx/default.conf`
- Modify: `docker-compose.production.yml`
- Modify: `docker-compose.document-processing.yml`
- Modify: `docker-compose.career-assistant.yml`
- Create: `tests/test_compose_contract.py`

- [ ] **Step 1: 写 Compose 静态契约测试**

  测试使用 `yaml.safe_load` 验证：

  - 不存在顶层 `name`、`container_name` 或 volume `name`；
  - 默认服务恰好包括 `career-postgres`、`career-migrate`、`career-api`、`career-agent-worker`、`career-web`；
  - 只有 Web 映射 `127.0.0.1:${ZHITAN_HTTP_PORT:-18081}:80`；
  - Postgres healthy 后 migration 才运行，migration 成功后 API/Worker 才启动；
  - Web healthcheck 请求 `/api/ready`，Worker 使用独立探针；
  - Caddy、Scheduler、Gotenberg、Docling 只存在于覆盖文件。

- [ ] **Step 2: 运行静态测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compose_contract.py -q`

  Expected: FAIL，报告旧固定容器名、固定卷名和缺少 migration/readiness 链。

- [ ] **Step 3: 创建 `compose.yaml` 五服务基础栈**

  Compose 资源遵循下列关键结构：

  ```yaml
  services:
    career-postgres:
      image: pgvector/pgvector:pg16
      environment:
        POSTGRES_DB: ${CAREER_POSTGRES_DB:-zhitan}
        POSTGRES_USER: ${CAREER_POSTGRES_USER:-zhitan}
        POSTGRES_PASSWORD: ${CAREER_POSTGRES_PASSWORD:?set in .env.quickstart}
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      volumes:
        - postgres_data:/var/lib/postgresql/data

    career-migrate:
      image: zhitan-career-api:local
      build:
        context: .
        dockerfile: docker/Dockerfile.api
      command: ["alembic", "upgrade", "head"]
      depends_on:
        career-postgres:
          condition: service_healthy
      restart: "no"

    career-api:
      image: zhitan-career-api:local
      depends_on:
        career-migrate:
          condition: service_completed_successfully
      healthcheck:
        test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8012/api/ready', timeout=5)"]

    career-agent-worker:
      image: zhitan-career-api:local
      command: ["python", "scripts/run_career_agent_worker.py"]
      depends_on:
        career-migrate:
          condition: service_completed_successfully
      healthcheck:
        test: ["CMD", "python", "scripts/check_career_worker_health.py"]

    career-web:
      image: zhitan-career-web:local
      build:
        context: .
        dockerfile: docker/Dockerfile.web
      ports:
        - "127.0.0.1:${ZHITAN_HTTP_PORT:-18081}:80"
      depends_on:
        career-api:
          condition: service_healthy
      healthcheck:
        test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1/api/ready"]

  volumes:
    postgres_data:
    application_data:
    credential_keys:
  ```

  API 和 Worker 共享 `application_data`、`credential_keys`；不发布数据库/API 端口。

- [ ] **Step 4: 建立生产与文档覆盖文件**

  `compose.production.yaml` 增加 Caddy、内容 Scheduler 和生产环境覆盖；`compose.document.yaml` 增加 Gotenberg/Docling。旧 `docker-compose.*.yml` 改为带醒目迁移说明的兼容入口，内部使用 Compose `include` 指向新文件；CI 同时验证兼容入口，确认当前 Docker Compose 支持该语法。

- [ ] **Step 5: 让健康探针通过容器内置工具运行**

  API healthcheck 使用 Python 标准库 `urllib.request`，不新增 curl 依赖；Web 使用 Alpine/BusyBox 自带的 `wget`。Nginx 配置继续将 `/api/` 代理到 `career-api:8012`，并允许 `/api/ready` 透传状态码。

- [ ] **Step 6: 验证 Compose 展开**

  Run: `docker compose --env-file .env.quickstart.example -f compose.yaml config --quiet`

  Expected: exit 0。

  Run: `docker compose --env-file .env.production.example -f compose.yaml -f compose.production.yaml config --quiet`

  Expected: exit 0，不出现固定 project/container/volume 名。

- [ ] **Step 7: 运行测试并提交**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compose_contract.py -q`

  Expected: PASS。

  ```powershell
  git add compose.yaml compose.production.yaml compose.document.yaml docker/nginx/default.conf docker-compose.production.yml docker-compose.document-processing.yml docker-compose.career-assistant.yml tests/test_compose_contract.py
  git commit -m "feat: add isolated quickstart compose stack"
  ```

---

## Task 5: 修复动态管理员配置并完善首次启动状态

**Files:**

- Modify: `src/platform_access/settings.py`
- Modify: `src/platform_access/contracts.py`
- Modify: `src/platform_access/bootstrap.py`
- Modify: `src/platform_access/repository.py`
- Modify: `src/platform_access/service.py`
- Modify: `src/platform_access/web.py`
- Modify: `scripts/bootstrap_first_admin.py`
- Modify: `tests/test_platform_access_settings.py`
- Create: `tests/test_platform_access_web.py`
- Create: `tests/test_first_admin_bootstrap.py`

- [ ] **Step 1: 写环境加载顺序回归测试**

  在模块已 import 后通过 `monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "owner@example.test")` 创建 service/repository，断言 bootstrap status 和初始化约束都使用新值，而不是 import 时冻结的 `admin@example.com`。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_platform_access_settings.py tests/test_first_admin_bootstrap.py tests/test_platform_access_web.py -q`

  Expected: FAIL，暴露 `PLATFORM_ADMIN_EMAIL` 常量在 import 时冻结。

- [ ] **Step 3: 以运行时 settings 取代跨模块常量**

  新接口：

  ```python
  @dataclass(frozen=True, slots=True)
  class PlatformAccessSettings:
      admin_email: str
      public_registration_enabled: bool
      email_auth_enabled: bool
      cli_bootstrap_only: bool

  def load_platform_access_settings(source: Mapping[str, str] | None = None) -> PlatformAccessSettings:
  ```

  实现统一复用现有邮箱格式校验和布尔环境变量解析；默认值依次为 `admin@example.com`、`false`、`false`、`true`。

  Service、repository、bootstrap helper 在构造时接收 settings；不在模块 import 时读取环境。

- [ ] **Step 4: 扩展 bootstrap status**

  响应固定包含：

  ```json
  {
    "requires_bootstrap": true,
    "admin_email": "owner@example.test",
    "public_registration_enabled": false,
    "email_auth_enabled": false,
    "cli_bootstrap_only": true,
    "bootstrap_command": "docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py"
  }
  ```

  `bootstrap_command` 是公开、无密码的展示文本；生产文档给出对应的生产覆盖命令。

- [ ] **Step 5: 验证 CLI 仍只从交互 stdin 读取密码**

  `scripts/bootstrap_first_admin.py --check` 只返回是否需要初始化；创建模式通过 `getpass.getpass()` 读取两次，不增加 `--password` 参数。

- [ ] **Step 6: 运行回归并提交**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_platform_access_settings.py tests/test_first_admin_bootstrap.py tests/test_platform_access_web.py -q`

  Expected: PASS。

  ```powershell
  git add src/platform_access/settings.py src/platform_access/contracts.py src/platform_access/bootstrap.py src/platform_access/repository.py src/platform_access/service.py src/platform_access/web.py scripts/bootstrap_first_admin.py tests/test_platform_access_settings.py tests/test_platform_access_web.py tests/test_first_admin_bootstrap.py
  git commit -m "fix: load platform bootstrap settings at runtime"
  ```

---

## Task 6: 实现无 Provider Key 的前端引导状态

**Files:**

- Modify: `web-ui/src/components/LoginPage.vue`
- Modify: `web-ui/src/login-career-home.test.js`
- Modify: `web-ui/e2e/support/api-fixtures.js`
- Create: `web-ui/src/provider-configuration.test.js`

- [ ] **Step 1: 写首次启动 UI 测试**

  断言 `requires_bootstrap=true` 时显示初始化命令和管理员邮箱，隐藏注册/找回密码；`email_auth_enabled=false` 时不显示邮件验证码入口；配置完成后显示正常密码登录。

- [ ] **Step 2: 运行前端测试并确认失败**

  Run: `npm --prefix web-ui test`

  Expected: FAIL，至少旧生产 Compose 命令和不应出现的邮件入口断言失败。

- [ ] **Step 3: 修改 LoginPage**

  使用后端 `bootstrap_command`，不在 Vue 中硬编码生产文件名；当外部 Provider 未配置时，仅在相关配置/调用入口显示“尚未配置”，不影响导航、管理员初始化和密码登录。

- [ ] **Step 4: 更新 fixture 并验证**

  Run: `npm --prefix web-ui test`

  Expected: PASS。

  Run: `npm --prefix web-ui run build`

  Expected: PASS；现有大 chunk warning 可记录为非阻断，不在本任务扩展重构范围。

- [ ] **Step 5: 提交**

  ```powershell
  git add web-ui/src/components/LoginPage.vue web-ui/src/login-career-home.test.js web-ui/src/provider-configuration.test.js web-ui/e2e/support/api-fixtures.js
  git commit -m "feat: guide first-run authentication setup"
  ```

---

## Task 7: 生成安全的实例配置与跨平台 setup 脚本

**Files:**

- Create: `.env.quickstart.example`
- Modify: `.env.example`
- Modify: `.gitignore`
- Create: `scripts/setup_quickstart.ps1`
- Create: `scripts/setup_quickstart.sh`
- Create: `scripts/setup_dev.ps1`
- Create: `scripts/setup_dev.sh`
- Create: `scripts/setup_common.py`
- Create: `tests/test_setup_common.py`
- Create: `tests/test_setup_scripts_contract.py`

- [ ] **Step 1: 写配置生成和端口检测测试**

  测试接口：

  ```python
  def generate_urlsafe_secret(byte_count: int = 32) -> str:
      return secrets.token_urlsafe(byte_count)

  def assert_ports_available(bindings: Mapping[str, tuple[str, int]]) -> None:

  def render_env(template_path: Path, destination_path: Path, values: Mapping[str, str]) -> None:
  ```

  `assert_ports_available` 在任一地址无法 bind 时抛出带变量名/端口的 `PortConflictError`；`render_env` 使用独占创建模式，目标存在时抛出 `FileExistsError`。

  断言生成值可安全进入 PostgreSQL URL，目标 env 已存在时不覆盖，端口冲突会列出变量名和占用端口。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_setup_common.py tests/test_setup_scripts_contract.py -q`

  Expected: FAIL，因为 setup 模块和脚本尚不存在。

- [ ] **Step 3: 实现 Quickstart setup**

  PowerShell/POSIX 脚本调用同一个 `scripts/setup_common.py quickstart`：检查 Docker Compose，检测 `ZHITAN_HTTP_PORT`，从 example 生成 `.env.quickstart`，创建 URL-safe PostgreSQL/HMAC/credential secrets，然后输出且可选择执行唯一命令：

  ```text
  docker compose --env-file .env.quickstart up -d --build --wait
  ```

  脚本不得读取原项目 `.env`，不得探测或复用旧卷。

- [ ] **Step 4: 实现源码开发 setup**

  `setup_dev.ps1` 始终使用 `.\.venv\Scripts\python.exe`；`setup_dev.sh` 始终使用 `.venv/bin/python`。两者安装 lock、运行 `npm --prefix web-ui ci`、生成 `.env.development.local`、检测 `54329/18080/5173`，并打印数据库、migration、API、Worker、Vite 的分终端启动命令。

- [ ] **Step 5: 验证与提交**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_setup_common.py tests/test_setup_scripts_contract.py -q`

  Expected: PASS。

  ```powershell
  git add .env.quickstart.example .env.example .gitignore scripts/setup_common.py scripts/setup_quickstart.ps1 scripts/setup_quickstart.sh scripts/setup_dev.ps1 scripts/setup_dev.sh tests/test_setup_common.py tests/test_setup_scripts_contract.py
  git commit -m "feat: add reproducible setup scripts"
  ```

---

## Task 8: 锁定 Python 依赖并统一 Docker/CI/用户安装入口

**Files:**

- Create: `requirements.lock.in`
- Create: `requirements.lock.txt`
- Modify: `requirements-development.txt`
- Modify: `docker/Dockerfile.api`
- Create: `tests/test_dependency_lock.py`

- [ ] **Step 1: 写依赖锁契约测试**

  断言 lock 文件无可编辑本地路径、无 `file://`、无用户目录，且声明文件中的直接依赖都能在 lock 中找到；Dockerfile 和 CI 只能从 `requirements.lock.txt` 安装运行/测试依赖。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dependency_lock.py -q`

  Expected: FAIL，因为 lock 尚不存在，Docker/CI 仍安装兼容范围文件。

- [ ] **Step 3: 创建输入与生成 lock**

  `requirements.lock.in` 内容：

  ```text
  -r requirements.txt
  -r requirements-career-assistant.txt
  -r requirements-development.txt
  ```

  在 Python 3.12 环境执行：

  ```powershell
  .\.venv\Scripts\python.exe -m pip install "pip-tools>=7.5,<8"
  .\.venv\Scripts\python.exe -m piptools compile --strip-extras --resolver=backtracking --output-file requirements.lock.txt requirements.lock.in
  ```

  然后在 Python 3.13 clean venv 再安装并运行核心 import smoke，确保支持范围交集可用。

- [ ] **Step 4: 切换消费者并验证**

  Run: `.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt`

  Expected: PASS。

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_dependency_lock.py -q`

  Expected: PASS。

- [ ] **Step 5: 提交**

  ```powershell
  git add requirements.lock.in requirements.lock.txt requirements-development.txt docker/Dockerfile.api tests/test_dependency_lock.py
  git commit -m "build: lock Python dependencies"
  ```

---

## Task 9: 建立真实 Quickstart smoke 与临时管理员登录验收

**Files:**

- Create: `scripts/create_ephemeral_test_admin.py`
- Create: `scripts/verify_quickstart.py`
- Create: `tests/test_ephemeral_admin_helper.py`
- Create: `tests/test_quickstart_verifier.py`

- [ ] **Step 1: 写 integration helper 安全测试**

  断言 helper 仅在 `ZHITAN_EPHEMERAL_TEST_MODE=true` 时可运行；密码只能从 stdin 读取；`sys.stdin.isatty()` 为真时拒绝；日志不回显密码；生产 bootstrap 脚本不新增密码参数。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ephemeral_admin_helper.py tests/test_quickstart_verifier.py -q`

  Expected: FAIL，因为 helper 尚不存在。

- [ ] **Step 3: 实现临时管理员 helper**

  接口：

  ```python
  def read_password_from_stdin() -> str:
      if sys.stdin.isatty():
          raise RuntimeError("ephemeral helper 只接受 CI stdin")
      password = sys.stdin.readline().rstrip("\r\n")
      if not password:
          raise ValueError("测试密码不能为空")
      return password
  ```

  Helper 调用与生产 bootstrap 相同的 service 层，不复制用户创建逻辑。

- [ ] **Step 4: 实现 HTTP/容器 verifier**

  `verify_quickstart.py` 接受 `--base-url` 和 `--compose-project`，依次验证：

  1. `/api/health` 200；
  2. `/api/ready` 200 且所有 checks 为 true；
  3. `/api/auth/bootstrap-status` 初始 `requires_bootstrap=true`；
  4. 匿名受保护 API 返回 401；
  5. helper 创建管理员后 bootstrap status 为 false；
  6. 用真实 HTTP 密码登录并访问一个受保护 API；
  7. migration 容器 exit code 0；
  8. Worker running/healthy、restart count 0；
  9. API/Worker/Web 日志不含 `Traceback`。

- [ ] **Step 5: 验证与提交**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ephemeral_admin_helper.py tests/test_quickstart_verifier.py -q`

  Expected: PASS。

  ```powershell
  git add scripts/create_ephemeral_test_admin.py scripts/verify_quickstart.py tests/test_ephemeral_admin_helper.py tests/test_quickstart_verifier.py
  git commit -m "test: add quickstart login smoke"
  ```

---

## Task 10: 重写活跃开源文档并归档历史状态

**Files:**

- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Create: `docs/getting-started.md`
- Create: `docs/local-development.md`
- Create: `docs/production-deployment.md`
- Create: `docs/troubleshooting.md`
- Create: `docs/archive/README.md`
- Move: `docs/global_memory.md` → `docs/archive/global_memory.md`
- Move: `docs/platform_production_deployment.md` → `docs/archive/platform_production_deployment.md`
- Move: `docs/platform_first_admin_bootstrap.md` → `docs/archive/platform_first_admin_bootstrap.md`
- Modify: `SECURITY.md`
- Create: `tests/test_active_documentation.py`

- [ ] **Step 1: 写活跃文档契约测试**

  断言 README/活跃 docs：使用 `master`；默认端口为 18081；使用 `.env.quickstart` 与 `compose.yaml`；虚拟环境命令使用 venv 内解释器；不出现固定服务器 IP、真实邮箱、57 Skill、旧 Git SHA 或 `docker-compose.production.yml` 作为 Quickstart；同时必须解释 `down` 与破坏性的 `down -v`。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_active_documentation.py -q`

  Expected: FAIL，列出旧分支、旧 Skill 数量、固定管理员和旧生产命令。

- [ ] **Step 3: 重写根 README**

  根 README 保留项目定位、核心能力、架构短图、五分钟 Docker Quickstart、管理员初始化、访问/ready 验证、许可证与四篇详细文档链接。推荐命令严格为：

  ```powershell
  Copy-Item .env.quickstart.example .env.quickstart
  .\scripts\setup_quickstart.ps1
  docker compose --env-file .env.quickstart up -d --build --wait
  docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py
  Invoke-RestMethod http://127.0.0.1:18081/api/ready
  ```

  POSIX 等价命令放在 `docs/getting-started.md`。

- [ ] **Step 4: 写四篇活跃文档**

  - `getting-started.md`：Windows/POSIX Quickstart、初始化、访问、停止、保留/删除数据；
  - `local-development.md`：setup scripts、手动等价命令、venv 解释器、端口联动、显式 migration；
  - `production-deployment.md`：基础+生产覆盖、域名/TLS/Secret、手动 Provider smoke，明确不构成生产授权；
  - `troubleshooting.md`：端口冲突、Compose project、migration、readiness、日志、Key 未配置状态。

- [ ] **Step 5: 归档旧状态文档**

  用 `git mv` 移动三篇历史文档；`docs/archive/README.md` 说明其命令、邮箱、Skill 数量和 Git SHA 只是历史记录，不是当前安装指南。更新所有活跃链接，确保没有导航指向 archive 作为安装入口。

- [ ] **Step 6: 验证文档命令语法**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_active_documentation.py -q`

  Expected: PASS。

  Run: `docker compose --env-file .env.quickstart.example config --quiet`

  Expected: PASS。

- [ ] **Step 7: 提交**

  ```powershell
  git add README.md CONTRIBUTING.md SECURITY.md docs tests/test_active_documentation.py
  git commit -m "docs: publish clean-room installation guides"
  ```

---

## Task 11: 将 clean-room Docker smoke 设为 `master` 门禁

**Files:**

- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/provider-smoke.yml`
- Create: `scripts/ci_quickstart_smoke.sh`
- Create: `.gitleaks.toml`
- Create: `tests/test_ci_contract.py`

- [ ] **Step 1: 写 CI 契约测试**

  断言 Python job 安装 lock；frontend 使用 `npm ci`；Docker job 配置唯一 `COMPOSE_PROJECT_NAME=zhitan-ci-${GITHUB_RUN_ID}` 和非默认空闲端口；失败/成功都只清理该 project；强制调用 `verify_quickstart.py`；Provider/DNS/TLS/mail 只在手动 workflow。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ci_contract.py -q`

  Expected: FAIL，因为现有 CI 没有 Docker smoke 且安装未锁定依赖。

- [ ] **Step 3: 更新 CI**

  保留 Python、Web、扩展和 build jobs；增加 `quickstart-smoke`：

  ```yaml
  env:
    COMPOSE_PROJECT_NAME: zhitan-ci-${{ github.run_id }}
    ZHITAN_HTTP_PORT: "18081"
  steps:
    - uses: actions/checkout@v7
    - name: Generate isolated config
      run: ./scripts/setup_quickstart.sh --non-interactive
    - name: Build and start clean-room stack
      run: docker compose --env-file .env.quickstart up -d --build --wait
    - name: Verify migration, readiness, worker and login
      run: ./scripts/ci_quickstart_smoke.sh
    - name: Destroy only this CI project
      if: always()
      run: docker compose --env-file .env.quickstart down -v --remove-orphans
  ```

  `ci_quickstart_smoke.sh` 通过 stdin 向 ephemeral helper 传随机测试密码；不写日志、不放入命令参数。

- [ ] **Step 4: 添加手动 Provider workflow**

  `provider-smoke.yml` 只允许 `workflow_dispatch`，使用 GitHub Secrets，验证真实模型/邮件/DNS/TLS 时明确会访问外部服务；它不是 PR 必过项。

- [ ] **Step 5: 验证并提交**

  Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ci_contract.py -q`

  Expected: PASS。

  ```powershell
  git add .github/workflows/ci.yml .github/workflows/provider-smoke.yml scripts/ci_quickstart_smoke.sh .gitleaks.toml tests/test_ci_contract.py
  git commit -m "ci: gate master on clean-room quickstart"
  ```

---

## Task 12: 本地完整回归、推送与 GitHub clean clone 最终验收

**Files:**

- Create: `docs/open-source-release-verification.md`
- Modify: `docs/superpowers/plans/2026-09-02-clean-room-open-source-quickstart.md`（勾选完成项）

- [ ] **Step 1: 记录原项目只读基线**

  在不执行任何 compose mutation 的前提下记录：

  ```powershell
  docker ps --format '{{.ID}}|{{.Names}}|{{.Status}}|{{.Ports}}'
  docker volume ls --format '{{.Name}}'
  Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 54329,18080,5173,18081
  git -C D:\MyPro\WechaOffiicialAccount status --short
  ```

  将结果的哈希/摘要写入验收文档，不写 Secret 或完整环境变量。

- [ ] **Step 2: 在源仓库运行全部本地验证**

  Run: `.\.venv\Scripts\python.exe -m pytest -q`

  Expected: PASS。

  Run: `npm --prefix web-ui test`

  Expected: PASS。

  Run: `npm --prefix web-ui run build`

  Expected: PASS。

  Run: `npm --prefix browser-extension/job-library test`

  Expected: PASS。

  Run: `docker compose --env-file .env.quickstart.example config --quiet`

  Expected: PASS。

  Run: `docker compose --env-file .env.production.example -f compose.yaml -f compose.production.yaml config --quiet`

  Expected: PASS。

- [ ] **Step 3: 执行敏感信息与 ignored 依赖扫描**

  Run: `docker run --rm -v "${PWD}:/repo" zricethezav/gitleaks:v8.28.0 git /repo --redact --no-banner`

  Expected: exit 0；`.gitleaks.toml` 只允许明确标注的测试假值，不允许真实 API Key 或密码。

  Run: `git grep -n -I -E "(DASHSCOPE_API_KEY|OPENAI_API_KEY|FIRECRAWL_API_KEY|POSTGRES_PASSWORD)=[^$<{[:space:]][^[:space:]]*" -- ':!tests' ':!scripts/verify_*' ':!*.example'`

  Expected: 无输出。随后执行 `git clean -ndX` 只预览 ignored 文件，不删除，确认 tracked 测试不引用预览中的任何路径。

- [ ] **Step 4: 更新验收记录并提交**

  `docs/open-source-release-verification.md` 记录版本、命令、结果、已知非阻断 warning 和原项目保护边界。

  ```powershell
  git add docs/open-source-release-verification.md docs/superpowers/plans/2026-09-02-clean-room-open-source-quickstart.md
  git commit -m "docs: record clean-room quickstart verification"
  ```

- [ ] **Step 5: 确认提交范围后推送 `master`**

  Run: `git status --short`

  Expected: 无输出。

  Run: `git log --oneline origin/master..master`

  Expected: 只包含本计划及实现提交。

  Run: `git push origin master`

  Expected: GitHub `HBelike/ZhiTan` 的 `master` 更新成功。

- [ ] **Step 6: 验证并清空唯一授权目录**

  先解析路径并要求其严格等于 `D:\MyPro\ZHITANTEST`，且不能是 workspace root、盘符根或原项目目录。若目录中存在 Git clone，先读取 remote 确认；只停止 `COMPOSE_PROJECT_NAME=zhitantest` 的实例。然后删除该目录内容并重新创建空目录。

- [ ] **Step 7: 从 GitHub 重新 clone 并严格执行 README**

  ```powershell
  git clone --branch master https://github.com/HBelike/ZhiTan.git D:\MyPro\ZHITANTEST
  Set-Location D:\MyPro\ZHITANTEST
  .\scripts\setup_quickstart.ps1 -NonInteractive
  $env:COMPOSE_PROJECT_NAME = 'zhitantest'
  docker compose --env-file .env.quickstart up -d --build --wait
  docker compose --env-file .env.quickstart exec career-api python scripts/bootstrap_first_admin.py --check
  ```

  使用交互 bootstrap 创建仅属于测试实例的管理员，再通过浏览器/HTTP 完成真实登录。

- [ ] **Step 8: 在 clone 中完成产品与测试验收**

  验证：Web `http://127.0.0.1:18081`；health/ready；migration head；Worker healthy/restart 0；无 Key 时配置提示；Python/Web/extension tests；frontend build；`git status --short` 只允许 ignored 的 `.env.quickstart`、`.venv`、`node_modules` 和 build 产物。

- [ ] **Step 9: 对比原项目基线**

  重新执行 Step 1 的只读命令。Expected：原项目 Git 状态、容器 ID/状态、卷集合和其占用的 `54329/18080/5173` 未因 ZHITANTEST 验收发生变化；新增资源的 Compose labels 只属于 `zhitantest`。

- [ ] **Step 10: 默认停止测试实例但保留数据**

  Run: `docker compose --env-file .env.quickstart down`

  Expected: 只停止/删除 `zhitantest` 容器与网络，保留 `zhitantest_postgres_data`、应用数据和凭据卷。除非用户另行明确授权，不执行 `down -v`。

- [ ] **Step 11: 等待 GitHub CI 并完成交付**

  确认 `master` 的 Python、frontend、extension/build、clean-room smoke 全绿；最终报告 GitHub commit、测试结果、访问/停止方式、保留的数据卷和唯一已知非阻断限制。

---

## Plan Self-Review

- [x] 设计第 1–15 节均有对应任务和验收步骤。
- [x] 所有新接口均给出名称、输入、输出或固定响应结构。
- [x] 所有行为变化都有先失败、后实现、再回归的 TDD 步骤。
- [x] 不含待办标记、省略文件名或需要执行者临场决定的占位内容。
- [x] Quickstart、生产覆盖、文档覆盖、源码开发、CI 和最终 clone 使用同一端口/环境/Compose 契约。
- [x] 原项目保护、ZHITANTEST 精确清理授权和默认保留数据均有显式边界。
