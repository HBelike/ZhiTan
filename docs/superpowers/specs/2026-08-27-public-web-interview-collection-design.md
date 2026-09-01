# 全网公开面经信息收集设计

## 目标

将面经库现有“小红书关键词信息收集”调整为“全网公开信息收集”。用户输入关键词和目标数量后，系统通过 Firecrawl 搜索公开网页，先按永久抓取账本排除已处理地址，再解析新页面正文，复用现有 `InterviewEvidenceAnalyzer`、OCR、自动入库和 RAG 索引链路。

系统必须在落库时保留可点击来源地址，并确保以后重复搜索同一地址时不再获取正文。不同地址包含相同正文时只保留一份面经：最早成功落库的地址作为主来源，其他地址登记为来源别名。

## 已确认范围

- 首期支持 PC 面经库的关键词收集入口，输入 5–50 条，默认 20 条。
- 搜索对象是无需登录即可访问的公开 HTTPS 网页，不限定小红书。
- 使用 Firecrawl `/search` 发现页面，使用 `/scrape` 解析经过账本检查的新地址。
- 不使用 `/interact`，不处理登录、验证码、Cookie、账号会话或付费墙。
- 默认提取 Markdown 主正文；正文不足时才复用现有临时图片 OCR。
- 原始 HTML 和图片不持久化；数据库保存清洗正文、内容哈希、来源记录、状态和错误。
- 去重范围按组织隔离，不跨组织共享用户资料或采集结果。
- 同一规范地址不重复获取；不同地址正文相同不重复入库。
- 最早成功地址为主来源，其他重复地址保留为来源别名。
- 既有面经的人工修改不被后续采集覆盖。
- 本次不实现定时任务、整站 crawl、登录态网页、语义近似合并或跨组织公共缓存。
- 默认只在本地修改、测试和构建，不部署生产。

## 方案取舍

### 方案一：搜索时直接携带所有正文

Firecrawl `/search` 同时抓取搜索结果正文，调用最少，但无法在解析前查询永久账本。重复地址仍会产生抓取成本，无法满足“以后避免重复获取”。不采用。

### 方案二：先搜索、去重，再选择性解析

`/search` 只返回 URL、标题和摘要；系统规范化 URL 并查询永久账本，只对新地址调用 `/scrape`。解析完成后再按正文哈希做第二层去重。该方案调用链清晰、成本可控，能够同时满足跨批次 URL 去重和跨站正文去重。采用此方案。

### 方案三：多搜索供应商加 Firecrawl 解析

为搜索层同时接入多个供应商可以增加覆盖率，但会显著增加结果合并、计费、错误处理和配置复杂度。首期不实现，保留搜索适配器接口便于后续替换。

## 总体架构

```text
面经库“全网公开信息收集”
  -> 创建 public_web / public_api 采集任务
  -> FirecrawlSearchClient.search(keyword, limit)
  -> URL 规范化与候选登记
  -> 永久抓取账本原子认领新地址
       -> 已完成/重复/过滤/永久失败：跳过抓取
       -> 临时失败且未到重试时间：跳过抓取
       -> 新地址或允许重试：进入解析
  -> FirecrawlScrapeClient.scrape(canonical_url)
  -> 正文清洗与 SHA-256
       -> 已存在相同正文：登记来源别名，标记 duplicate
       -> 新正文：Agent 分析与条件自动入库
  -> 入库成功后登记主来源、回写面经 ID
  -> 汇总发现、跳过、解析、重复、入库、过滤和失败数量
```

Firecrawl 访问封装在后端适配器中，Web UI 不接触 API Key。现有项目已经使用 `httpx` 维护第三方 HTTP 客户端，首期沿用该模式调用 Firecrawl REST API，不额外引入 SDK。适配器接口隔离具体 API 版本和响应结构，领域服务只消费稳定的搜索结果与页面文档对象。

## Firecrawl 配置

运行环境新增：

```dotenv
FIRECRAWL_API_KEY=fc-...
# 仅自托管时配置，缺省使用 Firecrawl 官方服务。
FIRECRAWL_API_URL=https://api.firecrawl.dev
```

