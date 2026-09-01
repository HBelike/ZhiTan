# 全网公开面经信息收集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将面经库的关键词信息收集改为基于 Firecrawl 的全网公开网页搜索、永久去重、正文解析、Agent 分析、自动入库与来源追踪链路。

**Architecture:** 后端使用 Firecrawl v2 `/search` 只发现 URL，经过本地 HTTPS 安全校验和组织级永久账本后，再用 `/scrape` 获取新页面的 Markdown 与正文图片；解析正文先按规范 URL 去重，再按规范正文 SHA-256 去重。现有 `InterviewEvidenceAnalyzer`、临时图片 OCR、`InterviewLibraryService.ingest` 与 RAG 索引保持为唯一分析和入库链路，新增来源别名表保证最早来源不被后续重复地址覆盖。

**Tech Stack:** Python 3.11、FastAPI/Pydantic、httpx、SQLAlchemy Core、PostgreSQL/Alembic、Vue 3/Vite、Node test runner、Firecrawl API v2。

**Spec:** `docs/superpowers/specs/2026-08-27-public-web-interview-collection-design.md`

## Global Constraints

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
- Firecrawl 请求固定使用 `POST /v2/search` 与 `POST /v2/scrape`；Web UI、日志和数据库都不能接触 `FIRECRAWL_API_KEY`。
- 保留工作区内与本功能无关的现有修改，每次提交只暂存当前任务列出的文件。

## 文件职责图

- `src/career_assistant/interview_library/public_web.py`：Firecrawl 配置、HTTP 适配器、URL 安全校验与规范化、Markdown 规范化、正文图片临时下载。
- `src/career_assistant/interview_library/models.py`：公开网页账本、来源别名和状态的稳定领域记录。
- `src/career_assistant/interview_library/repository.py`：账本登记/原子认领/重试、正文哈希查询、来源主从关系与查询。
- `migrations/versions/20260827_25_interview_public_web_sources.py`：永久账本、来源别名、索引、约束和既有来源回填。
- `src/career_assistant/interview_library/collection.py`：创建/执行公开网页任务，复用 Agent、OCR、入库和候选状态机。
- `src/career_assistant/web/router.py`：Firecrawl 依赖装配、能力门禁、创建任务接口、任务与来源 JSON 契约。
- `web-ui/src/public-web-collection.js`：公开网页进度、汇总和状态展示的纯函数。
- `web-ui/src/components/InterviewLibraryPage.vue`：PC 关键词收集表单、轮询、八项统计、候选来源与面经来源别名。
- `scripts/verify_public_web_collection.py`：不访问网络的编排回归。
- `scripts/verify_public_web_api.py`：FastAPI 创建任务、配置门禁和后台收敛回归。
- `scripts/verify_public_web_live.py`：真实本地 API 搜索、解析、落库和第二次去重验收。
- `tests/test_public_web_collection.py`：Firecrawl 适配器、URL/正文规范化与错误分类单元测试。
- `tests/test_interview_public_web_repository.py`：仓储 SQL、组织隔离、原子认领、重试与来源唯一性测试。
- `tests/test_interview_public_web_migration.py`：迁移结构、回填和回滚契约测试。
- `web-ui/src/public-web-collection.test.js`：前端纯函数测试。
- `web-ui/src/interview-library-public-web.test.js`：PC 页面契约测试。

---

### Task 1: Firecrawl v2 适配器与确定性规范化

**Files:**
- Create: `src/career_assistant/interview_library/public_web.py`
- Create: `tests/test_public_web_collection.py`
- Modify: `.env.career-assistant.example`

**Interfaces:**
- Produces: `FirecrawlSettings(api_key: str, api_url: str, timeout_seconds: float)`。
- Produces: `load_firecrawl_settings() -> FirecrawlSettings | None`。
- Produces: `canonicalize_public_url(value: str) -> str`。
- Produces: `validate_public_https_target(value: str, *, resolver=socket.getaddrinfo) -> None`。
- Produces: `normalize_public_markdown(value: str) -> str`。
- Produces: `hash_public_markdown(value: str) -> str`。
- Produces: `infer_public_platform(value: str) -> str`。
- Produces: `FirecrawlSearchResult(title: str | None, url: str, snippet: str | None, metadata: Mapping[str, object])`。
- Produces: `FirecrawlDocument(source_url: str, final_url: str, title: str | None, markdown: str, image_urls: tuple[str, ...], metadata: Mapping[str, object])`。
- Produces: `FirecrawlClient.search(keyword: str, *, limit: int) -> tuple[FirecrawlSearchResult, ...]`。
- Produces: `FirecrawlClient.scrape(source_url: str) -> FirecrawlDocument`。
- Produces: `PublicWebImageDownloader.download(urls: Sequence[str], *, limit: int = 10) -> tuple[DownloadedPublicImage, ...]`。

- [ ] **Step 1: 为 URL、Markdown 与 Firecrawl HTTP 契约写失败测试**

```python
def test_canonicalize_public_url_removes_only_tracking_identity() -> None:
    assert canonicalize_public_url(
        "HTTPS://Example.COM:443/a//b/?utm_source=x&b=2&a=1#card"
    ) == "https://example.com/a/b?a=1&b=2"


def test_canonicalize_public_url_rejects_private_or_nonstandard_target() -> None:
    for value in ("http://example.com/a", "https://127.0.0.1/a", "https://example.com:8443/a"):
        with pytest.raises(PublicWebUrlError):
            canonicalize_public_url(value)


def test_validate_public_https_target_rejects_hostname_resolving_to_private_ip() -> None:
    resolver = lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]
    with pytest.raises(PublicWebUrlError, match="内网"):
        validate_public_https_target("https://internal.example/interview", resolver=resolver)


def test_normalized_markdown_has_stable_hash() -> None:
    left = "# 面经\r\n\r\n-  Redis   缓存\n来源：https://a.example"
    right = "# 面经\n\n- Redis 缓存\n抓取时间：2026-08-27"
    assert hash_public_markdown(left) == hash_public_markdown(right)


def test_firecrawl_search_discovers_without_scraping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/search"
        assert request.headers["authorization"] == "Bearer fc-test"
        assert json.loads(request.content) == {
            "query": "agent开发面经 (面经 OR 面试经历 OR 面试题)",
            "limit": 6,
            "sources": ["web"],
            "country": "CN",
            "ignoreInvalidURLs": True,
        }
        return httpx.Response(200, json={"success": True, "data": {"web": [
            {"title": "Agent 面经", "description": "一面问题", "url": "https://example.com/a"}
        ]}})

    client = FirecrawlClient(
        FirecrawlSettings("fc-test", "https://api.firecrawl.dev", 60.0),
        transport=httpx.MockTransport(handler),
    )
    assert client.search("agent开发面经", limit=6)[0].url == "https://example.com/a"


def test_firecrawl_scrape_returns_markdown_images_and_cache_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/scrape"
        assert json.loads(request.content) == {
            "url": "https://example.com/a",
            "formats": ["markdown", "images"],
            "onlyMainContent": True,
            "removeBase64Images": True,
            "storeInCache": True,
            "timeout": 60000,
        }
        return httpx.Response(200, json={"success": True, "data": {
            "markdown": "# 一面\n\n1. 解释 Agent memory",
            "images": ["https://cdn.example.com/1.png"],
            "metadata": {"url": "https://example.com/final", "title": "面经", "statusCode": 200,
                         "cacheState": "hit", "cachedAt": "2026-08-27T00:00:00Z"},
        }})

    client = FirecrawlClient(
        FirecrawlSettings("fc-test", "https://api.firecrawl.dev", 60.0),
        transport=httpx.MockTransport(handler),
    )
    document = client.scrape("https://example.com/a")
    assert document.final_url == "https://example.com/final"
    assert document.image_urls == ("https://cdn.example.com/1.png",)
    assert document.metadata["cacheState"] == "hit"
```

