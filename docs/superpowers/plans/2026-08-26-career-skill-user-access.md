# 求职会话与 Skill 用户权限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让普通用户只能访问自己的求职会话和 Skill，让唯一管理员可完整读写所有用户资源，并使求职助手安装的 Skill 直接进入会话所有者的技能库。

**Architecture:** 认证中间件生成统一的 `ResourceAccessScope`，所有列表、详情、写操作和运行时调用都显式接收该范围。会话先由服务端解析真实所有者，再沿用该所有者访问消息、Turn、上下文和 Skill；Skill 正文继续使用共享持久卷，通过 `.agents/skills/_users/<user-id>/` 分区，不新增正文数据库或同步任务。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、PostgreSQL、ContextVar、文件系统原子写入、Vue 3、Element Plus、Node test runner、Vite

**Spec:** `docs/superpowers/specs/2026-08-26-career-skill-ownership-access-design.md`

## Global Constraints

- 仅在本地修改、测试和构建；没有本轮明确授权时不得部署到生产、连接生产服务器或重建生产容器。
- 唯一管理员的权限来源只能是服务端 `PlatformRole.ADMIN`；不得根据邮箱、页面标签或请求参数判断。
- 普通用户只能读写自己的会话与 Skill；越权访问会话 ID、Turn ID 或 Skill ID 时统一返回 404。
- 公共 `find-skills` 对所有用户可见、可调用；普通用户只读，管理员可维护。
- 现有 `.agents/skills/` 根目录内容不迁移：`find-skills` 视为公共内置，其余根目录 Skill 视为管理员资产。
- 新建或安装的私有 Skill 只写入 `.agents/skills/_users/<platform-user-uuid>/<skill-name>/`。
- 求职助手安装 Skill 时以数据库中会话真实所有者为目标，不接受模型参数或前端请求体声明的 `owner_id`。
- Skill 正文仍只有共享持久卷这一份事实源；不新增数据库正文、副本同步、GitHub 推送、WebSocket 推送或删除功能。
- GitHub 搜索缓存和 LangSmith Trace 不得包含用户私有 Skill 的名称、描述、正文、路径或所有者。
- 先完成并验证 PC 端；本计划不含手机端、iPad 或平板专项适配。
- 当前工作区已有未提交的用户改动；执行时不得覆盖 `docs/career_assistant_module.md`、`web-ui/src/components/CareerAssistantPage.vue` 等既有修改，必须基于实际 diff 合并。
- 每个任务完成后更新对应测试并单独提交；不把无关工作区文件加入提交。

## 文件职责映射

- `src/platform_access/contracts.py`：定义统一访问上下文和角色判断。
- `src/platform_access/request_scope.py`：保存每个 HTTP 请求的 ContextVar，并提供本地管理员兜底范围。
- `src/platform_access/repository.py`、`src/platform_access/service.py`：为管理员列表补充用户显示名称和邮箱查询。
- `src/career_assistant/persistence/conversation_repository.py`：执行会话列表与单资源的服务端授权查询。
- `src/career_assistant/web/router.py`：集中解析可访问会话，之后只沿用数据库中的真实所有者。
- `src/career_assistant/persistence/turn_job_repository.py`、`src/career_assistant/web/turn_router.py`：控制 Turn 状态、事件、取消和继续聊天的跨用户访问。
- `src/services/skill_library_service.py`：按访问范围扫描、读取、保存和安装 Skill，并执行目录包含检查。
- `src/web/api.py`：把平台会话角色写入访问上下文，并让 Skill API 全部使用该上下文。
- `src/career_assistant/skill_runtime.py`、`src/career_assistant/skill_tools.py`、`src/career_assistant/response_runner.py`：让 Mention、挂载和安装始终使用会话所有者范围。
- `web-ui/src/skill-access-view.js`：技能库 PC 端的所有者筛选和只读状态纯函数。
- `web-ui/src/career-history-access.js`：求职历史 PC 端的所有者筛选和展示纯函数。
- `web-ui/src/App.vue`、`web-ui/src/components/CareerAssistantPage.vue`：接入管理员筛选、所有者标识、只读按钮和安装成功提示。

---

### Task 1: 建立统一访问上下文与平台用户目录

**Files:**
- Modify: `src/platform_access/contracts.py`
- Create: `src/platform_access/request_scope.py`
- Modify: `src/platform_access/repository.py`
- Modify: `src/platform_access/service.py`
- Modify: `src/web/api.py:210-251`
- Modify: `src/career_assistant/live_interview/web.py:360-380`
- Test: `tests/test_resource_access_scope.py`
- Test: `tests/test_single_platform_admin_migration.py`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Consumes: 已认证的 `SessionResolution.user: PlatformUser`，以及本地开发使用的 `DEFAULT_ORGANIZATION_ID`、`DEFAULT_ACTOR_ID`。
- Produces: `ResourceAccessScope`、`set_resource_access_scope(scope)`、`reset_resource_access_scope(token)`、`get_resource_access_scope()`、`PlatformAccessService.list_active_users(scope)`。

- [ ] **Step 1: 写访问上下文失败测试**

```python
class ResourceAccessScopeTest(unittest.TestCase):
    def test_admin_scope_exposes_role_and_requester(self) -> None:
        scope = ResourceAccessScope(
            organization_id=UUID("00000000-0000-0000-0000-000000000001"),
            requester_id=UUID("00000000-0000-0000-0000-000000000002"),
            requester_role=PlatformRole.ADMIN,
        )
        self.assertTrue(scope.is_admin)
        self.assertEqual(scope.actor_id, scope.requester_id)

    def test_context_is_reset_after_request(self) -> None:
        token = set_resource_access_scope(self.user_scope)
        self.assertEqual(get_resource_access_scope(), self.user_scope)
        reset_resource_access_scope(token)
        self.assertNotEqual(get_resource_access_scope(), self.user_scope)
```

- [ ] **Step 2: 运行测试并确认当前缺少统一类型**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_resource_access_scope.py" -v`

Expected: FAIL，提示无法导入 `ResourceAccessScope` 或 `request_scope`。

- [ ] **Step 3: 实现不可变访问上下文与 ContextVar**

