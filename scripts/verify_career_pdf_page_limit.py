"""验证 Web 后端 PDF 页数限制与配置读取保持一致。"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pypdf import PdfWriter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.settings import load_attachment_processing_settings


def _create_blank_pdf(path: Path, page_count: int) -> None:
    """生成不含个人信息的空白 PDF，用于验证页数边界。"""

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as output_file:
        writer.write(output_file)


def _descriptor(path: Path) -> AttachmentDescriptor:
    """构造只在本次验证中使用的 PDF 附件描述符。"""

    return AttachmentDescriptor(
        attachment_id=uuid4(),
        kind=AttachmentKind.RESUME_PDF,
        original_filename=path.name,
        media_type="application/pdf",
        size_bytes=path.stat().st_size,
        temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def main() -> None:
    """验证 90 页可进入解析，91 页在 OCR 前被安全拒绝。"""

    settings = load_attachment_processing_settings(
        PROJECT_ROOT / "config" / "career_assistant.yaml",
    )
    assert settings.max_pdf_pages == 90

    with tempfile.TemporaryDirectory(prefix="career-pdf-page-limit-") as directory:
        temporary_root = Path(directory)
        parser = AttachmentParser(
            AttachmentSettings(
                temporary_root=temporary_root,
                max_size_bytes=10 * 1024 * 1024,
                ttl_seconds=300,
                max_pdf_pages=settings.max_pdf_pages,
            ),
        )
        allowed_pdf = temporary_root / "allowed-90-pages.pdf"
        rejected_pdf = temporary_root / "rejected-91-pages.pdf"
        _create_blank_pdf(allowed_pdf, 90)
        _create_blank_pdf(rejected_pdf, 91)

        parsed = parser.parse(_descriptor(allowed_pdf))
        assert parsed.page_count == 90

        try:
            parser.parse(_descriptor(rejected_pdf))
        except ValueError as exc:
            assert "90 页" in str(exc)
        else:
            raise AssertionError("91 页 PDF 未被页数限制拦截")

    print("career_pdf_page_limit_90_pages_ok")


if __name__ == "__main__":
    main()
