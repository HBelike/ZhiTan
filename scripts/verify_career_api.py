"""对求职助手独立 API Router 做真实 PostgreSQL 验证。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from fastapi.testclient import TestClient

from src.career_assistant.persistence import (
    CareerConversationRepository,
    CareerDatabase,
    CareerModelProfileRepository,
)
from src.career_assistant.persistence.conversation_repository import (
    DEFAULT_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
)
from src.web.api import create_app


def main() -> None:
    """验证会话、脱敏输入、模型档案与路由预览接口，并清理验证数据。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    conversation_id: str | None = None
    profile_id: str | None = None
    try:
        with TestClient(create_app(PROJECT_ROOT)) as client:
            health_response = client.get("/api/health")
            assert health_response.status_code == 200

            invalid_connection_response = client.post(
                "/api/career/model-connections/test",
                json={
                    "display_name": "错误地址验证",
                    "provider_key": "custom",
                    "model_id": "custom-model",
                    "api_key": "not-a-real-key",
                    "capabilities": ["text"],
                    "cost_tier": "free_quota",
                    "priority": 100,
                    "api_base_url": "http://insecure-provider.example/v1",
                    "provider_website_url": "https://provider.example",
                },
            )
            assert invalid_connection_response.status_code == 422
            assert "HTTPS" in invalid_connection_response.json()["detail"]

            conversation_response = client.post(
                "/api/career/conversations",
                json={"title": "API 求职会话验证"},
            )
            assert conversation_response.status_code == 201
            conversation_id = conversation_response.json()["id"]

            intake_response = client.post(
                f"/api/career/conversations/{conversation_id}/intake",
                json={"text": "请联系 13800138000 或 api-test@example.com。"},
            )
            assert intake_response.status_code == 202
            assert intake_response.json()["message"]["content"]
            submitted_turn = intake_response.json()["turn"]

            conversation_detail_response = client.get(
                f"/api/career/conversations/{conversation_id}",
            )
            assert conversation_detail_response.status_code == 200
            assert conversation_detail_response.json()["last_model_selection"] == {
                "mode": "free_quota_first",
                "profile_id": None,
            }
            latest_turn = conversation_detail_response.json()["latest_turn"]
            assert latest_turn is not None
            assert latest_turn["conversation_id"] == conversation_id
            assert latest_turn["id"] == submitted_turn["id"]
            assert latest_turn["status"] == submitted_turn["status"]

            profile_response = client.put(
                "/api/career/model-profiles/api-verification-free",
                json={
                    "display_name": "API 验证免费模型",
                    "provider_key": "groq",
                    "model_id": "api-verification-model",
                    "capabilities": ["text"],
                    "cost_tier": "free_quota",
                    "priority": 1,
                },
            )
            assert profile_response.status_code == 200
            profile_id = profile_response.json()["profile"]["id"]

            resolution_response = client.post(
                "/api/career/model-resolution",
                json={
                    "selection_mode": "specific_profile",
                    "model_profile_id": profile_id,
                },
            )
            assert resolution_response.status_code == 200
            assert resolution_response.json()["profile"]["id"] == profile_id
            assert resolution_response.json()["readiness"] == "credential_required"

        print("career_api_real_database_ok")
    finally:
        database = CareerDatabase(database_url)
        try:
            if conversation_id is not None:
                conversation_repository = CareerConversationRepository(database)
                deleted = conversation_repository.delete_conversation_permanently(
                    DEFAULT_ACTOR_ID,
                    UUID(conversation_id),
                )
                if not deleted:
                    raise RuntimeError("API 验证会话清理失败，请人工检查数据库")
            if profile_id is not None:
                profile_repository = CareerModelProfileRepository(database)
                deleted = profile_repository.delete_profile_permanently(
                    DEFAULT_ORGANIZATION_ID,
                    UUID(profile_id),
                )
                if not deleted:
                    raise RuntimeError("API 验证档案清理失败，请人工检查数据库")
        finally:
            database.close()


if __name__ == "__main__":
    main()