```python
@dataclass(frozen=True)
class ResourceAccessScope:
    organization_id: UUID
    requester_id: UUID
    requester_role: PlatformRole

    @property
    def actor_id(self) -> UUID:
        return self.requester_id

    @property
    def is_admin(self) -> bool:
        return self.requester_role is PlatformRole.ADMIN
```

```python
_resource_access_scope: ContextVar[ResourceAccessScope | None] = ContextVar(
    "resource_access_scope",
    default=None,
)

def set_resource_access_scope(scope: ResourceAccessScope) -> Token[ResourceAccessScope | None]:
    return _resource_access_scope.set(scope)

def reset_resource_access_scope(token: Token[ResourceAccessScope | None]) -> None:
    _resource_access_scope.reset(token)

LOCAL_DEFAULT_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
LOCAL_DEFAULT_REQUESTER_ID = UUID("00000000-0000-0000-0000-000000000002")

def get_resource_access_scope() -> ResourceAccessScope:
    current = _resource_access_scope.get()
    if current is not None:
        return current
    return ResourceAccessScope(
        organization_id=LOCAL_DEFAULT_ORGANIZATION_ID,
        requester_id=LOCAL_DEFAULT_REQUESTER_ID,
        requester_role=PlatformRole.ADMIN,
    )
```

本地兜底范围在 `get_resource_access_scope()` 内按现有默认组织和 Actor 构造 `PlatformRole.ADMIN`。为避免循环导入，默认 UUID 作为参数由 `src/web/api.py` 或 Career 兼容包装传入，不让 `platform_access` 反向依赖 Career 模块。

- [ ] **Step 4: 增加管理员可用的平台用户目录**

```python
def list_active_users(self, organization_id: UUID) -> tuple[PlatformUser, ...]:
    with self._database.transaction() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, organization_id, username, display_name, email,
                       email_verified_at, role, is_active, created_at
                FROM career_assistant.platform_users
                WHERE organization_id = :organization_id
                  AND is_active = TRUE
                ORDER BY display_name ASC, created_at ASC
                """
            ),
            {"organization_id": organization_id},
        ).mappings().all()
    return tuple(self._to_user(row) for row in rows)
```

`PlatformAccessService.list_active_users(scope)` 必须先检查 `scope.is_admin`，再以 `scope.organization_id` 调仓储；普通用户调用得到 `PermissionError`。

```python
def list_active_users(self, scope: ResourceAccessScope) -> tuple[PlatformUser, ...]:
    if not scope.is_admin:
        raise PermissionError("当前账号没有查看用户目录的权限")
    return self._repository.list_active_users(scope.organization_id)
```

- [ ] **Step 5: 让认证中间件写入角色并在 finally 中清理**

```python
scope_token = set_resource_access_scope(
    ResourceAccessScope(
        organization_id=session.user.organization_id,
        requester_id=session.user.id,
        requester_role=session.user.role,
    )
)
try:
    return await call_next(request)
finally:
    reset_resource_access_scope(scope_token)
```

保留 `CareerRequestActor = ResourceAccessScope` 和 Career 原有三个 ContextVar 函数的兼容包装，使现有调用方可以逐任务迁移，但不得保留第二份请求身份状态。`src/career_assistant/live_interview/web.py` 中两处构造也改为命名参数 `requester_id`、`requester_role`，避免旧的两个位置参数构造在 dataclass 字段扩展后错位。

- [ ] **Step 6: 运行访问上下文和管理员约束测试**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_resource_access_scope.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_single_platform_admin_migration.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_live_interview_web.py" -v`

Expected: 三组测试全部 PASS。

- [ ] **Step 7: 提交统一访问上下文**

```powershell
git add src/platform_access/contracts.py src/platform_access/request_scope.py src/platform_access/repository.py src/platform_access/service.py src/web/api.py src/career_assistant/live_interview/web.py tests/test_resource_access_scope.py tests/test_single_platform_admin_migration.py tests/test_live_interview_web.py
git commit -m "feat: 建立统一资源访问上下文"
```

---

### Task 2: 会话列表、详情与写操作按角色授权

**Files:**
- Modify: `src/career_assistant/persistence/records.py`
- Modify: `src/career_assistant/persistence/conversation_repository.py`
- Modify: `src/career_assistant/web/router.py`
- Test: `tests/test_career_conversation_pagination.py`
- Test: `tests/test_career_conversation_management.py`
- Test: `tests/test_career_context_profiles.py`
- Test: `tests/test_career_job_assessment_api.py`

**Interfaces:**
- Consumes: `ResourceAccessScope` 与 `PlatformAccessService.list_active_users()`。
- Produces: `list_accessible_conversation_page(scope, page, page_size, owner_id, include_archived)`、`get_accessible_conversation(scope, conversation_id)`、`resolve_accessible_conversation(repository, scope, conversation_id)`。

- [ ] **Step 1: 写用户隔离与管理员越权失败测试**

```python
def test_user_cannot_read_or_rename_another_users_conversation(self) -> None:
    response = self.client.get(
        f"/api/career/conversations/{self.user_b_conversation_id}",
        headers=self.user_a_headers,
    )
    self.assertEqual(response.status_code, 404)

    renamed = self.client.patch(
        f"/api/career/conversations/{self.user_b_conversation_id}",
        headers=self.user_a_headers,
        json={"title": "不应成功"},
    )
    self.assertEqual(renamed.status_code, 404)

def test_admin_lists_both_users_and_renames_by_real_owner(self) -> None:
    response = self.client.get("/api/career/conversations", headers=self.admin_headers)
    self.assertEqual(response.status_code, 200)
    self.assertEqual(
        {item["owner_id"] for item in response.json()["items"]},
        {str(self.user_a_id), str(self.user_b_id)},
    )
```

- [ ] **Step 2: 运行会话测试并确认管理员仍被当作普通 Actor**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_conversation_*.py" -v`

Expected: 新增管理员断言 FAIL；普通用户越权断言若已通过则保持通过。

- [ ] **Step 3: 增加带所有者元数据的会话读取模型和仓储方法**

```python
@dataclass(frozen=True)
class ConversationRecord:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    owner_display_name: str | None = None
    owner_email: str | None = None
```

