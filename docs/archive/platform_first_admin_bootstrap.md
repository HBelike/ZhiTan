# 首次管理员初始化与验收

## 目标

公网首发阶段不允许通过浏览器创建首个管理员。因为管理员尚不存在时，任何可访问登录页的人都可能抢先完成邮箱 bootstrap。生产环境通过服务器本机的交互式终端完成这项一次性操作；完成后，常规登录、会话续期和密码找回仍沿用既有账户模块。

管理员邮箱固定为 `admin@example.com`。迁移 `20260825_20` 同时保证角色只允许 `admin/user`、全平台最多一个管理员，并禁止其他邮箱持有 `admin`。系统不存储明文密码、API Key 或数据库连接串。

## 调用链

```text
服务器拥有者的 TTY
  -> scripts/bootstrap_first_admin.py
  -> src.platform_access.bootstrap.bootstrap_first_admin()
  -> hash_password()（scrypt）
  -> PlatformAccessRepository.create_first_admin()
  -> PostgreSQL pg_advisory_xact_lock
  -> platform_users（admin） + actors（同 ID Career Actor）
  -> 浏览器使用初始化邮箱和密码登录
```

`pg_advisory_xact_lock` 在数据库事务结束时自动释放。它将“是否已有活动管理员”的检查与管理员/Actor 写入放进同一个跨进程临界区，避免两个 SSH 终端或旧 HTTP 路径并发时创建多个管理员。数据库已有普通用户但尚无管理员时，仍可通过受控 CLI 创建固定管理员。

## 生产开关

生产 `.env.production` 必须包含：

```dotenv
PLATFORM_CLI_BOOTSTRAP_ONLY=true
PLATFORM_AUTH_REQUIRED=true
PLATFORM_PUBLIC_REGISTRATION_ENABLED=false
```

开启 `PLATFORM_CLI_BOOTSTRAP_ONLY` 后：

- `/api/auth/bootstrap/send-code` 与 `/api/auth/bootstrap/verify` 统一返回 403；
- 登录页仅展示服务器初始化说明，不展示邮箱、密码或验证码表单；
- 本地开发未设置该变量时，原有邮箱验证码 bootstrap 仍保持兼容。

## 服务器操作

在 Docker 服务成功启动、迁移容器已完成后，在项目目录执行。不要使用 `-T`，否则脚本会拒绝在非交互式终端中读取密码。

```bash
docker compose --env-file .env.production -f docker-compose.production.yml \
  exec -it career-api python scripts/bootstrap_first_admin.py --check

docker compose --env-file .env.production -f docker-compose.production.yml \
  exec -it career-api python scripts/bootstrap_first_admin.py
```

脚本固定使用 `admin@example.com`，并在 TTY 中读取显示名称、密码、密码确认和 `INITIALIZE` 二次确认。密码不作为命令行参数，不会显示、写入 shell history、日志或数据库明文列。成功时只输出：

```text
first_admin_bootstrap_ok role=admin
```

第二次执行不会覆盖已有管理员，而是以非零退出码拒绝。`--check` 可安全用于部署验收，输出 `first_admin_bootstrap_pending` 或 `first_admin_bootstrap_already_initialized`。

## 上线验收

1. 确认 `career-migrate` 已成功执行且 `career-api` 健康检查通过。
2. 运行 `--check`，首次应看到 `first_admin_bootstrap_pending`。
3. 交互式创建管理员，再运行 `--check`，应看到 `first_admin_bootstrap_already_initialized`。
4. 刷新 HTTPS 登录页，用初始化邮箱登录；未登录访问业务 API 仍应为 401。
5. 确认生产环境访问两个 HTTP bootstrap 接口均返回 403，且响应不含密码、Key、连接串或用户数据。

本机回归验证：

```powershell
.\.venv\Scripts\python.exe scripts\verify_platform_first_admin_bootstrap.py
.\.venv\Scripts\python.exe scripts\verify_platform_api_access_guard.py
```

预期分别输出 `platform_first_admin_bootstrap_ok` 与 `platform_api_access_guard_ok`。
