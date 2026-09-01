# 平台二元角色与固定管理员设计

## 目标

平台账号只保留 `admin` 与 `user` 两类角色。`admin@example.com` 是本地与生产唯一允许成为 `admin` 的账号；其他现有账号统一迁移为 `user`。管理员保存的路由模块开关控制普通用户的导航与直接地址访问，管理员专属页面仍只允许固定管理员访问。

本次只实施本地代码、迁移和验证，不连接或修改生产环境。

## 当前问题

当前代码定义 `viewer`、`operator`、`admin` 三级角色，但产品实际只需要管理员和普通用户两个权限域。生产还启用了 `PLATFORM_CLOSED_OPERATOR_MODE=true`，认证中间件会在 `/api/navigation/modules` 计算角色可见性之前拦截所有非管理员请求。前端收到 403 后使用全拒绝兜底，最终表现为普通账号登录后看不到任何模块。

现有 `20260825_20` 草稿迁移只保证最多一个 `admin`，没有保证管理员邮箱，也没有把旧角色收敛为普通用户。

## 角色与账号不变量

- 角色值仅允许 `admin`、`user`。
- `admin@example.com` 是唯一允许持有 `admin` 的规范化邮箱。
- 平台最多存在一个 `admin`；允许全新数据库在 bootstrap 前暂时没有管理员。
- 普通注册固定创建 `user`，并拒绝占用管理员专用邮箱。
- CLI bootstrap 只接受管理员专用邮箱；即使数据库中已经存在普通用户，只要尚无管理员，仍允许安全创建固定管理员。
- 会话解析继续从 `platform_users` 读取实时角色，因此迁移后已有会话立即按新角色生效，不复制或缓存旧角色。

## 数据迁移

修改尚未发布的 `20260825_20` 迁移，按以下顺序执行：

1. 删除旧的 `platform_users_role_check` 约束。
2. 将所有非管理员专用邮箱账号的角色更新为 `user`。
3. 如果管理员专用邮箱账号已存在，将其角色更新为 `admin`；如果不存在则保持零管理员，交给受控 CLI bootstrap 创建。
4. 新增只允许 `admin/user` 的角色检查约束。
5. 新增“`admin` 必须使用管理员专用邮箱”的检查约束。
6. 新增全平台单管理员部分唯一索引。

本地现有数据的预期结果是：`admin@example.com` 从 `viewer` 变为 `admin`，`user@example.com` 从 `admin` 变为 `user`，其余账号变为 `user`。生产部署不在本次范围内；未来获明确授权后，生产迁移预期保持 `admin@example.com` 为 `admin`，其余账号转为 `user`。

迁移 downgrade 只恢复旧角色约束和删除新增约束/索引；已归一化的数据不会猜测性恢复为 `viewer/operator`，统一回退为 `viewer` 后再恢复旧约束。

## 认证与路由调用链

登录后的调用链调整为：

`/api/auth/me` 返回 `admin/user` → `/api/navigation/modules` 按管理员模块配置计算 `accessible` → 前端侧栏和直接地址守卫消费同一结果。

- `admin` 无视普通模块开关，并可访问管理员专属模块。
- `user` 只能访问已启用且非 `admin_only` 的模块。
- 移除 `PLATFORM_CLOSED_OPERATOR_MODE` 对全部非管理员业务 API 的全局拦截；保留 `PLATFORM_AUTH_REQUIRED` 会话校验。
- 管理台、运行配置、LangSmith 等管理员接口继续使用服务端 `require_admin` 或等价角色检查，不能仅依赖前端隐藏。
- 路由配置读取失败时不伪造管理员配置；前端展示明确的加载失败状态和重试入口，避免无提示空白。

## 代码边界

- `src/platform_access/contracts.py`：二元 `PlatformRole` 与管理员邮箱常量。
- `src/platform_access/bootstrap.py`、`scripts/bootstrap_first_admin.py`：固定邮箱 bootstrap。
- `src/platform_access/repository.py`、`src/platform_access/service.py`：普通注册、管理员邮箱保留和角色写入。
- `src/web/api.py`、`src/platform_access/web.py`：移除旧单管理员运营总闸，保留认证和管理员接口保护。
- `src/platform_access/navigation_config.py`、`web-ui/src/navigation-access.js`、`web-ui/src/App.vue`：二元角色路由计算与可读错误状态。
- `migrations/versions/20260825_20_single_platform_admin.py`：角色归一化与数据库不变量。
- 对应 Python、Node 测试与平台访问运维文档同步更新。

不新增角色管理页面，不设计组织级自定义角色，不执行生产部署或生产数据写入。

## 错误处理

- 非固定邮箱尝试 bootstrap：返回明确错误，不生成密码散列写入。
- 管理员专用邮箱尝试普通注册：在发送/确认注册链路中拒绝，避免占用保留账号。
- 普通用户调用管理员接口：保持 403。
- 路由配置接口失败：页面展示错误与重试，不把“读取失败”误表现为“管理员关闭了全部模块”。

## 验证

- bootstrap：只接受固定邮箱；有普通用户但无管理员时仍可创建；已有管理员时拒绝。
- 注册：默认创建 `user`；管理员专用邮箱不能注册成普通用户。
- 迁移契约：旧角色归一化、固定管理员提升、邮箱检查、单管理员索引与 downgrade 完整。
- 导航：`admin` 始终可访问全部模块；`user` 服从模块开关且不能访问管理员模块。
- API：匿名请求 401；普通用户可以读取导航和普通业务 API；普通用户调用管理员接口仍为 403。
- 前端：导航配置成功、失败提示和重试均有测试。
- 全量执行定向 Python 测试、Web UI 测试、Alembic 单 head 检查和 Vite 生产构建。
