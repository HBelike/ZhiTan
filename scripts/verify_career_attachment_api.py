"""对临时简历上传链路执行真实 PostgreSQL 验证，并清理验证会话。"""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env.career-assistant", override=False)

from src.career_assistant.persistence import CareerConversationRepository, CareerDatabase
from src.career_assistant.persistence.conversation_repository import DEFAULT_ACTOR_ID
from src.web.api import create_app


def _build_scanned_pdf() -> bytes:
    """构造不含真实个人信息的扫描型简历样本，用于真实 OCR 链路验证。"""

    image = Image.new("RGB", (1800, 1200), color="white")
    drawer = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size=58)
    for index, line in enumerate(
        (
            "WORK EXPERIENCE",
            "BACKEND ENGINEER",
            "PROJECTS",
            "AGENT PLATFORM WITH FASTAPI",
            "SKILLS",
            "PYTHON POSTGRESQL DOCKER",
        ),
    ):
        drawer.text((120, 100 + (index * 150)), line, fill="black", font=font)
    output = BytesIO()
    image.save(output, "PDF", resolution=150.0)
    return output.getvalue()


def main() -> None:
    """验证上传、脱敏摘要、Graph 步骤与临时文件清理。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("未配置 CAREER_DATABASE_URL，无法执行真实数据库验证")

    conversation_id: str | None = None
    temporary_root = PROJECT_ROOT / "data" / "career-temporary-attachments"
    # 本地浏览器可能正有另一轮真实附件任务在运行。自检只验证自己创建的目录是否
    # 被清理，绝不把其他进程或用户正在处理的临时文件当成失败条件。
    directories_before_test = set(temporary_root.glob("career-turn-*"))
    try:
        with TestClient(create_app(PROJECT_ROOT)) as client:
            conversation_response = client.post(
                "/api/career/conversations",
                json={"title": "附件解析验证会话"},
            )
            assert conversation_response.status_code == 201
            conversation_id = conversation_response.json()["id"]

            response = client.post(
                f"/api/career/conversations/{conversation_id}/intake-with-materials",
                data={
                    "text": "请分析这份简历，联系方式为 13800138000。",
                    "selection_mode": "free_quota_first",
                },
                files={
                    "resume_file": (
                        "verification-resume.pdf",
                        _build_scanned_pdf(),
                        "application/pdf",
                    ),
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            # 当前个人部署关闭文本脱敏，消息会按原文保存，并由 is_redacted
            # 字段如实标记；原始附件和完整解析文本仍不会入库。
            assert "13800138000" in payload["message"]["content"]
            assert payload["message"]["is_redacted"] is False
            assert "简历 PDF（1 页）" in payload["message"]["content"]
            assert payload["turn"]["status"] == "succeeded"
            assert payload["assistant_message"]["role"] == "assistant"
            assert payload["assistant_message"]["content"].strip()
            processing = payload["attachment_processing"]
            assert processing["temporary_only"] is True
            assert processing["count"] == 1
            assert processing["text_characters"] > 0
            assert processing["resume_outline_characters"] > 0
            assert processing["pdf_without_extractable_text_count"] == 0
            assert processing["notices"] == []
            assert processing["items"] == [
                {
                    "kind": "resume_pdf",
                    "page_count": 1,
                    "processing_route": "structured_document",
                    "parser_name": "docling-serve",
                    "parser_status": "success",
                    "native_text_quality_score": 0.0,
                    "issue_codes": ["no_text_layer"],
                },
            ]
            assert processing["cleaned_after_turn"] is True
            assert payload["completed_steps"] == [
                "validate_input",
                "build_context",
                "parse_material",
                "extract_job_description",
                "redact_sensitive_data",
                "persist_history",
                "cleanup_temporary_files",
            ]

        remaining_test_directories = (
            set(temporary_root.glob("career-turn-*")) - directories_before_test
        )
        assert not remaining_test_directories
        print("career_attachment_api_real_database_ok")
    finally:
        database = CareerDatabase(database_url)
        try:
            if conversation_id is not None:
                repository = CareerConversationRepository(database)
                deleted = repository.delete_conversation_permanently(
                    DEFAULT_ACTOR_ID,
                    UUID(conversation_id),
                )
                if not deleted:
                    raise RuntimeError("附件验证会话清理失败，请人工检查数据库")
        finally:
            database.close()


if __name__ == "__main__":
    main()
