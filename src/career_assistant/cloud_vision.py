"""平台内置的可配置云端图片理解适配器。

图片理解是求职助手的基础能力，而非用户在聊天页选择的模型能力。该模块只接收
当前 Turn 的临时图片字节：Docling OCR 质量不足时，按服务端配置调用一个
OpenAI-compatible 视觉模型，并将结构化文本交回输入图。原图、Base64、API Key
和上游原始响应都不会被持久化。
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

from src.career_assistant.settings import (
    CloudVisionConnectionSettings,
    CloudVisionSettings,
)
from src.observability.langsmith_runtime import trace_operation


class CloudVisionError(RuntimeError):
    """可安全展示给前端的图片理解错误，不包含密钥或上游响应正文。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        """记录是否可按管理员明确配置的备用连接继续尝试。"""

        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class CloudVisionResult:
    """单张图片理解的临时结果，仅在当前 Turn 内传递。"""

    analysis_text: str
    provider_key: str
    model_id: str


class CloudVisionImageParser(Protocol):
    """供附件解析器依赖的最小图片理解契约，便于测试与替换 Provider。"""

    def analyze_image(self, media_type: str, image_bytes: bytes) -> CloudVisionResult:
        """返回结构化的图片理解文本；失败时抛出 ``CloudVisionError``。"""


