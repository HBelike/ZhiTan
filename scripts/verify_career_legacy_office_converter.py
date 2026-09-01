"""验证旧版 DOC/XLS 经 Gotenberg 转 PDF 后复用 PDF 解析链路。"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from pypdf import PdfWriter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import DocumentUnderstandingResult
from src.career_assistant.legacy_office import GotenbergOfficeConverter
from src.career_assistant.settings import LegacyOfficeConversionSettings


def _legacy_attachment(path: Path, media_type: str) -> AttachmentDescriptor:
    """构造无真实内容的旧版 Office 附件描述。"""

    return AttachmentDescriptor(
        attachment_id=uuid4(),
        kind=AttachmentKind.RESUME_DOCUMENT,
        original_filename=f"resume{path.suffix}",
        media_type=media_type,
        size_bytes=path.stat().st_size,
        temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


class _PdfParser:
    """只记录转换后的 PDF 是否进入既有解析器，不依赖 OCR 服务。"""

    def __init__(self) -> None:
        self.call_count = 0

    def parse_document(self, attachment: AttachmentDescriptor) -> DocumentUnderstandingResult:
        assert attachment.media_type == "application/pdf"
        self.call_count += 1
        return DocumentUnderstandingResult(
            parser_name="fake-docling",
            analysis_text="# 匿名简历\n\n- 已由 PDF 链路解析",
            status="success",
            processing_time_seconds=0.01,
        )


def _pdf_bytes() -> bytes:
    """生成单页空白 PDF，模拟 Gotenberg 的有效响应。"""

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with tempfile.SpooledTemporaryFile() as target:
        writer.write(target)
        target.seek(0)
        return target.read()


def main() -> None:
    """验证匿名 multipart、DOC/XLS 格式映射和转换文件清理。"""

    with tempfile.TemporaryDirectory(prefix="career-legacy-office-test-") as directory:
        root = Path(directory)
        source_path = root / "resume.doc"
        source_path.write_bytes(b"anonymous legacy office document")
        descriptor = _legacy_attachment(source_path, "application/msword")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url == "http://127.0.0.1:3000/forms/libreoffice/convert"
            body = request.content.decode("latin-1")
            assert 'filename="document.doc"' in body
            assert "resume.doc" not in body
            return httpx.Response(200, content=_pdf_bytes())

        converter = GotenbergOfficeConverter(
            LegacyOfficeConversionSettings(
                enabled=True,
                service_base_url="http://127.0.0.1:3000",
                request_timeout_seconds=5,
                max_converted_size_bytes=1_000_000,
            ),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            document_parser = _PdfParser()
            parser = AttachmentParser(
                AttachmentSettings(
                    temporary_root=root,
                    max_size_bytes=1_000_000,
                    ttl_seconds=120,
                ),
                document_understanding_parser=document_parser,
                legacy_office_converter=converter,
            )
            parsed = parser.parse(descriptor)
        finally:
            converter.close()

        assert "PDF 链路解析" in parsed.extracted_text
        assert parsed.kind is AttachmentKind.RESUME_DOCUMENT
        assert parsed.media_type == "application/msword"
        assert document_parser.call_count == 1
        assert not list(root.glob("*-converted.pdf"))

    print("career_legacy_office_converter_ok")


if __name__ == "__main__":
    main()
