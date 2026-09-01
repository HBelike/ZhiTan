"""验证图片简历的平台自动理解、文本模型路由与临时清理。"""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.persistence import CareerConversationRepository, CareerDatabase
from src.career_assistant.persistence.conversation_repository import DEFAULT_ACTOR_ID
from src.web.api import create_app


def _build_png() -> bytes:
    """生成不含个人资料的极小 PNG，用来覆盖图片解析路径。"""

    image = Image.new("RGB", (20, 20), color=(120, 160, 80))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def main() -> None:
    """上传图片，并确认平台 Qwen 复核不再要求用户模型具备 Vision 能力。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("未配置 CAREER_DATABASE_URL，无法执行真实数据库验证")

    conversation_id: str | None = None
    temporary_root = PROJECT_ROOT / "data" / "career-temporary-attachments"
    existing_temporary_directories = {
        path.resolve()
        for path in temporary_root.glob("career-turn-*")
        if path.is_dir()
    }
    try:
        with TestClient(create_app(PROJECT_ROOT)) as client:
            conversation_response = client.post(
                "/api/career/conversations",
                json={"title": "图片简历验证会话"},
            )
            conversation_id = conversation_response.json()["id"]
            response = client.post(
                f"/api/career/conversations/{conversation_id}/intake-with-materials",
                data={"selection_mode": "free_quota_first"},
                files={"resume_file": ("resume.png", _build_png(), "image/png")},
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert "resume_image" in payload["turn"]["input_kind_codes"]
            assert "简历图片（云端图片理解已完成）" in payload["message"]["content"]
            processing_items = payload["attachment_processing"]["items"]
            assert processing_items[0]["processing_route"] == "cloud_vision"
            assert payload["turn"]["status"] == "succeeded"

        remaining_new_directories = {
            path.resolve()
            for path in temporary_root.glob("career-turn-*")
            if path.is_dir() and path.resolve() not in existing_temporary_directories
        }
        assert not remaining_new_directories, (
            "本轮图片验证遗留了临时附件目录："
            f"{sorted(str(path) for path in remaining_new_directories)}"
        )
        print("career_image_intake_real_database_ok")
    finally:
        database = CareerDatabase(database_url)
        try:
            if conversation_id is not None:
                deleted = CareerConversationRepository(database).delete_conversation_permanently(
                    DEFAULT_ACTOR_ID,
                    UUID(conversation_id),
                )
                if not deleted:
                    raise RuntimeError("图片验证会话清理失败，请人工检查数据库")
        finally:
            database.close()


if __name__ == "__main__":
    main()
