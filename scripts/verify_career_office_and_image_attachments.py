"""验证求职助手的 Word、Excel 与扩展图片附件路由，不访问真实文档服务。"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.cloud_vision import CloudVisionResult
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import (
    DoclingServiceDocumentParser,
    DocumentUnderstandingResult,
)
from src.career_assistant.settings import DocumentUnderstandingSettings


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _descriptor(
    path: Path,
    *,
    kind: AttachmentKind,
    media_type: str,
) -> AttachmentDescriptor:
    """构造匿名测试附件描述，不含任何真实简历信息。"""

    return AttachmentDescriptor(
        attachment_id=uuid4(),
        kind=kind,
        original_filename=f"sample{path.suffix}",
        media_type=media_type,
        size_bytes=path.stat().st_size,
        temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


def _write_docx(path: Path) -> None:
    """生成最小 DOCX，验证契约和 multipart 格式而非 Office 排版质量。"""

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>匿名候选人</w:t></w:r></w:p></w:body></w:document>""",
        )


def _write_xlsx(path: Path) -> None:
    """生成最小 XLSX，覆盖表格文件的 Docling 输入格式选择。"""

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets></workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>匿名技能</t></is></c></row></sheetData></worksheet>""",
        )


class _FakeStructuredParser:
    """记录通用解析入口，确保 AttachmentParser 不再依赖 PDF 专用方法。"""

    def __init__(self) -> None:
        self.formats: list[str] = []

    def parse_document(self, attachment: AttachmentDescriptor) -> DocumentUnderstandingResult:
        self.formats.append(attachment.media_type)
        return DocumentUnderstandingResult(
            parser_name="fake-docling",
            analysis_text="# 匿名材料\n\n- 结构化文本",
            status="success",
            processing_time_seconds=0.01,
        )


def _docling_client(handler) -> DoclingServiceDocumentParser:
    """使用 MockTransport 验证请求内容，完全不访问网络。"""

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


def main() -> None:
    """覆盖 DOCX/XLSX、图片 OCR 路由和 BMP 归一化。"""

    with tempfile.TemporaryDirectory(prefix="career-office-test-") as temporary_directory:
        root = Path(temporary_directory)
        docx_path = root / "resume.docx"
        xlsx_path = root / "skills.xlsx"
        bmp_path = root / "resume.bmp"
        _write_docx(docx_path)
        _write_xlsx(xlsx_path)
        Image.new("RGB", (16, 12), color="white").save(bmp_path, format="BMP")

        docx = _descriptor(
            docx_path,
            kind=AttachmentKind.RESUME_DOCUMENT,
            media_type=DOCX_MEDIA_TYPE,
        )
        xlsx = _descriptor(
            xlsx_path,
            kind=AttachmentKind.RESUME_DOCUMENT,
            media_type=XLSX_MEDIA_TYPE,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            request_body = request.content.decode("latin-1")
            assert 'name="from_formats"' in request_body
            assert 'name="to_formats"' in request_body
            assert (
                'filename="document.docx"' in request_body
                or 'filename="document.xlsx"' in request_body
            )
            assert "sample.docx" not in request_body
            assert "sample.xlsx" not in request_body
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "document": {"md_content": "# 匿名材料\n\n- 已解析"},
                },
            )

        client = _docling_client(handler)
        try:
            assert "已解析" in client.parse_document(docx).analysis_text
            assert "已解析" in client.parse_document(xlsx).analysis_text
        finally:
            client.close()

        parser = _FakeStructuredParser()
        attachment_parser = AttachmentParser(
            AttachmentSettings(
                temporary_root=root,
                max_size_bytes=1_000_000,
                ttl_seconds=120,
            ),
            document_understanding_parser=parser,
        )
        parsed_docx = attachment_parser.parse(docx)
        parsed_xlsx = attachment_parser.parse(xlsx)
        assert parsed_docx.document_understanding_result is not None
        assert parsed_xlsx.document_understanding_result is not None
        assert parser.formats == [DOCX_MEDIA_TYPE, XLSX_MEDIA_TYPE]

        bmp = _descriptor(
            bmp_path,
            kind=AttachmentKind.RESUME_IMAGE,
            media_type="image/bmp",
        )
        parsed_bmp = attachment_parser.parse(bmp)
        assert parsed_bmp.requires_vision_model is False
        assert parsed_bmp.media_type == "image/png"
        assert parsed_bmp.image_bytes is None
        assert parsed_bmp.document_understanding_error is not None

        class _ImageStructuredParser:
            """模拟已启用的 Docling 图片 OCR，验证成功时不再强制选择 Vision。"""

            def parse_document(
                self,
                attachment: AttachmentDescriptor,
            ) -> DocumentUnderstandingResult:
                assert attachment.media_type == "application/pdf"
                assert attachment.temporary_path.suffix == ".pdf"
                return DocumentUnderstandingResult(
                    parser_name="fake-docling",
                    analysis_text="岗位名称：后端开发工程师\n任职要求：熟悉 Python、FastAPI 和 PostgreSQL。",
                    status="success",
                    processing_time_seconds=0.01,
                )

        image_parser = AttachmentParser(
            AttachmentSettings(
                temporary_root=root,
                max_size_bytes=1_000_000,
                ttl_seconds=120,
            ),
            document_understanding_parser=_ImageStructuredParser(),
        )
        parsed_image_ocr = image_parser.parse(bmp)
        assert parsed_image_ocr.document_understanding_result is not None
        assert "后端开发工程师" in parsed_image_ocr.extracted_text
        assert parsed_image_ocr.requires_vision_model is False
        assert parsed_image_ocr.image_bytes is None

        class _FakeCloudVisionParser:
            """模拟平台固定云端图片理解，确认它不依赖用户选择 Vision 模型。"""

            def analyze_image(
                self,
                media_type: str,
                image_bytes: bytes,
            ) -> CloudVisionResult:
                assert media_type == "image/png"
                assert image_bytes.startswith(b"\x89PNG")
                return CloudVisionResult(
                    analysis_text="图片类型：岗位截图\n职位：后端开发工程师\n技能：Python、FastAPI",
                    provider_key="qwen",
                    model_id="qwen3.6-flash",
                )

        class _LowQualityImageParser:
            """模拟 Docling 已响应但文字不足，必须触发平台固定云端复核。"""

            def parse_document(
                self,
                attachment: AttachmentDescriptor,
            ) -> DocumentUnderstandingResult:
                return DocumentUnderstandingResult(
                    parser_name="fake-docling",
                    analysis_text="岗位",
                    status="success",
                    processing_time_seconds=0.01,
                )

        vision_fallback_parser = AttachmentParser(
            AttachmentSettings(
                temporary_root=root,
                max_size_bytes=1_000_000,
                ttl_seconds=120,
            ),
            document_understanding_parser=_LowQualityImageParser(),
            cloud_vision_parser=_FakeCloudVisionParser(),
        )
        parsed_image_vision = vision_fallback_parser.parse(bmp)
        assert parsed_image_vision.requires_vision_model is False
        assert parsed_image_vision.cloud_vision_result is not None
        assert "后端开发工程师" in parsed_image_vision.extracted_text
        assert parsed_image_vision.document_understanding_error is None

    print("career_office_and_image_attachments_ok")


if __name__ == "__main__":
    main()
