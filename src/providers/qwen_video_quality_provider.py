from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src.config.config_manager import AppConfig
from src.observability.langsmith_runtime import trace_llm_call


class QwenVideoQualityApiError(RuntimeError):
    """Qwen 视觉质检 API 调用失败。"""


@dataclass(frozen=True)
class VideoQualityAssessment:
    """模型对一个视频片段关键帧给出的可追溯质检结论。"""

    score: int
    verdict: str
    issues: list[str]
    regeneration_instruction: str
    summary: str
    raw_response: dict[str, Any]


class QwenVideoQualityProvider:
    """通过 Qwen-VL 的 OpenAI-compatible 接口检查视频关键帧，不参与旁白创作。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def has_api_key(self) -> bool:
        """判断视觉质检密钥是否已由部署环境提供。"""

        return bool(os.getenv(self.config.video_quality_assurance_api_key_env, "").strip())

    def assess(
        self,
        frame_paths: list[Path],
        expected_contract: dict[str, Any],
    ) -> VideoQualityAssessment:
        """提交关键帧与事实合同，并要求模型只评价、不补写视频内容。"""

        if not frame_paths:
            raise ValueError("视觉质检至少需要一张视频关键帧")
        api_key = self._read_api_key()
        quality_instruction = self._quality_instruction(expected_contract)
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": quality_instruction,
            }
        ]
        for frame_path in frame_paths:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._to_data_url(frame_path)},
                }
            )
        payload = {
            "model": self.config.video_quality_assurance_model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
        }
        response_payload = trace_llm_call(
            run_name="media.video.qwen_quality.assess",
            provider="qwen-openai-compatible",
            model=self.config.video_quality_assurance_model,
            message_count=1 + len(frame_paths),
            input_characters=len(quality_instruction),
            execute=lambda: self._request_assessment(api_key=api_key, payload=payload),
            summarize=self._trace_summary,
        )
        content_text = self._extract_content(response_payload)
        structured = self._parse_json_object(content_text)
        score = self._normalize_score(structured.get("score"))
        verdict = str(structured.get("verdict", "manual_review")).strip().lower()
        if verdict not in {"pass", "retry", "manual_review"}:
            verdict = "manual_review"
        issues = self._normalize_text_items(structured.get("issues"))
        instruction = str(structured.get("regeneration_instruction", "")).strip()
        summary = str(structured.get("summary", "")).strip()
        return VideoQualityAssessment(
            score=score,
            verdict=verdict,
            issues=issues,
            regeneration_instruction=instruction,
            summary=summary,
            raw_response=response_payload,
        )

    def _request_assessment(self, *, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行一次 Qwen-VL HTTP 调用；关键帧读取与结果归一化不重复建 Trace。"""

        try:
            response = requests.post(
                self._chat_url(),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.config.video_quality_assurance_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise QwenVideoQualityApiError(f"视觉质检请求失败：{exc}") from exc
        return self._parse_response(response)

    @classmethod
    def _trace_summary(cls, response_payload: dict[str, Any]) -> dict[str, Any]:
        """只记录输出规模和 token 用量，不上传质检结论或再生成指令。"""

        try:
            output_characters = len(cls._extract_content(response_payload))
        except QwenVideoQualityApiError:
            output_characters = 0
        usage = response_payload.get("usage")
        return {
            "output_characters": output_characters,
            "usage": usage if isinstance(usage, dict) else {},
        }

    def _quality_instruction(self, expected_contract: dict[str, Any]) -> str:
        return (
            "你是技术教学短视频的严格视觉质检员。只根据关键帧判断画面质量和是否符合给定事实合同，"
            "不要创造、猜测或补充合同以外的功能与数据。检查：项目标识是否可读且未明显拼错、"
            "是否呈现所要求的教学结构/模块关系、是否有乱码或无关人物/营销镜头、连续视觉系统是否合理。"
            "返回严格 JSON：{\"score\":0-100,\"verdict\":\"pass|retry|manual_review\","
            "\"issues\":[\"...\"],\"regeneration_instruction\":\"仅在 retry 时给出可执行提示词补充\","
            "\"summary\":\"简短结论\"}。若关键帧不足以验证，不要判 pass，使用 manual_review。\n"
            "事实合同：\n"
            + json.dumps(expected_contract, ensure_ascii=False)
        )

    def _chat_url(self) -> str:
        return f"{self.config.video_quality_assurance_base_url.rstrip('/')}/chat/completions"

    def _read_api_key(self) -> str:
        value = os.getenv(self.config.video_quality_assurance_api_key_env, "").strip()
        if not value:
            raise QwenVideoQualityApiError(
                f"{self.config.video_quality_assurance_api_key_env} 未配置，无法执行视觉质检"
            )
        return value

    @staticmethod
    def _to_data_url(path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"待质检帧不存在：{path}")
        raw = path.read_bytes()
        if not raw:
            raise ValueError(f"待质检帧为空：{path}")
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    @staticmethod
    def _parse_response(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise QwenVideoQualityApiError("视觉质检服务返回非 JSON 内容") from exc
        if not response.ok:
            message = "未知错误"
            if isinstance(payload, dict):
                error = payload.get("error")
                message = str(error.get("message", message)) if isinstance(error, dict) else str(payload.get("message", message))
            raise QwenVideoQualityApiError(f"视觉质检服务返回 HTTP {response.status_code}：{message[:500]}")
        if not isinstance(payload, dict):
            raise QwenVideoQualityApiError("视觉质检服务 JSON 顶层不是对象")
        return payload

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise QwenVideoQualityApiError("视觉质检服务响应缺少 choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise QwenVideoQualityApiError("视觉质检服务响应缺少 message")
        content = str(message.get("content", "")).strip()
        if not content:
            raise QwenVideoQualityApiError("视觉质检服务未返回可用结论")
        return content

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start < 0 or end < start:
                raise QwenVideoQualityApiError("视觉质检结果不是合法 JSON")
            payload = json.loads(cleaned[start : end + 1])
        if not isinstance(payload, dict):
            raise QwenVideoQualityApiError("视觉质检结果 JSON 顶层不是对象")
        return payload

    @staticmethod
    def _normalize_score(value: Any) -> int:
        try:
            score = int(float(value))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, score))

    @staticmethod
    def _normalize_text_items(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [re.sub(r"\s+", " ", str(item)).strip()[:300] for item in value if str(item).strip()]
