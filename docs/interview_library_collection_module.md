# 面经库公共资料采集模块

更新时间：2026-08-28

## 目标

为面经库提供可追溯的资料进入路径：用户可粘贴公开文章链接生成 Markdown 候选，也可输入关键词从全网无需登录的公开网页收集面经。公开网页复用 OCR 与 `InterviewEvidenceAnalyzer` 做事实提取和正文清洗；高置信度完整面经自动写入，其余结果保留为候选，最终复用既有入库、切片、向量化和混合检索流程。

## 数据边界

- 持久化采集任务、候选资料的解析后 Markdown、来源 URL、来源平台、状态与错误原因；候选被人工保存后，在元数据中回写 `imported_experience_id`。
- 不持久化上传原件、网页原始 HTML、第三方账号、密码、Cookie 或登录会话。
- 公开 URL 导入只访问用户主动提交的 HTTPS 地址；不执行页面脚本，不携带浏览器会话。
- PC “公开信息收集”由 `public_api` 连接器调用 Firecrawl `/v2/search` 发现 URL，再对永久账本中的新地址调用 `/v2/scrape`；不接收或保存浏览器 Cookie、Firecrawl 原始响应、网页 HTML 和图片字节。
- 旧的小红书浏览器关键词接口继续保留兼容实现，但当前 PC 信息收集入口不再依赖 Chrome 扩展或小红书登录态。
- 原有小红书公开 URL 读取继续不携带浏览器会话；旧关键词接口仅为兼容现有调用保留，新 UI 不再使用。
- 牛客、脉脉等尚未具备公开检索实现的平台仍只登记为 `needs_user_interaction`。

## 当前连接器能力

| 场景 | 连接器 | 状态 | 说明 |
| --- | --- | --- | --- |
| 公开 HTTPS 文章 URL | `url_import` | 可用 | 提取正文并转换为候选 Markdown。 |
| 手动粘贴 Markdown | `manual_text` | 可用 | 使用既有面经导入功能。 |
| 文件资料 | `manual_upload` | 可用 | 复用求职助手的 PDF、Word、Excel、图片解析链路。 |
| 全网公开网页关键词收集 | `public_api` | 配置 Key 后可用 | 搜索、规范 URL 去重、正文解析、SHA-256 去重、Agent 甄别、自动入库和来源追踪。 |
| 小红书关键词信息收集 | `user_authorized_browser` | 可用 | `0.2.9` 从搜索页点击真实卡片并恢复上下文；`300031`/App 扫码限制只跳过单篇，登录/验证/限流时暂停，高置信度完整面经自动入库。 |
| 小红书公开 URL 读取 | `url_import` | 可用 | 只处理无登录请求实际公开的单篇或列表内容，默认人工确认后入库。 |
| 其他平台关键词搜索 | `user_authorized_browser` | 预留 | 仅在获得平台许可、官方 API 或合规用户授权连接器后启用。 |
| 平台官方 API | `official_api` | 预留 | 根据平台条款、授权范围和接口能力接入。 |

## 数据模型与迁移

迁移：`migrations/versions/20260809_05_interview_collection_jobs.py`

公开网页迁移：`migrations/versions/20260827_25_interview_public_web_sources.py`

- `career_assistant.interview_collection_jobs`：关键词、平台、连接器类型、策略决定、执行状态、可观察错误。
- `career_assistant.interview_collection_candidates`：候选 URL、标题、摘要、解析后 Markdown、内容指纹及候选状态。
- `career_assistant.interview_web_documents`：按组织隔离的永久 URL 账本、规范正文、SHA-256、重试时钟和最终入库关系。
- `career_assistant.interview_experience_sources`：每份面经的唯一主来源和相同正文来源别名；后续重复发现只追加检索词和最后发现时间，不覆盖人工编辑正文。

候选资料导入后调用 `InterviewLibraryService.ingest`：生成面经档案、RAG 切片与索引，不重复实现文档处理或检索链路。

## API