```python
def get_accessible_conversation(
    self,
    scope: ResourceAccessScope,
    conversation_id: UUID,
) -> ConversationRecord | None:
    actor_id = None if scope.is_admin else scope.requester_id
    return self._get_conversation_record(
        organization_id=scope.organization_id,
        conversation_id=conversation_id,
        actor_id=actor_id,
        include_deleted=False,
    )
```

分页 SQL 必须始终限定 `organization_id`、排除 `deleted`，普通用户追加 `actor_id = requester_id`；管理员只在提供 `owner_id` 时追加所有者条件，并 `LEFT JOIN platform_users` 返回显示名称和邮箱。

管理员传入 `owner_id` 时，仓储必须在同一组织的活动平台用户表中验证它；不存在时路由返回 422，普通用户传入与自己不同的 `owner_id` 时返回 404：

```python
def is_active_conversation_owner(self, organization_id: UUID, owner_id: UUID) -> bool:
    with self._database.transaction() as connection:
        return bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM career_assistant.platform_users
                        WHERE id = :owner_id
                          AND organization_id = :organization_id
                          AND is_active = TRUE
                    )
                    """
                ),
                {"owner_id": owner_id, "organization_id": organization_id},
            ).scalar_one()
        )
```

- [ ] **Step 4: 增加统一单资源解析函数**

```python
def resolve_accessible_conversation(
    repository: CareerConversationRepository,
    scope: ResourceAccessScope,
    conversation_id: UUID,
) -> ConversationRecord:
    conversation = repository.get_accessible_conversation(scope, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在、已删除或无访问权限",
        )
    return conversation
```

- [ ] **Step 5: 把所有会话入口改成先解析、再使用真实所有者**

```python
scope = get_resource_access_scope()
conversation = resolve_accessible_conversation(
    services.conversation_repository,
    scope,
    conversation_id,
)
effective_actor_id = conversation.actor_id
updated = services.conversation_repository.rename_conversation(
    effective_actor_id,
    conversation.id,
    request_body.title,
)
```

这个模式必须覆盖详情、消息、重命名、归档、永久删除、上下文读取与更新、岗位评估重试、最近模型选择和旧版兼容入口。创建新会话仍固定使用 `scope.requester_id`，不得增加替他人创建的请求字段。

- [ ] **Step 6: 运行会话相关回归**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_conversation_*.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_context_profiles*.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_job_assessment_*.py" -v`

Expected: 普通用户仅返回自己的总数和记录，管理员读写 A/B 会话全部 PASS。

- [ ] **Step 7: 提交会话授权**

```powershell
git add src/career_assistant/persistence/records.py src/career_assistant/persistence/conversation_repository.py src/career_assistant/web/router.py tests/test_career_conversation_pagination.py tests/test_career_conversation_management.py tests/test_career_context_profiles.py tests/test_career_job_assessment_api.py
git commit -m "feat: 隔离求职会话并开放管理员访问"
```

---

### Task 3: Turn 状态、事件、取消与继续聊天沿用会话所有者

**Files:**
- Modify: `src/career_assistant/persistence/turn_job_repository.py`
- Modify: `src/career_assistant/web/turn_router.py`
- Modify: `src/career_assistant/turn_payloads.py`
- Test: `tests/test_career_turn_job_repository.py`
- Test: `tests/test_career_turn_api.py`
- Test: `tests/test_career_turn_payloads.py`

**Interfaces:**
- Consumes: `resolve_accessible_conversation()` 与 `ResourceAccessScope`。
- Produces: `get_accessible_turn_status(scope, turn_id)`、`list_accessible_turn_events(scope, turn_id, after_id)`、`request_accessible_cancel(scope, turn_id)`；成功读取后返回的 Turn 仍携带真实 `actor_id`。

- [ ] **Step 1: 写 Turn 越权与管理员代操作失败测试**

```python
def test_turn_endpoints_hide_another_users_turn(self) -> None:
    for suffix in ("", "/events"):
        response = self.client.get(
            f"/api/career/turns/{self.user_b_turn_id}{suffix}",
            headers=self.user_a_headers,
        )
        self.assertEqual(response.status_code, 404)

def test_admin_enqueue_in_user_b_conversation_persists_user_b_actor(self) -> None:
    response = self.client.post(
        f"/api/career/conversations/{self.user_b_conversation_id}/turns",
        headers=self.admin_headers,
        json={"text": "继续分析", "selected_skill_ids": []},
    )
    self.assertEqual(response.status_code, 202)
    turn_id = UUID(response.json()["turn"]["id"])
    persisted = self.turn_repository.get_turn_status(self.user_b_id, turn_id)
    self.assertIsNotNone(persisted)
    self.assertEqual(persisted.turn.actor_id, self.user_b_id)
```

- [ ] **Step 2: 运行 Turn 测试并确认管理员无法访问 B 的 Turn**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_turn_*.py" -v`

Expected: 管理员跨用户用例 FAIL。

- [ ] **Step 3: 在仓储层实现范围版 Turn 查询**

```python
def get_accessible_turn_status(
    self,
    scope: ResourceAccessScope,
    turn_id: UUID,
) -> TurnQueueStatus | None:
    owner_id = self._find_turn_owner(
        organization_id=scope.organization_id,
        turn_id=turn_id,
    )
    if owner_id is None:
        return None
    if not scope.is_admin and owner_id != scope.requester_id:
        return None
    return self.get_turn_status(owner_id, turn_id)
```

事件读取和取消先执行同一所有者检查。普通用户无权时返回 `None`，路由映射为 404；只有“已经结束但有访问权”的取消请求继续返回 409。

- [ ] **Step 4: 提交 Turn 前先解析会话并重建有效范围**

```python
request_scope = get_resource_access_scope()
conversation = resolve_accessible_conversation(
    services.conversation_repository,
    request_scope,
    conversation_id,
)
owner_scope = ResourceAccessScope(
    organization_id=conversation.organization_id,
    requester_id=conversation.actor_id,
    requester_role=PlatformRole.USER,
)
inbound_message = await _build_enriched_inbound(
    services,
    owner_scope,
    conversation.id,
    text=request_body.text,
    job_url=request_body.job_url,
    selection_mode=request_body.selection_mode,
    model_profile_id=request_body.model_profile_id,
    interview_experience_ids=request_body.interview_experience_ids,
    selected_skill_ids=request_body.selected_skill_ids,
)
```

