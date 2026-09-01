"""求职助手附件的临时保存、解析与清理服务。"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from src.career_assistant.cloud_vision import (
    CloudVisionError,
    CloudVisionImageParser,
    CloudVisionResult,
)
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_probe import (
    DocumentProbeResult,
    PdfDocumentProbe,
)
from src.career_assistant.document_parsing import (
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
    StructuredDocumentParser,
)
from src.career_assistant.legacy_office import (
    LegacyOfficeConversionError,
    LegacyOfficeConverter,
)


@dataclass(frozen=True)
class AttachmentSettings:
    """临时附件处理的容量与时限约束。"""

    temporary_root: Path
    max_size_bytes: int
    ttl_seconds: int
    max_pdf_pages: int = 30
    max_extracted_characters: int = 50_000
    max_image_side_pixels: int = 8_000
    max_cloud_vision_image_bytes: int = 7 * 1024 * 1024
    minimum_native_characters_per_pdf_page: int = 24
    minimum_native_pdf_text_coverage: float = 0.8


@dataclass(frozen=True)
class ParsedAttachment:
    """仅在当前 Agent Turn 内存中存在的材料解析结果。"""

    kind: AttachmentKind
    media_type: str
    page_count: int | None
    image_width: int | None
    image_height: int | None
    extracted_text: str
    requires_vision_model: bool
    image_bytes: bytes | None
    document_probe: DocumentProbeResult | None = None
    document_understanding_result: DocumentUnderstandingResult | None = None
    document_understanding_error: str | None = None
    cloud_vision_result: CloudVisionResult | None = None


class TemporaryAttachmentStore:
    """每轮独立暂存附件，并在验证路径后清理临时目录。"""

    _ALLOWED_TYPES: dict[AttachmentKind, dict[str, set[str]]] = {
        AttachmentKind.RESUME_PDF: {
            ".pdf": {"application/pdf"},
        },
        AttachmentKind.RESUME_DOCUMENT: {
            ".doc": {"application/msword"},
            ".docx": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            },
            ".xls": {"application/vnd.ms-excel"},
            ".xlsx": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
        },
        AttachmentKind.RESUME_IMAGE: {
            ".jpg": {"image/jpeg"},
            ".jpeg": {"image/jpeg"},
            ".png": {"image/png"},
            ".webp": {"image/webp"},
            ".bmp": {"image/bmp", "image/x-ms-bmp"},
            ".tif": {"image/tiff"},
            ".tiff": {"image/tiff"},
        },
        AttachmentKind.INTERVIEW_EVIDENCE_IMAGE: {
            ".jpg": {"image/jpeg"},
            ".jpeg": {"image/jpeg"},
            ".png": {"image/png"},
            ".webp": {"image/webp"},
            ".bmp": {"image/bmp", "image/x-ms-bmp"},
            ".tif": {"image/tiff"},
            ".tiff": {"image/tiff"},
        },
        AttachmentKind.JOB_DESCRIPTION_IMAGE: {
            ".jpg": {"image/jpeg"},
            ".jpeg": {"image/jpeg"},
            ".png": {"image/png"},
            ".webp": {"image/webp"},
            ".bmp": {"image/bmp", "image/x-ms-bmp"},
            ".tif": {"image/tiff"},
            ".tiff": {"image/tiff"},
        },
        AttachmentKind.JOB_DESCRIPTION_FILE: {
            ".pdf": {"application/pdf"},
            ".doc": {"application/msword"},
            ".docx": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            },
            ".xls": {"application/vnd.ms-excel"},
            ".xlsx": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
        },
    }

    def __init__(self, settings: AttachmentSettings) -> None:
        """创建受控临时根目录，不创建具体请求目录。"""

        if settings.max_size_bytes <= 0 or settings.ttl_seconds <= 0:
            raise ValueError("附件大小上限和临时保存时长必须大于零")
        self._settings = settings
        self._root = settings.temporary_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    async def save_upload(self, upload: UploadFile, kind: AttachmentKind) -> AttachmentDescriptor:
        """按块保存与校验上传文件，失败时立即移除本轮临时目录。"""

        original_filename = Path(upload.filename or "").name.strip()
        media_type = (upload.content_type or "").strip().lower()
        self._validate_type(kind, original_filename, media_type)
        request_directory = Path(tempfile.mkdtemp(prefix="career-turn-", dir=str(self._root))).resolve()
        destination = request_directory / f"{uuid4().hex}{Path(original_filename).suffix.lower()}"
        written_bytes = 0
        try:
            with destination.open("xb") as output_file:
                while chunk := await upload.read(1024 * 1024):
                    written_bytes += len(chunk)
                    if written_bytes > self._settings.max_size_bytes:
                        raise ValueError("附件超过允许的最大大小")
                    output_file.write(chunk)
            if written_bytes == 0:
                raise ValueError("附件不能为空")
            return AttachmentDescriptor(
                attachment_id=uuid4(), kind=kind, original_filename=original_filename,
                media_type=media_type, size_bytes=written_bytes, temporary_path=destination,
                expires_at=datetime.now(UTC) + timedelta(seconds=self._settings.ttl_seconds),
            )
        except Exception:
            self._cleanup_directory(request_directory)
            raise
        finally:
            await upload.close()

    def save_bytes(
        self,
        data: bytes,
        filename: str,
        media_type: str,
        kind: AttachmentKind,
    ) -> AttachmentDescriptor:
        """同步暂存已下载的远程图片，复用统一的 MIME、大小和清理边界。

        小红书等公开页面的图片会先由上游连接器下载为字节，再通过该方法进入
        既有的 ``AttachmentParser`` OCR/Vision 路径；方法不负责网络请求，也不
        会把图片持久化到面经库。
        """

        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ValueError("临时附件内容必须是字节数据")
        original_filename = Path(filename or "").name.strip()
        normalized_media_type = (media_type or "").split(";", 1)[0].strip().lower()
        self._validate_type(kind, original_filename, normalized_media_type)
        payload = bytes(data)
        if not payload:
            raise ValueError("附件不能为空")
        if len(payload) > self._settings.max_size_bytes:
            raise ValueError("附件超过允许的最大大小")

        request_directory = Path(
            tempfile.mkdtemp(prefix="career-turn-", dir=str(self._root)),
        ).resolve()
        destination = request_directory / f"{uuid4().hex}{Path(original_filename).suffix.lower()}"
        try:
            with destination.open("xb") as output_file:
                output_file.write(payload)
            return AttachmentDescriptor(
                attachment_id=uuid4(),
                kind=kind,
                original_filename=original_filename,
                media_type=normalized_media_type,
                size_bytes=len(payload),
                temporary_path=destination,
                expires_at=datetime.now(UTC) + timedelta(seconds=self._settings.ttl_seconds),
            )
        except Exception:
            self._cleanup_directory(request_directory)
            raise

    def cleanup(self, attachments: tuple[AttachmentDescriptor, ...]) -> None:
        """清理同一 Turn 的临时目录；重复调用安全。"""

        directories: set[Path] = set()
        for attachment in attachments:
            try:
                temporary_path = attachment.temporary_path.resolve()
                temporary_path.relative_to(self._root)
            except (OSError, ValueError):
                continue
            directories.add(temporary_path.parent)
        for directory in directories:
            self._cleanup_directory(directory)

    def _cleanup_directory(self, directory: Path) -> None:
        """仅删除 temporary_root 的直接子目录，防止误删工作区其他文件。"""

        try:
            resolved_directory = directory.resolve()
            resolved_directory.relative_to(self._root)
        except (OSError, ValueError):
            return
        if resolved_directory.parent == self._root:
            shutil.rmtree(resolved_directory, ignore_errors=True)

    def _validate_type(self, kind: AttachmentKind, filename: str, media_type: str) -> None:
        """同时校验 MIME 与扩展名，阻止伪装材料进入解析器。"""

        if not filename:
            raise ValueError("附件必须提供文件名")
        suffix = Path(filename).suffix.lower()
        allowed_media_types = self._ALLOWED_TYPES[kind].get(suffix, set())
        if media_type not in allowed_media_types:
            raise ValueError(
                "附件类型不受支持；支持 PDF、Word（DOC/DOCX）、Excel（XLS/XLSX）、JPG、PNG、WebP、BMP 或 TIFF",
            )


class AttachmentParser:
    """按路由解析 PDF 文本、结构化文档和图片元数据。

    原生文本 PDF 保持轻量 ``pypdf`` 路径；扫描件或文本层不完整 PDF 才按需委托给
    ``StructuredDocumentParser``。解析结果只保留到当前 Turn 的内存对象中。
    """

    _MINIMUM_USABLE_IMAGE_OCR_CHARACTERS = 24

    def __init__(
        self,
        settings: AttachmentSettings,
        document_understanding_parser: StructuredDocumentParser | None = None,
        legacy_office_converter: LegacyOfficeConverter | None = None,
        cloud_vision_parser: CloudVisionImageParser | None = None,
    ) -> None:
        if settings.minimum_native_characters_per_pdf_page <= 0:
            raise ValueError("PDF 每页原生文本最小字符数必须大于零")
        if not 0 < settings.minimum_native_pdf_text_coverage <= 1:
            raise ValueError("PDF 原生文本覆盖率阈值必须位于 (0, 1] 区间")
        self._settings = settings
        self._pdf_document_probe = PdfDocumentProbe(
            minimum_characters_per_text_page=(
                settings.minimum_native_characters_per_pdf_page
            ),
            minimum_native_text_coverage=settings.minimum_native_pdf_text_coverage,
        )
        self._document_understanding_parser = document_understanding_parser
        self._legacy_office_converter = legacy_office_converter
        self._cloud_vision_parser = cloud_vision_parser

    def parse(self, attachment: AttachmentDescriptor) -> ParsedAttachment:
        """按媒体类型解析；异常交给 Graph 标记 Turn 失败并触发清理。"""

        attachment.validate()
        if attachment.media_type == "application/pdf":
            return self._parse_pdf(attachment)
        if attachment.kind in {
            AttachmentKind.RESUME_DOCUMENT,
            AttachmentKind.JOB_DESCRIPTION_FILE,
        }:
            return self._parse_structured_document(attachment)
        return self._parse_image(attachment)

    def _parse_pdf(self, attachment: AttachmentDescriptor) -> ParsedAttachment:
        """先探测 PDF 文本层，再按需委托结构化解析服务处理扫描件。"""

        reader = PdfReader(str(attachment.temporary_path), strict=False)
        if reader.is_encrypted:
            raise ValueError("暂不支持加密 PDF，请解除密码后重新上传")
        if len(reader.pages) > self._settings.max_pdf_pages:
            raise ValueError(f"PDF 页数不能超过 {self._settings.max_pdf_pages} 页")
        remaining = self._settings.max_extracted_characters
        text_parts: list[str] = []
        page_texts: list[str] = []
        page_text_character_counts: list[int] = []
        failed_page_count = 0
        for page in reader.pages:
            try:
                page_text = (page.extract_text() or "").strip()
            except Exception:
                page_text = ""
                failed_page_count += 1
            probe_sample_length = self._settings.minimum_native_characters_per_pdf_page
            page_texts.append(page_text[:probe_sample_length])
            page_text_character_counts.append(len(page_text))
            if page_text and remaining > 0:
                text_parts.append(page_text[:remaining])
                remaining -= len(page_text)
        document_probe = self._pdf_document_probe.probe(
            page_texts,
            page_character_counts=page_text_character_counts,
            failed_page_count=failed_page_count,
        )
        document_understanding_result: DocumentUnderstandingResult | None = None
        extracted_text = "\n".join(text_parts)
        document_understanding_error: str | None = None
        if (
            document_probe.requires_document_understanding
            and self._document_understanding_parser is not None
        ):
            try:
                document_understanding_result = (
                    self._document_understanding_parser.parse_document(attachment)
                )
                extracted_text = document_understanding_result.analysis_text[
                    : self._settings.max_extracted_characters
                ]
            except DocumentUnderstandingError as exc:
                # Docling/OCR 是可恢复的增强服务。服务暂时不可用时仍保留原生文本，
                # 让后续模型如实解释“已收到但尚未完整识别”，而不是整轮直接失败。
                document_understanding_error = str(exc)
        return ParsedAttachment(
            kind=attachment.kind, media_type=attachment.media_type, page_count=len(reader.pages),
            image_width=None, image_height=None, extracted_text=extracted_text,
            requires_vision_model=False, image_bytes=None, document_probe=document_probe,
            document_understanding_result=document_understanding_result,
            document_understanding_error=document_understanding_error,
        )

    def _parse_structured_document(
        self,
        attachment: AttachmentDescriptor,
    ) -> ParsedAttachment:
        """解析 Word/Excel 文档；统一交给 Docling 保留段落、表格与阅读顺序。"""

        if self._is_legacy_office_document(attachment):
            return self._parse_legacy_office_document(attachment)

        document_understanding_result: DocumentUnderstandingResult | None = None
        document_understanding_error: str | None = None
        extracted_text = ""
        if self._document_understanding_parser is None:
            document_understanding_error = (
                "Word 或 Excel 文档已接收，但文档解析服务尚未启用，请稍后重试或改为上传 PDF"
            )
        else:
            try:
                document_understanding_result = (
                    self._document_understanding_parser.parse_document(attachment)
                )
                extracted_text = document_understanding_result.analysis_text[
                    : self._settings.max_extracted_characters
                ]
            except DocumentUnderstandingError as exc:
                document_understanding_error = str(exc)

        return ParsedAttachment(
            kind=attachment.kind,
            media_type=attachment.media_type,
            page_count=None,
            image_width=None,
            image_height=None,
            extracted_text=extracted_text,
            requires_vision_model=False,
            image_bytes=None,
            document_understanding_result=document_understanding_result,
            document_understanding_error=document_understanding_error,
        )

    def _parse_legacy_office_document(
        self,
        attachment: AttachmentDescriptor,
    ) -> ParsedAttachment:
        """将 DOC/XLS 临时转为 PDF，再复用既有页数限制、OCR 与布局解析链路。"""

        if self._legacy_office_converter is None:
            return ParsedAttachment(
                kind=attachment.kind,
                media_type=attachment.media_type,
                page_count=None,
                image_width=None,
                image_height=None,
                extracted_text="",
                requires_vision_model=False,
                image_bytes=None,
                document_understanding_error=(
                    "DOC/XLS 已接收，但旧版 Office 转换服务尚未启用，请改为上传 DOCX、XLSX 或 PDF"
                ),
            )

        converted_attachment: AttachmentDescriptor | None = None
        try:
            converted_attachment = self._legacy_office_converter.convert_to_pdf(attachment)
            parsed_pdf = self._parse_pdf(converted_attachment)
            # DOC/XLS 经 LibreOffice 转出的 PDF 往往含有可复制文本，因此
            # `_parse_pdf` 可能合理地选择轻量 pypdf 路径。这里仍显式调用
            # Docling 一次，以保持旧版 Office 与 DOCX/XLSX 同样的表格、
            # 标题和阅读顺序恢复能力；Docling 不可用时则保留轻量结果降级。
            if (
                parsed_pdf.document_understanding_result is None
                and self._document_understanding_parser is not None
            ):
                try:
                    document_result = self._document_understanding_parser.parse_document(
                        converted_attachment,
                    )
                    parsed_pdf = replace(
                        parsed_pdf,
                        extracted_text=document_result.analysis_text[
                            : self._settings.max_extracted_characters
                        ],
                        document_understanding_result=document_result,
                        document_understanding_error=None,
                    )
                except DocumentUnderstandingError as exc:
                    parsed_pdf = replace(
                        parsed_pdf,
                        document_understanding_error=str(exc),
                    )
            return replace(
                parsed_pdf,
                kind=attachment.kind,
                media_type=attachment.media_type,
            )
        except LegacyOfficeConversionError as exc:
            return ParsedAttachment(
                kind=attachment.kind,
                media_type=attachment.media_type,
                page_count=None,
                image_width=None,
                image_height=None,
                extracted_text="",
                requires_vision_model=False,
                image_bytes=None,
                document_understanding_error=str(exc),
            )
        finally:
            if converted_attachment is not None:
                try:
                    converted_attachment.temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _is_legacy_office_document(attachment: AttachmentDescriptor) -> bool:
        """仅识别必须经过 Office 转换服务的二进制旧格式。"""

        return (
            attachment.media_type,
            attachment.temporary_path.suffix.lower(),
        ) in {
            ("application/msword", ".doc"),
            ("application/vnd.ms-excel", ".xls"),
        }

    def _parse_image(self, attachment: AttachmentDescriptor) -> ParsedAttachment:
        """先用 Docling OCR，质量不足时由平台固定 Qwen Vision 自动复核。"""

        try:
            with Image.open(attachment.temporary_path) as image:
                image.verify()
            with Image.open(attachment.temporary_path) as image:
                width, height = image.size
                normalized_media_type = attachment.media_type
                image_bytes = attachment.temporary_path.read_bytes()
                if attachment.media_type not in {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                }:
                    # 常见 Vision API 对 BMP/TIFF 的兼容性不一致，统一为 PNG 后再传给模型。
                    normalized_image = image.convert("RGBA")
                    output = BytesIO()
                    normalized_image.save(output, format="PNG")
                    image_bytes = output.getvalue()
                    normalized_media_type = "image/png"
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("图片无法读取，请重新上传 JPG、PNG、WebP、BMP 或 TIFF 文件") from exc
        if width <= 0 or height <= 0 or max(width, height) > self._settings.max_image_side_pixels:
            raise ValueError("图片尺寸无效或超过允许上限")

        document_understanding_result: DocumentUnderstandingResult | None = None
        document_understanding_error: str | None = None
        extracted_text = ""
        if self._document_understanding_parser is not None:
            converted_attachment: AttachmentDescriptor | None = None
            try:
                # 当前生产镜像直接以 ``image`` 格式处理中文 PNG 时会沿用英文 OCR 默认值。
                # 转为单页 PDF 后复用已验收的扫描 PDF 路径，可稳定恢复中文阅读顺序；
                # 中间文件与原图在同一临时目录中，并在本方法结束前立即删除。
                converted_attachment = self._create_image_pdf_for_ocr(attachment)
                document_understanding_result = (
                    self._document_understanding_parser.parse_document(converted_attachment)
                )
                extracted_text = document_understanding_result.analysis_text[
                    : self._settings.max_extracted_characters
                ]
            except (DocumentUnderstandingError, OSError) as exc:
                # 图片 OCR 是优先路径而非单点依赖。服务暂时不可用时，交由已配置的
                # Vision 模型继续理解图片；模型也不可用时会给出明确的能力提示。
                document_understanding_error = (
                    str(exc).strip() or "图片 OCR 暂时不可用，将尝试使用视觉模型理解图片"
                )
            finally:
                if converted_attachment is not None:
                    try:
                        converted_attachment.temporary_path.unlink(missing_ok=True)
                    except OSError:
                        pass

        cloud_vision_result: CloudVisionResult | None = None
        if not self._has_usable_image_ocr_text(extracted_text):
            if self._cloud_vision_parser is None:
                document_understanding_error = (
                    "图片文字识别结果不足，且平台云端图片理解服务尚未配置"
                )
            else:
                try:
                    normalized_media_type, image_bytes = self._prepare_cloud_vision_input(
                        attachment,
                        normalized_media_type,
                        image_bytes,
                    )
                    cloud_vision_result = self._cloud_vision_parser.analyze_image(
                        normalized_media_type,
                        image_bytes,
                    )
                    extracted_text = cloud_vision_result.analysis_text[
                        : self._settings.max_extracted_characters
                    ]
                    # 云端复核成功后，Docling 的低质量或暂时错误不再干扰用户对材料
                    # 已成功解析这一事实的理解；两个原始结果均不持久化。
                    document_understanding_error = None
                except (CloudVisionError, OSError, ValueError) as exc:
                    document_understanding_error = (
                        "图片文字识别结果不足，且云端图片理解未完成："
                        f"{str(exc).strip() or '请稍后重试'}"
                    )

        return ParsedAttachment(
            kind=attachment.kind, media_type=normalized_media_type, page_count=None,
            image_width=width, image_height=height, extracted_text=extracted_text,
            # 云端 Vision 是平台固定能力，绝不再要求用户当前聊天模型具备图片能力。
            requires_vision_model=False,
            image_bytes=None,
            document_understanding_result=document_understanding_result,
            document_understanding_error=document_understanding_error,
            cloud_vision_result=cloud_vision_result,
        )

    def _has_usable_image_ocr_text(self, text: str) -> bool:
        """以保守阈值识别 Docling 是否至少恢复出可供后续对话使用的文字。"""

        compact_text = "".join(text.split())
        return (
            len(compact_text) >= self._MINIMUM_USABLE_IMAGE_OCR_CHARACTERS
            and len(set(compact_text)) >= 6
        )

    def _prepare_cloud_vision_input(
        self,
        attachment: AttachmentDescriptor,
        media_type: str,
        image_bytes: bytes,
    ) -> tuple[str, bytes]:
        """将大图压缩到云端接口安全尺寸，避免 Base64 放大导致请求无谓失败。"""

        if len(image_bytes) <= self._settings.max_cloud_vision_image_bytes:
            return media_type, image_bytes

        with Image.open(attachment.temporary_path) as image:
            normalized_image = image.convert("RGB")
            normalized_image.thumbnail((3840, 3840))
            for quality in (88, 78, 68, 58):
                output = BytesIO()
                normalized_image.save(
                    output,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                )
                compacted_bytes = output.getvalue()
                if len(compacted_bytes) <= self._settings.max_cloud_vision_image_bytes:
                    return "image/jpeg", compacted_bytes
        raise ValueError("图片压缩后仍超过云端图片理解服务的安全大小限制")

    @staticmethod
    def _create_image_pdf_for_ocr(
        attachment: AttachmentDescriptor,
    ) -> AttachmentDescriptor:
        """将单张截图转换为匿名单页 PDF，复用已验证的中文扫描件 OCR 路径。"""

        destination = attachment.temporary_path.parent / f"{uuid4().hex}.pdf"
        try:
            with Image.open(attachment.temporary_path) as image:
                # PDF 不支持透明通道；统一 RGB 可避免 PNG/BMP/TIFF 的格式分歧。
                image.convert("RGB").save(destination, "PDF", resolution=200.0)
            if destination.stat().st_size > attachment.size_bytes * 3:
                raise OSError("图片转换后的临时 PDF 过大")
            return AttachmentDescriptor(
                attachment_id=uuid4(),
                kind=attachment.kind,
                original_filename="image.pdf",
                media_type="application/pdf",
                size_bytes=destination.stat().st_size,
                temporary_path=destination,
                expires_at=attachment.expires_at,
            )
        except Exception:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise
