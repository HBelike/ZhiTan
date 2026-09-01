# 面经库与 RAG 检索模块

## 目标

面经库是求职助手的长期知识底座：将手工录入、解析后的 PDF / Word / Excel / 图片内容沉淀为可编辑的 Markdown，按“公司 → 岗位与日期”组织，并向求职助手提供可追溯的检索证据。它不保存用户原始附件；长期数据只包含 Markdown、来源元数据、切片与检索反馈。

## 已实现范围

- `GET /api/career/interview-library/tree`：公司—岗位—日期树与关键词过滤。
- `POST /api/career/interview-library/experiences`：手工 Markdown 入库；同一组织、公司与“岗位 + 日期”再次保存时更新原面经、替换切片并重建索引，避免浏览器重试产生重复节点或数据库冲突。
- `POST /api/career/interview-library/import-file`：复用求职助手的受控解析链路导入文件，入库后清理原件。
- `GET /api/career/interview-library/experiences/{id}`、`PUT .../{id}`：预览、编辑、保存并重建索引。
- `DELETE /api/career/interview-library/experiences/{id}`：按组织永久删除面经；数据库同步删除 RAG 切片和来源记录，公开网页账本仅解除关联，避免同一来源被自动重新采集。
- `GET /api/career/interview-library/mentions`：为后续 `@面经` 候选选择提供轻量检索。
- `GET /api/career/interview-library/search`：返回带公司、岗位、标题路径与来源链接的 RAG 证据片段。
- `POST /api/career/live-interviews/archive/preview`、`POST /api/career/live-interviews/archive`：结束实时面试后聚合当前用户的多个会话，只把识别出的面试问题按“公司 → 岗位 + 日期”归并到面经库；已有正文先读取再追加去重，随后复用本模块的切片与索引链路。
- 求职助手输入框支持 `@面经`：前端只搜索并提交面经 ID；服务端重新执行组织边界校验、按本轮问题召回最多 6 个片段，并将带来源标识的最小证据注入模型上下文。

## 求职助手联动

1. 在求职助手输入框键入 `@` 加公司或岗位关键词，调用 `GET /api/career/interview-library/mentions` 获取候选资料；选择后显示可移除的引用标签。
   `@` 只服务面经候选，Skill 统一通过 `/名称` 唤醒，两类入口不会共享候选菜单或解析规则。
2. 提交文本或附件时，客户端仅传递 `interview_experience_ids`，不传递 Markdown 正文、切片内容或来源凭据。
3. 服务端通过 `_build_interview_evidence` 重新校验当前组织是否拥有这批资料，再通过 `InterviewRetrievalService` 取证据。关键词和向量均未命中时，已显式选择的资料会返回受限数量的结构化切片，避免 `@面经` 因查询措辞差异失效。
4. 模型 Prompt 会明确要求把面经视为经验参考而非官方事实；回答若采用资料观点，应保留形如 `[面经：公司 · 岗位 · 标题]` 的来源标识。
5. 切换会话、归档会话和提交成功都会清理浏览器草稿中的待引用标签；已入库的面经和会话中已持久化的文本不受影响。

## 实时面试归档

1. 浏览器结束面试时先停止音频与 WebSocket，再使用本场累积的全部 `session_ids` 请求问题预览。
2. 服务端同时校验组织和用户归属，从 `live_interview_answers.original_question` 按会话时间与问题版本读取问题；回答失败或取消不影响问题归档。
3. 问题只进行空白、大小写和句末标点规范化去重，不调用 LLM，不保存模型答案、候选人转写或原始音频。
4. 同公司、职位和日期已有面经时，先读取旧问题并追加新问题，再通过 `update_markdown` 重建切片和索引；非实时来源的既有面经只在末尾维护独立的“面试大师归档问题”区块，避免覆盖用户原正文。
5. 来源类型使用现有 `authenticated_session`，来源平台标记为 `interview_master`。重复保存同一批会话不会重复追加问题。

## 数据模型与迁移

Alembic 迁移 `20260809_04_interview_library_rag` 创建以下表：

