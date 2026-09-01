"""验证增强文档解析服务故障不会被误报成“没有收到简历”。"""

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
from src.career_assistant.document_parsing import DocumentUnderstandingError


class _UnavailableDocumentParser:
    """模拟下载中或暂不可用的 Docling 服务。"""

    def parse_document(self, attachment: AttachmentDescriptor):
        """保持真实解析器同一接口，并返回用户可理解的安全异常。"""

        del attachment
        raise DocumentUnderstandingError("无法连接文档解析服务，请确认 Docling 服务已启动后重试")


def main() -> None:
    """扫描 PDF 的增强解析失败应降级为可继续的当前 Turn 结果。"""

    with tempfile.TemporaryDirectory() as temporary_directory:
        pdf_path = Path(temporary_directory) / "resume.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with pdf_path.open("wb") as target:
            writer.write(target)

        descriptor = AttachmentDescriptor(
            attachment_id=uuid4(),
            kind=AttachmentKind.RESUME_PDF,
            original_filename="resume.pdf",
            media_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
            temporary_path=pdf_path,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        parser = AttachmentParser(
            AttachmentSettings(
                temporary_root=Path(temporary_directory),
                max_size_bytes=10 * 1024 * 1024,
                ttl_seconds=600,
            ),
            document_understanding_parser=_UnavailableDocumentParser(),
        )
        parsed = parser.parse(descriptor)

        assert parsed.document_probe is not None
        assert parsed.document_probe.requires_document_understanding
        assert parsed.document_understanding_result is None
        assert parsed.document_understanding_error is not None
        assert "无法连接文档解析服务" in parsed.document_understanding_error
        assert parsed.extracted_text == ""
    print("career_document_parser_degradation_ok")


if __name__ == "__main__":
    main()
