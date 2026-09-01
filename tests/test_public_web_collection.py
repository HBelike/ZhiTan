"""全网公开面经 Firecrawl 适配器与确定性清洗测试。"""

from __future__ import annotations

import json
import socket

import httpx
import pytest
from pydantic import ValidationError

from src.career_assistant.interview_library.collection import InterviewEvidenceAnalysis
from src.career_assistant.interview_library.public_web_collection import (
    PublicWebCollectionCoordinator,
)

from src.career_assistant.interview_library.public_web import (
    FirecrawlClient,
    FirecrawlRequestError,
    FirecrawlSettings,
    PublicWebUrlError,
    canonicalize_public_url,
    hash_public_markdown,
    infer_public_platform,
    load_firecrawl_settings,
    normalize_public_markdown,
    validate_public_https_target,
)
from src.career_assistant.web.router import CreatePublicWebImportRequest


def test_canonicalize_public_url_removes_tracking_and_preserves_identity_query() -> None:
    assert canonicalize_public_url(
        "HTTPS://Example.COM:443/a//b/?utm_source=x&b=2&a=1&source=feed#card",
    ) == "https://example.com/a/b?a=1&b=2"


def test_canonicalize_public_url_normalizes_idna_and_root_path() -> None:
    assert canonicalize_public_url("https://例子.测试/") == "https://xn--fsqu00a.xn--0zwm56d"


@pytest.mark.parametrize(
    "value",
    (
        "http://example.com/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://example.com:8443/a",
        "https://user:secret@example.com/a",
        "https://localhost/a",
    ),
)
def test_canonicalize_public_url_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(PublicWebUrlError):
        canonicalize_public_url(value)


def test_validate_public_https_target_rejects_private_dns_result() -> None:
    def resolver(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]

    with pytest.raises(PublicWebUrlError, match="内网"):
        validate_public_https_target(
            "https://internal.example/interview",
            resolver=resolver,
        )


def test_validate_public_https_target_accepts_public_dns_result() -> None:
    def resolver(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    validate_public_https_target("https://example.com/interview", resolver=resolver)


def test_normalized_markdown_removes_collection_wrappers_and_has_stable_hash() -> None:
    left = "# 面经\r\n\r\n-  Redis   缓存\n来源：https://a.example\n![配图](https://img/a.png)"
    right = "# 面经\n\n* Redis 缓存\n抓取时间：2026-08-27"

    assert normalize_public_markdown(left) == "# 面经\n\n- Redis 缓存"
    assert hash_public_markdown(left) == hash_public_markdown(right)


def test_load_firecrawl_settings_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert load_firecrawl_settings() is None

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("FIRECRAWL_API_URL", "https://firecrawl.example/")
    settings = load_firecrawl_settings()
    assert settings == FirecrawlSettings(
        api_key="fc-test",
        api_url="https://firecrawl.example",
        timeout_seconds=60.0,
    )


def test_firecrawl_search_discovers_urls_without_scraping() -> None:
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
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "Agent 面经",
                            "description": "一面问题",
                            "url": "https://example.com/a",
                            "category": "web",
                        },
                    ],
                },
            },
        )

    client = FirecrawlClient(
        FirecrawlSettings("fc-test"),
        transport=httpx.MockTransport(handler),
    )
    try:
        results = client.search("agent开发面经", limit=6)
    finally:
        client.close()

    assert len(results) == 1
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "一面问题"
    assert results[0].metadata == {"category": "web"}


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
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "# 一面\n\n1. 解释 Agent memory",
                    "images": [
                        "https://cdn.example.com/1.png",
                        "data:image/png;base64,ignored",
                    ],
                    "metadata": {
                        "url": "https://example.com/final",
                        "title": "面经",
                        "statusCode": 200,
                        "cacheState": "hit",
                        "cachedAt": "2026-08-27T00:00:00Z",
                    },
                },
            },
        )

    client = FirecrawlClient(
        FirecrawlSettings("fc-test"),
        transport=httpx.MockTransport(handler),
    )
    try:
        document = client.scrape("https://example.com/a")
    finally:
        client.close()

    assert document.final_url == "https://example.com/final"
    assert document.image_urls == ("https://cdn.example.com/1.png",)
    assert document.metadata["cacheState"] == "hit"


@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    (
        (401, "firecrawl_auth_failed", False),
        (429, "firecrawl_rate_limited", True),
        (503, "firecrawl_unavailable", True),
    ),
)
def test_firecrawl_error_classification(
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status_code, json={"success": False, "error": "blocked"}),
    )
    client = FirecrawlClient(FirecrawlSettings("fc-test"), transport=transport)
    try:
        with pytest.raises(FirecrawlRequestError) as raised:
            client.search("agent开发面经", limit=1)
    finally:
        client.close()

    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert "fc-test" not in raised.value.message


def test_infer_public_platform_uses_known_label_or_hostname() -> None:
    assert infer_public_platform("https://www.nowcoder.com/discuss/1") == "牛客"
    assert infer_public_platform("https://interview.example.com/a") == "interview.example.com"