- `POST /api/career/interview-library/parse-file`：临时解析上传文件并返回“公司、岗位、日期、标签、摘要、Markdown”的可编辑草稿；不写入数据库、不保存原文件。
- `GET /api/career/interview-library/collection-platforms`：获取平台和连接器策略。
- `POST /api/career/interview-library/public-web-imports`：创建 5–10 条全网公开网页任务；默认 10 条，搜索候选和实际 `/scrape` 调用均不超过本次配置，未配置 `FIRECRAWL_API_KEY` 时返回明确的 503 能力状态。
- `POST /api/career/interview-library/xiaohongshu-browser-imports`：创建 5–50 条的小红书浏览器关键词任务。
- `POST /api/career/interview-library/collection-jobs/{job_id}/xiaohongshu-discoveries`：登记搜索卡片，只接收 `note_id`、标题、作者和点赞展示值。
- `POST /api/career/interview-library/collection-jobs/{job_id}/xiaohongshu-notes`：受理一篇浏览器详情并在 `BackgroundTasks` 中执行 OCR、分析和条件自动入库。
- `POST /api/career/interview-library/collection-jobs/{job_id}/pause`：登录、验证、限流或用户停止时暂停/取消任务。
- `POST /api/career/interview-library/collection-jobs/{job_id}/complete`：所有已发现候选处理完成后结束任务。
- `POST /api/career/interview-library/collection-jobs`：保留既有平台关键词接口；小红书分支仍兼容旧的公开搜索导入。
- `GET /api/career/interview-library/collection-jobs/{job_id}`：查询任务与候选资料。
- `POST /api/career/interview-library/collect-url`：读取用户提交的公开 HTTPS URL。
- `POST /api/career/interview-library/collection-candidates/{candidate_id}/select`：标记候选待入库。
- `POST /api/career/interview-library/collection-candidates/{candidate_id}/import`：确认元数据和可编辑的 `markdown_content` 后写入面经库/RAG；人工正文优先于候选原正文。

## 小红书候选审核与人工保存

1. 采集器按公开笔记正文、图片顺序和 OCR 结果生成候选；图片字节只在临时解析期间存在。
2. `InterviewEvidenceAnalyzer` 只从已提取文本中做事实判断和清洗：删除平台标语、公开配图 URL、OCR 分段标签等采集噪音，不编造公司、岗位或题目。
3. 即使 Agent 标记为“需人工核验”或“不建议自动归档”，只要候选保留了正文，页面仍显示可编辑正文与“保存到面经库”入口。该判断只作为审核提示，不阻止用户决定。
4. 小红书公开 URL 任务的 `auto_import` 默认关闭；浏览器关键词任务默认开启，但仅在模型判定有效、置信度和结构化字段满足门槛时自动入库。
5. 保存时使用用户编辑后的连续正文，不写入“材料 N”“解析原文”“公开配图”“配图文字识别”等系统包装。来源、公司、岗位、摘要和标签由独立字段保存。
6. 入库成功后候选状态变为 `imported`，候选元数据保存 `imported_experience_id`，用于页面提示与防止重复保存。

## 安全与上线要求

URL 提取器拒绝 HTTP、URL 内嵌用户名密码、非标准端口、本机/私网/保留地址，并限制响应为 HTML、15 秒和 3MB 上限。生产部署时应在受控出网代理或网络策略中再次实施域名/IP 白名单与重定向校验，避免 DNS rebinding 等服务端请求伪造风险。

面向平台的自动采集必须在复核平台协议、官方 API 能力、访问频率、账号授权范围和内容使用权后再启用；不得通过保存用户密码、Cookie 或绕过验证码实现采集。

## 验证与运行

