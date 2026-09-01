"""管理员配置到内容流水线配置快照的转换。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from src.config.config_manager import AppConfig


DEFAULT_PIPELINE_CONFIG: dict[str, object] = {
    "top_n": 5,
    "github_keywords": ["agent", "AI", "LLM", "RAG"],
    "image_generation_enabled": True,
    "video_generation_enabled": False,
    "audio_generation_enabled": False,
    "summary_prompt": "",
    "image_prompt": "",
    "video_prompt": "",
}


def normalize_pipeline_config(value: dict[str, object] | None) -> dict[str, object]:
    """校验管理员输入，产出可安全持久化与运行的配置快照。"""

    source = value or {}
    try:
        top_n = int(source.get("top_n", DEFAULT_PIPELINE_CONFIG["top_n"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("本期项目数量必须是整数") from exc
    if not 1 <= top_n <= 12:
        raise ValueError("本期项目数量必须在 1 到 12 之间")

    raw_keywords = source.get("github_keywords", DEFAULT_PIPELINE_CONFIG["github_keywords"])
    if isinstance(raw_keywords, str):
        raw_keywords = raw_keywords.replace("，", ",").split(",")
    if not isinstance(raw_keywords, list):
        raise ValueError("主题关键词必须是文本列表")

    keywords: list[str] = []
    for item in raw_keywords:
        keyword = str(item).strip()
        if not keyword or keyword in keywords:
            continue
        if len(keyword) > 80:
            raise ValueError("单个主题关键词不能超过 80 个字符")
        keywords.append(keyword)
    if len(keywords) > 12:
        raise ValueError("主题关键词最多支持 12 个")

    normalized: dict[str, object] = {
        "top_n": top_n,
        "github_keywords": keywords,
    }
    for field, label in (
        ("image_generation_enabled", "图片生成开关"),
        ("video_generation_enabled", "视频生成开关"),
        ("audio_generation_enabled", "语音生成开关"),
    ):
        enabled = source.get(field, DEFAULT_PIPELINE_CONFIG[field])
        if not isinstance(enabled, bool):
            raise ValueError(f"{label}必须是布尔值")
        normalized[field] = enabled
    for field in ("summary_prompt", "image_prompt", "video_prompt"):
        prompt = str(source.get(field, "")).strip()
        if len(prompt) > 8_000:
            raise ValueError(f"{field} 不能超过 8000 个字符")
        normalized[field] = prompt
    return normalized


def pipeline_config_for_ui(value: dict[str, object] | None) -> dict[str, object]:
    """将空配置补齐为 UI 可直接编辑的默认值。"""

    merged = deepcopy(DEFAULT_PIPELINE_CONFIG)
    if value:
        merged.update(value)
    return normalize_pipeline_config(merged)


def apply_pipeline_config(base_config: AppConfig, value: dict[str, object] | None) -> AppConfig:
    """把版本化管理员配置叠加到一次运行的只读 AppConfig 快照。"""

    runtime_config = pipeline_config_for_ui(value)
    raw = deepcopy(base_config.raw)
    raw.setdefault("ranking", {})["top_n"] = runtime_config["top_n"]
    raw.setdefault("image", {})["paid_generation_enabled"] = runtime_config["image_generation_enabled"]
    raw.setdefault("video", {})["required_image_count"] = runtime_config["top_n"]
    raw.setdefault("video", {})["submit_enabled"] = runtime_config["video_generation_enabled"]
    raw["video"]["runtime_submit_enabled"] = runtime_config["video_generation_enabled"]
    raw.setdefault("audio", {})["enabled"] = runtime_config["audio_generation_enabled"]
    raw["audio"]["runtime_enabled"] = runtime_config["audio_generation_enabled"]
    raw.setdefault("github", {})["search_query"] = _build_github_query(runtime_config["github_keywords"])
    raw["runtime_prompts"] = {
        "summary": runtime_config["summary_prompt"],
        "image": runtime_config["image_prompt"],
        "video": runtime_config["video_prompt"],
    }
    return replace(base_config, raw=raw)


def _build_github_query(keywords: object) -> str:
    """将人类可读关键词转为 GitHub repository search 的主题子查询。"""

    if not isinstance(keywords, list) or not keywords:
        return "fork:false archived:false"
    escaped = [f'"{item}"' if " " in item else item for item in keywords]
    return f"({' OR '.join(escaped)}) fork:false archived:false"