- `interview_companies`：公司根节点，含规范名称与别名预留。
- `interview_experiences`：一份可编辑的面经 Markdown 与来源元数据。
- `interview_ingestion_jobs`：导入/扫描的可观测任务记录，不存放原文件。
- `interview_chunks`：结构化切片、全文检索字段、可选 `vector(1024)` 向量。
- `interview_source_connections`：为未来官方 API / 已授权会话保存加密凭据引用，不保存明文账号、密码或 Cookie。
- `interview_retrieval_feedback`：为未来召回质量评估、纠错与离线优化保留反馈入口。

PostgreSQL 负责事务、JSON 元数据、全文能力；`pgvector` 位于同一事务边界内，避免主库与独立向量库出现双写不一致。未来数据规模或多租户隔离需求显著提升时，可通过检索适配层平滑新增 Qdrant / Milvus，而无需改动面经正文和 Agent 调用契约。

## RAG 策略

1. 解析层先复用现有 Docling / OCR / Vision / Office 转换链路，输出结构化文本；不把二进制附件写入长期存储。
2. `HierarchicalMarkdownChunker` 按标题层级切片，目标约 260 字符、单片最大 340、重叠 36；每个片段携带公司、岗位、面经名称和标题路径。
3. 基础召回使用 PostgreSQL `pg_trgm` 与 `ILIKE`，检索正文、公司、岗位和面经名称；因此未配置向量模型、模型限流或云端不可用时仍可用。
4. 语义增强层使用 OpenAI-compatible `/embeddings` 协议。只有部署环境显式打开开关、提供模型 ID、HTTPS Base URL 和 Key 时才会调用。
5. 双路召回结果使用 Reciprocal Rank Fusion (RRF) 融合；不直接混用不同 Provider 的相似度数值。当前 RRF 即确定性重排，待有经标注评测集后再接入专用 reranker。
6. 输出包含 `citation`、公司、岗位、日期、标题路径、来源 URL 和片段正文，后续 Agent 回复必须引用这些证据，而不是把整库文本塞入 Prompt。
7. 全网公开网页的相同正文只建立一份面经和一套 RAG 切片；检索结果继续引用最早成功的主来源，其他地址保存在 `interview_experience_sources` 中供详情追溯，不扩大高频 RAG 响应。

## 运行配置

配置位于 `config/career_assistant.yaml` 的 `interview_library.retrieval`。

- 默认 `CAREER_INTERVIEW_EMBEDDING_ENABLED=false`，只启用关键词召回。
- 启用向量检索时，部署环境应提供 `CAREER_INTERVIEW_EMBEDDING_MODEL_ID`、`CAREER_INTERVIEW_EMBEDDING_BASE_URL` 与 `DASHSCOPE_API_KEY`（或更新 YAML 指向其他密钥环境变量）。
- 当前数据库索引固定为 1024 维。切换不同维度的 embedding 模型必须先新增迁移和重新索引，禁止仅修改配置。

## 安全与上线边界

- 只接受 HTTPS 的 embedding 服务地址，错误信息不包含密钥与上游响应正文。
- 文件上传只在临时目录存活，并沿用现有大小、90 页 PDF、TTL 与格式限制。
- 自动采集仅能使用官方 API、公开内容或用户合法授权的会话；须遵守目标平台条款、robots、速率限制和数据删除要求。账号/密码/Cookie 未来仅以服务端加密引用管理。
- 建议生产环境使用 PostgreSQL 16 + pgvector、对象临时存储或 `tmpfs`、独立 Docling 服务，并对解析、Embedding、检索延迟与失败率接入日志/指标。

## 验证命令

```powershell
Set-Location <original-worktree>
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\verify_interview_library.py
.\.venv\Scripts\python.exe scripts\verify_interview_library_api.py
npm --prefix web-ui run build
```

本模块额外回归覆盖：显式选择资料但查询无关键词命中时的兜底召回、面经树过滤、候选搜索、资料编辑重切片、组织隔离的永久删除与前端生产构建。
其中保存接口还覆盖同一面经的重复提交：返回原有面经 ID，并以最新 Markdown 重建索引。
实时面试归档额外覆盖多会话聚合、失败问题保留、问题去重、已有面经合并、无权会话拒绝、零问题拒绝与成功后按 `experience_id` 打开面经。

