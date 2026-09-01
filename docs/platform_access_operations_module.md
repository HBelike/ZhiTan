# 平台访问、运行配置与观测模块

## 设计目标

平台从本地单人预览演进为可部署的个人产品时，需要把“谁可以使用”“谁可以改生产参数”“一次任务按什么参数运行”从前端状态和 YAML 文件中拆开。本模块提供最小但可扩展的账号、角色、会话和配置版本能力；不修改既有微信公众号工作流、技能库或求职助手的业务数据模型。

## 方案与取舍

- **身份与会话**：PostgreSQL 保存账号、邮箱身份、角色和会话摘要，浏览器只保存 HttpOnly Cookie。登录页支持“邮箱 + 验证码”和“邮箱 + 密码”两种入口，两者验证成功后共用同一套服务端会话。密码使用标准库 `scrypt` 加盐散列；验证码使用服务端 HMAC 摘要，不保存明文密码、验证码或 API Key。
- **注册与角色**：平台只保留 `admin/user` 两类角色。`admin@example.com` 是唯一允许成为 `admin` 的邮箱，通过受控 bootstrap 创建；其余公开注册账号固定获得 `user`。普通用户按管理员保存的路由模块开关访问业务页面，管理台、运行配置与 LangSmith 等管理员接口继续由服务端拒绝非 admin 请求。
- **会话策略**：每次有效请求将空闲会话续至最多 7 天，但任何会话从登录时起最长 30 天；密码重置会撤销该账号的全部旧会话。
- **邮件投递**：验证码使用 Resend HTTP API；服务端只读取 `RESEND_API_KEY`、`RESEND_FROM_ADDRESS` 与 `PLATFORM_EMAIL_CODE_SECRET`。验证码有效 10 分钟、60 秒发送冷却、最多 5 次输入尝试。
- **发件域名**：`resend.dev` 仅可用于向 Resend 账户自身的登录邮箱做测试，不能向 QQ 等其他邮箱投递。正式运行需在 Resend 验证自己的域名，并把 `RESEND_FROM_ADDRESS` 配为该域名下的地址；失败时系统会删除未投递挑战，用户可在修复配置后立即重新发送。
- **注册开关**：生产环境的 `PLATFORM_PUBLIC_REGISTRATION_ENABLED=true` 时，用户先通过邮箱验证码完成注册，再以邮箱和密码登录；设为 `false` 时仅保留已有账号登录、绑定邮箱与密码找回。首个管理员仍由服务器 CLI 初始化，避免首账号被公网抢注。
- **运行配置**：每一次保存会形成 JSONB 版本快照。手动/定时流水线读取已激活版本并将版本号写入执行记录，避免编辑中的参数影响运行中的任务。
- **路由模块配置**：管理台以固定顺序维护求职助手、工作台、简历助手、面经库、职位库、技能库、评测中心、LangSmith、管理台九个顶级模块，并在“求职助手子功能”中分别维护 `career_interview_master` 与 `career_online_assessment`。两个开关默认开启，可独立控制面试大师和线上笔试助手的按钮与直达路由，并对包括管理员在内的所有用户生效。旧共享键 `career_interview_tools` 读取时会继承到两个新键。配置继续按组织保存到现有 `route_module_settings`，不新增表。管理台固定开启，评测中心、LangSmith、管理台仍需 `admin` 角色，模块开关不会提升普通用户权限。管理台按单页单任务拆分为 `/admin/modules`、`/admin/github`、`/admin/prompts` 三个子路由，旧地址 `/admin` 自动进入可见模块页。
- **观测**：LangSmith 是平台一级路由。LangChain 调用使用 `run_name`，非 LangChain 调用使用轻量 trace/span；默认只记录脱敏元数据、状态和用量，不上传简历、图片、Cookie、密码或 API Key。页面优先 iframe 嵌入，遇到 CSP、X-Frame-Options 或第三方 Cookie 限制时提供新窗口打开。
- **工作流实时日志**：管理员手动工作流把当前执行线程的 `INFO` 及以上日志和 Task 生命周期写入 PostgreSQL 事件表。工作台先读取历史事件，再通过 SSE 按游标续传；浏览器断线不会取消后台工作流，已完成运行仍可回看完整链路。

## 调用链