- [ ] **Step 2: 运行测试并确认适配器尚不存在**

Run: `python -m pytest tests/test_public_web_collection.py -q`

Expected: FAIL，导入 `src.career_assistant.interview_library.public_web` 失败。

- [ ] **Step 3: 实现稳定数据对象、URL/正文纯函数与错误分类**

```python
TRACKING_KEYS = frozenset({"spm", "from", "source", "ref", "referrer"})
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class FirecrawlSettings:
    api_key: str
    api_url: str = "https://api.firecrawl.dev"
    timeout_seconds: float = 60.0


class FirecrawlRequestError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


def load_firecrawl_settings() -> FirecrawlSettings | None:
    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        return None
    api_url = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev").strip().rstrip("/")
    return FirecrawlSettings(api_key=api_key, api_url=api_url)


def hash_public_markdown(value: str) -> str:
    normalized = normalize_public_markdown(value)
    return sha256(normalized.encode("utf-8")).hexdigest()
```

实现时必须同时满足：`canonicalize_public_url` 是不访问网络的纯函数，只允许标准端口 HTTPS，拒绝账号密码、本机名和私网/保留 IP 字面量，删除 fragment、默认端口、重复斜线、尾部斜线和已知追踪参数，并保留且排序可能决定正文的查询参数；`validate_public_https_target` 负责解析域名并拒绝解析到私网、链路本地、组播或保留地址。Markdown 用 NFC 统一 Unicode，删除“来源 URL/抓取时间/图片地址”采集包装行，但保留题目顺序与正文。

- [ ] **Step 4: 实现 Firecrawl v2 搜索、抓取和受限图片下载**

```python
class FirecrawlClient:
    def __init__(self, settings: FirecrawlSettings, *, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.api_url,
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=settings.timeout_seconds,
            transport=transport,
        )

    def search(self, keyword: str, *, limit: int) -> tuple[FirecrawlSearchResult, ...]:
        query = f"{' '.join(keyword.split())} (面经 OR 面试经历 OR 面试题)"
        payload = {"query": query, "limit": limit, "sources": ["web"],
                   "country": "CN", "ignoreInvalidURLs": True}
        body = self._post("/v2/search", payload, operation="search")
        return tuple(self._to_search_result(item) for item in body.get("data", {}).get("web", []))

    def scrape(self, source_url: str) -> FirecrawlDocument:
        payload = {"url": source_url, "formats": ["markdown", "images"],
                   "onlyMainContent": True, "removeBase64Images": True,
                   "storeInCache": True, "timeout": 60000}
        body = self._post("/v2/scrape", payload, operation="scrape")
        return self._to_document(source_url, body.get("data") or {})

    def close(self) -> None:
        self._client.close()
```

`PublicWebImageDownloader` 对每个最终跳转地址重新做公网 HTTPS 校验，只接受 `image/jpeg`、`image/png`、`image/webp`，单图最多 10 MiB、最多 10 张；只返回内存字节与媒体类型，不返回可持久化对象。401/403 归类为 `firecrawl_auth_failed`，429/5xx/网络超时归类为可重试，404/410/登录墙/付费墙/robots/无正文归类为永久失败；错误消息只保留用户可操作描述。

- [ ] **Step 5: 增加本地环境变量示例并验证不会暴露密钥**

```dotenv
# 全网公开面经收集。Key 只放本地 .env.career-assistant 或部署平台 Secret。
FIRECRAWL_API_KEY=
FIRECRAWL_API_URL=https://api.firecrawl.dev
```

Run: `python -m pytest tests/test_public_web_collection.py -q`

Expected: PASS，MockTransport 断言请求未携带 Cookie，测试输出不包含 `fc-test`。

- [ ] **Step 6: 提交适配器任务**

```powershell
git add -- src/career_assistant/interview_library/public_web.py tests/test_public_web_collection.py .env.career-assistant.example
git commit -m "feat: add Firecrawl public web adapter"
```

---

### Task 2: 永久抓取账本、来源别名与组织级仓储

**Files:**
- Create: `migrations/versions/20260827_25_interview_public_web_sources.py`
- Create: `tests/test_interview_public_web_migration.py`
- Create: `tests/test_interview_public_web_repository.py`
- Modify: `src/career_assistant/interview_library/models.py:68-89,187-235`
- Modify: `src/career_assistant/interview_library/repository.py:38-46,85-193,638-1190,1260-1425,1576-1680`

**Interfaces:**
- Consumes: Task 1 `canonicalize_public_url()` 与 `hash_public_markdown()`。
- Produces: `InterviewWebDocumentStatus` 八态枚举。
- Produces: `InterviewWebDocumentRecord` 与 `InterviewExperienceSourceRecord`。
- Produces: `register_web_document(...) -> InterviewWebDocumentRecord`。
- Produces: `claim_web_document(organization_id, document_id, *, now) -> InterviewWebDocumentRecord | None`。
- Produces: `mark_web_document_fetched(...)`、`mark_web_document_duplicate(...)`、`mark_web_document_imported(...)`、`mark_web_document_filtered(...)`、`mark_web_document_failed(...)`。
- Produces: `link_web_document_experience(organization_id, document_id, experience_id, *, status) -> InterviewWebDocumentRecord`。
- Produces: `find_imported_document_by_content_hash(organization_id, content_hash) -> InterviewWebDocumentRecord | None`。
- Produces: `list_web_documents_by_content_hash(organization_id, content_hash) -> list[InterviewWebDocumentRecord]`。
- Produces: `get_experience_by_source_content_hash(organization_id, content_hash) -> InterviewExperienceRecord | None`。
- Produces: `add_experience_source(...) -> InterviewExperienceSourceRecord`。
- Produces: `list_experience_sources(organization_id, experience_id) -> list[InterviewExperienceSourceRecord]`。

- [ ] **Step 1: 写迁移结构和回填的失败测试**

```python
def test_public_web_migration_creates_ledger_and_source_aliases() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'revision = "20260827_25"' in source
    assert 'down_revision = "20260826_24"' in source
    assert "CREATE TABLE career_assistant.interview_web_documents" in source
    assert "UNIQUE (organization_id, canonical_url)" in source
    assert "CREATE TABLE career_assistant.interview_experience_sources" in source
    assert "uq_interview_experience_sources_primary" in source
    assert "WHERE is_primary" in source


def test_public_web_migration_backfills_existing_primary_sources() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO career_assistant.interview_experience_sources" in source
    assert "FROM career_assistant.interview_experiences" in source
    assert "source_url IS NOT NULL" in source
    assert "ON CONFLICT (organization_id, canonical_url) DO NOTHING" in source
```

- [ ] **Step 2: 写仓储原子认领、重试与来源唯一性的失败测试**