要求：

- 密钥只从 `.env.career-assistant` 或部署平台 Secret 读取。
- 源码、日志、采集任务元数据和数据库不保存密钥。
- 未配置密钥时，平台能力接口将全网采集标记为未就绪；创建任务返回可操作错误，不创建伪运行任务。
- 真实验收必须通过应用实际调用路径分别完成一次 `/search` 和 `/scrape`。

## 搜索与页面选择

搜索适配器接收用户原始关键词、目标数量和语言区域。首期构造一个稳定查询：保留用户关键词，并补充“面经 OR 面试经历 OR 面试题”意图词。返回数量允许高于目标数量，以补偿已知地址、重复正文和无效页面，但最多不超过目标数量的三倍且上限 100。

搜索结果只保留：

- 标题；
- 原始 URL；
- 搜索摘要；
- Firecrawl 返回的来源元数据。

以下地址在登记前丢弃：

- 非 HTTPS；
- 本机、私网、保留地址和非标准端口；
- 明确的登录、账户、搜索结果、下载文件或站内操作页面；
- 不支持的协议及无法规范化的 URL。

搜索结果顺序作为候选优先级。任务在达到用户目标数量的有效终态候选后停止继续解析，不要求消费全部搜索结果。

## URL 规范化

规范化器生成 `canonical_url`，规则如下：

1. 主机名转小写，统一 IDNA 表示；
2. 移除默认端口和 URL fragment；
3. 规范化空路径、重复斜线与尾部斜线；
4. 删除 `utm_*`、`spm`、`from`、`source`、`ref` 等已知追踪参数；
5. 保留可能影响正文身份的其他查询参数，并按键和值排序；
6. Firecrawl 返回最终跳转地址后再次规范化，将原地址登记为别名；
7. 不通过访问目标站点来猜测 canonical URL，避免去重阶段产生额外抓取。

规范化规则必须是纯函数并有回归测试。规则变更不得批量重写既有主来源，只影响以后新发现地址的匹配。

## 数据模型

### 永久抓取账本

新增 `career_assistant.interview_web_documents`：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键 |
| `organization_id` | 组织隔离键 |
| `canonical_url` | 规范地址 |
| `source_url` | 首次发现的原始地址 |
| `source_platform` | 根据域名推断的来源平台 |
| `title` / `search_snippet` | 搜索阶段元数据 |
| `status` | `discovered`、`fetching`、`fetched`、`duplicate`、`imported`、`filtered`、`retryable_failed`、`permanent_failed` |
| `normalized_markdown` | 已清洗且可复用的正文；不保存原始 HTML |
| `content_hash` | 清洗后正文 SHA-256，可空 |
| `imported_experience_id` | 已关联面经 ID，可空 |
| `error_code` / `error_message` | 最近错误 |
| `first_seen_at` / `last_seen_at` | 首次和最近发现时间 |
| `last_fetched_at` / `next_retry_at` | 获取与重试控制 |
| `metadata_json` | 搜索排名、发现关键词、Firecrawl 缓存状态等轻量元数据 |
| `created_at` / `updated_at` | 审计时间 |

约束与索引：

- 唯一约束：`(organization_id, canonical_url)`；
- 索引：`(organization_id, content_hash)`；
- 索引：`(organization_id, status, next_retry_at)`；
- `fetching` 通过数据库原子更新认领，避免同一组织的并发任务重复解析同一地址。

### 面经来源别名

新增 `career_assistant.interview_experience_sources`：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键 |
| `organization_id` | 组织隔离键 |
| `experience_id` | 面经 ID |
| `canonical_url` / `source_url` | 规范地址和原始地址 |
| `source_platform` | 来源平台 |
| `is_primary` | 是否主来源 |
| `discovery_keywords_json` | 发现该地址的去重关键词列表 |
| `first_seen_at` / `last_seen_at` | 发现时间 |

约束：

