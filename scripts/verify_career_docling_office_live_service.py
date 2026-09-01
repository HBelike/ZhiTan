"""使用有效的 DOCX/XLSX 样本验收本地 Docling 服务。

该脚本是显式的服务联调，不使用手工拼接的最小 OOXML 文件。OOXML 包缺少
关系、样式或工作簿元数据时，Office/Docling 有权拒绝它；那种失败不能代表
用户上传的真实 Word 或 Excel 文件不能处理。
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import DoclingServiceDocumentParser
from src.career_assistant.settings import DocumentUnderstandingSettings


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _descriptor(path: Path, media_type: str) -> AttachmentDescriptor:
    """构造匿名附件描述符，避免测试将真实文件名送入解析服务。"""

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
    """读取有效 Office 样本目录；服务联调必须由调用者明确提供。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        required=True,
        help="包含 word_sample.docx 与 xlsx_01.xlsx 的目录",
    )
    return parser.parse_args()


def main() -> None:
    """验证 DOCX 和 XLSX 均能被本地 Docling 转为非空 Markdown。"""

    fixture_directory = _parse_arguments().fixtures_dir.resolve()
    docx_path = fixture_directory / "word_sample.docx"
    xlsx_path = fixture_directory / "xlsx_01.xlsx"
    for path in (docx_path, xlsx_path):
        if not path.is_file():
            raise FileNotFoundError(f"缺少有效 Office 联调样本：{path.name}")

    settings = DocumentUnderstandingSettings(
        enabled=True,
        service_base_url="http://127.0.0.1:5001",
        api_key_env=None,
        request_timeout_seconds=120,
        force_ocr=True,
        table_mode="accurate",
    )
    client = DoclingServiceDocumentParser(settings)
    try:
        docx_result = client.parse_document(_descriptor(docx_path, DOCX_MEDIA_TYPE))
        xlsx_result = client.parse_document(_descriptor(xlsx_path, XLSX_MEDIA_TYPE))
    finally:
        client.close()

    assert docx_result.status == "success" and docx_result.analysis_text.strip()
    assert xlsx_result.status == "success" and xlsx_result.analysis_text.strip()
    print("career_docling_office_live_service_ok")


if __name__ == "__main__":
    main()
