# 求职会话与 Skill 用户归属权限设计

## 状态

- 设计日期：2026-08-26
- 用户确认：已确认
- 实施范围：仅本地修改、测试和构建；不包含生产部署

## 目标

为求职助手历史会话和平台 Skill 建立一致的用户归属与管理员越权边界：

1. 唯一管理员可以查看并修改所有用户的求职会话和 Skill。
2. 普通用户只能查看、搜索、读取、修改和调用自己的资源。
3. 平台已安装的 `find-skills` 作为全员可见、可调用但普通用户不可修改的兜底 Skill。
4. 求职助手通过 `find-skills` 安装的 Skill 直接进入会话所有者的技能库，不建立第二套同步任务或正文数据库。
5. 列表、详情、写操作和运行时调用使用同一套服务端授权规则，不能仅依赖前端过滤。

## 非目标

- 不增加组织级共享、用户组共享、公开发布或细粒度 ACL。
- 不把用户安装的 Skill 自动推送到 GitHub 或其他外部源码仓库。
- 不新增 Skill 删除能力；现有保存和更新能力按授权范围工作。
- 不为权限功能引入数据库备份、容灾或其他超出需求的设计。
- 本轮先完成并验证 PC 端，不做手机端或平板端专项适配。

## 已有实现与问题

### 求职会话

生产认证中间件已经把平台用户 ID 映射为 Career `actor_id`。普通用户会话列表、详情、消息和大部分写操作已经按 `actor_id` 查询，因此现有普通用户隔离基础可复用。

当前缺口是请求身份不携带角色，仓储和路由始终把管理员视为普通用户。管理员无法列出全部会话，也无法对其他用户会话执行详情读取、重命名、归档、删除、上下文更新或继续聊天。

### Skill

`SkillLibraryService` 当前扫描共享目录，并把保存和 GitHub 安装结果统一写入 `.agents/skills/<skill-name>/`。技能库 API、求职助手 Skill 候选、运行时挂载和安装工具虽然复用同一服务，但都没有用户归属参数。因此任意已登录用户都可能看到、读取、编辑或调用同一批 Skill。

生产环境中的 API、Career Worker 和 Scheduler 已共享 `application_skills:/app/.agents` 持久卷。这个既有条件可以继续作为 Skill 的唯一正文事实源。

## 设计原则

1. **服务端默认拒绝**：资源不在当前访问范围内时返回不存在，不通过前端隐藏代替鉴权。
2. **一次解析，沿用真实所有者**：管理员操作其他用户会话时，服务端先解析会话真实所有者，后续关联查询和写入都使用该所有者，不能接受前端声明的所有者 ID。
3. **单一正文事实源**：Skill 正文仍只保存在持久卷，不同时复制到数据库。
4. **同一范围贯穿全链路**：Skill 列表、搜索、详情、保存、安装、Mention 候选和模型运行时挂载必须接收相同访问范围。
5. **兼容现有生产目录**：不移动现有 57 个 Skill，通过目录层级和角色解释其归属。

这些原则与成熟开源实现一致：Open WebUI 为 Tool 保存所有者，并在列表、详情和实际运行阶段分别校验访问权；OWASP 也要求默认拒绝并在每个请求上验证授权。

- Open WebUI Tool 路由：<https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/tools.py>
- Open WebUI Tool 模型：<https://github.com/open-webui/open-webui/blob/main/backend/open_webui/models/tools.py>
- OWASP Authorization Cheat Sheet：<https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>

## 统一访问上下文

新增不可变的服务端访问上下文，至少包含：

```python
@dataclass(frozen=True)
class ResourceAccessScope:
    requester_id: UUID
    requester_role: PlatformRole
    organization_id: UUID

    @property
    def is_admin(self) -> bool:
        return self.requester_role is PlatformRole.ADMIN
```

HTTP 中间件从已验证的 `platform_session` 构造该上下文。未启用生产认证的本地开发仍使用现有默认组织和默认 Actor，并按管理员范围处理，避免破坏离线验证脚本；生产环境没有会话时继续返回 401。