- 唯一约束：`(organization_id, canonical_url)`，同一地址只能归属一份面经；
- 每份面经只允许一个 `is_primary=true` 来源；
- 新面经入库时首个成功地址为主来源；
- 后续同内容地址只追加来源别名，不覆盖主来源；
- 迁移时为现有 `source_url` 非空的面经补建主来源记录。

现有面经表的 `source_url` 和 `source_platform` 继续保存主来源，保持 API 与 RAG 元数据兼容。来源别名表是新增信息，不改变既有正文和人工编辑字段。

## 正文规范化与内容去重

`content_hash` 基于解析后的 Markdown 计算，步骤为：

1. 删除导航、页脚、Cookie 提示和 Firecrawl 包装字段；
2. 删除来源 URL、抓取时间、图片地址等易变采集元数据；
3. 统一换行、空白、Unicode 和 Markdown 列表格式；
4. 保留题目顺序、公司、岗位、轮次和正文语义；
5. 对规范化文本计算 SHA-256。

去重顺序：

1. 搜索阶段按 `canonical_url` 跳过已知终态记录；
2. 解析阶段按 `content_hash` 查询账本和现有面经 `source_content_hash`；
3. 命中既有面经时，将新地址登记为来源别名并把账本标记为 `duplicate`；
4. 命中尚未入库的同内容文档时，只保留最早成功解析记录作为分析候选；
5. 未命中时才进入 Agent 分析和自动入库。

账本状态为 `fetched` 但尚未完成分析时，后续任务直接复用 `normalized_markdown`，只重试分析和入库，不再次调用 Firecrawl。候选表继续保存批次视图；永久账本保存跨批次复用所需的唯一一份清洗正文。

首期只做确定性的 URL 与正文哈希去重，不使用模糊标题、向量或语义相似度自动合并，避免将同一公司岗位的不同面试经历误判为重复。

## 解析、OCR 与自动入库

`/scrape` 默认请求 Markdown 主正文和页面 metadata，并允许 Firecrawl 使用其默认缓存。账本保存响应中的最终 URL、`cacheState` 和 `cachedAt`，用于解释内容来源，不把缓存命中误认为本地重复。

处理规则：

- Markdown 正文达到最低有效长度时直接进入内容清洗；
- 正文明显不足但页面包含公开图片时，最多选择 10 张正文图片进入现有临时 OCR；
- OCR 图片字节只保存在临时附件目录，解析后立即清理；
- 原始 HTML、完整 Firecrawl 响应和图片 URL 列表不持久化；
- OCR 与正文合并后复用 `InterviewEvidenceAnalyzer`；
- 满足现有有效面经、置信度、公司、岗位和具体问题门槛时自动入库；
- 不满足门槛的页面标记为 `filtered` 或保留为可人工核对候选，不伪造结构化字段。

## 任务、API 与状态

使用现有 `interview_collection_jobs` 和 `interview_collection_candidates` 记录批次与页面候选。任务使用：

- `platform_key=public_web`；
- `connector_kind=public_api`；
- `metadata_json.source_kind=firecrawl_public_web_search`。

新增接口：

- `POST /api/career/interview-library/public-web-imports`
  - 输入：`keyword`、`requested_limit`；
  - 行为：检查 Firecrawl 配置并创建后台任务。
- `GET /api/career/interview-library/collection-jobs/{job_id}`
  - 沿用现有任务详情，候选增加账本状态和来源信息。

后台执行流程复用现有任务服务和 `BackgroundTasks`。进程中断后允许用户重新启动同一关键词任务；永久账本保证已经解析或入库的 URL 不会重复获取。首期不新增持久化队列或租约恢复系统。

任务汇总字段：

- `discovered_count`：搜索发现数量；
- `known_url_count`：已知地址跳过数量；
- `scraped_count`：实际解析数量；
- `duplicate_count`：重复正文数量；
- `valid_count`：有效面经数量；
- `imported_count`：本批新入库数量；
- `filtered_count`：非面经或字段不足数量；
- `failed_count`：本批失败数量。

## 失败与重试