```python
def test_claim_web_document_is_organization_scoped_and_atomic() -> None:
    repository = InterviewLibraryRepository(FakeDatabase([FakeResult(row=WEB_DOCUMENT_ROW)]))
    claimed = repository.claim_web_document(ORG_ID, DOCUMENT_ID, now=NOW)
    sql, params = repository._database.connection.calls[0]
    assert "UPDATE career_assistant.interview_web_documents" in sql
    assert "status IN ('discovered', 'retryable_failed')" in sql
    assert "next_retry_at IS NULL OR next_retry_at <= :now" in sql
    assert "organization_id = :organization_id" in sql
    assert claimed is not None and claimed.status is InterviewWebDocumentStatus.FETCHING
    assert params["organization_id"] == ORG_ID


def test_add_duplicate_source_does_not_replace_primary() -> None:
    repository = InterviewLibraryRepository(FakeDatabase([FakeResult(row=ALIAS_SOURCE_ROW)]))
    source = repository.add_experience_source(
        ORG_ID, EXPERIENCE_ID,
        canonical_url="https://mirror.example/agent",
        source_url="https://mirror.example/agent?utm_source=search",
        source_platform="mirror.example",
        keyword="agent开发面经",
        is_primary=False,
    )
    sql, _ = repository._database.connection.calls[0]
    assert "ON CONFLICT (organization_id, canonical_url) DO UPDATE" in sql
    assert "is_primary = interview_experience_sources.is_primary" in sql
    assert source.is_primary is False
```

- [ ] **Step 3: 运行测试并确认迁移、记录与仓储方法尚不存在**

Run: `python -m pytest tests/test_interview_public_web_migration.py tests/test_interview_public_web_repository.py -q`

Expected: FAIL，迁移文件和公开网页领域类型不存在。

- [ ] **Step 4: 创建迁移，严格实现两张表、约束、索引和回填**

```sql
CREATE TABLE career_assistant.interview_web_documents (
  id UUID PRIMARY KEY,
  organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
  canonical_url TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_platform TEXT NOT NULL,
  title TEXT,
  search_snippet TEXT,
  status TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN (
    'discovered','fetching','fetched','duplicate','imported','filtered',
    'retryable_failed','permanent_failed'
  )),
  normalized_markdown TEXT,
  content_hash TEXT,
  imported_experience_id UUID REFERENCES career_assistant.interview_experiences(id) ON DELETE SET NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_message TEXT,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_fetched_at TIMESTAMPTZ,
  next_retry_at TIMESTAMPTZ,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (organization_id, canonical_url)
);

CREATE TABLE career_assistant.interview_experience_sources (
  id UUID PRIMARY KEY,
  organization_id UUID NOT NULL REFERENCES career_assistant.organizations(id) ON DELETE CASCADE,
  experience_id UUID NOT NULL REFERENCES career_assistant.interview_experiences(id) ON DELETE CASCADE,
  canonical_url TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_platform TEXT NOT NULL,
  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
  discovery_keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (organization_id, canonical_url)
);
```

同时创建 `(organization_id, content_hash)`、`(organization_id, status, next_retry_at)` 索引，以及 `CREATE UNIQUE INDEX ... ON ... (organization_id, experience_id) WHERE is_primary`。回填时用迁移内的确定性 SQL 规范化现有 URL 的 fragment 和追踪参数无法可靠完成，因此迁移仅将原始 `source_url` 同时写入 `canonical_url`；以后由应用规范化新地址，避免迁移改写用户历史来源。

- [ ] **Step 5: 增加领域状态与稳定记录**

```python
class InterviewWebDocumentStatus(StrEnum):
    DISCOVERED = "discovered"
    FETCHING = "fetching"
    FETCHED = "fetched"
    DUPLICATE = "duplicate"
    IMPORTED = "imported"
    FILTERED = "filtered"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENT_FAILED = "permanent_failed"


@dataclass(frozen=True)
class InterviewExperienceSourceRecord:
    id: UUID
    organization_id: UUID
    experience_id: UUID
    canonical_url: str
    source_url: str
    source_platform: str
    is_primary: bool
    discovery_keywords: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime
```

`InterviewWebDocumentRecord` 必须完整映射迁移字段；`normalized_markdown` 只保存清洗正文，`metadata_json` 只允许搜索排名、关键词、最终 URL、`cacheState`、`cachedAt`、失败阶段和轻量分析结果。

- [ ] **Step 6: 实现账本登记、原子认领、状态更新和重试时间**

```python
RETRY_DELAYS = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))


def claim_web_document(self, organization_id: UUID, document_id: UUID, *, now: datetime) -> InterviewWebDocumentRecord | None:
    row = connection.execute(text("""
        UPDATE career_assistant.interview_web_documents
        SET status = 'fetching', attempt_count = attempt_count + 1,
            error_code = NULL, error_message = NULL, next_retry_at = NULL, updated_at = NOW()
        WHERE id = :document_id AND organization_id = :organization_id
          AND status IN ('discovered', 'retryable_failed')
          AND (next_retry_at IS NULL OR next_retry_at <= :now)
        RETURNING *
    """), {"document_id": document_id, "organization_id": organization_id, "now": now}).mappings().one_or_none()
```

`register_web_document` 使用 `INSERT ... ON CONFLICT (organization_id, canonical_url) DO UPDATE`，只更新 `last_seen_at`、非空标题/摘要和合并后的关键词/排名元数据，不把终态改回 `discovered`。`mark_web_document_failed` 根据 `attempt_count` 计算 1h/6h/24h，第三次后固定为 `permanent_failed`；Agent/OCR 失败调用 `mark_web_document_fetched` 保留正文，不走 Firecrawl 重试状态。

- [ ] **Step 7: 实现正文哈希查询和不可覆盖的来源主从关系**

```python
def add_experience_source(..., keyword: str, is_primary: bool) -> InterviewExperienceSourceRecord:
    row = connection.execute(text("""
        INSERT INTO career_assistant.interview_experience_sources (
          id, organization_id, experience_id, canonical_url, source_url,
          source_platform, is_primary, discovery_keywords_json
        ) VALUES (
          :id, :organization_id, :experience_id, :canonical_url, :source_url,
          :source_platform, :is_primary, jsonb_build_array(:keyword)
        )
        ON CONFLICT (organization_id, canonical_url) DO UPDATE
        SET last_seen_at = NOW(), updated_at = NOW(),
            discovery_keywords_json = (
              SELECT jsonb_agg(DISTINCT item)
              FROM jsonb_array_elements_text(
                interview_experience_sources.discovery_keywords_json || jsonb_build_array(:keyword)
              ) AS item
            ),
            is_primary = interview_experience_sources.is_primary
        RETURNING *
    """), params).mappings().one()
```

新增 `get_experience_by_source_content_hash` 只读查询，不能调用 `create_experience` 的 upsert 来试探重复；`list_web_documents_by_content_hash` 返回相同正文的全部账本记录，供自动或人工入库后一次性登记来源别名；`list_experience_sources` 强制 `organization_id` 和 `experience_id` 双条件并按 `is_primary DESC, first_seen_at ASC` 排序。

- [ ] **Step 8: 运行迁移与仓储测试**

Run: `python -m pytest tests/test_interview_public_web_migration.py tests/test_interview_public_web_repository.py -q`