`owner_scope` 只能在 `resolve_accessible_conversation` 成功后构造。Worker 继续读取持久化 Payload 中的真实 `actor_id`，不得读取 HTTP ContextVar。

- [ ] **Step 5: SSE 首次授权后固定使用 Turn 真实所有者**

```python
queue_status = services.turn_job_repository.get_accessible_turn_status(scope, turn_id)
if queue_status is None:
    raise HTTPException(status_code=404, detail="Turn 不存在或无访问权限")
return StreamingResponse(
    _persistent_event_stream(
        services,
        owner_id=queue_status.turn.actor_id,
        turn_id=turn_id,
        after_id=after_id,
    ),
    media_type="text/event-stream",
)
```

- [ ] **Step 6: 运行 Turn API、队列和 Worker 回归**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_turn_*.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_turn_worker*.py" -v`

Expected: 普通用户猜测他人 Turn 全部 404；管理员观察、取消和继续 B 会话成功；Worker 仍按 B 的 Actor 恢复。

- [ ] **Step 7: 提交 Turn 授权**

```powershell
git add src/career_assistant/persistence/turn_job_repository.py src/career_assistant/web/turn_router.py src/career_assistant/turn_payloads.py tests/test_career_turn_job_repository.py tests/test_career_turn_api.py tests/test_career_turn_payloads.py
git commit -m "feat: 按会话所有者授权求职 Turn"
```

---

### Task 4: Skill 文件目录分区与服务端授权

**Files:**
- Modify: `src/services/skill_library_service.py`
- Create: `tests/test_skill_library_access.py`
- Modify: `tests/test_skill_star_refresh.py`

**Interfaces:**
- Consumes: `ResourceAccessScope`。
- Produces: `list_skills(scope)`、`get_skill(scope, skill_id)`、`save_skill(scope, name, markdown, skill_id=None)`、`search_skills(scope, query, force_refresh=False)`、`install_skill_repository(owner_id, repository, skill_names)`。

- [ ] **Step 1: 写目录归属、公共兜底和同名隔离失败测试**

```python
def test_user_lists_only_own_skills_and_shared_find_skills(self) -> None:
    names = {item.name for item in self.service.list_skills(self.user_a_scope)}
    self.assertEqual(names, {"find-skills", "resume-helper"})
    shared = next(item for item in self.service.list_skills(self.user_a_scope) if item.name == "find-skills")
    self.assertEqual(shared.visibility, "shared_readonly")
    self.assertFalse(shared.editable)

def test_same_name_isolated_by_owner(self) -> None:
    self.service.install_skill_repository(self.user_a_id, "owner/repo", ["firecrawl"])
    self.service.install_skill_repository(self.user_b_id, "owner/repo", ["firecrawl"])
    a_item = next(item for item in self.service.list_skills(self.user_a_scope) if item.name == "firecrawl")
    b_item = next(item for item in self.service.list_skills(self.user_b_scope) if item.name == "firecrawl")
    self.assertNotEqual(a_item.id, b_item.id)
```

- [ ] **Step 2: 运行 Skill 服务测试并确认当前扫描到全局目录**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_skill_library_access.py" -v`

Expected: FAIL，普通用户仍看到根目录和其他用户 Skill，且方法签名不接受 scope。

- [ ] **Step 3: 扩展 Skill 元数据并建立授权目录枚举**

```python
@dataclass(frozen=True)
class SkillSummary:
    id: str
    name: str
    description: str
    description_zh: str
    author: str
    homepage_url: str | None
    repository_full_name: str | None
    stars: int | None
    previous_stars: int | None
    star_delta: int | None
    star_growth_rate: float | None
    stars_updated_at: str | None
    source: str
    source_label: str
    path_hint: str
    owner_id: str | None
    owner_display_name: str | None
    visibility: Literal["private", "shared_readonly"]
    editable: bool
```

```python
def _iter_authorized_skill_files(
    self,
    scope: ResourceAccessScope,
) -> list[tuple[Path, str, str, UUID | None, str]]:
    items = self._shared_find_skill_files()
    items.extend(self._user_skill_files(scope.requester_id))
    if scope.is_admin:
        items.extend(self._legacy_admin_skill_files(scope.requester_id))
        items.extend(self._all_user_skill_files())
        items.extend(self._administrator_local_skill_files())
    return items
```

管理员列表不能再按 `name` 去重，因为不同用户允许同名；使用稳定 `skill_id` 去重。普通用户范围内同名冲突时优先自己的私有 Skill，但保留公共 `find-skills` 的保留名规则。

- [ ] **Step 4: 实现用户根目录与绝对路径包含检查**

```python
def _user_skill_root(self, owner_id: UUID) -> Path:
    return (self.project_skill_root / "_users" / str(owner_id)).resolve()

def _assert_within(self, path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise LookupError("Skill 不存在或无访问权限") from exc
    return resolved
```

Skill 名称验证必须拒绝 `_users`、`find-skills` 的普通用户新建、`.`、`..`、绝对路径、正反斜杠和不符合 `_skill_name_pattern` 的名称。

- [ ] **Step 5: 让读取、保存和安装使用授权路径**

```python
def save_skill(
    self,
    scope: ResourceAccessScope,
    *,
    name: str,
    markdown: str,
    skill_id: str | None = None,
) -> SkillSaveResult:
    if skill_id is None:
        target_root = self._user_skill_root(scope.requester_id)
        target = self._assert_new_skill_target(target_root, name)
    else:
        detail = self.get_skill(scope, skill_id)
        if not detail.summary.editable or detail.skill_path is None:
            raise PermissionError("当前 Skill 为只读")
        target = detail.skill_path
    return self._write_skill_atomically(scope, target, name, markdown)
```

`install_skill_repository` 只接收内部调用提供的 `owner_id: UUID`，临时目录必须创建在该用户根目录内，校验通过后再原子移动。已有同名目录继续加入 `skipped_names`，不得覆盖。

- [ ] **Step 6: 验证普通用户看不到宿主机与其他用户目录**