访问上下文只描述请求者。目标资源所有者必须从数据库记录或受控文件目录解析，不能从请求体中的任意 `owner_id` 获取。

## 求职会话授权设计

### 访问矩阵

| 操作 | 普通用户 | 管理员 |
| --- | --- | --- |
| 会话分页列表 | 仅自己的活动会话 | 全部用户的活动会话 |
| 会话详情与消息 | 仅自己的会话 | 全部会话 |
| 重命名、归档、删除 | 仅自己的会话 | 全部会话 |
| 更新上下文、重试评估 | 仅自己的会话 | 全部会话 |
| 提交、观察、取消 Turn | 仅自己的会话 | 全部会话 |
| 继续他人会话 | 禁止 | 允许，按会话所有者身份落库 |

### 列表

会话仓储增加访问范围版分页查询：

- 普通用户继续使用 `WHERE conversation.actor_id = :requester_id`。
- 管理员不加 `actor_id` 限制，仍排除 `deleted` 并保留既有状态、排序、分页和总数规则。
- 管理员响应为每条会话附加所有者 ID、显示名称和邮箱；普通用户响应不需要重复返回自己的邮箱。
- 管理员列表支持可选所有者筛选，筛选值必须是实际平台用户 ID。

### 单资源解析

增加统一的会话访问解析函数：

```python
def resolve_accessible_conversation(
    repository: CareerConversationRepository,
    scope: ResourceAccessScope,
    conversation_id: UUID,
) -> ConversationRecord:
    ...
```

普通用户查询时要求 `conversation.actor_id == scope.requester_id`；管理员只要求会话存在且未删除。失败统一映射为 404“会话不存在、已删除或无访问权限”。

解析成功后，详情消息、最近模型选择、最新 Turn、上下文、岗位评估和后续写操作全部使用 `conversation.actor_id` 作为有效所有者。这样管理员操作他人会话时不需要放宽每张关联表的所有者条件，也不会把管理员自己的简历、Skill 或 Turn 错接到目标会话。

只有“创建新会话”始终以请求者为所有者；管理员不会通过普通创建接口替其他用户创建会话。

### Turn 与 Worker

管理员向其他用户会话提交消息时，API 先完成访问解析，再把会话真实 `actor_id` 写入 Turn 和持久化 Payload。Worker 继续只处理已经确定所有者的任务，不接收或信任管理员角色，也不在后台放宽查询。

按 `turn_id` 操作的观察、取消和事件流接口需要先解析 Turn 所属会话，再应用同一访问矩阵。普通用户猜测其他用户的 `turn_id` 时同样返回 404。

## Skill 归属与目录设计

### 目录规则

```text
.agents/skills/
├── find-skills/                 # 公共只读兜底，管理员可维护
├── <legacy-admin-skill>/        # 现有根目录 Skill，视为管理员资产
└── _users/
    └── <platform-user-uuid>/
        └── <skill-name>/
            ├── SKILL.md
            └── ...              # GitHub 安装时保留的同目录资源
```

`_users` 是保留目录名，不作为 Skill 暴露。普通用户创建或安装时只能写入自己的 UUID 目录。管理员在技能库编辑其他用户 Skill 时，服务端根据已授权的现有 Skill ID 定位原路径；创建新 Skill 仍默认归管理员自己。

现有根目录不迁移：

- `find-skills` 解释为 `visibility=shared_readonly`。
- 其他根目录 Skill 解释为 `visibility=private` 且所有者为唯一管理员。
- 本地开发环境扫描到的宿主机用户 Skill 和插件 Skill 只对管理员可见，普通平台用户永远不扫描宿主机 `Path.home()` 下的目录。

### Skill 响应元数据

`SkillSummary` 增加：

- `owner_id: str | None`
- `owner_display_name: str | None`
- `visibility: "private" | "shared_readonly"`
- `editable: bool`

普通用户看到公共 `find-skills` 时 `editable=false`。管理员查看公共或任意用户 Skill 时 `editable=true`。管理员列表用所有者标识区分不同用户的同名 Skill。

### 授权范围

Skill 服务方法不再隐式扫描全局，而是显式接收访问范围：