Expected: PASS；额外运行 `python -m pytest tests/test_interview_library_edit.py -q`，确认人工编辑路径未改变主来源。

- [ ] **Step 9: 提交持久化任务**

```powershell
git add -- migrations/versions/20260827_25_interview_public_web_sources.py tests/test_interview_public_web_migration.py tests/test_interview_public_web_repository.py src/career_assistant/interview_library/models.py src/career_assistant/interview_library/repository.py
git commit -m "feat: persist public web interview sources"
```

---

### Task 3: 搜索、账本去重、解析、OCR、正文去重与自动入库编排

**Files:**
- Create: `scripts/verify_public_web_collection.py`
- Modify: `src/career_assistant/interview_library/collection.py:707-790,1363-1609,1997-2225`

**Interfaces:**
- Consumes: Task 1 `FirecrawlClient`、`PublicWebImageDownloader` 与规范化函数。
- Consumes: Task 2 账本、哈希查询与来源仓储方法。
- Produces: `create_public_web_import_job(organization_id, *, keyword, requested_limit) -> InterviewCollectionJobRecord`。
- Produces: `run_public_web_import(organization_id, job_id) -> None`。
- Produces: 八项任务汇总 `discovered_count`、`known_url_count`、`scraped_count`、`duplicate_count`、`valid_count`、`imported_count`、`filtered_count`、`failed_count`。

- [ ] **Step 1: 写离线端到端编排替身与失败断言**

```python
def test_public_web_run_skips_known_url_and_deduplicates_same_body() -> None:
    repository = FakeRepository(known_urls={"https://known.example/interview"})
    firecrawl = FakeFirecrawlClient(
        search_results=[
            result("https://known.example/interview"),
            result("https://a.example/agent?utm_source=x"),
            result("https://mirror.example/agent"),
        ],
        documents={
            "https://a.example/agent": document("https://a.example/agent", BODY),
            "https://mirror.example/agent": document("https://mirror.example/agent", BODY),
        },
    )
    service = build_service(repository, firecrawl, analyzer=ValidAnalyzer())
    job = service.create_public_web_import_job(ORG_ID, keyword="agent开发面经", requested_limit=5)
    service.run_public_web_import(ORG_ID, job.id)

    assert firecrawl.scrape_calls == ["https://a.example/agent", "https://mirror.example/agent"]
    assert len(repository.imported_experiences) == 1
    assert [item.is_primary for item in repository.sources] == [True, False]
    assert repository.jobs[job.id].metadata_json["summary"] == {
        "discovered_count": 3, "known_url_count": 1, "scraped_count": 2,
        "duplicate_count": 1, "valid_count": 1, "imported_count": 1,
        "filtered_count": 0, "failed_count": 0,
    }


def test_fetched_document_retries_analysis_without_second_scrape() -> None:
    repository = FakeRepository(fetched_markdown={"https://a.example/agent": BODY})
    firecrawl = FakeFirecrawlClient(search_results=[result("https://a.example/agent")])
    service = build_service(repository, firecrawl, analyzer=ValidAnalyzer())
    job = service.create_public_web_import_job(ORG_ID, keyword="agent开发面经", requested_limit=5)
    service.run_public_web_import(ORG_ID, job.id)
    assert firecrawl.scrape_calls == []
    assert len(repository.imported_experiences) == 1


def test_ocr_failure_keeps_scraped_markdown_and_does_not_schedule_refetch() -> None:
    service = build_service_with_failing_ocr()
    service.run_public_web_import(ORG_ID, JOB_ID)
    document = service._repository.documents_by_url["https://image.example/interview"]
    assert document.status is InterviewWebDocumentStatus.FETCHED
    assert document.normalized_markdown
    assert document.next_retry_at is None
```

- [ ] **Step 2: 运行编排脚本并确认公开网页任务方法尚不存在**

Run: `python scripts/verify_public_web_collection.py`

Expected: FAIL，`InterviewCollectionService.create_public_web_import_job` 不存在。

- [ ] **Step 3: 注入 Firecrawl 与图片下载器并增加动态公开网页能力**

```python
def __init__(..., firecrawl_client: FirecrawlClient | None = None,
             public_image_downloader: PublicWebImageDownloader | None = None) -> None:
    ...
    self._firecrawl_client = firecrawl_client
    self._public_image_downloader = public_image_downloader or PublicWebImageDownloader()


def public_web_ready(self) -> bool:
    return self._firecrawl_client is not None


def close(self) -> None:
    ...
    if self._firecrawl_client is not None:
        self._firecrawl_client.close()
    self._public_image_downloader.close()
```

在 `_POLICIES` 新增 `public_web`，`connector_kind=CollectionConnectorKind.PUBLIC_API`，策略文案固定为“只搜索和解析无需登录的公开 HTTPS 页面；不发送账号、Cookie、组织资料或面经库正文，不绕过登录、验证码和访问限制。”

- [ ] **Step 4: 实现任务创建和初始八项汇总**

```python
def create_public_web_import_job(self, organization_id: UUID, *, keyword: str,
                                 requested_limit: int) -> InterviewCollectionJobRecord:
    normalized_keyword = " ".join(keyword.split())
    if not self.public_web_ready():
        raise CollectionOperationError("firecrawl_not_configured", "未配置全网公开信息收集服务。")
    if not normalized_keyword:
        raise ValueError("请输入搜索关键词。")
    if not 5 <= requested_limit <= 50:
        raise ValueError("全网公开信息收集数量必须在 5 到 50 之间")
    return self._repository.create_collection_job(
        organization_id=organization_id, platform_key="public_web",
        keyword=normalized_keyword, requested_limit=requested_limit,
        connector_kind=CollectionConnectorKind.PUBLIC_API,
        policy_decision=self._POLICIES["public_web"].policy_decision,
        metadata_json={"source_kind": "firecrawl_public_web_search", "phase": "search",
                       "progress_percent": 0, "progress_message": "正在搜索公开网页。",
                       "summary": self._new_public_web_summary()},
    )
```

- [ ] **Step 5: 实现两阶段搜索与 URL 账本认领**

```python
search_limit = min(100, job.requested_limit * 3)
results = self._firecrawl_client.search(job.keyword, limit=search_limit)
for rank, result in enumerate(results, start=1):
    try:
        canonical_url = canonicalize_public_url(result.url)
        validate_public_https_target(canonical_url)
    except PublicWebUrlError:
        summary["filtered_count"] += 1
        continue
    document = self._repository.register_web_document(
        organization_id, canonical_url=canonical_url, source_url=result.url,
        source_platform=infer_public_platform(canonical_url), title=result.title,
        search_snippet=result.snippet, keyword=job.keyword, search_rank=rank,
    )
    candidate = self._repository.create_collection_candidate(
        organization_id, collection_job_id=job.id, source_url=result.url,
        canonical_url=canonical_url, source_platform=document.source_platform,
        title=result.title, snippet=result.snippet,
        metadata_json={"web_document_id": str(document.id),
                       "ledger_status": document.status.value,
                       "scrape_performed": False},
    )
```

终态 `duplicate/imported/filtered/permanent_failed` 增加 `known_url_count` 后跳过；`fetched` 直接复用 `normalized_markdown`；`retryable_failed` 未到 `next_retry_at` 视为已知跳过；只有 `claim_web_document` 返回记录才调用 `/scrape`。候选状态继续使用现有枚举，并把更细账本状态放在 `metadata.ledger_status`，避免修改旧表 CHECK 约束。

