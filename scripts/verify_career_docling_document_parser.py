"""验证 Docling 服务协议封装与扫描 PDF 路由，不访问真实 OCR 服务。"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
from pypdf import PdfWriter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import (
    DoclingServiceDocumentParser,
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
)
from src.career_assistant.settings import DocumentUnderstandingSettings


def _attachment(path: Path) -> AttachmentDescriptor:
    """构造不含真实简历信息的临时 PDF 描述符。"""

    return AttachmentDescriptor(
        attachment_id=uuid4(),
        kind=AttachmentKind.RESUME_PDF,
        original_filename="candidate-resume.pdf",
        media_type="application/pdf",
        size_bytes=path.stat().st_size,
        temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


def _blank_pdf(path: Path) -> None:
    """创建只有空白页的扫描件替身，用于验证路由而非 OCR 准确率。"""

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    path.write_bytes(output.getvalue())


def _docling_client(handler) -> DoclingServiceDocumentParser:
    """用 MockTransport 建立客户端，确保测试不会请求外网。"""

    return DoclingServiceDocumentParser(
        DocumentUnderstandingSettings(
            enabled=True,
            service_base_url="http://127.0.0.1:5001",
            api_key_env=None,
            request_timeout_seconds=5,
            force_ocr=True,
            table_mode="accurate",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class _FakeStructuredParser:
    """替身结构化解析器，用于验证 AttachmentParser 的路由衔接。"""

    def __init__(self) -> None:
        self.called_attachment_ids = []

    def parse_document(self, attachment: AttachmentDescriptor) -> DocumentUnderstandingResult:
        self.called_attachment_ids.append(attachment.attachment_id)
        return DocumentUnderstandingResult(
            parser_name="fake-docling",
            analysis_text="# 简历\n\n- 熟悉 Python\n- 有 Agent 项目经验",
            status="success",
            processing_time_seconds=0.01,
        )


def main() -> None:
    """验证 multipart 请求、错误映射与扫描件升级到结构化解析的行为。"""

    with tempfile.TemporaryDirectory(prefix="career-docling-test-") as temporary_directory:
        pdf_path = Path(temporary_directory) / "source.pdf"
        _blank_pdf(pdf_path)
        attachment = _attachment(pdf_path)

        def success_handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url == "http://127.0.0.1:5001/v1/convert/file"
            assert request.headers["content-type"].startswith("multipart/form-data")
            request_body = request.content.decode("latin-1")
            assert 'name="from_formats"' in request_body
            assert 'name="to_formats"' in request_body
            assert 'name="force_ocr"' in request_body
            assert 'filename="document.pdf"' in request_body
            assert "candidate-resume.pdf" not in request_body
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "processing_time": 0.42,
                    "document": {
                        "md_content": "# 候选人简历\n\n## 技能\n\nPython、LangGraph",
                    },
                },
            )

        document_client = _docling_client(success_handler)
        try:
            document_result = document_client.parse_pdf(attachment)
        finally:
            document_client.close()
        assert document_result.parser_name == "docling-serve"
        assert "LangGraph" in document_result.analysis_text
        assert document_result.processing_time_seconds == 0.42

        def unauthorized_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "hidden"})

        failing_client = _docling_client(unauthorized_handler)
        try:
            try:
                failing_client.parse_pdf(attachment)
            except DocumentUnderstandingError as exc:
                assert "鉴权失败" in str(exc)
            else:
                raise AssertionError("Docling 401 应转换为安全的解析异常")
        finally:
            failing_client.close()

        fake_parser = _FakeStructuredParser()
        attachment_parser = AttachmentParser(
            AttachmentSettings(
                temporary_root=Path(temporary_directory),
                max_size_bytes=1_000_000,
                ttl_seconds=120,
            ),
            document_understanding_parser=fake_parser,
        )
        parsed_attachment = attachment_parser.parse(attachment)
        assert fake_parser.called_attachment_ids == [attachment.attachment_id]
        assert parsed_attachment.document_understanding_result is not None
        assert "Agent 项目经验" in parsed_attachment.extracted_text

    print("career_docling_document_parser_ok")


if __name__ == "__main__":
    main()