```python
list_skills(scope: ResourceAccessScope) -> list[SkillSummary]
get_skill(scope: ResourceAccessScope, skill_id: str) -> SkillDetail
save_skill(scope: ResourceAccessScope, ...) -> SkillSaveResult
search_skills(scope: ResourceAccessScope, query: str, ...) -> SkillSearchResult
install_skill_repository(owner_id: UUID, source: str, skill_names: Sequence[str]) -> SkillRepositoryInstallResult
```

普通用户允许的读取集合是自己的目录和公共 `find-skills`。管理员允许的读取集合是根目录、所有用户目录和管理员可见的本地开发来源。

详情、保存和安装时使用解析后的绝对路径做目录包含检查。无法在授权集合中解析的 Skill ID 统一返回 404。普通用户不能通过修改名称、Skill ID 或路径把文件写入其他用户目录。

编辑现有 Skill 时请求携带稳定 `skill_id`，服务端据此确定原所有者和原路径；新建时没有 `skill_id`，目标固定为请求者自己的目录。普通用户不能以 `find-skills` 为名称创建覆盖公共兜底，管理员维护公共 `find-skills` 时必须通过它的既有 `skill_id` 更新。

### 搜索缓存

左侧“已安装技能”搜索继续在 `/api/skills` 返回的授权列表中执行，因此不会接触未授权条目。

右侧 GitHub 开放 Skill 搜索保持公共候选能力，但需要满足：

1. 本地搜索和超时回退只使用 `list_skills(scope)` 的授权结果。
2. 全局 `skill_search_cache.json` 只保存公共 GitHub 候选和公共仓库元数据。
3. 用户私有 Skill 的名称、描述、正文、路径和所有者不得写入全局搜索缓存或 LangSmith Trace。

## 求职助手安装入库

### 调用链

```text
用户在求职会话输入 /find-skills 并明确要求安装
→ Career API/Worker 从 Conversation 或 Turn 得到真实 owner_id
→ find-skills 调用 inspect_skill_repository
→ install_skill_repository 接收 owner_id
→ 下载到同一用户目录内的临时目录
→ 校验文件数、总大小、相对路径和 SKILL.md
→ 原子复制到 _users/<owner_id>/<skill-name>/
→ Turn 结果返回 installed_names 与明确提示
→ /skills 按登录身份重新扫描同一持久卷
```

普通用户会话的安装结果归该用户；管理员自己的会话归管理员；管理员在其他用户会话中安装时归该会话所有者。

新安装的 Skill 在落盘后即可被后续请求的 Mention 和运行时加载，不在安装它的当前模型轮次中自动切换或执行。助手回复需要提示用户在下一轮使用 `/<skill-name>`。

### 跨进程一致性

Career Worker、API 和 Scheduler 继续挂载同一个 `application_skills:/app/.agents` 卷。`SkillLibraryService` 每次列表或详情请求按授权目录扫描文件，不保存进程内私有目录缓存，因此 Worker 安装完成后 API 无需接收同步事件。

技能库页面在进入 `/skills` 时重新请求列表；已打开页面可通过现有刷新或重新进入页面读取新条目。本轮不增加 WebSocket 或跨标签页实时推送。

### 安装冲突与错误

- 同一所有者目录已有同名 Skill 时保持现有“跳过已存在项”语义，不覆盖用户修改。
- 不同用户可以安装同名 Skill，目录和 Skill ID 独立。
- 下载、ZIP、路径、文件数、大小或 `SKILL.md` 校验失败时不创建目标目录。
- 安装已落盘但前端连接中断时，Skill 仍视为成功；下次技能库列表会显示。
- 工具结果继续返回 `installed_names`、`skipped_existing_names` 和文件数，并把用户可读消息改为“已安装到你的技能库”。

## API 与 PC 端行为

### 求职助手

- 普通用户历史栏保持现有布局，只返回自己的会话。
- 管理员历史栏显示会话所有者，并提供所有者筛选。
- 管理员选择其他用户会话后，现有重命名、归档、删除和发送入口保持可用。
- 普通用户无需看到角色或权限说明，越权请求以现有空结果或 404 呈现。

