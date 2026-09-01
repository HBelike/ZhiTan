"""验证 PDF 文本层探测与扫描件路由，不依赖数据库、OCR 模型或外部网络。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.document_probe import (
    DocumentParseRoute,
    DocumentProbeIssue,
    PdfDocumentProbe,
)


def main() -> None:
    """验证空文本层、混合文本层和正常文本层的路由结果。"""

    probe = PdfDocumentProbe(
        minimum_characters_per_text_page=24,
        minimum_native_text_coverage=0.8,
    )

    scanned_result = probe.probe(("", ""))
    assert scanned_result.recommended_route is DocumentParseRoute.DOCUMENT_UNDERSTANDING
    assert scanned_result.total_native_characters == 0
    assert DocumentProbeIssue.NO_TEXT_LAYER in scanned_result.issues
    assert scanned_result.native_text_quality_score == 0.0

    mixed_result = probe.probe(("有完整文本的第一页。" * 10, ""))
    assert mixed_result.recommended_route is DocumentParseRoute.DOCUMENT_UNDERSTANDING
    assert mixed_result.pages_with_native_text == 1
    assert DocumentProbeIssue.PARTIAL_TEXT_LAYER in mixed_result.issues

    native_result = probe.probe(("求职者具备 Python、Java 与项目交付经验。" * 5,))
    assert native_result.recommended_route is DocumentParseRoute.NATIVE_TEXT
    assert native_result.issues == ()
    assert native_result.native_text_coverage == 1.0

    failed_page_result = probe.probe(("",), failed_page_count=1)
    assert failed_page_result.recommended_route is DocumentParseRoute.DOCUMENT_UNDERSTANDING
    assert DocumentProbeIssue.PAGE_TEXT_EXTRACTION_FAILED in failed_page_result.issues

    print("career_document_probe_ok")


if __name__ == "__main__":
    main()
