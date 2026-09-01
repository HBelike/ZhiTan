from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.config.config_manager import AppConfig
from src.observability.langsmith_runtime import trace_llm_call


MAX_REQUEST_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 0.8
TRANSIENT_REQUEST_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
TRANSIENT_DOWNLOAD_ERRORS = (*TRANSIENT_REQUEST_ERRORS, OSError)


class SeedreamApiError(RuntimeError):
    """Seedream 图片接口调用失败。"""


@dataclass(frozen=True)
class SeedreamImageResult:
    """Seedream 成功生成并落盘后的图片结果。"""

    output_path: Path
    source_url: str | None
    raw_response: dict[str, Any]


class SeedreamProvider:
    """封装 Seedream 文生图真实 API 调用。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def has_api_key(self) -> bool:
        """检查图片服务密钥是否已经配置。"""
        return bool(os.getenv(self.config.image_api_key_env, "").strip())

    def generate_image(self, prompt: str, output_path: Path) -> SeedreamImageResult:
        """调用 Seedream 生成图片并保存到本地。"""
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Seedream prompt 不能为空")

        api_key = os.getenv(self.config.image_api_key_env, "").strip()
        if not api_key:
            raise SeedreamApiError(f"{self.config.image_api_key_env} 未配置，无法调用 Seedream")

        endpoint = self._build_endpoint()
        payload = {
            "model": self.config.image_model,
            "prompt": normalized_prompt,
            "size": self.config.image_size,
            "n": self.config.image_n,
            "response_format": self.config.image_response_format,
            "watermark": self.config.image_watermark,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        response_payload = trace_llm_call(
            run_name="media.image.seedream.generate",
            provider="volcengine-ark",
            model=self.config.image_model,
            message_count=1,
            input_characters=len(normalized_prompt),
            execute=lambda: self._request_generation(endpoint=endpoint, headers=headers, payload=payload),
            summarize=self._trace_summary,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        source_url = self._save_first_image(response_payload, output_path)
        return SeedreamImageResult(
            output_path=output_path,
            source_url=source_url,
            raw_response=response_payload,
        )

    def _request_generation(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """只执行 Seedream 模型 HTTP 请求；图片下载不属于 LLM Trace。"""

        use_environment_proxy = True
        response: requests.Response | None = None
        last_error: requests.RequestException | None = None

        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                with requests.Session() as session:
                    session.trust_env = use_environment_proxy
                    response = session.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=self.config.image_timeout_seconds,
                    )
            except TRANSIENT_REQUEST_ERRORS as exc:
                last_error = exc
                if isinstance(exc, requests.exceptions.ProxyError):
                    # 系统代理是可选网络路径；代理断开后改为直连，避免重复撞击故障代理。
                    use_environment_proxy = False
                if attempt < MAX_REQUEST_ATTEMPTS:
                    self._sleep_before_retry(attempt)
                    continue
                raise SeedreamApiError(
                    f"Seedream 请求失败，已尝试 {MAX_REQUEST_ATTEMPTS} 次：{exc}"
                ) from exc
            except requests.RequestException as exc:
                raise SeedreamApiError(f"Seedream 请求失败：{exc}") from exc

            if self._is_retryable_status(response.status_code):
                if attempt < MAX_REQUEST_ATTEMPTS:
                    self._sleep_before_retry(attempt)
                    continue
                raise SeedreamApiError(
                    f"Seedream 返回错误状态码 {response.status_code}，"
                    f"已尝试 {MAX_REQUEST_ATTEMPTS} 次：{response.text[:500]}"
                )
            break

        if response is None:
            raise SeedreamApiError(
                f"Seedream 请求失败，已尝试 {MAX_REQUEST_ATTEMPTS} 次：{last_error}"
            ) from last_error
        if response.status_code >= 400:
            raise SeedreamApiError(f"Seedream 返回错误状态码 {response.status_code}：{response.text[:500]}")

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise SeedreamApiError("Seedream 返回内容不是合法 JSON") from exc
        if not isinstance(response_payload, dict):
            raise SeedreamApiError("Seedream JSON 响应不是对象")
        return response_payload

    @staticmethod
    def _trace_summary(response_payload: dict[str, Any]) -> dict[str, Any]:
        """返回图片响应的匿名计数和用量，不上传 URL、Base64 或生成图片。"""

        data = response_payload.get("data")
        usage = response_payload.get("usage")
        return {
            "image_count": len(data) if isinstance(data, list) else 0,
            "usage": usage if isinstance(usage, dict) else {},
        }

    def _build_endpoint(self) -> str:
        """拼接图片生成接口地址。"""
        return f"{self.config.image_base_url.rstrip('/')}/{self.config.image_generations_endpoint.lstrip('/')}"

    def _save_first_image(self, response_payload: dict[str, Any], output_path: Path) -> str | None:
        """从接口响应中取第一张图片，支持 url 和 b64_json 两种返回方式。"""
        data = response_payload.get("data")
        if not isinstance(data, list) or not data:
            raise SeedreamApiError("Seedream 响应缺少 data[0]")

        first_item = data[0]
        if not isinstance(first_item, dict):
            raise SeedreamApiError("Seedream 响应 data[0] 不是对象")

        image_url = first_item.get("url")
        if isinstance(image_url, str) and image_url.strip():
            self._download_image(image_url.strip(), output_path)
            return image_url.strip()

        b64_json = first_item.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            output_path.write_bytes(base64.b64decode(b64_json))
            return None

        raise SeedreamApiError("Seedream 响应既没有 url，也没有 b64_json")

    def _download_image(self, image_url: str, output_path: Path) -> None:
        """下载 Seedream 返回的签名图片 URL，并处理对象存储的瞬时 TLS 断连。"""

        temporary_path = output_path.with_name(f"{output_path.name}.part")
        last_error: Exception | None = None
        use_environment_proxy = True

        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                # 不复用可能已被对象存储关闭的连接，避免半文件被误判为成功结果。
                with requests.Session() as session:
                    session.trust_env = use_environment_proxy
                    with session.get(
                        image_url,
                        headers={"Connection": "close"},
                        stream=True,
                        timeout=(15, self.config.image_timeout_seconds),
                    ) as response:
                        if self._is_retryable_status(response.status_code):
                            last_error = SeedreamApiError(
                                f"Seedream 图片下载返回错误状态码 {response.status_code}"
                            )
                            self._remove_partial_file(temporary_path)
                            if attempt < MAX_REQUEST_ATTEMPTS:
                                self._sleep_before_retry(attempt)
                                continue
                            break
                        if response.status_code >= 400:
                            raise SeedreamApiError(f"Seedream 图片下载返回错误状态码 {response.status_code}")
                        with temporary_path.open("wb") as handle:
                            for chunk in response.iter_content(chunk_size=1024 * 256):
                                if chunk:
                                    handle.write(chunk)

                if not temporary_path.exists() or temporary_path.stat().st_size <= 0:
                    raise OSError("图片下载结果为空")
                temporary_path.replace(output_path)
                return
            except SeedreamApiError:
                self._remove_partial_file(temporary_path)
                raise
            except TRANSIENT_DOWNLOAD_ERRORS as exc:
                last_error = exc
                if isinstance(exc, requests.exceptions.ProxyError):
                    use_environment_proxy = False
                self._remove_partial_file(temporary_path)
                if attempt < MAX_REQUEST_ATTEMPTS:
                    self._sleep_before_retry(attempt)
            except requests.RequestException as exc:
                self._remove_partial_file(temporary_path)
                raise SeedreamApiError(f"Seedream 图片下载失败：{exc.__class__.__name__}") from exc

        detail = self._download_failure_detail(last_error)
        raise SeedreamApiError(
            f"Seedream 图片下载失败，已尝试 {MAX_REQUEST_ATTEMPTS} 次：{detail}"
        ) from last_error

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        """只重试限流和服务端故障；其他 4xx 由调用方立即修正。"""

        return status_code == 429 or 500 <= status_code <= 599

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        """按指数退避等待，最多等待 6.4 秒，避免连续冲击远端服务。"""

        time.sleep(min(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), 6.4))

    @staticmethod
    def _remove_partial_file(path: Path) -> None:
        """删除失败下载留下的临时文件。"""

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _download_failure_detail(error: Exception | None) -> str:
        """转换网络错误，不将对象存储的签名 URL 写入任务错误信息。"""

        if error is None:
            return "下载连接未返回有效结果"
        if isinstance(error, requests.exceptions.SSLError):
            return "TLS 连接被远端中断，请稍后重试"
        if isinstance(error, requests.exceptions.Timeout):
            return "下载超时，请稍后重试"
        if isinstance(error, requests.exceptions.ConnectionError):
            return "对象存储连接失败，请稍后重试"
        if isinstance(error, SeedreamApiError):
            return str(error)
        return error.__class__.__name__