```text
LoginPage
  -> /api/auth/bootstrap-status
  -> /api/auth/bootstrap/send-code | /api/auth/register/send-code
  -> /api/auth/email-login/send-code | /api/auth/email-login/verify
  -> /api/auth/*/verify | /api/auth/login
  -> HttpOnly platform_session Cookie
  -> /api/auth/me
  -> /api/navigation/modules
  -> 前端路由守卫（角色权限 ∩ 管理员模块开关）
      -> 管理员配置 / 可观测性 / 人工运行入口
      -> 后端角色依赖校验

AdminConsolePage
  -> /admin/modules | /admin/github | /admin/prompts
  -> GET /api/navigation/modules
  -> PUT /api/admin/navigation-modules
  -> PlatformAccessService.save_route_modules
  -> PlatformAccessRepository.save_route_module_settings
  -> career_assistant.route_module_settings

career_interview_master
  -> App.vue 校验父模块与面试大师开关
  -> CareerAssistantPage 隐藏面试大师入口
  -> /career/interview-master 直达守卫

career_online_assessment
  -> App.vue 校验父模块与线上笔试开关
  -> CareerAssistantPage 隐藏线上笔试入口
  -> /career/online-assessment 直达守卫

ManualPipelinePanel
  -> GET /api/admin/pipeline-runs/{id}/logs（历史事件）
  -> GET /api/admin/pipeline-runs/{id}/logs/stream（SSE 增量事件）
  -> PlatformAccessService.list_manual_pipeline_events
  -> PlatformAccessRepository.list_pipeline_execution_events
  -> career_assistant.pipeline_execution_events
```

## 数据边界与部署

- 新表位于 `career_assistant` schema：`platform_users`、`platform_sessions`、`platform_email_challenges`、`pipeline_config_versions`、`pipeline_execution_requests`、`pipeline_execution_events`、`route_module_settings`。
- `20260825_20` 将旧 `viewer/operator` 归一化为 `user`，把 `admin@example.com` 提升或保持为 `admin`，并通过角色检查、管理员邮箱检查与 `uq_platform_users_single_admin` 部分唯一索引共同固定管理员边界。
- migration 只修改平台账号角色及其约束，不修改现有会话、面经、技能库或微信公众号表；会话解析实时读取账号角色，因此无需重建会话。
- 生产环境必须通过 `CAREER_DATABASE_URL` 连接 PostgreSQL，并以 HTTPS 部署 Cookie；本地开发可通过 HTTP 使用同一代码路径。
- LangSmith Key、模型 Key、平台登录 Cookie 只能放在服务端环境变量或受控密钥服务，永不进入页面接口。

## 验证清单

1. 迁移后可查询 bootstrap 状态。
2. 首个管理员和普通用户均需完成邮箱验证码；第二次 bootstrap 被拒绝。
3. 未登录访问管理员接口返回 401，非 admin 返回 403。
4. 登录后刷新页面仍可通过会话 Cookie 恢复身份；退出后 Cookie 和服务端会话一并失效。
5. 配置保存生成递增版本，执行记录关联固定版本。
6. 使用 `resend.dev` 向非账户邮箱投递时，接口返回明确的域名验证提示；发件失败后不会留下冷却中的验证码挑战。
7. 求职助手位于导航首位，并作为根路径、登录成功和未知地址的默认入口；若普通用户的求职助手被关闭，则跳转到其首个可访问模块。
8. 路由模块默认全部启用；非管理员保存配置返回 403；关闭模块后普通用户侧栏不再展示，直接输入对应地址会跳转到首个可访问模块。
9. 管理员始终可访问全部模块，包括开关关闭或配置读取失败时；关闭评测中心或 LangSmith 不会改变其他模块，启用管理员专属模块也不会让普通用户获得访问权限。
10. 手动工作流运行中打开日志弹窗可持续收到事件；刷新或断线后按最后事件 ID 补发；运行结束后 SSE 关闭且历史日志仍可读取。
11. 面试大师和线上笔试助手可以分别关闭；关闭项对普通用户和管理员都隐藏入口并阻止直达，未关闭项保持可用；管理员始终可从管理台恢复配置。

## 2026-09-02：面试大师与线上笔试独立开关