- [ ] **Step 6: 实现抓取、最终跳转重登记、正文清洗和 OCR 降级**

```python
scraped = self._firecrawl_client.scrape(document.canonical_url)
final_url = canonicalize_public_url(scraped.final_url)
markdown = normalize_public_markdown(scraped.markdown)
if len(markdown) < MIN_PUBLIC_WEB_MARKDOWN_CHARACTERS and scraped.image_urls:
    markdown = self._append_public_web_ocr(
        markdown, self._public_image_downloader.download(scraped.image_urls, limit=10)
    )
normalized_markdown = normalize_public_markdown(markdown)
content_hash = hash_public_markdown(normalized_markdown)
self._repository.mark_web_document_fetched(
    organization_id, document.id, canonical_url=final_url,
    normalized_markdown=normalized_markdown, content_hash=content_hash,
    metadata_json={"cacheState": scraped.metadata.get("cacheState"),
                   "cachedAt": scraped.metadata.get("cachedAt")},
)
self._repository.update_collection_candidate(
    organization_id, candidate.id,
    metadata_json={"ledger_status": "fetched", "scrape_performed": True},
)
```

`_append_public_web_ocr` 必须沿用 `TemporaryAttachmentStore.save_bytes`、`AttachmentKind.INTERVIEW_EVIDENCE_IMAGE`、`AttachmentParser.parse` 和 `cleanup`，并在 `finally` 清理全部临时附件；候选和账本元数据只保存 OCR 成功/失败数量及简化错误，不保存图片 URL 或图片字节。

- [ ] **Step 7: 实现正文哈希去重、主来源和来源别名**

```python
existing_document = self._repository.find_imported_document_by_content_hash(
    organization_id, content_hash
)
existing_experience = (
    self._repository.get_experience(organization_id, existing_document.imported_experience_id)
    if existing_document and existing_document.imported_experience_id else
    self._repository.get_experience_by_source_content_hash(organization_id, content_hash)
)
if existing_experience is not None:
    self._repository.add_experience_source(
        organization_id, existing_experience.id,
        canonical_url=final_url, source_url=document.source_url,
        source_platform=document.source_platform, keyword=job.keyword, is_primary=False,
    )
    self._repository.mark_web_document_duplicate(
        organization_id, document.id, imported_experience_id=existing_experience.id
    )
    summary["duplicate_count"] += 1
    continue
```

同内容但尚未入库时，只允许 `first_seen_at` 最早的账本记录进入分析；其余记录标记 `duplicate`，在 metadata 保存 `duplicate_of_canonical_url`，不重复调用 Agent。自动入库或用户通过既有候选“保存到面经库”后，统一调用 `_attach_content_sources(organization_id, experience_id, content_hash, primary_document_id)`：读取 `list_web_documents_by_content_hash`，把最早成功记录设为主来源、其余记录设为别名，并将所有账本的 `imported_experience_id` 回写为同一面经。绝不使用标题或向量相似度自动合并。

```python
def _attach_content_sources(self, organization_id: UUID, experience_id: UUID,
                            content_hash: str, primary_document_id: UUID) -> None:
    documents = self._repository.list_web_documents_by_content_hash(organization_id, content_hash)
    for item in documents:
        self._repository.add_experience_source(
            organization_id, experience_id,
            canonical_url=item.canonical_url, source_url=item.source_url,
            source_platform=item.source_platform,
            keyword=str(item.metadata_json.get("latest_keyword") or "公开网页收集"),
            is_primary=item.id == primary_document_id,
        )
        self._repository.link_web_document_experience(
            organization_id, item.id, experience_id,
            status=(InterviewWebDocumentStatus.IMPORTED
                    if item.id == primary_document_id else InterviewWebDocumentStatus.DUPLICATE),
        )
```

- [ ] **Step 8: 复用 Agent 门槛、自动入库和候选回写**

```python
analysis = self._evidence_analyzer.analyze(
    organization_id, source_url=final_url, title=scraped.title or document.title,
    markdown_content=normalized_markdown,
)
if not self._can_auto_import(analysis, {"evidence": {"is_complete": True}}):
    self._repository.mark_web_document_filtered(organization_id, document.id)
    self._repository.update_collection_candidate(
        organization_id, candidate.id, status=CollectionCandidateStatus.FETCHED,
        extracted_markdown=analysis.normalized_markdown or normalized_markdown,
        content_hash=content_hash,
        metadata_json={"analysis": analysis.as_metadata(), "ledger_status": "filtered"},
    )
    summary["filtered_count"] += 1
    continue

experience = self._library_service.ingest(
    organization_id,
    InterviewExperienceDraft(
        company_name=analysis.company_name or "",
        role_name=analysis.role_name or "",
        interview_date=analysis.interview_date,
        markdown_content=analysis.normalized_markdown or normalized_markdown,
        source_type=InterviewSourceType.PUBLIC_URL,
        source_platform=document.source_platform,
        source_url=final_url,
        summary_text=analysis.summary_text or scraped.title,
        tags=analysis.tags,
        job_name=self._xiaohongshu_job_name(analysis.role_name or "未知岗位", content_hash),
    ),
    trigger_type=IngestionTriggerType.API_SYNC,
)
self._attach_content_sources(
    organization_id, experience.id, content_hash, document.id
)
```

在既有 `ingest_selected_candidate` 成功后，如果候选 metadata 含 `web_document_id`，读取对应账本的 `content_hash` 并调用同一个 `_attach_content_sources`；这样人工确认保存也会一次性登记同正文地址。达到用户请求的 `valid_count` 后停止继续 scrape；搜索/配置错误使任务 `FAILED`，单页错误只更新该账本和候选并继续；所有任务终态都写回八项汇总和 100% 进度。

- [ ] **Step 9: 验证 URL 去重、正文去重、重试和 OCR 不重抓**

Run: `python scripts/verify_public_web_collection.py`

Expected: 输出 `public_web_collection_orchestration_ok`。

Run: `python scripts/verify_interview_collection.py && python scripts/verify_xiaohongshu_import_orchestration.py`

Expected: 两个既有采集链路继续通过。

- [ ] **Step 10: 提交编排任务**

```powershell
git add -- src/career_assistant/interview_library/collection.py scripts/verify_public_web_collection.py
git commit -m "feat: orchestrate public web interview imports"
```

---

### Task 4: FastAPI 配置门禁、任务接口与来源 JSON 契约

**Files:**
- Create: `scripts/verify_public_web_api.py`
- Modify: `src/career_assistant/web/router.py:195-248,700-772,398-420,1825-1910,2179-2194,3425-3503`

**Interfaces:**
- Consumes: Task 1 `load_firecrawl_settings()` 与 `FirecrawlClient`。
- Consumes: Task 3 `create_public_web_import_job()` 与 `run_public_web_import()`。
- Produces: `POST /api/career/interview-library/public-web-imports`，输入 `{keyword, requested_limit}`，成功返回 HTTP 202。
- Produces: `GET /api/career/interview-library/collection-platforms` 中 `public_web.ready` 与 `public_web.unavailable_reason`。
- Produces: 面经详情 `sources: [{source_url, canonical_url, source_platform, is_primary, discovery_keywords, first_seen_at, last_seen_at}]`。

- [ ] **Step 1: 写配置缺失、创建任务和来源输出的失败验证**

```python
with TestClient(app) as client:
    unavailable = client.post(
        "/api/career/interview-library/public-web-imports",
        json={"keyword": "agent开发面经", "requested_limit": 20},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "未配置全网公开信息收集服务，请先设置 FIRECRAWL_API_KEY。"

    response = ready_client.post(
        "/api/career/interview-library/public-web-imports",
        json={"keyword": "agent开发面经", "requested_limit": 20},
    )
    assert response.status_code == 202
    assert response.json()["job"]["platform_key"] == "public_web"
    assert background_calls == [(ORG_ID, UUID(response.json()["job"]["id"]))]

    detail = ready_client.get(f"/api/career/interview-library/experiences/{EXPERIENCE_ID}").json()
    assert detail["sources"][0]["is_primary"] is True
    assert detail["sources"][1]["discovery_keywords"] == ["agent开发面经"]
```

- [ ] **Step 2: 运行 API 验证并确认接口尚不存在**

Run: `python scripts/verify_public_web_api.py`

Expected: FAIL，创建接口返回 404。

- [ ] **Step 3: 在共享服务容器中装配可选 Firecrawl 客户端**

```python
firecrawl_settings = load_firecrawl_settings()
firecrawl_client = FirecrawlClient(firecrawl_settings) if firecrawl_settings is not None else None
interview_collection_service = InterviewCollectionService(
    interview_library_repository,
    interview_library_service,
    model_gateway=model_gateway,
    model_connection_client=model_connection_client,
    temporary_attachment_store=temporary_attachment_store,
    attachment_parser=attachment_parser,
    firecrawl_client=firecrawl_client,
)
```

密钥只在服务初始化时读取；`CareerAssistantServices.close()` 继续只调用 `interview_collection_service.close()`，由采集服务释放 Firecrawl 和图片下载 HTTP Client。

- [ ] **Step 4: 增加请求模型、能力状态和 HTTP 202 后台任务**

```python
@dataclass(frozen=True)
class PlatformCollectionPolicy:
    key: str
    label: str
    can_run_keyword_search: bool
    connector_kind: CollectionConnectorKind
    policy_decision: str
    ready: bool = True
    unavailable_reason: str | None = None


class CreatePublicWebImportRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=120)
    requested_limit: int = Field(default=20, ge=5, le=50)


@router.post("/interview-library/public-web-imports", status_code=status.HTTP_202_ACCEPTED)
def create_public_web_import(
    request_body: CreatePublicWebImportRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, object]:
    actor = get_request_actor()
    services = get_career_services(request)
    if not services.interview_collection_service.public_web_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="未配置全网公开信息收集服务，请先设置 FIRECRAWL_API_KEY。",
        )
    job = services.interview_collection_service.create_public_web_import_job(
        actor.organization_id,
        keyword=request_body.keyword,
        requested_limit=request_body.requested_limit,
    )
    background_tasks.add_task(
        services.interview_collection_service.run_public_web_import,
        actor.organization_id,
        job.id,
    )
    return {"job": _collection_job_payload(job)}
```

`list_platform_policies()` 每次读取 `public_web_ready()` 动态返回 `ready`；未配置时 `unavailable_reason="未配置 FIRECRAWL_API_KEY"`。`_collection_platform_payload` 输出这两个字段。`CollectionOperationError("firecrawl_not_configured", ...)` 映射 503；输入错误映射 422；Firecrawl 401/403 发生在后台时写任务失败，不把第三方原文或 Key 返回前端。

- [ ] **Step 5: 扩展候选和面经详情来源契约**

```python
sources = services.interview_library_repository.list_experience_sources(
    actor.organization_id, experience.id
)
return _interview_experience_payload(experience, sources=sources)


def _interview_source_payload(source) -> dict[str, object]:
    return {
        "source_url": source.source_url,
        "canonical_url": source.canonical_url,
        "source_platform": source.source_platform,
        "is_primary": source.is_primary,
        "discovery_keywords": list(source.discovery_keywords),
        "first_seen_at": source.first_seen_at.isoformat(),
        "last_seen_at": source.last_seen_at.isoformat(),
    }
```

仅详情接口读取来源列表；树列表和 RAG 候选继续只返回主来源，避免扩大高频响应。候选 payload 从 `metadata.web_document_id` 和 `metadata.ledger_status` 暴露账本状态，但不暴露 `normalized_markdown` 的第二份副本、图片 URL 或 Firecrawl 完整响应。

- [ ] **Step 6: 运行 API 与既有接口回归**

Run: `python scripts/verify_public_web_api.py`

Expected: 输出 `public_web_api_contract_ok`。

Run: `python scripts/verify_interview_collection_api.py && python scripts/verify_interview_library_api.py && python scripts/verify_xiaohongshu_import_api.py`

Expected: 全部通过。

- [ ] **Step 7: 提交 API 任务**

```powershell
git add -- src/career_assistant/web/router.py scripts/verify_public_web_api.py
git commit -m "feat: expose public web interview collection API"
```

---

### Task 5: PC 全网信息收集页面与来源别名展示

**Files:**
- Create: `web-ui/src/public-web-collection.js`
- Create: `web-ui/src/public-web-collection.test.js`
- Create: `web-ui/src/interview-library-public-web.test.js`
- Modify: `web-ui/src/components/InterviewLibraryPage.vue:1-130,755-1025,1295-1420,1565-1822,1880-1945`

**Interfaces:**
- Consumes: Task 4 公开网页创建接口、现有任务轮询接口和面经详情 `sources`。
- Produces: `normalizePublicWebLimit(value: unknown) -> number`。
- Produces: `publicWebSummary(metadata: object) -> eight-count object`。
- Produces: `publicWebProgress(metadata: object, status: string) -> {percent, currentIndex, detail}`。
- Produces: PC “全网公开信息收集”表单与来源别名列表。

- [ ] **Step 1: 为纯函数写失败测试**

```javascript
test('全网收集数量固定在 5 到 50，默认 20', () => {
  assert.equal(normalizePublicWebLimit(undefined), 20)
  assert.equal(normalizePublicWebLimit(1), 5)
  assert.equal(normalizePublicWebLimit(99), 50)
})

test('八项汇总缺失字段时返回零', () => {
  assert.deepEqual(publicWebSummary({ summary: { discovered_count: 7, imported_count: 2 } }), {
    discovered: 7, knownUrl: 0, scraped: 0, duplicate: 0,
    valid: 0, imported: 2, filtered: 0, failed: 0,
  })
})

test('公开网页进度对应五个固定阶段', () => {
  assert.equal(publicWebProgress({ phase: 'deduplicate' }, 'running').currentIndex, 3)
  assert.equal(publicWebProgress({ phase: 'completed' }, 'succeeded').percent, 100)
})
```

- [ ] **Step 2: 为 PC 页面入口、接口和来源别名写失败契约测试**