在测试中同时创建根目录管理员 Skill、`Path.home()` 模拟 Skill、插件 Skill、A/B 用户 Skill，断言普通用户列表和直接 ID 读取均无法访问；管理员能读取并更新全部已授权来源。

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_skill_library_access.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_skill_star_refresh.py" -v`

Expected: 全部 PASS，现有 Star 刷新仍可由管理员后台范围扫描根目录。

- [ ] **Step 7: 提交 Skill 文件授权**

```powershell
git add src/services/skill_library_service.py tests/test_skill_library_access.py tests/test_skill_star_refresh.py
git commit -m "feat: 按用户目录隔离 Skill 文件"
```

---

### Task 5: Skill API、搜索回退与缓存隐私

**Files:**
- Modify: `src/web/api.py:265-315`
- Modify: `src/services/article_skill_prompt_loader.py`
- Modify: `src/tasks/summary_task.py`
- Create: `tests/test_skill_library_api_access.py`
- Modify: `tests/test_article_skill_prompt_loader.py`

**Interfaces:**
- Consumes: Task 1 的请求 scope、用户目录；Task 4 的范围版 Skill 服务方法。
- Produces: 范围版 `/api/skills`、`/api/skills/{skill_id}`、`/api/skills/search`、`/api/skills/save`；保存请求新增 `skill_id: str | None`。

- [ ] **Step 1: 写 API 直接 ID、只读保存与管理员更新失败测试**

```python
def test_user_cannot_read_or_save_another_users_skill(self) -> None:
    detail = self.client.get(
        f"/api/skills/{self.user_b_skill_id}",
        headers=self.user_a_headers,
    )
    self.assertEqual(detail.status_code, 404)
    saved = self.client.post(
        "/api/skills/save",
        headers=self.user_a_headers,
        json={"skill_id": self.user_b_skill_id, "name": "secret", "markdown": self.markdown},
    )
    self.assertEqual(saved.status_code, 404)

def test_user_cannot_update_shared_find_skills(self) -> None:
    response = self.client.post(
        "/api/skills/save",
        headers=self.user_a_headers,
        json={"skill_id": self.find_skills_id, "name": "find-skills", "markdown": self.markdown},
    )
    self.assertEqual(response.status_code, 403)
```

- [ ] **Step 2: 运行 API 测试并确认现有接口未传访问范围**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_skill_library_api_access.py" -v`

Expected: FAIL。

- [ ] **Step 3: 修改请求模型和四个 Skill API**

```python
class SkillSaveRequest(BaseModel):
    skill_id: str | None = Field(default=None, max_length=160)
    name: str = Field(min_length=2, max_length=80)
    markdown: str = Field(min_length=1, max_length=200_000)
```

```python
@app.get("/api/skills")
def list_skills() -> dict[str, Any]:
    scope = get_resource_access_scope()
    skills = skill_library_service.list_skills(scope)
    owner_names = _skill_owner_names(platform_access_service, scope, skills)
    return {"items": [_skill_summary_payload(replace_owner_name(item, owner_names)) for item in skills]}
```

两个所有者展示 helper 在 `src/web/api.py` 中定义为：

```python
def _skill_owner_names(
    platform_access_service: PlatformAccessService,
    scope: ResourceAccessScope,
    skills: Sequence[SkillSummary],
) -> dict[str, str]:
    if not scope.is_admin or not any(item.owner_id for item in skills):
        return {}
    users = platform_access_service.list_active_users(scope)
    return {
        str(user.id): user.display_name or user.email or user.username
        for user in users
    }

def replace_owner_name(
    skill: SkillSummary,
    owner_names: Mapping[str, str],
) -> SkillSummary:
    if skill.owner_id is None:
        return skill
    return replace(skill, owner_display_name=owner_names.get(skill.owner_id))
```

普通用户不调用用户目录接口；管理员才加载所有者名称和邮箱。`LookupError` 统一映射 404，公共只读保存映射 403，路径和请求字段不得回显到错误消息。

- [ ] **Step 4: 让 GitHub 搜索的本地结果与超时回退使用 scope**

```python
def search_skills(
    self,
    scope: ResourceAccessScope,
    query: str,
    *,
    force_refresh: bool = False,
) -> SkillSearchResult:
    return trace_operation(
        run_name="skills.search",
        run_type="chain",
        inputs={"query_characters": len(query.strip()), "force_refresh": force_refresh},
        metadata={"component": "skill_library", "search_scope": "github_open_skill"},
        tags=("skills", "search", "github"),
        execute=lambda: self._search_skills_untraced(scope, query, force_refresh=force_refresh),
        summarize=self._summarize_search_trace,
    )
```

缓存写入函数只接收 `data_source == "github_snapshot"` 的公共候选；`local_installed` 和本地回退结果永不写入 `skill_search_cache.json`。Trace 继续只有字符数、结果数、耗时和公共搜索状态。

- [ ] **Step 5: 更新非 HTTP 管理员调用方**

`ArticleSkillPromptLoader` 构造函数显式接收管理员 `ResourceAccessScope`，`SummaryTask` 使用现有默认组织/Actor 构造本地管理员 scope；不能让范围版服务重新出现无参数默认扫描。

```python
def __init__(
    self,
    skill_library: SkillLibraryService,
    access_scope: ResourceAccessScope,
    *,
    max_characters: int = 32_000,
) -> None:
    self._skill_library = skill_library
    self._access_scope = access_scope
    self._max_characters = max(1, max_characters)
```

- [ ] **Step 6: 运行 API、缓存隐私和文章 Skill 回归**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_skill_library_api_access.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_article_skill_prompt_loader.py" -v`

Expected: 用户 A 的 API 只出现 A 与公共兜底；管理员能更新 B；缓存 JSON 与 Trace 捕获值不含私有字段。

- [ ] **Step 7: 提交 Skill API 授权**

```powershell
git add src/web/api.py src/services/article_skill_prompt_loader.py src/tasks/summary_task.py tests/test_skill_library_api_access.py tests/test_article_skill_prompt_loader.py
git commit -m "feat: 收紧 Skill API 与搜索范围"
```

---

### Task 6: 求职 Mention、运行时挂载与安装写入会话所有者目录