- **设计目标**：把原先的共享开关拆成两个独立开关，并让子功能开关对包括管理员在内的所有用户生效。
- **技术取舍**：继续复用 `route_module_settings` JSONB，不新增迁移或数据表；新键为 `career_interview_master`、`career_online_assessment`，读取旧键 `career_interview_tools` 时将其布尔值继承给两个新键，显式新键优先。
- **调用链**：管理台分别保存两个布尔值 → 后端按角色返回同一子功能可访问状态 → `App.vue` 分别控制直达路由 → `CareerAssistantPage` 分别控制两个按钮。
- **权限边界**：顶级模块仍保留管理员豁免；两个求职子功能没有管理员豁免。管理台本身固定开启，管理员不会因关闭子功能而失去恢复入口。
- **验证结果**：覆盖默认目录、旧键继承、独立开关、管理员关闭状态、按钮属性和直达路由消费键；前后端定向测试通过。

## 2026-09-01：求职助手子功能展示开关（初版，已由双开关替代）

- **设计目标**：用一个管理台开关同时控制面试大师与线上笔试助手的普通用户展示。
- **技术取舍**：复用路由模块 JSONB 配置，在固定目录中增加 `scope=feature` 的 `career_interview_tools`，不新增表、接口或独立管理页面。
- **调用链**：管理台保存完整布尔映射 → `/api/admin/navigation-modules` → `route_module_settings` → 普通用户加载 `/api/navigation/modules` → 求职助手入口和两个直达路由共同消费 `accessible`。
- **权限边界**：开关只影响普通用户；管理员继续无视展示开关，业务 API 的原有身份校验不变。

## 2026-08-30：手动工作流实时日志

- **设计目标**：在工作台每条手动运行记录中提供“查看日志”按钮，动态展示工作流与 Task 链路，并支持运行结束后的历史回看。
- **技术取舍**：不解析混合多个请求的全局 `app.log`，新增 `pipeline_execution_events` 保存结构化事件；实时传输使用现有 FastAPI `StreamingResponse` SSE 模式，不引入 WebSocket 或新依赖。
- **调用链**：`ManualPipelineRunner` 创建专属 `PipelineExecutionLogHandler` → `Application/LoggerManager` 附加到当前工作流线程 → `BaseTask` 输出结构化生命周期字段 → PostgreSQL 持久化 → 历史 JSON 接口 + SSE 游标接口 → `PipelineLogDialog.vue` 合并去重并自动滚动。
- **数据边界**：接口继续受 `require_admin` 保护，并按 `organization_id` 联表校验运行归属；单条消息限制 8000 字符，默认不新增 Prompt、模型原始响应、Cookie、密码或 API Key 的采集。
- **验证结果**：本地 PostgreSQL 已升级至 `20260830_31`；日志 Handler、SSE 游标与终态测试通过；PC 弹窗完成本地实际渲染检查；Alembic 保持单 head，前端生产构建通过。
- **后续边界**：本次不实现日志搜索、下载、删除、远程归档和手机端专项适配；未执行生产迁移或部署。

## 2026-08-28：面经管理员身份与会话透传修复

- **设计目标**：当前唯一管理员 `admin@example.com` 可维护全部公开面经，普通用户继续只维护自己创建或采集入库的记录。
- **技术取舍**：前端不根据账户区的“管理员”文字自行放权，面经详情、编辑和删除统一依赖服务端注入的 `CareerRequestActor.role` 与返回的 `can_write`。
- **调用链**：浏览器 Cookie → `enforce_platform_access` 解析平台会话 → 注入真实 `organization_id/actor_id/role` → 面经详情计算 `can_write` → 编辑、删除接口再次执行相同的服务端权限检查。即使本地关闭 `PLATFORM_AUTH_REQUIRED`，只要存在有效会话也会传递真实角色；该开关只决定未登录业务请求是否返回 401。
- **数据边界**：管理员邮箱和数据库约束继续沿用 `20260825_20`，不新增账号迁移；本次只修复会话角色到面经请求上下文的传递。
- **验证结果**：本地后端、前端、Alembic 单 head 与 Vite 生产构建通过；生产部署仍需单独授权。

## 2026-08-25：默认入口与管理员路由边界

