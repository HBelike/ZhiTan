from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from src.repositories.video_clip_plan_repository import VideoClipPlanRecord


@dataclass(frozen=True)
class NarrationTimelineClip:
    """一段视频画面对应的受约束旁白与字幕时间片。"""

    clip_plan_id: int
    clip_index: int
    repository_full_name: str | None
    start_seconds: float
    end_seconds: float
    visual_duration_seconds: float
    character_budget: int
    source_narration: str
    narration: str
    subtitle: str
    required_tokens: list[str]
    fit_status: str


class NarrationTimelineService:
    """根据真实视频时长生成并校验旁白时间线，不允许旁白脱离分镜事实。"""

    _sentence_boundary = re.compile(r"(?<=[。！？；.!?])")

    def __init__(self, characters_per_second: float) -> None:
        if characters_per_second <= 0:
            raise ValueError("旁白字符速率必须大于 0")
        self.characters_per_second = characters_per_second

    def build_source_specs(
        self,
        plans: list[VideoClipPlanRecord],
        durations_by_plan_id: dict[int, float],
    ) -> list[dict[str, Any]]:
        """将分镜计划转换为可交给 LLM 改写的事实合同。"""

        cursor = 0.0
        specs: list[dict[str, Any]] = []
        for plan in sorted(plans, key=lambda item: item.clip_index):
            duration = durations_by_plan_id.get(plan.id, float(plan.planned_duration_seconds))
            if duration <= 0:
                raise ValueError(f"视频片段时长无效：clip_index={plan.clip_index}")
            budget = self.character_budget(duration)
            required_tokens = [plan.repository_full_name] if plan.repository_full_name else []
            specs.append(
                {
                    "clip_plan_id": plan.id,
                    "clip_index": plan.clip_index,
                    "repository_full_name": plan.repository_full_name,
                    "start_seconds": round(cursor, 3),
                    "end_seconds": round(cursor + duration, 3),
                    "visual_duration_seconds": round(duration, 3),
                    "character_budget": budget,
                    "source_narration": plan.narration.strip(),
                    "source_subtitle": plan.subtitle.strip(),
                    "required_tokens": required_tokens,
                }
            )
            cursor += duration
        return specs

    def normalize_generated_clips(
        self,
        source_specs: list[dict[str, Any]],
        generated_payload: dict[str, Any],
    ) -> list[NarrationTimelineClip]:
        """校验模型改写结果；不满足事实或时长约束时以安全本地结果兜底。"""

        raw_clips = generated_payload.get("clips", [])
        by_index: dict[int, dict[str, Any]] = {}
        if isinstance(raw_clips, list):
            for item in raw_clips:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("clip_index"))
                except (TypeError, ValueError):
                    continue
                by_index[index] = item

        normalized: list[NarrationTimelineClip] = []
        for spec in source_specs:
            raw_item = by_index.get(int(spec["clip_index"]), {})
            candidate_narration = self._clean_text(raw_item.get("narration"))
            candidate_subtitle = self._clean_text(raw_item.get("subtitle"))
            if self._is_valid_candidate(candidate_narration, spec):
                narration = candidate_narration
                subtitle = candidate_subtitle or narration
                fit_status = "llm_fitted"
            else:
                narration = self._safe_local_fit(
                    source_text=str(spec["source_narration"]),
                    required_tokens=list(spec["required_tokens"]),
                    budget=int(spec["character_budget"]),
                )
                subtitle = self._safe_local_fit(
                    source_text=str(spec["source_subtitle"]) or narration,
                    required_tokens=[],
                    budget=min(22, int(spec["character_budget"])),
                )
                fit_status = "local_fallback_requires_review"

            normalized.append(
                NarrationTimelineClip(
                    clip_plan_id=int(spec["clip_plan_id"]),
                    clip_index=int(spec["clip_index"]),
                    repository_full_name=spec["repository_full_name"],
                    start_seconds=float(spec["start_seconds"]),
                    end_seconds=float(spec["end_seconds"]),
                    visual_duration_seconds=float(spec["visual_duration_seconds"]),
                    character_budget=int(spec["character_budget"]),
                    source_narration=str(spec["source_narration"]),
                    narration=narration,
                    subtitle=subtitle,
                    required_tokens=list(spec["required_tokens"]),
                    fit_status=fit_status,
                )
            )
        return normalized

    def fallback_clips(self, source_specs: list[dict[str, Any]]) -> list[NarrationTimelineClip]:
        """在 LLM 暂不可用时产生不虚构事实的保守时间线，并显式标记人工复核。"""

        return self.normalize_generated_clips(source_specs=source_specs, generated_payload={"clips": []})

    def character_budget(self, duration_seconds: float) -> int:
        """以偏保守的中文口播速率估算该片段可容纳的字符数。"""

        return max(8, math.floor(duration_seconds * self.characters_per_second))

    def _is_valid_candidate(self, text: str, spec: dict[str, Any]) -> bool:
        """检查候选旁白是否为空、是否超预算、是否遗漏不可改写的仓库标识。"""

        if not text or len(text) > int(spec["character_budget"]):
            return False
        required_tokens = [str(token) for token in spec["required_tokens"] if str(token).strip()]
        return all(token in text for token in required_tokens)

    def _safe_local_fit(self, source_text: str, required_tokens: list[str], budget: int) -> str:
        """按句截取原始文本；兜底不得补写任何原文中不存在的事实。"""

        normalized = self._clean_text(source_text)
        if not normalized:
            normalized = "本段展示本周项目的关键工程思路。"

        prefix = "。".join(token for token in required_tokens if token and token not in normalized)
        if prefix:
            normalized = f"{prefix}。{normalized}"

        if len(normalized) <= budget:
            return normalized

        parts = [part.strip() for part in self._sentence_boundary.split(normalized) if part.strip()]
        result = ""
        for part in parts:
            if len(result) + len(part) <= budget:
                result += part
            elif not result:
                result = part[:budget]
                break

        result = result or normalized[:budget]
        for token in required_tokens:
            if token and token not in result:
                remaining = max(0, budget - len(token) - 1)
                result = f"{token}。{result[:remaining]}"
        return result[:budget].rstrip("，、；：") + "。"

    @staticmethod
    def _clean_text(value: Any) -> str:
        """清理模型可能返回的 Markdown、换行和多余空白。"""

        text = str(value or "").strip()
        text = re.sub(r"[`*_#]", "", text)
        return re.sub(r"\s+", "", text)
