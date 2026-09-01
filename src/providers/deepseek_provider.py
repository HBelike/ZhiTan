from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

import requests

from src.config.config_manager import AppConfig
from src.observability.langsmith_runtime import trace_llm_call


class DeepSeekApiError(RuntimeError):
    """DeepSeek API 调用失败时抛出的业务异常。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"DeepSeek API 请求失败：status_code={status_code} message={message}")
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class DeepSeekMessage:
    """OpenAI-compatible chat message。"""

    role: str
    content: str


@dataclass(frozen=True)
class DeepSeekChatResponse:
    """DeepSeek ChatCompletions 响应摘要。"""

    content: str
    model: str
    raw_response: dict[str, Any]


class DeepSeekProvider:
    """负责调用 DeepSeek ChatCompletions API。"""

    empty_content_retry_count = 1

    def __init__(self, config: AppConfig, *, run_name: str = "platform.llm.deepseek.chat") -> None:
        self.config = config
        self.run_name = run_name
        self.logger = logging.getLogger(__name__)

    def chat(
        self,
        messages: list[DeepSeekMessage],
        *,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
        retry_empty_content: bool = True,
        trace_metadata: Mapping[str, Any] | None = None,
    ) -> DeepSeekChatResponse:
        """发送请求，并记录不含正文的 LangSmith 生命周期 Trace。

        绝大多数业务沿用全局模型配置。少数交互式轻量请求（例如 Skill 搜索关键词
        改写）可以收紧超时和输出长度，避免一个辅助 LLM 调用拖住整个页面。
        """

        return trace_llm_call(
            run_name=self.run_name,
            provider="deepseek-compatible",
            model=self.config.llm_model,
            message_count=len(messages),
            input_characters=sum(len(message.content) for message in messages),
            execute=lambda: self._chat_without_trace(
                messages,
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
                retry_empty_content=retry_empty_content,
            ),
            summarize=self._trace_summary,
            metadata=trace_metadata,
        )

    def _chat_without_trace(
        self,
        messages: list[DeepSeekMessage],
        *,
        timeout_seconds: float | None,
        max_tokens: int | None,
        retry_empty_content: bool,
    ) -> DeepSeekChatResponse:
        """执行原始模型请求；调用方不会将正文交给观测系统。"""
        api_key = self._read_api_key()
        request_timeout = self.config.llm_timeout_seconds if timeout_seconds is None else max(0.1, timeout_seconds)
        requested_max_tokens = self.config.llm_max_tokens if max_tokens is None else max(1, max_tokens)
        payload = {
            "model": self.config.llm_model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in messages
            ],
            "temperature": self.config.llm_temperature,
            "max_tokens": requested_max_tokens,
        }
        # DeepSeek V4 默认开启 thinking，推理内容与最终正文共享输出预算。
        # 周榜及媒体链路需要的是严格 JSON；显式关闭 thinking，避免内部推理
        # 先耗尽 max_tokens，导致 article_markdown 在字符串中途被截断。
        if self.config.llm_model.lower().startswith("deepseek-v4"):
            payload["thinking"] = {"type": "disabled"}
        if self.config.llm_response_format_json:
            payload["response_format"] = {"type": "json_object"}

        last_error: DeepSeekApiError | None = None
        total_attempts = 1 + (self.empty_content_retry_count if retry_empty_content else 0)
        for attempt_number in range(1, total_attempts + 1):
            response = requests.post(
                self._build_chat_url(),
                headers=self._build_headers(api_key),
                json=payload,
                timeout=request_timeout,
            )
            response_payload = self._parse_json_response(response)
            try:
                content = self._extract_message_content(
                    status_code=response.status_code,
                    response_payload=response_payload,
                )
            except DeepSeekApiError as exc:
                last_error = exc
                if exc.message == "DeepSeek 返回内容为空" and attempt_number < total_attempts:
                    self._log_empty_content_retry(
                        response_payload=response_payload,
                        attempt_number=attempt_number,
                        total_attempts=total_attempts,
                    )
                    continue
                raise

            return DeepSeekChatResponse(
                content=content,
                model=str(response_payload.get("model", self.config.llm_model)),
                raw_response=response_payload,
            )

        if last_error is not None:
            raise last_error
        raise DeepSeekApiError(0, "DeepSeek 请求没有返回可用结果")

    @staticmethod
    def _trace_summary(response: DeepSeekChatResponse) -> dict[str, Any]:
        """只返回模型、长度和用量，确保 LangSmith 不保留业务正文。"""

        raw_usage = response.raw_response.get("usage", {})
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        return {
            "model": response.model,
            "output_characters": len(response.content),
            "usage": {
                key: usage.get(key)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if usage.get(key) is not None
            },
        }

    def _extract_message_content(self, status_code: int, response_payload: dict[str, Any]) -> str:
        """从 OpenAI-compatible 响应中提取 message.content，并做结构校验。"""
        choices = response_payload.get("choices", [])
        if not choices:
            raise DeepSeekApiError(status_code, "DeepSeek 响应缺少 choices")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise DeepSeekApiError(status_code, "DeepSeek choices[0] 不是对象")

        message = first_choice.get("message", {})
        if not isinstance(message, dict):
            raise DeepSeekApiError(status_code, "DeepSeek message 不是对象")

        if first_choice.get("finish_reason") == "length":
            raise DeepSeekApiError(
                status_code,
                "DeepSeek 输出达到 max_tokens，返回内容被截断",
            )

        content = str(message.get("content", "")).strip()
        if not content:
            raise DeepSeekApiError(status_code, "DeepSeek 返回内容为空")

        return content

    def _log_empty_content_retry(
        self,
        response_payload: dict[str, Any],
        attempt_number: int,
        total_attempts: int,
    ) -> None:
        """记录空 content 补偿重试，不输出模型正文或密钥。"""

        choices = response_payload.get("choices", [])
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
        reasoning_content = ""
        if isinstance(message, dict):
            reasoning_content = str(message.get("reasoning_content", ""))
        self.logger.warning(
            "DeepSeek 返回空 content，将自动重试：attempt=%s/%s model=%s finish_reason=%s reasoning_content_length=%s",
            attempt_number,
            total_attempts,
            response_payload.get("model", self.config.llm_model),
            first_choice.get("finish_reason") if isinstance(first_choice, dict) else None,
            len(reasoning_content),
        )

    def _build_chat_url(self) -> str:
        """拼出 ChatCompletions 请求地址。"""
        base_url = self.config.llm_base_url.rstrip("/")
        endpoint = self.config.llm_chat_completions_endpoint.lstrip("/")
        return f"{base_url}/{endpoint}"

    def _build_headers(self, api_key: str) -> dict[str, str]:
        """构造 DeepSeek 请求头。"""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _read_api_key(self) -> str:
        """从环境变量读取 DeepSeek API Key。"""
        api_key = os.getenv(self.config.llm_api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"{self.config.llm_api_key_env} 未配置，无法调用 DeepSeek")
        return api_key

    def _parse_json_response(self, response: requests.Response) -> dict[str, Any]:
        """解析 JSON 响应，并处理非成功状态。"""
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeepSeekApiError(response.status_code, "DeepSeek 返回了非 JSON 响应") from exc

        if not response.ok:
            message = "未知错误"
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message", message))
                else:
                    message = str(payload.get("message", message))
            raise DeepSeekApiError(response.status_code, message)

        if not isinstance(payload, dict):
            raise DeepSeekApiError(response.status_code, "DeepSeek JSON 响应不是对象")

        return payload


def parse_json_object_from_text(text: str) -> dict[str, Any]:
    """从模型文本中解析 JSON 对象。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        parsed = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise
        parsed = json.loads(cleaned[start : end + 1], strict=False)

    if not isinstance(parsed, dict):
        raise ValueError("模型返回的 JSON 顶层必须是对象")
    return parsed