## 后续演进

1. 在求职助手输入框实现 `@面经` 候选、确认选择与带引用上下文的对话组装。
2. 新增 URL 采集任务：优先官方接口与用户粘贴 URL 的主体内容提取；登录态采集必须采用加密凭据、最小权限和可撤销机制。
3. 建立离线检索评测集，评估命中率、引用完整性和重排效果，再决定云端 embedding / reranker 供应商。
4. 基于面经证据实现高频考题聚类、语音模拟面试、追问状态机与回答复盘。

## 2026-08-26 面经归属编辑与阅读视口统一

### 设计目标与取舍

- 编辑态新增“公司名称”和“面试岗位”两个必填字段，使用户可以修正解析结果；独立的面经标题 `job_name`、面试日期和来源信息不在本次编辑范围内，避免把归属修正扩大成整份元数据编辑。
- 编辑正文、归属字段与补充信息统一使用 900px 内容视口，和预览正文保持一致；PC 端不再出现收缩成手机编辑器宽度的问题。
- 蓝色主题使用全局 `--ui-*` 变量覆盖字段、正文、字数徽标、辅助面板与焦点态，并移除编辑区的英文装饰眉题。

### 调用链与依赖

1. `InterviewLibraryPage.vue` 在进入编辑态时同步公司、岗位、Markdown、摘要和标签。
2. `PUT /api/career/interview-library/experiences/{id}` 接收 `company_name`、`role_name` 与正文信息。
3. `InterviewLibraryService.update_markdown` 通过 `upsert_company` 取得目标公司，再更新面经的 `company_id`、`role_name` 与规范化岗位名。
4. 更新后的公司、岗位和原有 `job_name` 一起参与 Markdown 切片重建；向量索引仍沿用原有可降级链路。
5. 保存成功后前端重新请求公司树，因此面经会从旧公司节点移动到新公司节点；目标公司下存在同名面经时返回可读的冲突提示。

本次没有新增第三方依赖或数据库迁移，复用现有公司表、面经字段、切片器和树查询。

### 验证结果与边界

- 后端回归覆盖公司迁移、岗位更新、`job_name` 保持不变和切片上下文刷新。
- 前端回归覆盖两个字段、保存后树刷新、900px 同宽视口、中文标题和蓝色主题覆盖。

## 2026-08-28 面经永久删除

### 设计目标与调用链

1. 详情页提供“删除面经”，二次确认会明确正文、检索索引和来源记录不可恢复。
2. 前端调用 `DELETE /api/career/interview-library/experiences/{id}`；服务层和仓储使用当前 `actor_id` 校验创建者，管理员通过 `PlatformRole.ADMIN` 取得全量维护权限，其他用户返回 403。
3. 删除 `interview_experiences` 后，数据库外键级联清理 `interview_chunks` 和 `interview_experience_sources`；入库任务、检索反馈与公开网页账本按既有 `ON DELETE SET NULL` 规则保留历史但解除关联。
4. 删除成功后清空详情并刷新公司树；没有面经的公司不会被树查询返回，但公司基础记录不做额外清理。

### 验证结果与边界

- 仓储测试确认删除 SQL 强制创建者或管理员条件并返回真实删除结果；服务测试确认不存在或越权时不会伪报成功。
- 前端测试覆盖二次确认、`DELETE` 请求、树刷新和成功提示。
- 本次不增加回收站、软删除或批量删除。
- 已通过面经编辑相关测试、实时面试归档回归和前端生产构建。
- 本次只完成本地实现与验证，未连接或部署生产环境；手机端仅继承现有单列规则，不做平板专项适配。

### 标题与保存时间展示规则

