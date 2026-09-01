"""对接独立文档理解服务的受控客户端。

本模块不在 Web 后端内安装 OCR 模型。它把重型版面分析和 OCR 放到可独立部署的
Docling 服务中，调用方只得到当前 Turn 内存可用的 Markdown 文本与非敏感处理状态。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

from src.career_assistant.contracts import AttachmentDescriptor
from src.career_assistant.settings import DocumentUnderstandingSettings
from src.observability.langsmith_runtime import trace_operation


class DocumentUnderstandingError(RuntimeError):
    """文档理解服务的安全异常。

    异常信息只描述可执行的服务状态，不拼接上游响应正文、文件名、OCR 原文或 API Key，
    因而可以安全展示给当前用户。
    """


@dataclass(frozen=True)
class DocumentUnderstandingResult:
    """文档理解服务返回的当前 Turn 临时结果。

    ``analysis_text`` 优先保留 Markdown，从而携带标题、列表、表格和阅读顺序；结果仅由
    ``AttachmentParser`` 传入本轮模型上下文，绝不直接持久化。
    """

    parser_name: str
    analysis_text: str
    status: str
    processing_time_seconds: float | None


class StructuredDocumentParser(Protocol):
    """可替换的结构化文档解析器契约。

    未来 PaddleOCR 兜底或托管文档服务只需实现该协议，无需修改 LangGraph 或附件清理逻辑。
    """

    def parse_document(
        self,
        attachment: AttachmentDescriptor,
    ) -> DocumentUnderstandingResult:
        """解析临时结构化文档，并返回仅供当前 Turn 使用的文本。"""


class DoclingServiceDocumentParser:
    """通过 Docling Serve 同步 API 解析文档与图片材料。

    客户端复用一个 ``httpx.Client`` 连接池，可安全由应用服务容器在多个请求间共享；
    解析调用本身是同步的，调用方应通过现有 SSE 状态事件告知用户处理进度。服务端不保留
    原始附件，临时文件仍由 ``TemporaryAttachmentStore`` 在 Turn 结束时删除。旧版 DOC/XLS
    必须由上层先转换为 PDF，避免请求不支持的 Docling 格式。
    """

    _PARSER_NAME = "docling-serve"
    _DOCUMENT_FORMATS: dict[tuple[str, str], str] = {
        ("application/pdf", ".pdf"): "pdf",
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".docx",
        ): "docx",
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xlsx",
        ): "xlsx",
        ("image/jpeg", ".jpg"): "image",
        ("image/jpeg", ".jpeg"): "image",
        ("image/png", ".png"): "image",
        ("image/webp", ".webp"): "image",
        ("image/bmp", ".bmp"): "image",
        ("image/x-ms-bmp", ".bmp"): "image",
        ("image/tiff", ".tif"): "image",
        ("image/tiff", ".tiff"): "image",
    }

    def __init__(
        self,
        settings: DocumentUnderstandingSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """校验配置并创建或接收可测试的 HTTP 客户端。"""

        if not settings.enabled:
            raise ValueError("Docling 文档理解服务未启用，不能创建解析客户端")
        if settings.request_timeout_seconds <= 0:
            raise ValueError("Docling 文档理解服务超时时间必须大于零")
        if settings.table_mode not in {"fast", "accurate"}:
            raise ValueError("Docling 表格模式仅支持 fast 或 accurate")

        self._settings = settings
        self._base_url = self._normalize_base_url(settings.service_base_url)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            # Docling 是宿主机或 Docker 内网服务。不能继承系统代理，否则本机
            # 127.0.0.1 请求会被错误转发到代理并表现为 502。
            trust_env=False,
        )

    def close(self) -> None:
        """关闭本实例创建的连接池；注入的测试客户端由调用方负责关闭。"""

        if self._owns_client:
            self._client.close()

    def parse_document(
        self,
        attachment: AttachmentDescriptor,
    ) -> DocumentUnderstandingResult:
        """将临时文档匿名上传到 Docling，并返回结构化 Markdown。

        输入只接受已通过 ``AttachmentDescriptor.validate`` 的本地 PDF、DOC/DOCX 或 XLS/XLSX；
        上传文件名固定为 ``document.<format>``，避免把原始文件名发送到解析服务。失败时抛出可安全展示的
        ``DocumentUnderstandingError``，由上层统一终止本轮并清理临时材料。
        """

        document_format = self._trace_document_format(attachment)
        return trace_operation(
            run_name="career.document.docling_parse",
            run_type="tool",
            inputs={
                "document_format": document_format,
                "input_bytes": attachment.size_bytes,
                "max_attempts": self._settings.max_attempts,
                "force_ocr": self._settings.force_ocr,
                "table_mode": self._settings.table_mode,
            },
            metadata={
                "component": "career_document_understanding",
                "parser": self._PARSER_NAME,
                "document_format": document_format,
                "privacy_mode": "metadata_only",
            },
            tags=("career", "document", "docling", "tool"),
            execute=lambda: self._parse_document_untraced(attachment),
            summarize=self._summarize_trace_result,
        )

    def _parse_document_untraced(
        self,
        attachment: AttachmentDescriptor,
    ) -> DocumentUnderstandingResult:
        """执行原有 Docling 解析流程；原始附件始终留在本进程与 Docling 服务间。"""

        attachment.validate()
        document_format = self._document_format_for(attachment)
        if not attachment.temporary_path.is_file():
            raise DocumentUnderstandingError("临时文档已不可用，请重新上传后再试")

        headers = self._headers()
        form_data = {
            "from_formats": document_format,
            "to_formats": "md",
            "do_ocr": "true",
            "force_ocr": str(
                self._settings.force_ocr
                if document_format in {"pdf", "image"}
                else False,
            ).lower(),
            "image_export_mode": "placeholder",
            "table_mode": self._settings.table_mode,
        }
        try:
            response = self._request_document(
                attachment,
                document_format=document_format,
                headers=headers,
                form_data=form_data,
            )
        except OSError as exc:
            raise DocumentUnderstandingError("无法读取临时文档，请重新上传后再试") from exc
        except httpx.TimeoutException as exc:
            raise DocumentUnderstandingError(
                "文档解析服务处理超时，请稍后重试或上传更小的文档",
            ) from exc
        except httpx.RequestError as exc:
            raise DocumentUnderstandingError(
                "无法连接文档解析服务，请确认 Docling 服务已启动后重试",
            ) from exc

        if response.status_code >= 400:
            raise DocumentUnderstandingError(
                self._http_error_message(response.status_code),
            )
        return self._result_from_response(response)

    @classmethod
    def _trace_document_format(cls, attachment: AttachmentDescriptor) -> str:
        """仅为 Trace 生成非敏感格式标签，不提前改变业务校验顺序。"""

        key = (attachment.media_type, attachment.temporary_path.suffix.lower())
        return cls._DOCUMENT_FORMATS.get(key, "unsupported")

    @staticmethod
    def _summarize_trace_result(
        result: DocumentUnderstandingResult,
    ) -> dict[str, str | int | float | None]:
        """返回可观测统计，明确排除 OCR 正文、文件标识与服务地址。"""

        return {
            "parser": result.parser_name,
            "status": result.status,
            "output_characters": len(result.analysis_text),
            "processing_time_seconds": result.processing_time_seconds,
        }

    def _request_document(
        self,
        attachment: AttachmentDescriptor,
        *,
        document_format: str,
        headers: dict[str, str],
        form_data: dict[str, str],
    ) -> httpx.Response:
        """以有限次数调用 Docling；只重试网络与服务端短暂异常。"""

        last_error: DocumentUnderstandingError | None = None
        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                with attachment.temporary_path.open("rb") as source_file:
                    response = self._client.post(
                        f"{self._base_url}/v1/convert/file",
                        headers=headers,
                        data=form_data,
                        files={
                            "files": (
                                self._anonymous_filename(attachment, document_format),
                                source_file,
                                attachment.media_type,
                            ),
                        },
                    )
            except OSError:
                raise
            except httpx.TimeoutException as exc:
                last_error = DocumentUnderstandingError(
                    "文档解析服务处理超时，请稍后重试或上传更小的文档。",
                )
            except httpx.RequestError as exc:
                last_error = DocumentUnderstandingError(
                    "无法连接文档解析服务，请确认 Docling 服务已启动后重试",
                )
            else:
                if response.status_code < 400:
                    return response
                last_error = DocumentUnderstandingError(
                    self._http_error_message(response.status_code),
                )
                if not self._is_retryable_status(response.status_code):
                    raise last_error

            if attempt < self._settings.max_attempts:
                time.sleep(self._settings.retry_backoff_seconds)

        if last_error is None:
            raise RuntimeError("Docling 请求未产生结果")
        raise last_error

    def parse_pdf(
        self,
        attachment: AttachmentDescriptor,
    ) -> DocumentUnderstandingResult:
        """兼容既有调用方的 PDF 入口，实际统一走通用文档解析。"""

        if attachment.media_type != "application/pdf":
            raise ValueError("parse_pdf 仅接受 PDF 附件")
        return self.parse_document(attachment)

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        """非幂等文档调用仅在拥塞和服务端故障时重试一次。"""

        return status_code == 429 or 500 <= status_code <= 599

    @classmethod
    def _document_format_for(cls, attachment: AttachmentDescriptor) -> str:
        """根据已双重校验的 MIME 与扩展名确定 Docling 输入格式。"""

        key = (attachment.media_type, attachment.temporary_path.suffix.lower())
        document_format = cls._DOCUMENT_FORMATS.get(key)
        if document_format is None:
            raise ValueError(
                "Docling 直连仅支持 PDF、DOCX、XLSX 和常见图片；"
                "旧版 DOC/XLS 需先转换为 PDF"
            )
        return document_format

    @staticmethod
    def _anonymous_filename(
        attachment: AttachmentDescriptor,
        document_format: str,
    ) -> str:
        """生成不含原始文件名的上传名称，并为图片保留可识别的后缀。"""

        if document_format != "image":
            return f"document.{document_format}"
        return f"document{attachment.temporary_path.suffix.lower()}"

    def _headers(self) -> dict[str, str]:
        """按需添加服务端 API Key，绝不把 Key 写入异常或返回值。"""

        if not self._settings.api_key_env:
            return {}
        api_key = os.getenv(self._settings.api_key_env, "").strip()
        if not api_key:
            raise DocumentUnderstandingError(
                "文档解析服务已启用鉴权，但未配置对应 API Key 环境变量",
            )
        return {"X-Api-Key": api_key}

    def _result_from_response(
        self,
        response: httpx.Response,
    ) -> DocumentUnderstandingResult:
        """只接受 Docling Serve 的成功响应，并检查 Markdown 是否可供模型分析。"""

        try:
            payload = response.json()
        except ValueError as exc:
            raise DocumentUnderstandingError("文档解析服务返回格式异常") from exc
        if not isinstance(payload, dict):
            raise DocumentUnderstandingError("文档解析服务返回格式异常")

        status = payload.get("status")
        if status not in {"success", "partial_success"}:
            raise DocumentUnderstandingError("文档解析服务未能完成该文档的识别")

        document = payload.get("document")
        if not isinstance(document, dict):
            raise DocumentUnderstandingError("文档解析服务没有返回文档内容")
        markdown = document.get("md_content")
        text = document.get("text_content")
        analysis_text = markdown if isinstance(markdown, str) and markdown.strip() else text
        if not isinstance(analysis_text, str) or not analysis_text.strip():
            raise DocumentUnderstandingError(
                "文档解析完成但未识别到可分析文本；请上传更清晰的原件或图片简历",
            )

        processing_time = payload.get("processing_time")
        normalized_processing_time = (
            float(processing_time)
            if isinstance(processing_time, int | float)
            else None
        )
        return DocumentUnderstandingResult(
            parser_name=self._PARSER_NAME,
            analysis_text=analysis_text.strip(),
            status=status,
            processing_time_seconds=normalized_processing_time,
        )

    @staticmethod
    def _normalize_base_url(value: str | None) -> str:
        """校验并规范化服务根地址，避免把具体 API 路径重复拼接。"""

        normalized_value = (value or "").strip().rstrip("/")
        parsed_url = urlparse(normalized_value)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Docling 服务地址必须是完整的 http 或 https URL")
        if parsed_url.path not in {"", "/"}:
            raise ValueError("Docling 服务地址请填写根地址，不要包含 /v1/convert/file")
        return normalized_value

    @staticmethod
    def _http_error_message(status_code: int) -> str:
        """将服务状态映射为不会泄露请求正文的用户提示。"""

        if status_code == 400:
            return "文档解析服务未接受该文档，请确认文件未损坏、未加密且格式受支持"
        if status_code in {401, 403}:
            return "文档解析服务鉴权失败，请检查服务端 API Key 配置"
        if status_code == 413:
            return "文档超出解析服务允许的大小，请压缩后重新上传"
        if status_code == 429:
            return "文档解析服务当前繁忙，请稍后重试"
        if 500 <= status_code <= 599:
            return "文档解析服务暂时异常，请稍后重试"
        return f"文档解析服务拒绝了本次请求（状态码 {status_code}）"