```javascript
test('关键词模式不再依赖浏览器扩展', () => {
  assert.match(component, /全网公开信息收集/)
  assert.match(component, /\/interview-library\/public-web-imports/)
  assert.doesNotMatch(component, /submitKeywordCollection[^]*searchXiaohongshuNotes/)
  for (const stage of ['搜索公开网页', '排除已知地址', '解析新内容', '去重与分析', '写入面经库']) {
    assert.match(component, new RegExp(stage))
  }
})

test('面经详情展示一个主来源和可展开的相同来源', () => {
  assert.match(component, /selectedExperience\.sources/)
  assert.match(component, /其他相同来源/)
  assert.match(component, /source\.is_primary/)
})
```

- [ ] **Step 3: 运行前端测试并确认新模块和页面文案尚不存在**

Run: `node --test web-ui/src/public-web-collection.test.js web-ui/src/interview-library-public-web.test.js`

Expected: FAIL，新模块无法导入或页面仍显示小红书浏览器文案。

- [ ] **Step 4: 实现公开网页纯函数**

```javascript
export function normalizePublicWebLimit(value) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return 20
  return Math.min(50, Math.max(5, parsed))
}

export function publicWebSummary(metadata = {}) {
  const value = metadata?.summary ?? {}
  return {
    discovered: Number(value.discovered_count ?? 0),
    knownUrl: Number(value.known_url_count ?? 0),
    scraped: Number(value.scraped_count ?? 0),
    duplicate: Number(value.duplicate_count ?? 0),
    valid: Number(value.valid_count ?? 0),
    imported: Number(value.imported_count ?? 0),
    filtered: Number(value.filtered_count ?? 0),
    failed: Number(value.failed_count ?? 0),
  }
}

const PHASE_INDEX = { search: 0, ledger: 1, scrape: 2, deduplicate: 3,
  analyze: 3, import: 4, completed: 4, failed: 4 }
```

- [ ] **Step 5: 将 keyword 模式改为后端公开网页任务**

```javascript
async function submitKeywordCollection() {
  if (collectionSubmitting.value) return
  const keyword = collectionDraft.value.keyword.trim()
  if (!keyword) {
    collectionError.value = '请输入搜索关键词。'
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  collectionNotice.value = ''
  try {
    const payload = await requestJson('/api/career/interview-library/public-web-imports', {
      method: 'POST',
      body: JSON.stringify({
        keyword,
        requested_limit: normalizePublicWebLimit(collectionDraft.value.requestedLimit),
      }),
    })
    collectionJob.value = payload.job
    collectionCandidates.value = []
    collectionNotice.value = '任务已启动，系统正在搜索公开网页并排除已处理地址。'
    await refreshCollectionJob(payload.job.id)
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '创建全网信息收集任务失败。'
  } finally {
    collectionSubmitting.value = false
  }
}
```

删除 keyword 模式对 Chrome 扩展能力、localStorage 任务 ID、暂停/继续按钮和浏览器串行控制的依赖；保留“小红书链接导入”和“单篇公开链接”两个独立 Tab。关闭弹窗只停止轮询显示，不取消后端任务。

- [ ] **Step 6: 实现五阶段、八项统计和候选来源显示**

```vue
<span v-for="(stage, index) in ['搜索公开网页', '排除已知地址', '解析新内容', '去重与分析', '写入面经库']"
      :key="stage" :class="{ active: collectionProgress.currentIndex >= index }">
  <i>{{ index + 1 }}</i>{{ stage }}
</span>

<section v-if="collectionJob" class="collection-summary-grid public-web-summary-grid">
  <div><span>搜索发现</span><strong>{{ collectionAnalysisSummary.discovered }}</strong></div>
  <div><span>已知地址</span><strong>{{ collectionAnalysisSummary.knownUrl }}</strong></div>
  <div><span>实际解析</span><strong>{{ collectionAnalysisSummary.scraped }}</strong></div>
  <div><span>重复正文</span><strong>{{ collectionAnalysisSummary.duplicate }}</strong></div>
  <div><span>有效面经</span><strong>{{ collectionAnalysisSummary.valid }}</strong></div>
  <div><span>新写入</span><strong>{{ collectionAnalysisSummary.imported }}</strong></div>
  <div><span>已过滤</span><strong>{{ collectionAnalysisSummary.filtered }}</strong></div>
  <div><span>失败</span><strong>{{ collectionAnalysisSummary.failed }}</strong></div>
</section>
```

候选卡继续用 `target="_blank" rel="noreferrer"` 打开 `source_url`，展示域名来源、标题、摘要、账本状态和简化错误；重复候选显示“正文已存在，来源已登记”，不显示第二份正文编辑器。

- [ ] **Step 7: 在面经详情展示主来源和来源别名**

```vue
<a v-if="primaryExperienceSource" :href="primaryExperienceSource.source_url"
   target="_blank" rel="noreferrer">查看主来源 ↗</a>
<details v-if="alternateExperienceSources.length" class="experience-source-aliases">
  <summary>其他相同来源 {{ alternateExperienceSources.length }} 个</summary>
  <a v-for="source in alternateExperienceSources" :key="source.canonical_url"
     :href="source.source_url" target="_blank" rel="noreferrer">
    {{ source.source_platform }} · {{ source.source_url }}
  </a>
</details>
```

主来源取 `sources.find(item => item.is_primary)`，接口未返回 `sources` 时回退到既有 `selectedExperience.source_url`，保证旧数据和其他入库方式不退化。

- [ ] **Step 8: 运行前端测试和 PC 构建**

Run: `npm --prefix web-ui test`

Expected: 全部 Node 测试通过，包括既有 `interview-library-xiaohongshu-browser.test.js`；若旧测试仍要求关键词模式使用扩展，则只更新该测试为“小红书链接导入仍保留、关键词模式已迁移到公开网页”，不删除小红书单链接回归。

Run: `npm --prefix web-ui run build`

Expected: Vite production build 成功，无未使用导入和模板编译错误。

- [ ] **Step 9: 提交 PC 页面任务**

```powershell
git add -- web-ui/src/public-web-collection.js web-ui/src/public-web-collection.test.js web-ui/src/interview-library-public-web.test.js web-ui/src/components/InterviewLibraryPage.vue web-ui/src/interview-library-xiaohongshu-browser.test.js
git commit -m "feat: add public web collection UI"
```

---

### Task 6: 模块文档、真实落库脚本与重复抓取验收

**Files:**
- Create: `scripts/verify_public_web_live.py`
- Modify: `docs/interview_library_collection_module.md`
- Modify: `docs/interview_library_rag_module.md`
- Modify: `docs/xiaohongshu_interview_import.md`

**Interfaces:**
- Consumes: Task 4 本地 API 和任务详情。
- Produces: 可重复执行的真实验收命令，只有任务终态、数据库来源记录和第二次零重复抓取同时满足才退出 0。

- [ ] **Step 1: 先写真实验收脚本的可执行契约**