- 面经树叶节点和详情页大标题统一显示 `role_name`，即用户可编辑的“面试岗位”。
- `job_name` 是导入时生成的内部面经名称，继续用于去重、归档兼容和检索上下文，不再作为页面主标题展示。
- 面经树次级信息和详情标题元信息统一显示最近保存时间，优先读取 `updated_at`，缺失时回退到 `created_at`。新建记录的 `updated_at` 即入库时间，每次编辑保存会由仓储更新为当前时间。
- `interview_date` 继续作为实际面试日期保存在数据模型中，但不再显示在面经树和详情主标题区域，避免与保存时间混淆。

## 2026-08-28 全平台公开读取与归属写权限

### 设计目标与技术取舍

- 面经库定位为全平台公开知识库：公司树、详情、来源、`@面经` 候选和 RAG 召回均跨 `organization_id` 读取，不执行面经级读权限判断；平台统一登录机制保持不变。
- `interview_experiences.created_by_actor_id` 记录面经创建者，`interview_collection_jobs.created_by_actor_id` 记录公开网页、小红书或公开 URL 任务发起人。两个字段都允许为空并使用 `ON DELETE SET NULL`，避免用户账号删除时一并删除公共知识。
- 迁移不回填历史数据。历史面经保持 `created_by_actor_id IS NULL`，普通用户只读，管理员可以修改或删除。
- 普通用户只能修改、删除自己创建或自己发起拉取后生成的面经；管理员可以维护全部记录。`PUT`、`DELETE` 和候选入库均在服务端复核，前端 `can_write` 仅用于展示，不作为权限边界。
- 同名面经的 `ON CONFLICT DO UPDATE` 增加创建者/管理员条件，普通用户不能借重复入库覆盖他人正文或取得归属。

### 调用链与依赖

1. 平台认证中间件把 `organization_id`、`actor_id` 和 `PlatformRole` 写入 `CareerRequestActor`。
2. 手动创建、文件上传和实时面试归档直接把 `actor_id` 传入 `InterviewLibraryService.ingest`；异步收集先把发起人写入 `interview_collection_jobs`，最终入库时继承该字段。
3. 详情接口通过 `get_public_experience` 跨组织读取，并只返回布尔值 `can_write`，不向前端暴露创建者 ID。
4. 编辑和删除先跨组织读取目标记录，再执行“创建者相同或管理员”判断；管理员跨组织编辑时继续在目标面经原组织内维护公司、正文和索引。
5. 全局公司树按 `normalized_name` 合并同名公司；关键词和向量检索取消组织过滤，显式选择的面经 ID 同样可以跨组织进入 RAG 上下文。

### 数据库与验证边界

- 迁移：`20260828_28_interview_experience_ownership.py`，只新增两个可空外键和查询索引，不修改历史归属。
- 自动化覆盖跨组织详情和树读取、普通用户只读、创建者写入、管理员维护历史面经、服务端 403、防覆盖条件、异步拉取归属及前端只读态。
- 本次不增加角色体系、审批流、共享编辑者、软删除或历史归属推断；匿名访问不在本次范围内，仍沿用平台现有登录入口。

## 2026-08-28 管理员写权限运行态修复

- **问题原因**：新前端严格要求面经详情返回 `can_write=true`；如果仍连接未更新的 API，该字段缺失会按只读处理。可选认证环境此前也不会把已有登录会话的角色注入 Career Actor。
- **身份边界**：唯一管理员继续是 `admin@example.com`，沿用迁移 `20260825_20` 的现有约束，不新增或调整管理员账号迁移。
- **调用链修复**：`PLATFORM_AUTH_REQUIRED` 只控制缺少有效会话时是否返回 401。业务 API 只要携带有效 Cookie，强制或可选认证模式都会把真实 `PlatformRole` 注入 `CareerRequestActor`。
- **权限边界**：前端仍以服务端 `can_write` 为准；`PUT` 和 `DELETE` 继续由服务层复核管理员或创建者身份，不因页面显示“管理员”而跳过后端校验。
- **运行版本**：健康接口返回 `career_runtime_revision=2026-08-28-interview-admin-permissions-v2`，用于确认运行中的后端已经包含该修复。
- **验证结果**：后端、前端测试通过，Alembic 仍以 `20260828_28` 为唯一 head，Vite 生产构建通过；本次未部署生产。
