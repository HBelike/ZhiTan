# 平台二元角色与固定管理员 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将平台角色收敛为 `admin/user`，固定 `admin@example.com` 为唯一管理员，并恢复普通用户按管理员模块配置访问页面的能力。

**Architecture:** 由共享契约定义固定管理员邮箱和二元角色；Alembic 将旧角色及本地现有账号一次性归一化，并用数据库约束封住绕过路径。认证中间件只负责登录，会由具体管理员接口继续执行 admin 校验；前端只消费服务端计算的模块权限，并把配置故障展示为可重试错误。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy、Alembic、PostgreSQL、Vue 3、Node test runner、Vite

**Spec:** `docs/superpowers/specs/2026-08-25-binary-platform-role-and-fixed-admin-design.md`

## Global Constraints

- 角色值仅允许 `admin`、`user`。
- 唯一管理员邮箱固定为 `admin@example.com`。
- 本次只修改与验证本地环境，不连接、部署或写入生产环境。
- 保留工作区已有未提交改动；不提交包含无关用户改动的文件。
- 管理员专属 API 必须继续由服务端校验，不能依赖前端隐藏。

---

### Task 1: 二元角色契约与数据库迁移

**Files:**
- Modify: `src/platform_access/contracts.py`
- Modify: `migrations/versions/20260825_20_single_platform_admin.py`
- Modify: `tests/test_single_platform_admin_migration.py`
- Modify: `tests/test_navigation_config.py`

**Interfaces:**
- Produces: `PLATFORM_ADMIN_EMAIL: str = "admin@example.com"`
- Produces: `PlatformRole.USER`、`PlatformRole.ADMIN` 与 `PlatformRole.allows(required)`
- Produces: Alembic revision `20260825_20`，把旧 `viewer/operator` 归一化为 `user`

- [ ] **Step 1: 写失败测试**

```python
def test_role_model_only_contains_user_and_admin(self) -> None:
    self.assertEqual({role.value for role in PlatformRole}, {"user", "admin"})
    self.assertEqual(PLATFORM_ADMIN_EMAIL, "admin@example.com")

def test_migration_normalizes_roles_and_pins_admin_email(self) -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    self.assertIn("UPDATE career_assistant.platform_users", source)
    self.assertIn("admin@example.com", source)
    self.assertIn("role IN ('user', 'admin')", source)
    self.assertIn("role <> 'admin' OR email_normalized = 'admin@example.com'", source)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m unittest tests.test_single_platform_admin_migration tests.test_navigation_config -v`

Expected: FAIL，原因是仍存在 `viewer/operator` 且迁移没有固定邮箱与归一化 SQL。

- [ ] **Step 3: 实现最小契约与迁移**

```python
PLATFORM_ADMIN_EMAIL = "admin@example.com"

class PlatformRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

    def allows(self, required: "PlatformRole") -> bool:
        return self is PlatformRole.ADMIN or required is PlatformRole.USER
```

迁移必须先删除旧 role CHECK，再归一化数据、添加新 CHECK 与部分唯一索引；downgrade 将 `user` 统一映射回 `viewer` 后恢复旧约束。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv\Scripts\python.exe -m unittest tests.test_single_platform_admin_migration tests.test_navigation_config -v`

Expected: PASS。

### Task 2: 固定管理员 bootstrap 与普通注册

**Files:**
- Modify: `src/platform_access/bootstrap.py`
- Modify: `src/platform_access/repository.py`
- Modify: `src/platform_access/service.py`
- Modify: `src/platform_access/web.py`
- Modify: `scripts/bootstrap_first_admin.py`
- Modify: `scripts/verify_platform_first_admin_bootstrap.py`
- Modify: `scripts/verify_platform_auth_email_contract.py`

**Interfaces:**
- Consumes: `PLATFORM_ADMIN_EMAIL`、`PlatformRole.USER`
- Produces: `FirstAdminRepository.has_active_admin() -> bool`
- Produces: 普通注册始终写入 `user`，管理员专用邮箱不能进入普通注册链路

- [ ] **Step 1: 扩展内存仓储验证**

```python
repository = FakeFirstAdminRepository()
bootstrap_first_admin(
    repository,
    email=PLATFORM_ADMIN_EMAIL,
    display_name="部署管理员",
    password="safe-password-123",
)

