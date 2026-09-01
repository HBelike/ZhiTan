"""验证面经公共资料采集 API 的真实 PostgreSQL 契约。

该脚本只创建一个关键词任务，验证其合规状态，并在结束时删除测试数据。
不访问第三方网页，也不产生任何平台抓取行为。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.persistence import CareerDatabase
from src.web.api import create_app


def main() -> None:
    """确认关键词任务的状态和查询接口，最后清理测试任务。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供 PostgreSQL 连接串")

    job_id: UUID | None = None
    database = CareerDatabase(database_url)
    try:
        with TestClient(create_app(PROJECT_ROOT)) as client:
            platforms = client.get("/api/career/interview-library/collection-platforms")
            assert platforms.status_code == 200, platforms.text
            platform_items = {item["key"]: item for item in platforms.json()["items"]}
            assert platform_items["xiaohongshu"]["can_run_keyword_search"] is True
            assert platform_items["xiaohongshu"]["connector_kind"] == "url_import"
            assert platform_items["public_url"]["connector_kind"] == "url_import"

            created = client.post(
                "/api/career/interview-library/collection-jobs",
                json={
                    "platform_key": "xiaohongshu",
                    "keyword": "Agent 开发 面经 API 验证",
                    "requested_limit": 3,
                },
            )
            assert created.status_code == 201, created.text
            payload = created.json()
            job_id = UUID(payload["id"])
            assert payload["status"] in {"queued", "running", "succeeded", "failed"}
            assert payload["connector_kind"] == "url_import"
            assert payload["metadata"]["source_kind"] == "xiaohongshu_keyword_search"
            assert "search_result" in payload["metadata"]["source_url"]

            detail = client.get(f"/api/career/interview-library/collection-jobs/{job_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["id"] == str(job_id)
            assert detail.json()["candidates"] == []

        print("interview_collection_api_real_database_ok")
    finally:
        try:
            if job_id is not None:
                with database.transaction() as connection:
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_collection_candidates "
                            "WHERE collection_job_id = :job_id"
                        ),
                        {"job_id": job_id},
                    )
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_collection_jobs "
                            "WHERE id = :job_id"
                        ),
                        {"job_id": job_id},
                    )
        finally:
            database.close()


if __name__ == "__main__":
    main()
