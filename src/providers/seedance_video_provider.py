from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.config.config_manager import AppConfig
from src.observability.langsmith_runtime import trace_llm_call


class SeedanceVideoApiError(RuntimeError):
    """Seedance 视频接口调用失败。"""


@dataclass(frozen=True)
class SeedanceVideoTaskResult:
    """Seedance 创建视频任务后的返回结果。"""

    task_id: str
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class SeedanceVideoTaskStatus:
    """Seedance 视频任务状态查询结果。"""

    task_id: str
    status: str
    video_url: str | None
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class SeedanceDownloadedVideo:
    """Seedance 视频下载结果。"""

    output_path: Path
    source_url: str
    content_type: str | None
    size_bytes: int


class SeedanceVideoProvider:
    """封装 Seedance 视频生成 API 的真实调用边界。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def has_api_key(self) -> bool:
        """检查视频 API Key 是否已经配置。"""
        return bool(os.getenv(self.config.video_api_key_env, "").strip())

    def create_video_task(
        self,
        prompt: str,
        image_urls: list[str] | None = None,
        duration_seconds: int | None = None,
    ) -> SeedanceVideoTaskResult:
        """提交 Seedance 视觉视频任务；默认纯文生，仅在显式启用时附带参考图。"""
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Seedance 视频 prompt 不能为空")

        normalized_image_urls = [url.strip() for url in (image_urls or []) if url.strip()]

        api_key = self._api_key()
        payload = self._build_generation_payload(
            prompt=normalized_prompt,
            image_urls=normalized_image_urls,
            duration_seconds=duration_seconds,
        )
        response_payload = trace_llm_call(
            run_name="media.video.seedance.submit",
            provider="volcengine-ark",
            model=self.config.video_model,
            message_count=1 + len(normalized_image_urls),
            input_characters=len(normalized_prompt),
            execute=lambda: self._post_json(
                url=self._build_generation_url(),
                payload=payload,
                api_key=api_key,
            ),
            summarize=self._trace_summary,
        )
        task_id = self._extract_task_id(response_payload)
        return SeedanceVideoTaskResult(
            task_id=task_id,
            raw_response=response_payload,
        )

    @staticmethod
    def _trace_summary(response_payload: dict[str, Any]) -> dict[str, Any]:
        """只记录任务是否创建及服务端用量，不上传任务 ID 或媒体 URL。"""

        data = response_payload.get("data")
        task_id_candidates = [
            response_payload.get("id"),
            response_payload.get("task_id"),
            response_payload.get("taskId"),
        ]
        if isinstance(data, dict):
            task_id_candidates.extend([data.get("id"), data.get("task_id"), data.get("taskId")])
        usage = response_payload.get("usage")
        return {
            "task_created": any(isinstance(value, str) and bool(value.strip()) for value in task_id_candidates),
            "usage": usage if isinstance(usage, dict) else {},
        }

    def create_reference_video_task(self, prompt: str, image_urls: list[str]) -> SeedanceVideoTaskResult:
        """兼容旧调用方；新代码应使用 ``create_video_task``。"""
        return self.create_video_task(prompt=prompt, image_urls=image_urls)

    def get_task_status(self, task_id: str) -> SeedanceVideoTaskStatus:
        """查询视频生成任务状态。"""
        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise ValueError("Seedance task_id 不能为空")

        api_key = self._api_key()
        url = self._build_task_status_url(normalized_task_id)
        headers = self._build_headers(api_key)
        try:
            response = requests.get(url, headers=headers, timeout=self.config.video_timeout_seconds)
        except requests.RequestException as exc:
            raise SeedanceVideoApiError(f"Seedance 任务状态查询失败：{exc}") from exc

        payload = self._parse_response(response)
        return SeedanceVideoTaskStatus(
            task_id=normalized_task_id,
            status=self._extract_status(payload),
            video_url=self._extract_video_url(payload),
            raw_response=payload,
        )

    def download_video(self, video_url: str, output_path: Path) -> SeedanceDownloadedVideo:
        """把 Seedance 返回的视频 URL 下载到本地 outputs 目录。"""
        normalized_url = video_url.strip()
        if not normalized_url.startswith(("http://", "https://")):
            raise ValueError("视频下载 URL 必须是 http 或 https")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with requests.get(normalized_url, stream=True, timeout=self.config.video_timeout_seconds) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type")
                size_bytes = 0
                with output_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        file.write(chunk)
                        size_bytes += len(chunk)
        except requests.RequestException as exc:
            raise SeedanceVideoApiError(f"Seedance 视频下载失败：{exc}") from exc
        except OSError as exc:
            raise SeedanceVideoApiError(f"Seedance 视频保存失败：{exc}") from exc

        if size_bytes <= 0:
            raise SeedanceVideoApiError("Seedance 视频下载结果为空")

        return SeedanceDownloadedVideo(
            output_path=output_path,
            source_url=normalized_url,
            content_type=content_type,
            size_bytes=size_bytes,
        )

    def _api_key(self) -> str:
        """读取视频 API Key。"""
        api_key = os.getenv(self.config.video_api_key_env, "").strip()
        if not api_key:
            raise SeedanceVideoApiError(f"{self.config.video_api_key_env} 未配置，无法调用视频生成 API")
        return api_key

    def _build_generation_payload(
        self,
        prompt: str,
        image_urls: list[str],
        duration_seconds: int | None,
    ) -> dict[str, Any]:
        """构造火山方舟内容生成任务请求体。"""

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": prompt.strip(),
            }
        ]
        for image_url in image_urls:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url,
                    },
                    "role": "reference_image",
                }
            )

        payload: dict[str, Any] = {
            "model": self.config.video_model,
            "content": content,
            "generate_audio": self.config.video_generate_audio,
            "ratio": self.config.video_aspect_ratio,
            "duration": duration_seconds or self.config.video_clip_duration_seconds,
            "watermark": self.config.video_watermark,
        }
        if self.config.video_return_last_frame:
            payload["return_last_frame"] = True
        if self.config.video_seed >= 0:
            payload["seed"] = self.config.video_seed
        return payload

    def _append_prompt_parameters(self, prompt: str, duration_seconds: int | None = None) -> str:
        """把 Ark Seedance 常用视频参数稳定追加到文本 prompt 尾部。"""

        parameters = [
            f"--ratio {self.config.video_aspect_ratio}",
            f"--resolution {self.config.video_resolution}",
            f"--duration {duration_seconds or self.config.video_clip_duration_seconds}",
            f"--watermark {str(self.config.video_watermark).lower()}",
        ]
        if self.config.video_return_last_frame:
            parameters.append("--return_last_frame true")
        if self.config.video_seed >= 0:
            parameters.append(f"--seed {self.config.video_seed}")
        return f"{prompt.strip()} {' '.join(parameters)}"

    def _post_json(self, url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        """发送 JSON POST 请求。"""
        try:
            response = requests.post(
                url,
                headers=self._build_headers(api_key),
                json=payload,
                timeout=self.config.video_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SeedanceVideoApiError(f"Seedance 请求失败：{exc}") from exc

        return self._parse_response(response)

    def _parse_response(self, response: requests.Response) -> dict[str, Any]:
        """解析接口响应并处理 HTTP 错误。"""
        try:
            payload = response.json()
        except ValueError as exc:
            raise SeedanceVideoApiError("Seedance 返回内容不是合法 JSON") from exc

        if response.status_code >= 400:
            raise SeedanceVideoApiError(
                f"Seedance 返回错误状态码 {response.status_code}：{self._sanitize_error_payload(payload)}"
            )

        if not isinstance(payload, dict):
            raise SeedanceVideoApiError("Seedance JSON 响应不是对象")

        return payload

    def _build_generation_url(self) -> str:
        """拼接视频生成提交接口地址。"""
        return f"{self.config.video_base_url.rstrip('/')}/{self.config.video_generation_endpoint.lstrip('/')}"

    def _build_task_status_url(self, task_id: str) -> str:
        """拼接视频任务状态查询接口地址。"""
        endpoint = self.config.video_task_status_endpoint_template.replace("{task_id}", task_id)
        return f"{self.config.video_base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def _build_headers(self, api_key: str) -> dict[str, str]:
        """构造视频接口请求头。"""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _extract_task_id(self, payload: dict[str, Any]) -> str:
        """从不同 API 返回形态中提取任务 ID。"""
        candidates = [
            payload.get("id"),
            payload.get("task_id"),
            payload.get("taskId"),
        ]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("id"), data.get("task_id"), data.get("taskId")])

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        raise SeedanceVideoApiError("Seedance 响应缺少任务 ID")

    def _extract_status(self, payload: dict[str, Any]) -> str:
        """从任务状态响应中提取状态。"""
        candidates: list[Any] = [payload.get("status"), payload.get("state")]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("status"), data.get("state")])

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        return "unknown"

    def _extract_video_url(self, payload: dict[str, Any]) -> str | None:
        """从任务状态响应中提取最终视频 URL。"""
        candidates = [
            payload.get("video_url"),
            payload.get("videoUrl"),
            payload.get("url"),
        ]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("video_url"), data.get("videoUrl"), data.get("url")])
            output = data.get("output")
            if isinstance(output, dict):
                candidates.extend([output.get("video_url"), output.get("videoUrl"), output.get("url")])
        content = payload.get("content")
        if isinstance(content, dict):
            candidates.extend([content.get("video_url"), content.get("videoUrl"), content.get("url")])
            video_url = content.get("video_url")
            if isinstance(video_url, str):
                candidates.append(video_url)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                candidates.extend([item.get("video_url"), item.get("videoUrl"), item.get("url")])
                nested_video = item.get("video")
                if isinstance(nested_video, dict):
                    candidates.extend(
                        [nested_video.get("video_url"), nested_video.get("videoUrl"), nested_video.get("url")]
                    )

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    def _sanitize_error_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """脱敏远程错误响应，避免日志记录账号号段或其它长标识。"""

        sanitized = dict(payload)
        error = sanitized.get("error")
        if isinstance(error, dict):
            sanitized_error = dict(error)
            message = str(sanitized_error.get("message", ""))
            if message:
                message = re.sub(r"account\s+\d+", "account <redacted>", message, flags=re.IGNORECASE)
                message = re.sub(r"Request id:\s*[A-Za-z0-9_-]+", "Request id: <redacted>", message)
                sanitized_error["message"] = message
            sanitized["error"] = sanitized_error
        return sanitized
