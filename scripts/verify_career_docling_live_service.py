"""以匿名、临时生成的扫描 PDF 验证本机 Docling 服务。"""

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
from src.career_assistant.document_parsing import DoclingServiceDocumentParser
from src.career_assistant.settings import DocumentUnderstandingSettings


def main() -> None:
    """生成不含个人信息的扫描型 PDF，并确认 OCR 结果能通过真实适配器返回。"""

    with tempfile.TemporaryDirectory(prefix="career-docling-verify-") as directory:
        pdf_path = Path(directory) / "sample.pdf"
        _create_scanned_test_pdf(pdf_path)
        attachment = AttachmentDescriptor(
            attachment_id=uuid4(),
            kind=AttachmentKind.RESUME_PDF,
            original_filename="sample.pdf",
            media_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
            temporary_path=pdf_path,
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
        try:
            result = parser.parse_pdf(attachment)
        finally:
            parser.close()

        assert "工作经历" in result.analysis_text
        assert "项目经历" in result.analysis_text
        assert result.status in {"success", "partial_success"}
        print(
            "career_docling_live_service_ok "
            f"parser={result.parser_name} status={result.status} "
            f"characters={len(result.analysis_text)}",
        )


def _create_scanned_test_pdf(destination: Path) -> None:
    """生成图像型 PDF，确保验证实际经过 OCR 而不是 PDF 内嵌文本层。"""

    image = Image.new("RGB", (1800, 1200), color="white")
    drawer = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size=58)
    lines = (
        "简历解析验证",
        "工作经历：后端工程师",
        "项目经历：智能体平台",
        "技能：Python、FastAPI、PostgreSQL",
    )
    for index, line in enumerate(lines):
        drawer.text((120, 160 + (index * 180)), line, fill="black", font=font)
    image.save(destination, "PDF", resolution=150.0)


if __name__ == "__main__":
    main()