```python
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-limit", type=int, choices=(1,))
    args = parser.parse_args()
    base_url = os.getenv("CAREER_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    keyword = os.getenv("PUBLIC_WEB_TEST_KEYWORD", "agent开发面经")
    requested_limit = int(os.getenv("PUBLIC_WEB_TEST_LIMIT", "50"))
    if args.smoke_limit == 1:
        settings = load_firecrawl_settings()
        assert settings is not None, "FIRECRAWL_API_KEY 未配置"
        firecrawl = FirecrawlClient(settings)
        try:
            results = firecrawl.search(keyword, limit=1)
            assert results, "Firecrawl /v2/search 未返回公开网页"
            document = firecrawl.scrape(canonicalize_public_url(results[0].url))
            assert normalize_public_markdown(document.markdown), "Firecrawl /v2/scrape 未返回正文"
        finally:
            firecrawl.close()
        print("public_web_firecrawl_smoke_ok")
        return
    first = create_and_wait(base_url, keyword, requested_limit)
    assert first["job"]["status"] == "succeeded", first
    summary = first["job"]["metadata"]["summary"]
    assert summary["discovered_count"] > 0
    assert summary["scraped_count"] > 0
    assert summary["imported_count"] > 0
    assert_sources_exist(base_url, first["candidates"])

    second = create_and_wait(base_url, keyword, requested_limit)
    second_summary = second["job"]["metadata"]["summary"]
    assert second_summary["known_url_count"] > 0
    first_urls = {item["canonical_url"] for item in first["candidates"]}
    repeated = [item for item in second["candidates"] if item["canonical_url"] in first_urls]
    assert repeated, "第二次搜索没有返回可核对的已知地址"
    assert all(item["metadata"].get("scrape_performed") is False for item in repeated), repeated
    print(json.dumps({"first": summary, "second": second_summary}, ensure_ascii=False, indent=2))
```

脚本每 2 秒轮询，最长 30 分钟；失败时打印任务 ID、终态、八项汇总和候选简化错误，绝不打印环境变量、Authorization Header、完整 Firecrawl 响应或正文。

- [ ] **Step 2: 更新模块文档中的设计目标、调用链、依赖和边界**

```text
PC 信息收集
  -> POST public-web-imports
  -> Firecrawl /v2/search（只发现 URL）
  -> interview_web_documents（规范 URL 去重）
  -> Firecrawl /v2/scrape（只处理新地址）
  -> Markdown/OCR -> SHA-256（正文去重）
  -> InterviewEvidenceAnalyzer -> InterviewLibraryService.ingest -> RAG
  -> interview_experience_sources（主来源 + 别名）
```

`docs/interview_library_collection_module.md` 记录状态机、八项计数、重试时钟和第二次不 scrape；`docs/interview_library_rag_module.md` 记录 RAG 只引用主来源；`docs/xiaohongshu_interview_import.md` 明确小红书单链接入口仍存在，关键词入口已改为全网公开链路，不使用浏览器登录态绕过验证。

- [ ] **Step 3: 运行完整离线回归**

Run:

```powershell
python -m pytest tests/test_public_web_collection.py tests/test_interview_public_web_migration.py tests/test_interview_public_web_repository.py tests/test_interview_library_edit.py -q
python scripts/verify_public_web_collection.py
python scripts/verify_public_web_api.py
python scripts/verify_interview_collection.py
python scripts/verify_interview_collection_api.py
python scripts/verify_interview_library.py
python scripts/verify_interview_library_api.py
python scripts/verify_xiaohongshu_collection.py
python scripts/verify_xiaohongshu_import_orchestration.py
python scripts/verify_xiaohongshu_import_api.py
npm --prefix web-ui test
npm --prefix web-ui run build
```

Expected: 所有命令退出码为 0。

- [ ] **Step 4: 配置本地 Firecrawl Key 并执行 limit=1 烟雾测试**

将真实 Key 仅写入未跟踪的 `.env.career-assistant`：

```dotenv
FIRECRAWL_API_KEY=fc-实际本地密钥
FIRECRAWL_API_URL=https://api.firecrawl.dev
```

先把本地开发数据库升级到本次迁移：

```powershell
python -m alembic upgrade head
```

Expected: 本地 schema 版本为 `20260827_25`；不连接或修改生产数据库。

Run:

```powershell
$env:PUBLIC_WEB_TEST_KEYWORD='agent开发面经'
$env:PUBLIC_WEB_TEST_LIMIT='5'
python scripts/verify_public_web_live.py --smoke-limit 1
```

Expected: 通过应用内 `FirecrawlClient` 实际完成一次 `/v2/search` 和一次 `/v2/scrape`；密钥不出现在日志和数据库。该烟雾测试不写面经，完整落库由下一步验证。

- [ ] **Step 5: 执行用户指定的 50 条真实任务并验证来源**

Run:

```powershell
$env:PUBLIC_WEB_TEST_KEYWORD='agent开发面经'
$env:PUBLIC_WEB_TEST_LIMIT='50'
python scripts/verify_public_web_live.py
```

Expected: 第一轮任务进入 `succeeded`，数据库存在新面经及可点击主来源；正文重复地址出现在来源别名中；第二轮与第一轮相交的规范地址全部计入 `known_url_count`，且对应候选的 `metadata.scrape_performed == false`。第二轮若发现此前未出现的新地址，可以产生新的 `scraped_count`。搜索结果不足或页面被过滤时，报告真实发现/入库数量，不能把目标 50 伪报为已入库 50。

- [ ] **Step 6: 检查数据库中没有原始网页和图片残留**

```sql
SELECT count(*) AS stored_sources
FROM career_assistant.interview_experience_sources
WHERE organization_id = :organization_id;

SELECT status, count(*)
FROM career_assistant.interview_web_documents
WHERE organization_id = :organization_id
GROUP BY status
ORDER BY status;

SELECT count(*) AS invalid_primary_count
FROM career_assistant.interview_experience_sources
WHERE organization_id = :organization_id AND is_primary
GROUP BY experience_id
HAVING count(*) > 1;
```

Expected: 来源数大于零，无任何面经拥有多个主来源；表结构中不存在 `raw_html`、`image_bytes`、`cookie`、`api_key` 字段，临时附件目录没有本任务残留文件。

- [ ] **Step 7: 检查差异并提交文档和验收脚本**

```powershell
git diff --check
git status --short
git add -- scripts/verify_public_web_live.py docs/interview_library_collection_module.md docs/interview_library_rag_module.md docs/xiaohongshu_interview_import.md
git commit -m "docs: verify public web interview collection"
```

只在真实任务进入终态且来源记录可查询后向用户报告完成；不执行生产部署、生产迁移、生产密钥配置或生产容器重建。

## Self-review

- 规格中的 Firecrawl 配置、两阶段调用、URL 安全过滤、规范 URL 去重、正文 SHA-256 去重、永久账本、主来源/别名、OCR 临时文件、Agent 自动入库、八项统计、失败重试、PC 页面、来源展示和真实重复抓取验收分别映射到 Tasks 1–6。
- Task 1 定义的 `FirecrawlSearchResult`、`FirecrawlDocument`、规范化函数与异常类型被 Task 3 原样消费；Task 2 定义的状态枚举和仓储签名在 Task 3/4 中保持一致；前后端八项字段使用固定 snake_case 到 camelCase 映射。
- 候选表继续使用既有状态枚举，细分账本状态只放候选 metadata，避免遗漏旧表 CHECK 迁移。
- 人工编辑只修改面经正文和现有 `source_content_hash`，不会更新或覆盖 `interview_experience_sources` 的主来源；跨批次去重以永久账本为主。
- 计划没有 `/interact`、登录态、Cookie、验证码绕过、定时任务、整站 crawl、语义近似合并、生产部署或跨组织缓存步骤。
- 每个任务都先建立失败测试或验证脚本，再实现最小行为、运行回归并做独立提交。
