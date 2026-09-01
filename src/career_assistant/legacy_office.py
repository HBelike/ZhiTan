"""将旧版 Word/Excel 临时转换为 PDF 的受控 Gotenberg 客户端。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from src.career_assistant.contracts import AttachmentDescriptor
from src.career_assistant.settings import LegacyOfficeConversionSettings
from src.observability.langsmith_runtime import trace_operation


class LegacyOfficeConversionError(RuntimeError):
    """可安全展示的旧版 Office 转换异常，不含原始文件名、正文或上游响应。"""


class LegacyOfficeConverter(Protocol):
    """旧版 Office 转换的可替换契约，便于将来替换 Gotenberg 部署。"""

    def convert_to_pdf(self, attachment: AttachmentDescriptor) -> AttachmentDescriptor:
        """将当前 Turn 的 DOC/XLS 转为同一临时目录内的 PDF 描述符。"""


class GotenbergOfficeConverter:
    """复用 Gotenberg LibreOffice API 转换旧版 DOC/XLS，不在 Web 进程执行 Office。"""

    _LEGACY_FORMATS: set[tuple[str, str]] = {
        ("application/msword", ".doc"),
        ("application/vnd.ms-excel", ".xls"),
    }

    def __init__(
        self,
        settings: LegacyOfficeConversionSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """校验服务地址并创建可复用 HTTP 客户端。"""

        if not settings.enabled:
            raise ValueError("旧版 Office 转换服务未启用，不能创建转换客户端")
        if settings.request_timeout_seconds <= 0:
            raise ValueError("旧版 Office 转换服务超时时间必须大于零")
        if settings.max_converted_size_bytes <= 0:
            raise ValueError("旧版 Office 转换文件大小上限必须大于零")
        self._base_url = self._normalize_base_url(settings.service_base_url)
        self._max_converted_size_bytes = settings.max_converted_size_bytes
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            # Gotenberg 同为本机/Docker 内网服务，避免错误走系统代理。
            trust_env=False,
        )

    def close(self) -> None:
        """关闭本实例创建的 HTTP 客户端。"""

        if self._owns_client:
            self._client.close()

    def convert_to_pdf(self, attachment: AttachmentDescriptor) -> AttachmentDescriptor:
        """匿名上传旧版 Office 文件，接收 PDF 并保存在同一临时目录。"""

        suffix = attachment.temporary_path.suffix.lower()
        return trace_operation(
            run_name="career.document.legacy_office_convert",
            run_type="tool",
            inputs={
                "input_bytes": attachment.size_bytes,
            },
            metadata={
                "component": "legacy_office_converter",
                "format": suffix.removeprefix(".") or "unknown",
                "provider": "gotenberg",
                "stage": "document_conversion",
            },
            execute=lambda: self._convert_to_pdf(attachment),
            summarize=lambda converted: {
                "status": "completed",
                "output_bytes": converted.size_bytes,
                "format": "pdf",
            },
            tags=("career.document", "upstream.tool"),
        )

    def _convert_to_pdf(self, attachment: AttachmentDescriptor) -> AttachmentDescriptor:
        """执行一次真实转换；仅由带隐私边界的公开方法调用。"""

        attachment.validate()
        suffix = attachment.temporary_path.suffix.lower()
        if (attachment.media_type, suffix) not in self._LEGACY_FORMATS:
            raise ValueError("旧版 Office 转换仅接受 DOC 或 XLS 附件")
        if not attachment.temporary_path.is_file():
            raise LegacyOfficeConversionError("临时文档已不可用，请重新上传后再试")

        try:
            with attachment.temporary_path.open("rb") as source_file:
                response = self._client.post(
                    f"{self._base_url}/forms/libreoffice/convert",
                    headers={"Gotenberg-Output-Filename": "document"},
                    files={
                        "files": (
                            f"document{suffix}",
                            source_file,
                            attachment.media_type,
                        ),
                    },
                )
        except OSError as exc:
            raise LegacyOfficeConversionError("无法读取临时文档，请重新上传后再试") from exc
        except httpx.TimeoutException as exc:
            raise LegacyOfficeConversionError("旧版 Office 转换超时，请稍后重试") from exc
        except httpx.RequestError as exc:
            raise LegacyOfficeConversionError(
                "无法连接旧版 Office 转换服务，请稍后重试或改为上传 PDF",
            ) from exc

        if response.status_code >= 400:
            raise LegacyOfficeConversionError(self._http_error_message(response.status_code))
        pdf_bytes = response.content
        if not pdf_bytes.startswith(b"%PDF-"):
            raise LegacyOfficeConversionError("旧版 Office 转换服务未返回有效 PDF")
        if len(pdf_bytes) > self._max_converted_size_bytes:
            raise LegacyOfficeConversionError("转换后的 PDF 超过允许大小，请拆分文件后重试")

        target_path = attachment.temporary_path.with_name(
            f"{attachment.attachment_id.hex}-converted.pdf",
        )
        try:
            target_path.write_bytes(pdf_bytes)
        except OSError as exc:
            raise LegacyOfficeConversionError("无法暂存转换后的 PDF，请稍后重试") from exc

        return replace(
            attachment,
            original_filename="document.pdf",
            media_type="application/pdf",
            size_bytes=len(pdf_bytes),
            temporary_path=target_path,
        )

    @staticmethod
    def _normalize_base_url(value: str | None) -> str:
        """只接受 Gotenberg 根地址，避免部署配置意外拼入 API 路径。"""

        normalized_value = (value or "").strip().rstrip("/")
        parsed_url = urlparse(normalized_value)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("旧版 Office 转换服务地址必须是完整的 http 或 https URL")
        if parsed_url.path not in {"", "/"}:
            raise ValueError("旧版 Office 转换服务地址请填写根地址")
        return normalized_value

    @staticmethod
    def _http_error_message(status_code: int) -> str:
        """将 Gotenberg 状态码映射为不泄露文件内容的用户提示。"""

        if status_code == 400:
            return "旧版 Office 文档无法转换，请确认文件未损坏、未加密"
        if status_code == 413:
            return "旧版 Office 文档超过转换服务允许的大小，请压缩后重试"
        if status_code == 429:
            return "旧版 Office 转换服务当前繁忙，请稍后重试"
        if 500 <= status_code <= 599:
            return "旧版 Office 转换服务暂时异常，请稍后重试或改为上传 PDF"
        return f"旧版 Office 转换服务拒绝了本次请求（状态码 {status_code}）"
