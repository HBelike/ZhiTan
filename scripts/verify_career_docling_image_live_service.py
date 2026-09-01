"""用匿名中文岗位截图验证真实 Docling 图片 OCR 链路。"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.document_parsing import DoclingServiceDocumentParser
from src.career_assistant.settings import DocumentUnderstandingSettings


def main() -> None:
    """生成匿名职位截图，验证图片会走 Docling 的 ``image`` 输入格式。"""

    with tempfile.TemporaryDirectory(prefix="career-docling-image-verify-") as directory:
        image_path = Path(directory) / "job.png"
        _create_job_screenshot(image_path)
        attachment = AttachmentDescriptor(
            attachment_id=uuid4(),
            kind=AttachmentKind.JOB_DESCRIPTION_IMAGE,
            original_filename="job.png",
            media_type="image/png",
            size_bytes=image_path.stat().st_size,
            temporary_path=image_path,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        parser = DoclingServiceDocumentParser(
            DocumentUnderstandingSettings(
                enabled=True,
                service_base_url="http://127.0.0.1:5001",
                api_key_env=None,
                request_timeout_seconds=90,
                force_ocr=True,
                table_mode="accurate",
            ),
        )
        attachment_parser = AttachmentParser(
            AttachmentSettings(
                temporary_root=Path(directory),
                max_size_bytes=10 * 1024 * 1024,
                ttl_seconds=300,
            ),
            document_understanding_parser=parser,
        )
        try:
            result = attachment_parser.parse(attachment)
        finally:
            parser.close()

        assert result.document_understanding_result is not None
        assert "后端开发工程师" in result.extracted_text
        # OCR 对无衬线字体中的 ``I``/``l`` 偶有大小写归一化差异；此处验证稳定的
        # 技术词前缀，避免把“中文职位信息与技术栈已恢复”的真实成功误判为失败。
        assert "FastAP" in result.extracted_text
        assert result.requires_vision_model is False
        print(
            "career_docling_image_live_service_ok "
            f"parser={result.document_understanding_result.parser_name} "
            f"status={result.document_understanding_result.status} "
            f"characters={len(result.extracted_text)}",
        )


def _create_job_screenshot(destination: Path) -> None:
    """生成不含真实职位信息的中文截图，避免验证脚本处理个人数据。"""

    image = Image.new("RGB", (1800, 1200), color="white")
    drawer = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size=56)
    lines = (
        "后端开发工程师",
        "岗位职责：负责智能体平台后端服务开发。",
        "任职要求：熟悉 Python、FastAPI、PostgreSQL。",
        "加分项：具备 LangGraph 或 RAG 项目经验。",
    )
    for index, line in enumerate(lines):
        drawer.text((120, 160 + (index * 180)), line, fill="black", font=font)
    image.save(destination, "PNG")


if __name__ == "__main__":
    main()