- **设计目标**：求职助手成为平台默认任务入口；管理员维护的模块开关只决定普通用户可见页面，不能反向限制管理员本人。
- **技术取舍**：后端保留 `enabled` 作为真实配置值，同时把管理员的 `accessible` 固定为 `true`；前端再按登录角色做同样的管理员豁免，避免旧接口响应或配置读取失败造成管理员被锁在页面外。
- **调用链**：登录或根路径进入 `DEFAULT_APP_ROUTE=/career` → `App.vue` 加载 `/api/navigation/modules` → `route_modules_for_ui` 计算普通用户可访问状态 → 前端路由守卫校验直接地址并在必要时跳到首个可访问模块。
- **管理员账号**：migration `20260825_20` 把角色收敛为 `admin/user`，并把唯一管理员邮箱固定为 `admin@example.com`；CLI bootstrap 与普通注册均执行相同的邮箱保留规则。
- **验证结果**：本地后端 163 项测试、前端 105 项测试、3 个平台权限验证脚本、Alembic 单 head 检查和 Vite 生产构建均通过；本地迁移回退再升级通过，最终账号为 1 个 admin、2 个 user。
- **后续边界**：本次不增加角色管理页面、不改变各业务 API 的既有鉴权策略，也不执行生产迁移或部署。

## 2026-08-25：二元角色与固定管理员

- **设计目标**：平台只区分唯一管理员和普通用户，修复生产旧总闸导致普通账号登录后导航全空的问题。
- **技术取舍**：认证中间件只验证会话并注入真实 Career Actor；普通用户可以请求路由目录和普通业务 API，管理员接口继续通过 `require_admin` 保护，不再使用 `PLATFORM_CLOSED_OPERATOR_MODE` 全局拦截。
- **调用链**：`/api/auth/me` 返回 `admin/user` → `/api/navigation/modules` 结合模块开关计算 `accessible` → 前端侧栏与直接地址守卫消费同一结果；读取失败时展示可重试错误，不伪装为管理员关闭全部模块。
- **本地数据预期**：`admin@example.com` 为唯一 `admin`，`user@example.com` 与其他账号均为 `user`。
- **发布边界**：本次只修改和验证本地代码与数据库；生产账号和容器未修改，需再次获得明确投产授权后才执行生产迁移。

## 认证门户界面调整（2026-08-13）

- **设计目标**：把登录、注册和密码找回收敛为单任务认证门户，减少双标签、状态徽标和长期说明造成的视觉噪音，同时保留职业工作台的品牌识别。
- **技术取舍**：桌面端使用品牌叙事与 420px 单列表单的双栏结构；注册入口、密码找回改为文字级次要动作。`900px` 以下隐藏装饰面板，仅保留紧凑品牌和认证表单，避免移动端横向溢出。
- **调用链**：`LoginPage.vue` 根据 `/login`、`/register`、`/forgot-password` 选择表单状态，仍调用原有 `/api/auth/*` 接口；本次没有修改认证协议、数据表或邮件投递逻辑。
- **验证结果**：本地桌面端三条认证路由完成视觉检查；`390×844` 登录页无横向溢出，前端控制台无 error，`npm run build` 与 `git diff --check` 通过。
- **发布边界**：当前调整只在本地工作区验证，未提交、未推送、未更新生产环境；需在用户明确授权后再发布。

## 登录双通道调整（2026-08-16）

- **设计目标**：登录入口允许用户在“邮箱 + 验证码”和“邮箱 + 密码”之间直接切换，注册与密码找回继续使用原有独立入口。
- **技术取舍**：验证码登录只新增发送与校验接口，校验成功后复用现有 `platform_session` 会话；前端始终展示邮箱和 6 位验证码输入框，将“获取验证码”和“登录”拆成两个独立动作，避免发送失败时用户无处输入验证码。数据库迁移 `20260816_09` 仅把 `platform_email_challenges.purpose` 的允许值扩展为包含 `login`。
- **调用链**：`LoginPage.vue -> /api/auth/email-login/send-code -> PlatformAccessService.send_login_code() -> ResendEmailDelivery`；校验阶段为 `LoginPage.vue -> /api/auth/email-login/verify -> PlatformAccessService.authenticate_with_code() -> platform_session Cookie`。
- **验证结果**：本地 PostgreSQL 已升级至 `20260816_09`；真实发送接口向 QQ 邮箱返回 `accepted=true`，认证接口契约脚本、Python 编译检查和前端生产构建通过；桌面端两种方式切换正常，`390×844` 手机视口无横向溢出，浏览器控制台无错误。
- **功能边界**：本次未修改注册、密码重置、角色授权和生产部署配置，改动仅保留在本地工作区。