**Files:**
- Modify: `src/career_assistant/contracts.py`
- Modify: `src/career_assistant/skill_runtime.py`
- Modify: `src/career_assistant/skill_tools.py`
- Modify: `src/career_assistant/response_runner.py`
- Modify: `src/career_assistant/web/router.py`
- Modify: `src/career_assistant/turn_worker.py`
- Modify: `tests/test_career_skill_runtime.py`
- Modify: `tests/test_career_skill_tools.py`
- Modify: `tests/test_career_turn_worker.py`

**Interfaces:**
- Consumes: `ResourceAccessScope`、已解析 `ConversationRecord.actor_id`、Task 4 的 `install_skill_repository(owner_id, repository, skill_names)`。
- Produces: `CareerSkillRuntime.list_mentions(scope, query)`、`resolve(scope, selected_ids, user_text)`、`resolve_for_conversation(scope, selected_ids, user_text, inherited)`；`SkillToolRegistry.execute(..., owner_id)`。

- [ ] **Step 1: 写 Mention、直接调用和管理员代安装失败测试**

```python
def test_user_runtime_cannot_resolve_another_users_skill(self) -> None:
    result = self.runtime.resolve(
        self.user_a_scope,
        selected_ids=(self.user_b_skill_id,),
        user_text="/secret-skill 执行",
    )
    self.assertEqual(result.skills, ())

def test_admin_in_user_b_conversation_installs_for_user_b(self) -> None:
    execution = self.registry.execute(
        self.install_call,
        execution_mode="tool_calling",
        owner_id=self.user_b_id,
    )
    self.assertIn("已安装到你的技能库", execution.trace.message)
    self.assertTrue((self.root / "_users" / str(self.user_b_id) / "firecrawl" / "SKILL.md").is_file())
```

- [ ] **Step 2: 运行 Skill Runtime 与 Tool 测试并确认调用链没有 owner_id**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_skill_*.py" -v`

Expected: FAIL。

- [ ] **Step 3: 给 Mention 和激活解析增加明确 scope**

```python
def list_mentions(
    self,
    scope: ResourceAccessScope,
    query: str,
    *,
    limit: int = 8,
) -> tuple[SkillMention, ...]:
    return self._rank_mentions(self._skill_library.list_skills(scope), query, limit)
```

把现有候选排序主体提取成这个确定签名，排序规则保持原样：

```python
def _rank_mentions(
    self,
    skills: Sequence[SkillSummary],
    query: str,
    limit: int,
) -> tuple[SkillMention, ...]:
    normalized_query = query.strip().casefold()
    ranked: list[tuple[int, SkillSummary]] = []
    for skill in skills:
        name = skill.name.casefold()
        searchable = " ".join(
            (skill.name, skill.description, skill.description_zh),
        ).casefold()
        if normalized_query and normalized_query not in searchable:
            continue
        score = 0
        if normalized_query:
            score = 3 if name == normalized_query else 2 if name.startswith(normalized_query) else 1
        ranked.append((score, skill))
    ranked.sort(key=lambda item: (-item[0], item[1].name.casefold(), item[1].id))
    return tuple(
        SkillMention(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            description_zh=skill.description_zh,
            source_label=skill.source_label,
        )
        for _, skill in ranked[:limit]
    )
```

`owner_read_scope` 是在服务端已经完成会话授权后才允许使用的纯转换：

```python
def owner_read_scope(conversation: ConversationRecord) -> ResourceAccessScope:
    return ResourceAccessScope(
        organization_id=conversation.organization_id,
        requester_id=conversation.actor_id,
        requester_role=PlatformRole.USER,
    )
```

管理员打开 B 的会话时，路由先解析会话，再构造 `requester_id=B、requester_role=USER` 的 owner scope 供 Mention 和运行时使用；这可避免把管理员的全部 Skill 注入 B 的隐私会话。没有选中会话时，管理员 Mention 使用管理员请求 scope。

- [ ] **Step 4: 让 Mention API 接收可选 conversation_id 并在服务端解析**

```python
@router.get("/skills/mentions")
def list_skill_mentions(
    request: Request,
    query: str = "",
    conversation_id: UUID | None = None,
) -> dict[str, object]:
    request_scope = get_resource_access_scope()
    runtime_scope = request_scope
    if conversation_id is not None:
        conversation = resolve_accessible_conversation(
            get_career_services(request).conversation_repository,
            request_scope,
            conversation_id,
        )
        runtime_scope = owner_read_scope(conversation)
    return {"items": [_skill_mention_payload(item) for item in runtime.list_mentions(runtime_scope, query)]}
```

- [ ] **Step 5: 将真实 owner_id 贯穿模型 Tool Calling**

```python
def execute(
    self,
    tool_call: FunctionToolCall,
    *,
    execution_mode: str,
    owner_id: UUID,
    skill_name: str = "skill-runtime",
) -> SkillToolExecution:
    if tool_call.name == "install_skill_repository":
        result_payload, result_count, message = self._install_repository(payload, owner_id)
```

`response_runner` 从当前持久 Turn 或会话记录取得 `actor_id` 传给 registry；模型工具 schema 不增加 `owner_id`。安装成功消息固定为“已安装到你的技能库；下一轮可使用 `/<skill-name>` 调用”，并保留 `installed_names`、`skipped_names`、`installed_file_count`。

为让 Worker 事件和 PC 页面都能稳定读取安装结果，扩展安全摘要而不携带仓库路径或正文：

```python
@dataclass(frozen=True)
class SkillExecutionTrace:
    skill_name: str
    tool_name: str
    execution_mode: str
    status: str
    result_count: int = 0
    message: str = ""
    installed_names: tuple[str, ...] = ()
    skipped_existing_names: tuple[str, ...] = ()
```

`turn_worker` 和同步 Career 响应 payload 都把这两个 tuple 序列化为字符串数组；其他工具执行保持空数组。

- [ ] **Step 6: 验证共享目录跨服务读取和当前轮不自动启用**

