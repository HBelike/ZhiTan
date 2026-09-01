"""验证面经库 FastAPI 接口的创建、树查询、编辑与 @ 候选检索闭环。"""

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
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ORGANIZATION_ID,
)
from src.web.api import create_app


def main() -> None:
    """调用公开 API，并删除本验证过程生成的面经、任务与公司节点。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    experience_id: UUID | None = None
    company_id: UUID | None = None
    database = CareerDatabase(database_url)
    try:
        with TestClient(create_app(PROJECT_ROOT)) as client:
            created_response = client.post(
                "/api/career/interview-library/experiences",
                json={
                    "company_name": "面经 API 验证公司",
                    "role_name": "Java 开发工程师",
                    "interview_date": "2026-08-09",
                    "markdown_content": "# 一面\n\n讨论数据库事务和限流设计。",
                    "source_type": "manual_text",
                    "tags": ["Java", "系统设计"],
                },
            )
            assert created_response.status_code == 201, created_response.text
            created = created_response.json()
            experience_id = UUID(created["id"])
            company_id = UUID(created["company_id"])
            assert created["status"] == "indexed"

            repeated_response = client.post(
                "/api/career/interview-library/experiences",
                json={
                    "company_name": "面经 API 验证公司",
                    "role_name": "Java 开发工程师",
                    "interview_date": "2026-08-09",
                    "markdown_content": "# 同一份面经再次保存\n\n验证数据库事务场景下的重复提交会更新正文并重建索引。",
                    "source_type": "manual_text",
                    "tags": ["Java", "幂等保存"],
                },
            )
            assert repeated_response.status_code == 201, repeated_response.text
            repeated = repeated_response.json()
            assert repeated["id"] == created["id"]
            assert repeated["markdown_content"].startswith("# 同一份面经再次保存")

            tree_response = client.get(
                "/api/career/interview-library/tree",
                params={"query": "API 验证"},
            )
            assert tree_response.status_code == 200
            assert tree_response.json()["items"][0]["children"][0]["id"] == created["id"]

            mention_response = client.get(
                "/api/career/interview-library/mentions",
                params={"query": "Java"},
            )
            assert mention_response.status_code == 200
            assert mention_response.json()["items"][0]["id"] == created["id"]

            retrieval_response = client.get(
                "/api/career/interview-library/search",
                params={"query": "数据库事务", "limit": 3},
            )
            assert retrieval_response.status_code == 200, retrieval_response.text
            retrieval_payload = retrieval_response.json()
            assert retrieval_payload["retrieval_mode"] == "lexical"
            assert retrieval_payload["items"]
            assert retrieval_payload["items"][0]["experience_id"] == created["id"]
            assert retrieval_payload["items"][0]["citation"]

            updated_response = client.put(
                f"/api/career/interview-library/experiences/{created['id']}",
                json={
                    "markdown_content": "# 更新版\n\n补充 RAG 召回与重排追问。",
                    "summary_text": "面经 API 验证摘要",
                    "tags": ["RAG"],
                },
            )
            assert updated_response.status_code == 200, updated_response.text
            assert updated_response.json()["markdown_content"].startswith("# 更新版")

        print("interview_library_api_real_database_ok")
    finally:
        try:
            with database.transaction() as connection:
                if experience_id is not None:
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_ingestion_jobs "
                            "WHERE experience_id = :experience_id",
                        ),
                        {"experience_id": experience_id},
                    )
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_experiences "
                            "WHERE id = :experience_id",
                        ),
                        {"experience_id": experience_id},
                    )
                if company_id is not None:
                    connection.execute(
                        text(
                            "DELETE FROM career_assistant.interview_companies "
                            "WHERE id = :company_id",
                        ),
                        {"company_id": company_id},
                    )
        finally:
            database.close()


if __name__ == "__main__":
    main()