### 技能库

- 普通用户左侧仅显示自己的 Skill 和公共 `find-skills`。
- `find-skills` 显示“公共内置”与“只读”，保存按钮禁用。
- 管理员列表显示所有者标识，并提供所有者筛选。
- 管理员选择其他用户 Skill 后可以通过稳定 Skill ID 更新原文件。
- 求职助手安装成功后的最终状态明确显示已进入技能库；用户进入技能库时自动重新加载。

## 安全边界

1. 前端提交的 `owner_id` 不参与权限判定。
2. 普通用户对他人会话 ID、Turn ID 或 Skill ID 的直接访问统一返回 404。
3. 所有文件写入在解析后的用户根目录内再次执行 `relative_to` 边界检查。
4. `_users`、`.`、`..`、绝对路径和路径分隔符不能作为 Skill 名称的一部分。
5. 公共 `find-skills` 的只读限制同时应用于保存接口和安装冲突处理，不能只禁用按钮。
6. Worker 只使用持久化 Turn 的真实所有者，不继承 HTTP ContextVar，也不相信模型工具参数中的所有者。
7. 管理员权限来自服务端 `PlatformRole.ADMIN` 和现有唯一管理员约束，不根据邮箱、前端标签或请求参数临时判断。

## 验证策略

### 后端单元与仓储测试

- 普通用户 A 的会话分页只返回 A，会话总数不包含 B。
- A 对 B 的会话详情、重命名、归档、删除、上下文和 Turn 操作均得到 404。
- 管理员列表返回 A、B，会话详情和全部现有写操作成功。
- 管理员在 B 会话继续聊天时，新 Turn、消息、上下文和 Skill 范围保持 B 的 `actor_id`。
- 普通用户 A 的 Skill 列表只包含 A 和公共 `find-skills`。
- A 按 B 的 Skill ID 读取、保存或调用时得到 404 或“未找到 Skill”。
- 管理员能列出并更新 A、B 和根目录管理员 Skill。
- 普通用户不能更新公共 `find-skills`，管理员可以。
- A、B 安装同名 Skill 后目录和 ID 独立。
- 管理员在 B 会话安装后，B 可见、A 不可见、管理员可见。
- GitHub 搜索缓存和 Trace 不包含用户私有 Skill 字段。
- Worker 安装与 API 列表使用临时共享目录模拟生产持久卷并通过跨服务读取。

### 前端测试

- 普通用户技能列表不渲染未授权条目。
- 公共 `find-skills` 显示只读状态并禁用保存。
- 管理员 Skill 和会话列表显示所有者与筛选入口。
- 求职助手安装完成状态显示“已安装到你的技能库”。
- 进入技能库会重新拉取列表并显示刚安装的 Skill。

### 回归验证

- 求职会话分页、历史详情、重命名、归档、删除、Turn 队列、Worker 恢复和 SSE 测试。
- Skill 列表、详情、保存、GitHub 搜索、仓库检查、安装、Mention、会话继承和 Tool Calling 测试。
- Python 编译检查、后端相关测试、前端 Node 测试和 Vite production build。

## 文档同步

实施完成后同步更新：

- `docs/career_assistant_module.md`：会话权限、管理员跨用户操作和按会话所有者安装 Skill 的调用链。
- `docs/skill_library_module.md`：目录归属、公共 `find-skills`、接口授权和安装入库行为。
- `docs/platform_production_deployment.md`：共享持久卷中的管理员根目录与用户命名空间，以及本地验收步骤。

## 完成标准

1. 普通用户不能通过列表、搜索、直接 ID、写接口或运行时调用接触其他用户的会话或 Skill。
2. 管理员可以通过现有产品入口完整读写所有用户的会话和 Skill。
3. 每个普通用户至少能看到并调用公共只读 `find-skills`。
4. 求职助手安装成功的 Skill 无需复制或同步任务即可出现在正确用户的技能库。
5. 现有 57 个 Skill 不移动：`find-skills` 全员只读，其余仅管理员可见。
6. 相关后端、前端和构建验证通过，项目模块文档与实现一致。