测试使用同一个临时 `project_root` 构造 Worker 侧与 API 侧两个 `SkillLibraryService`：Worker 安装后，API 服务重新 `list_skills(user_b_scope)` 立即可见；`user_a_scope` 不可见；当前 Turn 的 activated skills 不新增刚安装项，下一 Turn 才能解析。

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_skill_*.py" -v`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_turn_worker.py" -v`

Expected: 全部 PASS。

- [ ] **Step 7: 提交求职 Skill 安装链路**

```powershell
git add src/career_assistant/contracts.py src/career_assistant/skill_runtime.py src/career_assistant/skill_tools.py src/career_assistant/response_runner.py src/career_assistant/web/router.py src/career_assistant/turn_worker.py tests/test_career_skill_runtime.py tests/test_career_skill_tools.py tests/test_career_turn_worker.py
git commit -m "feat: 将求职 Skill 安装到会话所有者"
```

---

### Task 7: 技能库 PC 端所有者筛选与公共只读状态

**Files:**
- Create: `web-ui/src/skill-access-view.js`
- Create: `web-ui/src/skill-access-view.test.js`
- Modify: `web-ui/src/App.vue`

**Interfaces:**
- Consumes: Skill API 的 `owner_id`、`owner_display_name`、`visibility`、`editable`。
- Produces: `filterInstalledSkills(skills, query, ownerId)`、`skillOwnerOptions(skills, isAdmin)`、`skillSaveState(skill)`。

- [ ] **Step 1: 写纯函数失败测试**

```javascript
test('普通用户的公共 find-skills 为只读', () => {
  assert.deepEqual(skillSaveState({ visibility: 'shared_readonly', editable: false }), {
    disabled: true,
    label: '公共内置 · 只读',
  })
})

test('管理员可以按所有者筛选同名 Skill', () => {
  const visible = filterInstalledSkills(skills, 'firecrawl', 'user-b')
  assert.deepEqual(visible.map((item) => item.owner_id), ['user-b'])
})
```

- [ ] **Step 2: 运行前端测试并确认 helper 尚不存在**

Run: `node --test web-ui/src/skill-access-view.test.js`

Expected: FAIL，模块无法导入。

- [ ] **Step 3: 实现无权限逻辑的展示纯函数**

```javascript
export function skillSaveState(skill) {
  if (!skill) return { disabled: true, label: '请选择 Skill' }
  if (skill.visibility === 'shared_readonly' && !skill.editable) {
    return { disabled: true, label: '公共内置 · 只读' }
  }
  return { disabled: !skill.editable, label: skill.editable ? '保存并更新' : '只读' }
}
```

- [ ] **Step 4: 在 App.vue 接入管理员所有者筛选和稳定 skill_id 保存**

```javascript
const payload = {
  skill_id: selectedSkill.value?.editable ? selectedSkill.value.id : null,
  name: skillEditorName.value,
  markdown: skillEditorMarkdown.value,
}
```

管理员显示“全部用户”筛选和每项所有者标签；普通用户不显示权限说明，只在公共 `find-skills` 上显示“公共内置”“只读”。只读详情的名称、Markdown 编辑器和保存按钮都禁用。进入 `/skills` 路由时继续无条件调用 `loadSkills()`，确保刚安装项能重新扫描出现。

- [ ] **Step 5: 运行技能库前端测试与 production build**

Run: `node --test web-ui/src/skill-access-view.test.js`

Run: `npm --prefix web-ui test`

Run: `npm --prefix web-ui run build`

Expected: 测试全部 PASS，Vite build 成功。

- [ ] **Step 6: 提交技能库 PC 端改动**

```powershell
git add web-ui/src/skill-access-view.js web-ui/src/skill-access-view.test.js web-ui/src/App.vue
git commit -m "feat: 展示 Skill 所有者与只读状态"
```

---

### Task 8: 求职助手 PC 端管理员会话筛选与安装反馈

**Files:**
- Create: `web-ui/src/career-history-access.js`
- Create: `web-ui/src/career-history-access.test.js`
- Modify: `web-ui/src/App.vue`
- Modify: `web-ui/src/components/CareerAssistantPage.vue`
- Modify: `web-ui/src/career-turn-presentation.js`
- Modify: `web-ui/src/career-turn-presentation.test.js`

**Interfaces:**
- Consumes: 当前登录用户 `authUser.role`；会话项的 `owner_id`、`owner_display_name`、`owner_email`；Tool trace 的 `installed_names` 和 `message`。
- Produces: `careerHistoryUrl(page, pageSize, ownerId)`、`careerSkillMentionUrl(query, conversationId)`、`conversationOwnerLabel(item)`、`installationFeedback(turnEvent)`。

- [ ] **Step 1: 写管理员筛选 URL 和安装提示失败测试**

```javascript
test('管理员所有者筛选写入服务端分页请求', () => {
  assert.equal(
    careerHistoryUrl(2, 10, 'user-b'),
    '/api/career/conversations?page=2&page_size=10&owner_id=user-b',
  )
})

test('安装完成提示指向下一轮调用', () => {
  assert.equal(
    installationFeedback({ installed_names: ['firecrawl'] }),
    '已安装到你的技能库；下一轮可使用 /firecrawl 调用。',
  )
})

test('Skill Mention 请求携带当前会话以确定真实所有者', () => {
  assert.equal(
    careerSkillMentionUrl('fire', 'conversation-b'),
    '/api/career/skills/mentions?query=fire&conversation_id=conversation-b',
  )
})
```

- [ ] **Step 2: 运行前端测试并确认 owner filter helper 尚不存在**

Run: `node --test web-ui/src/career-history-access.test.js web-ui/src/career-turn-presentation.test.js`

Expected: FAIL。

- [ ] **Step 3: 实现会话查询与所有者展示纯函数**

```javascript
export function careerHistoryUrl(page, pageSize, ownerId = '') {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (ownerId) params.set('owner_id', ownerId)
  return `/api/career/conversations?${params.toString()}`
}

export function conversationOwnerLabel(item) {
  return item.owner_display_name || item.owner_email || item.owner_id || ''
}

export function careerSkillMentionUrl(query, conversationId = '') {
  const params = new URLSearchParams({ query })
  if (conversationId) params.set('conversation_id', conversationId)
  return `/api/career/skills/mentions?${params.toString()}`
}
```

- [ ] **Step 4: 向 CareerAssistantPage 传入登录角色并接入管理员筛选**

```vue
<CareerAssistantPage
  v-else-if="activeRoute === '/career'"
  :current-user="authUser"