def test_pending_model_uses_strict_deterministic_interview_evidence() -> None:
    pending = InterviewEvidenceAnalysis(
        is_valid_interview=None,
        confidence=1.0,
        company_name="字节跳动",
        role_name="AI Agent",
        interview_date=None,
        interview_stage=None,
        tags=("Agent",),
        summary_text="自动识别到 3 个面试问题。",
        questions=(),
        normalized_markdown=None,
        rejection_reason=None,
        analyzer_status="pending_model",
        analyzer_message="模型未配置",
        model_label=None,
    )
    result = PublicWebCollectionCoordinator._apply_deterministic_fallback(
        pending,
        title="字节 AI Agent 二面面经",
        markdown=(
            "1. Agent 如何管理长期记忆？\n"
            "2. 如何设计工具调用失败后的重试？\n"
            "3. RAG 召回质量如何评估？"
        ),
    )

    assert result.is_valid_interview is True
    assert result.analyzer_status == "deterministic_fallback"
    assert len(result.questions) == 3


def test_deterministic_fallback_rejects_weak_or_promotional_content() -> None:
    pending = InterviewEvidenceAnalysis(
        is_valid_interview=None,
        confidence=1.0,
        company_name="字节跳动",
        role_name="AI Agent",
        interview_date=None,
        interview_stage=None,
        tags=(),
        summary_text=None,
        questions=(),
        normalized_markdown=None,
        rejection_reason=None,
        analyzer_status="pending_model",
        analyzer_message="模型未配置",
        model_label=None,
    )
    result = PublicWebCollectionCoordinator._apply_deterministic_fallback(
        pending,
        title="AI Agent 学习资料",
        markdown="限时领取课程。\n1. 添加客服领取资料。",
    )

    assert result.is_valid_interview is None


def test_public_web_request_rejects_more_than_ten_pages() -> None:
    with pytest.raises(ValidationError):
        CreatePublicWebImportRequest(keyword="Agent 面经", requested_limit=11)


def test_collection_caps_search_and_scrape_candidates_at_requested_limit() -> None:
    from scripts.verify_public_web_collection import (
        FakeFirecrawlClient,
        FakeLibraryService,
        FakeRepository,
        ValidAnalyzer,
        document,
        result,
        ORG_ID,
        ACTOR_ID,
    )

    urls = [f"https://example{i}.com/agent" for i in range(12)]

    class RecordingFirecrawl(FakeFirecrawlClient):
        search_limit = None

        def search(self, keyword, *, limit):  # type: ignore[no-untyped-def]
            self.search_limit = limit
            return super().search(keyword, limit=limit)

    repository = FakeRepository()
    library = FakeLibraryService()
    firecrawl = RecordingFirecrawl(
        [result(url) for url in urls],
        {url: document(url) for url in urls},
    )
    coordinator = PublicWebCollectionCoordinator(
        repository,
        library,
        firecrawl,
        ValidAnalyzer(),
        url_validator=lambda _url: None,
    )
    job = coordinator.create_job(
        ORG_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="Agent 面经",
        requested_limit=10,
    )

    coordinator.run(ORG_ID, job.id)

    assert firecrawl.search_limit == 10
    assert len(firecrawl.scrape_calls) == 10
    # 十个地址正文相同，永久账本只入库一份主面经，其余登记为来源别名。
    assert len(library.ingested) == 1
    assert all(
        item.ownership["created_by_actor_id"] == ACTOR_ID
        for item in library.ingested
    )


def test_failed_page_does_not_roll_back_later_successful_import() -> None:
    from scripts.verify_public_web_collection import (
        FakeFirecrawlClient,
        FakeLibraryService,
        FakeRepository,
        ValidAnalyzer,
        document,
        result,
        ORG_ID,
        ACTOR_ID,
    )

    failed_url = "https://failed.example/agent"
    successful_url = "https://successful.example/agent"

    class PartiallyFailingFirecrawl(FakeFirecrawlClient):
        def scrape(self, source_url):  # type: ignore[no-untyped-def]
            self.scrape_calls.append(source_url)
            if source_url == failed_url:
                raise FirecrawlRequestError(
                    "firecrawl_timeout",
                    "公开网页服务响应超时，请稍后重试。",
                    retryable=True,
                )
            return self.documents[source_url]

    repository = FakeRepository()
    library = FakeLibraryService()
    firecrawl = PartiallyFailingFirecrawl(
        [result(failed_url), result(successful_url)],
        {successful_url: document(successful_url)},
    )
    coordinator = PublicWebCollectionCoordinator(
        repository,
        library,
        firecrawl,
        ValidAnalyzer(),
        url_validator=lambda _url: None,
    )
    job = coordinator.create_job(
        ORG_ID,
        created_by_actor_id=ACTOR_ID,
        keyword="Agent 面经",
        requested_limit=5,
    )

    coordinator.run(ORG_ID, job.id)

    summary = repository.jobs[job.id].metadata_json["summary"]
    assert summary["failed_count"] == 1
    assert summary["imported_count"] == 1
    assert len(library.ingested) == 1
