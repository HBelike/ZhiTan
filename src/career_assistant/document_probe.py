"""求职助手文档理解链路的轻量级 PDF 探测器。

本模块只判断 PDF 是否具备可信的原生文本层，不执行 OCR、版面分析或外部模型调用。
它让后续的 Docling 与 PaddleOCR 能够按需接管扫描件，避免把空文本直接交给聊天模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence


class DocumentParseRoute(StrEnum):
    """PDF 在文档理解链路中建议进入的下一条路径。"""

    NATIVE_TEXT = "native_text"
    DOCUMENT_UNDERSTANDING = "document_understanding"


class DocumentProbeIssue(StrEnum):
    """探测阶段发现的非敏感问题代码，供前端展示和后续路由使用。"""

    NO_TEXT_LAYER = "no_text_layer"
    PARTIAL_TEXT_LAYER = "partial_text_layer"
    PAGE_TEXT_EXTRACTION_FAILED = "page_text_extraction_failed"
    LOW_TEXT_DENSITY = "low_text_density"


@dataclass(frozen=True)
class DocumentProbeResult:
    """单份 PDF 的文本层健康度结果。

    此对象只保留页数、字符数和问题代码，不保存简历正文，因而可以安全地用于
    任务状态和历史摘要。原文始终由当前 Turn 的 ``ParsedAttachment`` 临时持有。
    """

    page_count: int
    pages_with_native_text: int
    total_native_characters: int
    native_text_coverage: float
    native_text_quality_score: float
    recommended_route: DocumentParseRoute
    issues: tuple[DocumentProbeIssue, ...]

    @property
    def requires_document_understanding(self) -> bool:
        """判断是否需要由 Docling 或 OCR 处理，而非继续使用原生文本。"""

        return self.recommended_route is DocumentParseRoute.DOCUMENT_UNDERSTANDING


class PdfDocumentProbe:
    """根据每页已提取的文本判断 PDF 是否可能为扫描件或缺少文本层。

    该类故意不依赖 OCR、GPU 或 LLM。它由 ``AttachmentParser`` 在读取 PDF 后调用，
    输出稳定、可测试的路由建议；后续节点再根据建议调用重量级解析器。
    """

    def __init__(
        self,
        *,
        minimum_characters_per_text_page: int = 24,
        minimum_native_text_coverage: float = 0.8,
    ) -> None:
        """创建探测器并校验启发式阈值。

        ``minimum_characters_per_text_page`` 用于过滤页眉、页码等无意义碎片；
        ``minimum_native_text_coverage`` 用于识别部分扫描、部分可复制的混合 PDF。
        """

        if minimum_characters_per_text_page <= 0:
            raise ValueError("每页原生文本最小字符数必须大于零")
        if not 0 < minimum_native_text_coverage <= 1:
            raise ValueError("原生文本覆盖率阈值必须位于 (0, 1] 区间")

        self._minimum_characters_per_text_page = minimum_characters_per_text_page
        self._minimum_native_text_coverage = minimum_native_text_coverage

    def probe(
        self,
        page_texts: Sequence[str],
        *,
        page_character_counts: Sequence[int] | None = None,
        failed_page_count: int = 0,
    ) -> DocumentProbeResult:
        """从已读取的各页文本生成路由建议。

        输入来自 PDF 库的文本层提取结果。提取失败的页按空文本处理但会保留问题代码；
        这样单页损坏不会让整个上传请求异常中断，也不会被误判为“用户没有上传文件”。
        """

        if not page_texts:
            raise ValueError("PDF 至少需要包含一页才能进行文档探测")
        if failed_page_count < 0 or failed_page_count > len(page_texts):
            raise ValueError("PDF 文本提取失败页数无效")

        normalized_page_texts = tuple((page_text or "").strip() for page_text in page_texts)
        if page_character_counts is None:
            character_counts = tuple(len(page_text) for page_text in normalized_page_texts)
        else:
            if len(page_character_counts) != len(normalized_page_texts):
                raise ValueError("PDF 页面文本字符数与页面数不一致")
            if any(count < 0 for count in page_character_counts):
                raise ValueError("PDF 页面文本字符数不能为负数")
            character_counts = tuple(page_character_counts)
        pages_with_native_text = sum(
            count >= self._minimum_characters_per_text_page
            for count in character_counts
        )
        total_native_characters = sum(character_counts)
        page_count = len(normalized_page_texts)
        coverage = pages_with_native_text / page_count
        issues: list[DocumentProbeIssue] = []

        if total_native_characters == 0:
            issues.append(DocumentProbeIssue.NO_TEXT_LAYER)
        elif coverage < self._minimum_native_text_coverage:
            issues.append(DocumentProbeIssue.PARTIAL_TEXT_LAYER)

        if failed_page_count:
            issues.append(DocumentProbeIssue.PAGE_TEXT_EXTRACTION_FAILED)

        expected_character_count = page_count * self._minimum_characters_per_text_page
        if 0 < total_native_characters < expected_character_count:
            issues.append(DocumentProbeIssue.LOW_TEXT_DENSITY)

        requires_document_understanding = (
            total_native_characters == 0
            or coverage < self._minimum_native_text_coverage
            or failed_page_count > 0
        )
        return DocumentProbeResult(
            page_count=page_count,
            pages_with_native_text=pages_with_native_text,
            total_native_characters=total_native_characters,
            native_text_coverage=round(coverage, 3),
            native_text_quality_score=self._calculate_quality_score(
                coverage=coverage,
                total_native_characters=total_native_characters,
                page_count=page_count,
            ),
            recommended_route=(
                DocumentParseRoute.DOCUMENT_UNDERSTANDING
                if requires_document_understanding
                else DocumentParseRoute.NATIVE_TEXT
            ),
            issues=tuple(issues),
        )

    def _calculate_quality_score(
        self,
        *,
        coverage: float,
        total_native_characters: int,
        page_count: int,
    ) -> float:
        """计算仅用于路由的原生文本启发式分数，不把它伪装为 OCR 准确率。"""

        expected_character_count = (
            page_count * self._minimum_characters_per_text_page
        )
        density_score = min(
            total_native_characters / expected_character_count,
            1.0,
        )
        return round((coverage * 0.7) + (density_score * 0.3), 3)