with raises(FirstAdminBootstrapError):
    bootstrap_first_admin(
        FakeFirstAdminRepository(),
        email="other@example.com",
        display_name="错误管理员",
        password="safe-password-123",
    )
```

同时验证：已有普通用户但无管理员时允许固定邮箱 bootstrap；已有管理员时拒绝；普通注册固定邮箱时拒绝。

- [ ] **Step 2: 运行验证确认失败**

Run: `.venv\Scripts\python.exe scripts/verify_platform_first_admin_bootstrap.py`

Expected: FAIL，旧逻辑接受任意管理员邮箱并以“是否存在任意用户”判断 bootstrap。

- [ ] **Step 3: 实现固定邮箱保护**

```python
normalized_email = normalize_email(email)
if normalized_email != PLATFORM_ADMIN_EMAIL:
    raise FirstAdminBootstrapError(f"管理员邮箱必须是 {PLATFORM_ADMIN_EMAIL}")
if repository.has_active_admin():
    raise FirstAdminBootstrapError("管理员已初始化，请使用已有管理员账号登录")
```

`send_registration_code(..., bootstrap=False)` 在创建挑战前拒绝管理员专用邮箱；`create_registered_user` 默认角色改为 `PlatformRole.USER` 并在仓储层再次拒绝固定管理员邮箱。

- [ ] **Step 4: 运行认证验证**

Run: `.venv\Scripts\python.exe scripts/verify_platform_first_admin_bootstrap.py`

Run: `.venv\Scripts\python.exe scripts/verify_platform_auth_email_contract.py`

Expected: 两个脚本均输出各自的 `_ok` 标记。

### Task 3: 普通用户 API 与路由权限恢复

**Files:**
- Modify: `src/web/api.py`
- Modify: `src/platform_access/web.py`
- Modify: `scripts/verify_platform_api_access_guard.py`
- Modify: `src/platform_access/navigation_config.py`

**Interfaces:**
- Consumes: `PlatformRole.USER`、`PlatformRole.ADMIN`
- Produces: 匿名业务 API 返回 401；登录 user 可进入普通业务 API；管理员接口继续返回非 admin 403

- [ ] **Step 1: 改写 API 守卫回归场景**

```python
user_session = _session_for(PlatformRole.USER)
with patch("src.web.api.get_platform_access_service", return_value=FakeAccessService(user_session)):
    with TestClient(app) as client:
        client.cookies.set("platform_session", "test-session")
        assert client.get("/api/_deployment-auth-check").status_code == 200
```

再用平台 router 的管理员接口或 `require_admin` 验证 user 仍被拒绝。

- [ ] **Step 2: 运行验证确认失败**

Run: `.venv\Scripts\python.exe scripts/verify_platform_api_access_guard.py`

Expected: FAIL，旧 `PLATFORM_CLOSED_OPERATOR_MODE` 仍对 user 返回 403。

- [ ] **Step 3: 删除旧总闸并保留会话注入**

删除中间件中的 `platform_closed_operator_mode()` 分支及对应生产配置读取；保留 Cookie 会话解析、401、Career Actor 注入与 Cookie 续期。管理员 endpoint 的 `require_admin` 不变。

- [ ] **Step 4: 运行 API 与导航验证**

Run: `.venv\Scripts\python.exe scripts/verify_platform_api_access_guard.py`

Run: `.venv\Scripts\python.exe -m unittest tests.test_navigation_config -v`

Expected: PASS。

### Task 4: 前端导航失败状态与二元角色

**Files:**
- Modify: `web-ui/src/navigation-access.js`
- Modify: `web-ui/src/navigation-access.test.js`
- Modify: `web-ui/src/App.vue`

**Interfaces:**
- Produces: `navigationError: Ref<string>` 与 `retryNavigationConfig()`
- Produces: user 按 `configuredModule.accessible` 访问，admin 始终可访问

- [ ] **Step 1: 写前端失败场景测试**

```javascript
test('普通用户服从已启用模块配置', () => {
  assert.equal(
    canAccessNavigationItem(careerItem, { accessible: true }, 'user'),
    true
  )
})

