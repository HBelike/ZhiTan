"""使用有效 DOC/XLS 样本验收 Gotenberg 到 Docling 的旧版 Office 链路。"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import DoclingServiceDocumentParser
from src.career_assistant.legacy_office import GotenbergOfficeConverter
from src.career_assistant.settings import (
    DocumentUnderstandingSettings,
    LegacyOfficeConversionSettings,
)


def _descriptor(path: Path, media_type: str) -> AttachmentDescriptor:
    """创建短期匿名描述符，确保测试文件只在本地临时目录内使用。"""

    return AttachmentDescriptor(
        attachment_id=uuid4(),
        kind=AttachmentKind.RESUME_DOCUMENT,
        original_filename=f"document{path.suffix.lower()}",
        media_type=media_type,
        size_bytes=path.stat().st_size,
        temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _parse_arguments() -> argparse.Namespace:
    """读取包含真实二进制 Office 样本的本地目录。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        required=True,
        help="包含 legacy_sample.doc 与 legacy_sample.xls 的目录",
    )
    return parser.parse_args()


def main() -> None:
    """验证 DOC/XLS 转 PDF 后仍能被同一附件解析器提取文本。"""

    fixture_directory = _parse_arguments().fixtures_dir.resolve()
    doc_path = fixture_directory / "legacy_sample.doc"
    xls_path = fixture_directory / "legacy_sample.xls"
    for path in (doc_path, xls_path):
        if not path.is_file():
            raise FileNotFoundError(f"缺少有效旧版 Office 联调样本：{path.name}")

    docling = DoclingServiceDocumentParser(
        DocumentUnderstandingSettings(
            enabled=True,
            service_base_url="http://127.0.0.1:5001",
            api_key_env=None,
            request_timeout_seconds=120,
            force_ocr=True,
            table_mode="accurate",
        ),
    )
    converter = GotenbergOfficeConverter(
        LegacyOfficeConversionSettings(
            enabled=True,
            service_base_url="http://127.0.0.1:3000",
            request_timeout_seconds=120,
            max_converted_size_bytes=20 * 1024 * 1024,
        ),
    )
    parser = AttachmentParser(
        AttachmentSettings(
            temporary_root=fixture_directory,
            max_size_bytes=1_000_000,
            ttl_seconds=120,
            max_pdf_pages=90,
        ),
        document_understanding_parser=docling,
        legacy_office_converter=converter,
    )
    try:
        doc_result = parser.parse(_descriptor(doc_path, "application/msword"))
        xls_result = parser.parse(
            _descriptor(xls_path, "application/vnd.ms-excel"),
        )
    finally:
        docling.close()
        converter.close()

    assert doc_result.extracted_text.strip(), doc_result.document_understanding_error
    assert xls_result.extracted_text.strip(), xls_result.document_understanding_error
    assert doc_result.document_understanding_result is not None
    assert xls_result.document_understanding_result is not None
    print("career_gotenberg_legacy_office_live_service_ok")


if __name__ == "__main__":
    main()