```powershell
# 执行数据库迁移
.\.venv\Scripts\alembic.exe upgrade head

# 离线验证采集策略与安全 URL 校验
.\.venv\Scripts\python.exe scripts\verify_interview_collection.py

# 验证小红书采集、Agent 清洗、人工正文优先与入库 ID 回写
.\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_orchestration.py

# 验证浏览器关键词采集、去重、暂停、OCR 清理和自动入库
.\.venv\Scripts\python.exe scripts\verify_xiaohongshu_browser_collection.py

# 验证小红书创建接口默认关闭自动入库
.\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_api.py

# 真实 PostgreSQL API 契约验证（仅创建并清理一个受限平台关键词任务）
.\.venv\Scripts\python.exe scripts\verify_interview_collection_api.py

# 前端构建
npm --prefix web-ui run build

# 真实 Firecrawl search/scrape 烟雾验证
$env:PUBLIC_WEB_TEST_KEYWORD='agent开发面经'
.\.venv\Scripts\python.exe scripts\verify_public_web_live.py --smoke-limit 1

# 真实 10 条任务、来源记录与第二轮不重复抓取验收
$env:PUBLIC_WEB_TEST_LIMIT='10'
.\.venv\Scripts\python.exe scripts\verify_public_web_live.py
```

## 全网公开网页调用链与计数

```text
PC 公开信息收集
  → POST public-web-imports
  → Firecrawl /v2/search（只发现 URL）
  → interview_web_documents（规范 URL 永久去重）
  → Firecrawl /v2/scrape（只处理新地址）
  → Markdown / 临时 OCR → SHA-256（正文去重）
  → InterviewEvidenceAnalyzer
  → InterviewLibraryService.ingest → RAG
  → interview_experience_sources（主来源 + 来源别名）
```

任务固定返回八项计数：发现地址、已知地址、实际解析、重复正文、有效面经、新入库、已过滤和失败。每个候选独立完成抓取、甄别和入库：一条有效面经通过分析后立即写入，不等待整批结束；其他页面失败不会回滚或阻塞已经成功的面经。可重试失败依次延后 1、6、24 小时，第四次失败转为永久失败；同一规范地址在未到重试时间前不会再次抓取。图片仅在正文不足时进入既有临时 OCR，解析结束后清理。

模型连接暂不可用时，只对标题明确属于面经、公司与岗位齐全、置信度不低于 0.7 且至少包含两个编号问题的正文启用确定性兜底；其他内容继续保留为候选，不会冒充完整面经。若任务曾在“正文入库成功、来源写入失败”之间中断，后续再次命中该地址会自动补齐唯一主来源，再把同正文的其他地址登记为来源别名。

## 文件导入交互

1. 用户仅选择 PDF、Word、Excel 或图片，所有元数据字段均可留空。
2. 后端复用 Docling/OCR 与云端 Vision 的附件解析链路，先得到正文文本；原文件始终只在临时目录内存在。
3. 本地元数据提取器从标题、字段行和问题列表预填公司、岗位、轮次、日期、技术标签与摘要，例如“字节AI Agent开发一面面经（TikTok）”会预填为“字节跳动 / AI Agent开发 / 一面”。
4. 前端展示识别置信度和证据，用户可修改任意字段及 Markdown 正文；只有点击“确认保存并建立索引”后，才会写入面经档案与 RAG 切片。

## 后续演进

1. 根据小红书页面结构变化维护卡片和详情 DOM 适配器，并保留真实浏览器 50 篇冒烟验证；验收以任务终态和数据库入库记录为准。
2. 后续需要跨进程恢复时再引入持久化 worker；当前任务恢复依赖页面保存的任务编号。

## 2026-08-28 采集归属与写权限

- 所有公开网页、小红书浏览器、小红书 URL、平台关键词和公开 URL 任务创建时写入 `interview_collection_jobs.created_by_actor_id`。
- 后台任务不依赖请求线程中的临时身份：自动入库时从持久化任务读取发起人，并写入 `interview_experiences.created_by_actor_id`；部分页面失败不会改变成功面经的归属。
- 人工确认候选时，服务端同时校验当前用户是否为采集任务发起人；管理员可以代为确认，最终面经仍归原任务发起人。历史无发起人的任务只有管理员可以确认，确认后新面经归该管理员。
- 前端不提交可伪造的创建者字段，所有归属都来自认证上下文或服务端持久化任务。
