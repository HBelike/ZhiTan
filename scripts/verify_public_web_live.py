"""真实验证 Firecrawl 搜索、公开网页解析、入库、来源追踪与二次去重。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID

from dotenv import load_dotenv
import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.interview_library.public_web import (
    FirecrawlClient,
    canonicalize_public_url,
    load_firecrawl_settings,
    normalize_public_markdown,
)


TERMINAL_STATUSES = {"succeeded", "completed", "failed", "cancelled"}


def smoke(keyword: str) -> None:
    """实际调用一次 search 和 scrape，但不写数据库。"""

    settings = load_firecrawl_settings()
    if settings is None:
        raise RuntimeError("FIRECRAWL_API_KEY 未配置")
    firecrawl = FirecrawlClient(settings)
    try:
        results = firecrawl.search(keyword, limit=1)
        if not results:
            raise AssertionError("Firecrawl /v2/search 未返回公开网页")
        document = firecrawl.scrape(canonicalize_public_url(results[0].url))
        if not normalize_public_markdown(document.markdown):
            raise AssertionError("Firecrawl /v2/scrape 未返回正文")
    finally:
        firecrawl.close()
    print("public_web_firecrawl_smoke_ok")


def create_and_wait(
    client: httpx.Client,
    base_url: str,
    keyword: str,
    requested_limit: int,
    *,
    timeout_seconds: int = 1800,
) -> dict[str, object]:
    """创建真实任务并等待终态，只输出任务级元数据。"""

    response = client.post(
        f"{base_url}/api/career/interview-library/public-web-imports",
        json={"keyword": keyword, "requested_limit": requested_limit},
    )
    response.raise_for_status()
    job = response.json()["job"]
    job_id = UUID(job["id"])
    deadline = time.monotonic() + timeout_seconds
    last_progress = None
    while time.monotonic() < deadline:
        detail_response = client.get(
            f"{base_url}/api/career/interview-library/collection-jobs/{job_id}",
        )
        detail_response.raise_for_status()
        detail = detail_response.json()
        progress = detail.get("metadata", {}).get("progress_percent")
        if progress != last_progress:
            print(f"job={job_id} progress={progress}% status={detail.get('status')}")
            last_progress = progress
        if str(detail.get("status", "")).lower() in TERMINAL_STATUSES:
            return detail
        time.sleep(2)
    raise TimeoutError(f"公开网页任务 {job_id} 在 {timeout_seconds} 秒内未结束")


def imported_experience_ids(detail: dict[str, object]) -> set[UUID]:
    """从候选稳定元数据读取本任务关联的面经 ID。"""

    result: set[UUID] = set()
    for candidate in detail.get("candidates", []):
        metadata = candidate.get("metadata") or {}
        value = metadata.get("imported_experience_id")
        if isinstance(value, str):
            result.add(UUID(value))
    return result


def assert_sources(
    client: httpx.Client,
    base_url: str,
    experience_ids: set[UUID],
) -> tuple[int, int]:
    """确认入库面经具有可点击来源，并统计主来源和别名。"""

    primary_count = 0
    alias_count = 0
    for experience_id in experience_ids:
        response = client.get(
            f"{base_url}/api/career/interview-library/experiences/{experience_id}",
        )
        response.raise_for_status()
        sources = response.json().get("sources") or []
        if not sources:
            raise AssertionError(f"面经 {experience_id} 缺少来源记录")
        if sum(bool(item.get("is_primary")) for item in sources) != 1:
            raise AssertionError(f"面经 {experience_id} 的主来源数量不是 1")
        primary_count += 1
        alias_count += sum(not bool(item.get("is_primary")) for item in sources)
    return primary_count, alias_count


def verify_live(base_url: str, keyword: str, requested_limit: int) -> None:
    """执行两轮真实任务并核对落库来源与第二轮 URL 去重。"""

    # 本地回环 API 不能继承宿主 HTTP_PROXY，否则 127.0.0.1 会被错误送入代理并返回 502。
    # FirecrawlClient 仍独立保留正常公网网络配置。
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        first = create_and_wait(client, base_url, keyword, requested_limit)
        first_summary = first.get("metadata", {}).get("summary", {})
        if first.get("status") != "succeeded":
            raise AssertionError({
                "job_id": first.get("id"),
                "status": first.get("status"),
                "summary": first_summary,
                "error_code": first.get("error_code"),
                "error_message": first.get("error_message"),
            })
        if int(first_summary.get("discovered_count", 0)) <= 0:
            raise AssertionError("第一轮没有发现公开网页")
        experience_ids = imported_experience_ids(first)
        if not experience_ids:
            raise AssertionError({
                "job_id": first.get("id"),
                "summary": first_summary,
                "message": "第一轮没有完成真实面经入库",
            })
        primary_count, alias_count = assert_sources(
            client,
            base_url,
            experience_ids,
        )

        second = create_and_wait(client, base_url, keyword, requested_limit)
        second_summary = second.get("metadata", {}).get("summary", {})
        if second.get("status") != "succeeded":
            raise AssertionError({
                "job_id": second.get("id"),
                "status": second.get("status"),
                "summary": second_summary,
            })
        first_urls = {
            item.get("canonical_url")
            for item in first.get("candidates", [])
            if item.get("canonical_url")
        }
        repeated = [
            item
            for item in second.get("candidates", [])
            if item.get("canonical_url") in first_urls
        ]
        if not repeated:
            raise AssertionError("第二轮没有返回可核对的已知地址")
        if any(item.get("metadata", {}).get("scrape_performed") is not False for item in repeated):
            raise AssertionError("第二轮存在重复地址被再次 scrape")

    print(json.dumps({
        "keyword": keyword,
        "requested_limit": requested_limit,
        "first_job_id": first.get("id"),
        "first_summary": first_summary,
        "verified_experiences": primary_count,
        "verified_source_aliases": alias_count,
        "second_job_id": second.get("id"),
        "second_summary": second_summary,
        "repeated_urls_without_scrape": len(repeated),
    }, ensure_ascii=False, indent=2))
    print("public_web_live_import_and_dedup_ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-limit", type=int, choices=(1,))
    args = parser.parse_args()
    keyword = os.getenv("PUBLIC_WEB_TEST_KEYWORD", "agent开发面经").strip()
    requested_limit = int(os.getenv("PUBLIC_WEB_TEST_LIMIT", "10"))
    if args.smoke_limit == 1:
        smoke(keyword)
        return
    base_url = os.getenv("CAREER_API_BASE_URL", "http://127.0.0.1:18080").rstrip("/")
    verify_live(base_url, keyword, requested_limit)


if __name__ == "__main__":
    main()