- Firecrawl 401/403：任务失败，提示配置或授权问题；不创建页面永久失败记录。
- Firecrawl 搜索限流或 5xx：任务进入失败状态，可重新启动；不伪造搜索结果。
- 页面超时、网络错误、Firecrawl 429/5xx：`retryable_failed`，按 1 小时、6 小时、24 小时递增，最多三次。
- 页面 404/410、明确登录墙、付费墙、robots/访问拒绝或无可用正文：`permanent_failed`，以后搜索直接跳过。
- Agent 或 OCR 暂时失败：保留已经解析的正文和内容哈希，不重新调用 Firecrawl；只重试分析阶段。
- 单页失败不中断其余页面；任务完成时如实展示失败数量。

失败状态允许用户在候选详情中看到来源地址、失败阶段和简化错误，但不展示密钥、完整响应、内部堆栈或第三方敏感信息。

## 页面设计

面经库顶部保留“信息收集”入口，将弹窗关键词模式改名为“全网公开信息收集”。原“小红书 URL 读取”作为单链接入口继续保留，不参与全网关键词任务。

弹窗字段：

- 搜索关键词；
- 最多收集数量，默认 20、范围 5–50；
- 简明说明：系统只读取公开网页，已处理地址不会重复获取。

进度阶段：

```text
搜索公开网页 -> 排除已知地址 -> 解析新内容 -> 去重与分析 -> 写入面经库
```

候选卡展示来源平台、标题、可点击地址和状态。任务完成后展示八项汇总计数。面经详情继续显示主来源链接，并增加“其他相同来源 N 个”的可展开列表。

## 安全与数据边界

- Firecrawl 只接收用户关键词和经过校验的公开 HTTPS URL；
- 不向 Firecrawl 发送用户账号、Cookie、面经库正文、组织信息或浏览器会话；
- 不解析登录后页面，不使用 Firecrawl Profile 或 `/interact`；
- 对搜索结果继续执行本地 URL 安全校验，拒绝本机、私网、保留地址和非标准端口；
- 数据库不保存原始 HTML、图片字节或完整第三方响应；
- 用户可从主来源和来源别名追溯每份自动收录面经。

## 测试与验收

### 单元测试

- URL 规范化：追踪参数、fragment、查询参数顺序、尾部斜线、IDNA 和非法地址；
- 账本认领：并发任务只有一个可以进入 `fetching`；
- 终态地址在新任务中不调用 `/scrape`；
- 临时失败到期前跳过，到期后允许重试；
- 不同 URL 相同正文只生成一个内容哈希和一份面经；
- 来源别名追加且不覆盖最早主来源；
- 已有面经 `source_content_hash` 可被新任务识别；
- Agent/OCR 重试不重复请求 Firecrawl。

### API 与前端测试

- 未配置 Firecrawl 时能力门禁和错误文案；
- 5–50 条输入限制；
- 任务汇总八项计数准确；
- 候选和面经详情展示可点击来源；
- 来源别名列表和主来源标识；
- 再次提交相同关键词时已知地址被跳过；
- 现有文件导入、小红书单 URL 导入和面经编辑功能不退化。

### 真实验收

1. 配置真实 `FIRECRAWL_API_KEY`；
2. 通过应用接口执行一次 limit=1 的搜索与解析烟雾测试；
3. 使用“agent开发面经”、目标 50 创建真实任务；
4. 核对搜索数、已知地址跳过数、实际解析数、重复数和新入库数；
5. 在数据库确认新面经的主来源和重复正文的来源别名；
6. 再次执行同一关键词，确认已处理规范地址不再触发 `/scrape`；
7. 只有任务进入真实终态且数据库存在可追溯来源记录时才报告完成。

## 文档与发布

实现时同步更新：

- `docs/interview_library_collection_module.md`；
- 新增 Firecrawl 环境变量示例；
- 本地启动和真实烟雾测试说明；
- 面经来源与去重规则说明。

生产部署、生产密钥配置和生产数据库迁移不在本次本地实施授权范围内。
