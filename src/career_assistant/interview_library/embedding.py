"""面经库的 OpenAI-compatible Embedding 适配器。

向量化服务是可选增强：资料入库永远不依赖这条网络链路。该模块只读取部署环境
中的凭证变量，不把 API Key 写入数据库、日志或面经 Markdown。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from src.career_assistant.settings import InterviewEmbeddingSettings
from src.observability.langsmith_runtime import trace_operation


class InterviewEmbeddingError(RuntimeError):
    """不包含密钥与上游响应正文的可控 Embedding 调用错误。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class EmbeddingBatch:
    """一次 API 调用返回的、按输入顺序排列的向量。"""

    vectors: tuple[tuple[float, ...], ...]


class OpenAICompatibleEmbeddingClient:
    """调用 ``POST /embeddings`` 的最小通用客户端。

    它不假定某个商业模型或免费额度长期存在。只要部署者在 YAML 与环境变量中提供
    已测试的 OpenAI-compatible Embedding 端点，面经库即可启用语义召回。
    """

    def __init__(self, settings: InterviewEmbeddingSettings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
        )

    @property
    def is_configured(self) -> bool:
        """判断是否具备显式启用、模型名、地址与凭证四项前提。"""

        if not self._settings.enabled:
            return False
        if not self._settings.model_id or not self._settings.api_key_env or not self._settings.api_base_url_env:
            return False
        return bool(
            os.getenv(self._settings.api_key_env, "").strip()
            and os.getenv(self._settings.api_base_url_env, "").strip()
        )

    @property
    def model_id(self) -> str | None:
        return self._settings.model_id

    @property
    def expected_dimensions(self) -> int:
        return self._settings.expected_dimensions

    def close(self) -> None:
        """关闭进程级 HTTP 连接池。"""

        self._client.close()

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        """将一批切片或查询文本转为同维向量，并严格保留输入顺序。"""

        if not texts:
            return EmbeddingBatch(vectors=())
        if len(texts) > self._settings.max_batch_size:
            raise ValueError("Embedding 批次超过配置上限")
        if not self.is_configured:
            raise InterviewEmbeddingError("面经向量服务尚未配置", retryable=False)

        credential = os.getenv(self._settings.api_key_env or "", "").strip()
        base_url = os.getenv(self._settings.api_base_url_env or "", "").strip().rstrip("/")
        if not base_url.startswith("https://"):
            raise InterviewEmbeddingError("Embedding API Base URL 必须使用 HTTPS")

        return trace_operation(
            run_name="interview.embedding.batch",
            run_type="embedding",
            execute=lambda: self._embed_texts(texts, credential, base_url),
            summarize=lambda result: {
                "vector_count": len(result.vectors),
                "dimensions": len(result.vectors[0]) if result.vectors else 0,
            },
            metadata={
                "provider": self._settings.provider_key,
                "model": self._settings.model_id,
                "text_count": len(texts),
                "input_characters": sum(len(text) for text in texts),
                "expected_dimensions": self._settings.expected_dimensions,
                "privacy_mode": "metadata_only",
            },
            tags=("career", "interview", "embedding", "privacy:metadata-only"),
        )

    def _embed_texts(
        self,
        texts: list[str],
        credential: str,
        base_url: str,
    ) -> EmbeddingBatch:
        """执行一次 Embedding HTTP 请求；追踪层只记录规模与维度。"""

        try:
            response = self._client.post(
                f"{base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.model_id,
                    "input": texts,
                    "encoding_format": "float",
                },
            )
        except httpx.TimeoutException as exc:
            raise InterviewEmbeddingError("面经向量服务响应超时", retryable=True) from exc
        except httpx.RequestError as exc:
            raise InterviewEmbeddingError("无法访问面经向量服务", retryable=True) from exc

        if response.status_code >= 400:
            raise InterviewEmbeddingError(
                self._http_error_message(response.status_code),
                retryable=response.status_code in {408, 409, 425, 429} or response.status_code >= 500,
            )
        try:
            payload = response.json()
            data = payload["data"]
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors = tuple(
                tuple(float(value) for value in item["embedding"])
                for item in ordered
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InterviewEmbeddingError("Embedding 服务返回格式异常") from exc

        if len(vectors) != len(texts):
            raise InterviewEmbeddingError("Embedding 返回数量与输入数量不一致")
        for vector in vectors:
            if len(vector) != self._settings.expected_dimensions:
                raise InterviewEmbeddingError(
                    "Embedding 向量维度与当前 pgvector 索引不一致；请检查模型配置",
                )
        return EmbeddingBatch(vectors=vectors)

    @staticmethod
    def _http_error_message(status_code: int) -> str:
        if status_code == 400:
            return "Embedding 服务未接受请求，请检查模型 ID 与 API Base URL"
        if status_code == 401:
            return "Embedding API Key 无效或已失效"
        if status_code == 403:
            return "当前 API Key 没有调用该 Embedding 模型的权限"
        if status_code == 404:
            return "未找到 Embedding 接口或模型，请检查 API Base URL"
        if status_code == 429:
            return "Embedding 服务已限流或额度不足"
        if 500 <= status_code <= 599:
            return "Embedding 服务暂时不可用"
        return f"Embedding 服务拒绝本次请求（状态码 {status_code}）"