test('导航配置失败展示错误和重试入口', async () => {
  const source = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
  assert.match(source, /navigationError/)
  assert.match(source, /重新加载模块/)
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- --test-name-pattern="导航|普通用户"`

Workdir: `web-ui`

Expected: FAIL，测试仍使用 viewer 且 App 没有错误状态。

- [ ] **Step 3: 实现错误状态**

`loadNavigationConfig()` 成功时清空错误；失败时记录“无法读取可用模块，请检查网络后重试”，保留全拒绝安全兜底，并由模板在路由内容前展示重试按钮。账号角色标签对任何非 admin 统一显示“普通用户”。

- [ ] **Step 4: 运行前端测试与构建**

Run: `npm test`

Run: `npm run build`

Workdir: `web-ui`

Expected: 全部 PASS，Vite 构建成功。

### Task 5: 文档、本地迁移与终验

**Files:**
- Modify: `.env.production.example`
- Modify: `CLAUDE.md`
- Modify: `docs/platform_access_operations_module.md`
- Modify: `docs/platform_first_admin_bootstrap.md`
- Modify: `docs/platform_production_deployment.md`
- Modify: `docs/global_memory.md`

**Interfaces:**
- Consumes: 已完成的二元角色、固定管理员和路由调用链
- Produces: 与实现一致的本地/未来生产运维说明

- [ ] **Step 1: 清理旧角色与旧总闸文档**

把 `viewer/operator` 改为 `user/admin`；删除 `PLATFORM_CLOSED_OPERATOR_MODE` 示例与“所有业务 API 只允许管理员”的描述；记录本次仅本地实施。

- [ ] **Step 2: 运行静态与迁移检查**

Run: `rg -n "PlatformRole\.(VIEWER|OPERATOR)|PLATFORM_CLOSED_OPERATOR_MODE" src scripts tests web-ui/src .env.production.example docs/platform_*.md CLAUDE.md`

Expected: 没有遗留运行时代码引用；历史设计文档可保留历史语境。

Run: `.venv\Scripts\python.exe -m alembic heads`

Expected: 仅 `20260825_20 (head)`。

- [ ] **Step 3: 执行本地数据库迁移**

Run: `.venv\Scripts\python.exe -m alembic upgrade head`

Expected: 本地数据库从 `20260823_19` 升至 `20260825_20`。

- [ ] **Step 4: 只读核对本地账号结果**

Run: `docker exec wechat-agent-career-postgres psql -U career_assistant -d career_assistant -At -F "|" -c "SELECT email, role, is_active FROM career_assistant.platform_users ORDER BY email;"`

Expected: `admin@example.com|admin|t`；其他账号均为 `user`；admin 数量为 1。

- [ ] **Step 5: 完整回归验证**

Run: `.venv\Scripts\python.exe -m unittest tests.test_single_platform_admin_migration tests.test_navigation_config -v`

Run: `.venv\Scripts\python.exe scripts/verify_platform_first_admin_bootstrap.py`

Run: `.venv\Scripts\python.exe scripts/verify_platform_auth_email_contract.py`

Run: `.venv\Scripts\python.exe scripts/verify_platform_api_access_guard.py`

Run: `npm test && npm run build`

Workdir for final command: `web-ui`

Expected: 所有测试与构建通过。

- [ ] **Step 6: 检查变更边界**

Run: `git diff --check`

Run: `git status --short`

Expected: 无空白错误；只报告本次文件和进入任务前已存在的用户改动，不连接生产。