/>
```

组件新增 `currentUser` prop。只有 `currentUser.role === 'admin'` 时展示所有者筛选和历史项标签；普通用户历史栏布局保持不变。切换所有者时页码重置为 1，并由服务端 `owner_id` 查询，不对已返回的跨用户全集做纯前端隔离。Skill Mention 请求使用 `careerSkillMentionUrl(query, activeConversationId)`，使管理员编辑 B 会话时只得到 B 与公共兜底候选。

- [ ] **Step 5: 把安装 trace 映射成明确的完成状态**

```javascript
export function installationFeedback(trace) {
  const names = Array.isArray(trace?.installed_names) ? trace.installed_names.filter(Boolean) : []
  if (names.length === 0) return ''
  return `已安装到你的技能库；下一轮可使用 /${names[0]} 调用。`
}
```

如果后端事件只提供 `message`，优先展示已脱敏的 `message`；不得把安装目标绝对路径渲染到页面。

- [ ] **Step 6: 运行求职助手前端测试与 build**

Run: `node --test web-ui/src/career-history-access.test.js web-ui/src/career-turn-presentation.test.js`

Run: `npm --prefix web-ui test`

Run: `npm --prefix web-ui run build`

Expected: 全部 PASS；PC 历史栏和技能库可构建。

- [ ] **Step 7: 提交求职助手 PC 端改动**

```powershell
git add web-ui/src/career-history-access.js web-ui/src/career-history-access.test.js web-ui/src/App.vue web-ui/src/components/CareerAssistantPage.vue web-ui/src/career-turn-presentation.js web-ui/src/career-turn-presentation.test.js
git commit -m "feat: 增加管理员会话筛选与安装反馈"
```

---

### Task 9: 全链路权限回归、文档同步与本地验收

**Files:**
- Modify: `docs/career_assistant_module.md`
- Modify: `docs/skill_library_module.md`
- Modify: `docs/platform_production_deployment.md`
- Create: `tests/test_career_skill_access_integration.py`

**Interfaces:**
- Consumes: 前八个任务的最终 API、目录结构和 PC 行为。
- Produces: A/B/管理员端到端验收用例、模块文档、共享卷本地验收命令。

- [ ] **Step 1: 写完整 A/B/管理员矩阵集成测试**

```python
def test_admin_installs_for_b_and_catalogs_remain_isolated(self) -> None:
    turn = self.enqueue_find_skill_install(
        headers=self.admin_headers,
        conversation_id=self.user_b_conversation_id,
        repository="mendableai/firecrawl",
        skill_name="firecrawl",
    )
    self.wait_for_success(turn["id"])
    self.assertIn("firecrawl", self.skill_names(self.user_b_headers))
    self.assertNotIn("firecrawl", self.skill_names(self.user_a_headers))
    self.assertIn("firecrawl", self.skill_names(self.admin_headers))
```

矩阵还必须覆盖：A/B 会话列表总数、直接会话 ID、重命名、归档、删除、上下文、评估重试、Turn 状态、SSE、取消、直接 Skill ID、保存、公共 `find-skills`、Mention、运行时挂载、同名安装和缓存隐私。

- [ ] **Step 2: 运行集成测试并修复发现的调用方签名遗漏**

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_career_skill_access_integration.py" -v`

Expected: PASS；任何未传 scope 的调用会在这里暴露为 `TypeError`，不得通过给服务方法增加宽松默认参数绕过。

- [ ] **Step 3: 更新三份模块与部署文档**

`docs/career_assistant_module.md` 写清访问矩阵、真实所有者解析、管理员继续他人会话和安装链路；`docs/skill_library_module.md` 写清根目录兼容规则、`_users/<uuid>` 命名空间、公共只读兜底及四类 API 的授权；`docs/platform_production_deployment.md` 只记录既有 `application_skills:/app/.agents` 共享卷要求和本地验收，不执行部署。

- [ ] **Step 4: 运行 Python 编译和全部后端测试**

Run: `\.\.venv\Scripts\python.exe -m compileall src`

Run: `\.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: 编译成功，全部测试 PASS。

- [ ] **Step 5: 运行全部前端测试和 production build**

Run: `npm --prefix web-ui test`

Run: `npm --prefix web-ui run build`

Expected: 全部测试 PASS，Vite production build 成功。

- [ ] **Step 6: 检查隐私字段、目录边界和工作区提交范围**

Run: `rg -n "owner_id|owner_display_name|path_hint|markdown" data/skill_search_cache.json src/observability tests -g "*.json" -g "*.py"`

Expected: 搜索缓存与 Trace 实现不写入私有名称、正文、路径和所有者；测试夹具中的断言允许出现这些字段名。

Run: `git status --short`

Expected: 本功能提交之外的用户原有改动仍被保留，没有被误加入提交。

- [ ] **Step 7: 提交集成测试与文档**

```powershell
git add tests/test_career_skill_access_integration.py docs/career_assistant_module.md docs/skill_library_module.md docs/platform_production_deployment.md
git commit -m "docs: 记录求职会话与 Skill 权限边界"
```

## 完成验收清单

- [ ] 普通用户 A 的会话列表、总数、详情和所有写入口均不接触 B。
- [ ] 普通用户 A 的 Skill 列表、搜索回退、详情、保存、Mention 和运行时均不接触 B。
- [ ] 管理员可以从现有 PC 入口筛选、读取和修改 A/B 的会话与 Skill。
- [ ] 公共 `find-skills` 对所有用户可见、可调用，普通用户不可保存，管理员可更新。
- [ ] 管理员在 B 会话安装 Skill 后，目录属于 B，B 与管理员可见，A 不可见。
- [ ] Worker 安装完成后 API 重新扫描同一共享卷即可看到结果，不需要同步事件。
- [ ] 当前轮不自动执行新安装 Skill，完成消息提示下一轮使用 `/<skill-name>`。
- [ ] 现有根目录 57 个 Skill 未移动，普通用户不会扫描管理员根目录、宿主机用户目录或插件目录。
- [ ] 越权会话 ID、Turn ID、Skill ID 都返回 404，文件写入再次通过 `relative_to` 检查。
- [ ] 后端测试、前端测试、Python 编译和 Vite build 全部通过。
- [ ] 三份模块文档与实现一致，本轮没有生产部署动作。