class OpenAICompatibleCloudVisionClient:
    """调用一条 OpenAI-compatible 图片理解连接。

    该类只知道连接协议，不把 Qwen、Gemini 等服务商名称写进业务逻辑。谁被选为
    主连接由 ``CloudVisionRouter`` 和部署 YAML 决定，保证模型更替不影响附件链路。
    """

    _PROMPT = """你是求职助手的图片材料解析器，只负责忠实理解当前图片，不与用户闲聊。
请使用中文 Markdown 输出以下信息：
1. 图片类型与可见结构（简历、岗位截图、表格、证书、面经或其他）；
2. 可辨认文字，保留标题、列表、表格的阅读顺序；
3. 与求职有关的字段：职位、公司、职责、技能、经历、教育、项目、成果、薪资或面试问题；
4. 表格按“字段：值”整理；
5. 看不清、可能识别错误或缺失的内容必须标注“待确认”，绝不能编造。
不要描述 API、模型或处理流程。"""

    def __init__(
        self,
        settings: CloudVisionConnectionSettings,
        client: httpx.Client | None = None,
    ) -> None:
        """读取单条服务端连接并创建可复用 HTTP 连接池。"""

        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
        )

    def close(self) -> None:
        """在 FastAPI 退出时释放本连接创建的 HTTP 客户端。"""

        if self._owns_client:
            self._client.close()

    def analyze_image(self, media_type: str, image_bytes: bytes) -> CloudVisionResult:
        """在 metadata-only Span 内执行单次视觉 Provider 调用。"""

        return trace_operation(
            run_name="career.vision.provider_call",
            run_type="llm",
            execute=lambda: self._analyze_image(media_type, image_bytes),
            summarize=lambda result: {
                "provider": result.provider_key,
                "model": result.model_id,
                "output_characters": len(result.analysis_text),
                "has_output": bool(result.analysis_text),
            },
            metadata={
                "provider": self._settings.provider_key,
                "model": self._settings.model_id,
                "media_type": media_type,
                "image_bytes": len(image_bytes),
                "privacy_mode": "metadata_only",
            },
            tags=("career", "vision", "provider", "privacy:metadata-only"),
        )

    def _analyze_image(self, media_type: str, image_bytes: bytes) -> CloudVisionResult:
        """调用当前连接对应的视觉模型并返回可送入文本模型的结果。"""

        if not self._settings.enabled:
            raise CloudVisionError("当前图片理解连接未启用")
        if not image_bytes:
            raise CloudVisionError("图片内容为空，无法进行理解")
        credential = os.getenv(self._settings.api_key_env, "").strip()
        if not credential:
            raise CloudVisionError("平台图片理解服务尚未配置 API Key")
        base_url = os.getenv(self._settings.api_base_url_env, "").strip().rstrip("/")
        if not base_url:
            raise CloudVisionError("平台图片理解服务尚未配置 API Host")
        parsed_url = urlparse(base_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise CloudVisionError("平台图片理解服务的 API Host 必须是 HTTPS 地址")

        image_data_url = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        try:
            response = self._client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": self._PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_data_url},
                                },
                            ],
                        },
                    ],
                    "temperature": 0.1,
                    "max_tokens": self._settings.max_completion_tokens,
                },
            )
        except httpx.TimeoutException as exc:
            raise CloudVisionError(
                "云端图片理解超时，请稍后重试或上传更清晰的图片",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise CloudVisionError(
                "无法访问云端图片理解服务，请检查服务器网络与 API Host",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            raise self._http_error(response.status_code)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise CloudVisionError("云端图片理解服务返回格式异常") from exc

        analysis_text = self._content_text(content)
        if not analysis_text:
            raise CloudVisionError("云端图片理解服务未返回可用内容")
        return CloudVisionResult(
            analysis_text=analysis_text.strip(),
            provider_key=self._settings.provider_key,
            model_id=self._settings.model_id,
        )

    @staticmethod
    def _content_text(content: object) -> str:
        """兼容 OpenAI-compatible 的字符串与分段文本返回。"""

        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(
                item.get("text", "").strip()
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ).strip()
        return ""

    @staticmethod
    def _http_error(status_code: int) -> CloudVisionError:
        """把常见上游状态映射为可行动且不泄露 Secret 的提示。"""

        if status_code == 400:
            return CloudVisionError(
                "云端图片理解未接受请求，请检查模型 ID、图片格式与 API Host",
            )
        if status_code == 401:
            return CloudVisionError(
                "云端图片理解 API Key 无效或已失效，请更新服务器端配置",
            )
        if status_code == 403:
            return CloudVisionError(
                "当前云端图片理解 Key 没有调用该模型的权限，请在供应商控制台开通模型",
            )
        if status_code == 404:
            return CloudVisionError(
                "未找到云端图片理解接口或模型，请检查 API Host 与模型 ID",
                retryable=True,
            )
        if status_code == 413:
            return CloudVisionError(
                "图片发送给云端服务时体积过大，请上传更小或更清晰的图片",
            )
        if status_code == 429:
            return CloudVisionError(
                "云端图片理解服务当前触发限流或额度不足，请稍后重试",
                retryable=True,
            )
        if 500 <= status_code <= 599:
            return CloudVisionError(
                "云端图片理解服务暂时异常，请稍后重试",
                retryable=True,
            )
        return CloudVisionError(f"云端图片理解服务拒绝本次请求（状态码 {status_code}）")


class CloudVisionRouter:
    """按部署配置选择视觉连接，并仅对显式允许的备用连接做故障回退。"""

    def __init__(self, settings: CloudVisionSettings) -> None:
        """预创建主连接与备用连接，避免请求期重新读取配置或 Secret。"""

        if not settings.enabled:
            raise ValueError("未启用图片理解服务时不能创建 CloudVisionRouter")
        self._settings = settings
        self._connections = settings.resolution_order()
        self._clients = tuple(
            OpenAICompatibleCloudVisionClient(connection)
            for connection in self._connections
        )

    def close(self) -> None:
        """释放主连接和所有显式备用连接的 HTTP 资源。"""

        for client in self._clients:
            client.close()

    def analyze_image(self, media_type: str, image_bytes: bytes) -> CloudVisionResult:
        """用一个 Router Span 覆盖主连接、重试与显式备用连接。"""

        return trace_operation(
            run_name="career.vision.route",
            run_type="chain",
            execute=lambda: self._analyze_image(media_type, image_bytes),
            summarize=lambda result: {
                "provider": result.provider_key,
                "model": result.model_id,
                "output_characters": len(result.analysis_text),
                "has_output": bool(result.analysis_text),
            },
            metadata={
                "max_attempts": self._settings.max_attempts,
                "connection_count": len(self._clients),
                "media_type": media_type,
                "image_bytes": len(image_bytes),
                "privacy_mode": "metadata_only",
            },
            tags=("career", "vision", "router", "privacy:metadata-only"),
        )

    def _analyze_image(self, media_type: str, image_bytes: bytes) -> CloudVisionResult:
        """优先主模型；只有可恢复错误才转向管理员指定的下一条连接。"""

        last_error: CloudVisionError | None = None
        for index, client in enumerate(self._clients):
            for attempt in range(1, self._settings.max_attempts + 1):
                try:
                    return client.analyze_image(media_type, image_bytes)
                except CloudVisionError as exc:
                    last_error = exc
                    if not exc.retryable:
                        raise
                    if attempt < self._settings.max_attempts:
                        time.sleep(self._settings.retry_backoff_seconds)
                        continue
                    has_next_connection = index < len(self._clients) - 1
                    if not has_next_connection:
                        raise
        if last_error is not None:
            raise last_error
        raise CloudVisionError("平台图片理解服务未配置可用连接")


# 兼容既有 Python 导入；新代码应使用协议无关的 OpenAICompatibleCloudVisionClient。
QwenCloudVisionClient = OpenAICompatibleCloudVisionClient
