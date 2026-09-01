from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none, loads_json_or_empty


@dataclass(frozen=True)
class VideoClipPlanInput:
    """准备写入 video_clip_plans 的单段视频生成计划。"""

    content_id: int
    storyboard_id: int
    clip_index: int
    source_scene_index: int
    clip_title: str
    repository_full_name: str | None
    planned_duration_seconds: int
    output_start_second: int
    output_end_second: int
    narration: str
    subtitle: str
    visual_design: str
    motion_design: str
    transition_to_next: str
    seedance_prompt: str
    reference_image_asset_ids: list[int]
    provider: str
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VideoClipPlanRecord:
    """video_clip_plans 表的一条只读记录。"""

    id: int
    content_id: int
    storyboard_id: int
    clip_index: int
    source_scene_index: int
    clip_title: str
    repository_full_name: str | None
    planned_duration_seconds: int
    output_start_second: int
    output_end_second: int
    narration: str
    subtitle: str
    visual_design: str
    motion_design: str
    transition_to_next: str
    seedance_prompt: str
    reference_image_asset_ids: list[int]
    provider: str
    status: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class VideoClipPlanRepository:
    """负责 7 段短视频 clip 计划的持久化读写。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def replace_for_storyboard(
        self,
        storyboard_id: int,
        clip_plans: list[VideoClipPlanInput],
    ) -> list[VideoClipPlanRecord]:
        """按 storyboard_id 原子替换全部 clip 计划，保证重生成时不会残留旧计划。"""

        if storyboard_id <= 0:
            raise ValueError("storyboard_id 必须大于 0")
        if not clip_plans:
            raise ValueError("clip_plans 不能为空")
        for clip_plan in clip_plans:
            self._validate(clip_plan)
            if clip_plan.storyboard_id != storyboard_id:
                raise ValueError("clip_plan.storyboard_id 必须与 replace_for_storyboard 参数一致")

        with self.database_manager.connection() as conn:
            conn.execute(
                """
                DELETE FROM video_clip_plans
                WHERE storyboard_id = ?
                """,
                (storyboard_id,),
            )
            for clip_plan in clip_plans:
                conn.execute(
                    """
                    INSERT INTO video_clip_plans (
                        content_id,
                        storyboard_id,
                        clip_index,
                        source_scene_index,
                        clip_title,
                        repository_full_name,
                        planned_duration_seconds,
                        output_start_second,
                        output_end_second,
                        narration,
                        subtitle,
                        visual_design,
                        motion_design,
                        transition_to_next,
                        seedance_prompt,
                        reference_image_asset_ids_json,
                        provider,
                        status,
                        metadata_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (
                        clip_plan.content_id,
                        clip_plan.storyboard_id,
                        clip_plan.clip_index,
                        clip_plan.source_scene_index,
                        clip_plan.clip_title,
                        clip_plan.repository_full_name,
                        clip_plan.planned_duration_seconds,
                        clip_plan.output_start_second,
                        clip_plan.output_end_second,
                        clip_plan.narration,
                        clip_plan.subtitle,
                        clip_plan.visual_design,
                        clip_plan.motion_design,
                        clip_plan.transition_to_next,
                        clip_plan.seedance_prompt,
                        dumps_json_or_none({"items": clip_plan.reference_image_asset_ids}),
                        clip_plan.provider,
                        clip_plan.status,
                        dumps_json_or_none(clip_plan.metadata),
                    ),
                )

            rows = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    storyboard_id,
                    clip_index,
                    source_scene_index,
                    clip_title,
                    repository_full_name,
                    planned_duration_seconds,
                    output_start_second,
                    output_end_second,
                    narration,
                    subtitle,
                    visual_design,
                    motion_design,
                    transition_to_next,
                    seedance_prompt,
                    reference_image_asset_ids_json,
                    provider,
                    status,
                    metadata_json,
                    created_at,
                    updated_at
                FROM video_clip_plans
                WHERE storyboard_id = ?
                ORDER BY clip_index ASC
                """,
                (storyboard_id,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def list_by_content_id(self, content_id: int) -> list[VideoClipPlanRecord]:
        """读取某条内容下的全部 clip 计划。"""

        if content_id <= 0:
            raise ValueError("content_id 必须大于 0")

        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    storyboard_id,
                    clip_index,
                    source_scene_index,
                    clip_title,
                    repository_full_name,
                    planned_duration_seconds,
                    output_start_second,
                    output_end_second,
                    narration,
                    subtitle,
                    visual_design,
                    motion_design,
                    transition_to_next,
                    seedance_prompt,
                    reference_image_asset_ids_json,
                    provider,
                    status,
                    metadata_json,
                    created_at,
                    updated_at
                FROM video_clip_plans
                WHERE content_id = ?
                ORDER BY clip_index ASC
                """,
                (content_id,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def list_recent(self, limit: int = 300) -> list[VideoClipPlanRecord]:
        """读取最近的视频分片计划，供工作台资源库显示未完成状态。"""

        normalized_limit = min(max(int(limit), 1), 500)
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    storyboard_id,
                    clip_index,
                    source_scene_index,
                    clip_title,
                    repository_full_name,
                    planned_duration_seconds,
                    output_start_second,
                    output_end_second,
                    narration,
                    subtitle,
                    visual_design,
                    motion_design,
                    transition_to_next,
                    seedance_prompt,
                    reference_image_asset_ids_json,
                    provider,
                    status,
                    metadata_json,
                    created_at,
                    updated_at
                FROM video_clip_plans
                ORDER BY id DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def merge_metadata_and_status(
        self,
        clip_plan_id: int,
        metadata_patch: dict[str, Any],
        status: str,
    ) -> VideoClipPlanRecord:
        """合并 clip 计划 metadata，并更新计划状态。"""

        if clip_plan_id <= 0:
            raise ValueError("clip_plan_id 必须大于 0")
        if status not in {"planned", "submitted", "processing", "completed", "failed"}:
            raise ValueError(f"不支持的 clip 计划状态：{status}")

        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT metadata_json
                FROM video_clip_plans
                WHERE id = ?
                """,
                (clip_plan_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"video_clip_plans 记录不存在：clip_plan_id={clip_plan_id}")

            metadata = loads_json_or_empty(row["metadata_json"])
            metadata.update(metadata_patch)
            conn.execute(
                """
                UPDATE video_clip_plans
                SET status = ?,
                    metadata_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (status, dumps_json_or_none(metadata), clip_plan_id),
            )
            updated = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    storyboard_id,
                    clip_index,
                    source_scene_index,
                    clip_title,
                    repository_full_name,
                    planned_duration_seconds,
                    output_start_second,
                    output_end_second,
                    narration,
                    subtitle,
                    visual_design,
                    motion_design,
                    transition_to_next,
                    seedance_prompt,
                    reference_image_asset_ids_json,
                    provider,
                    status,
                    metadata_json,
                    created_at,
                    updated_at
                FROM video_clip_plans
                WHERE id = ?
                """,
                (clip_plan_id,),
            ).fetchone()

        if updated is None:
            raise RuntimeError(f"更新 video_clip_plans 后无法读取记录：clip_plan_id={clip_plan_id}")
        return self._row_to_record(updated)

    def _validate(self, clip_plan: VideoClipPlanInput) -> None:
        """写入数据库前做边界检查，避免无效计划进入后续生成任务。"""

        if clip_plan.content_id <= 0:
            raise ValueError("content_id 必须大于 0")
        if clip_plan.storyboard_id <= 0:
            raise ValueError("storyboard_id 必须大于 0")
        if clip_plan.clip_index <= 0:
            raise ValueError("clip_index 必须大于 0")
        if clip_plan.source_scene_index <= 0:
            raise ValueError("source_scene_index 必须大于 0")
        if clip_plan.planned_duration_seconds <= 0:
            raise ValueError("planned_duration_seconds 必须大于 0")
        if clip_plan.output_start_second < 0:
            raise ValueError("output_start_second 不能小于 0")
        if clip_plan.output_end_second <= clip_plan.output_start_second:
            raise ValueError("output_end_second 必须大于 output_start_second")
        if not clip_plan.clip_title.strip():
            raise ValueError("clip_title 不能为空")
        if not clip_plan.narration.strip():
            raise ValueError("narration 不能为空")
        if not clip_plan.visual_design.strip():
            raise ValueError("visual_design 不能为空")
        if not clip_plan.motion_design.strip():
            raise ValueError("motion_design 不能为空")
        if not clip_plan.seedance_prompt.strip():
            raise ValueError("seedance_prompt 不能为空")
        if not clip_plan.provider.strip():
            raise ValueError("provider 不能为空")
        if clip_plan.status not in {"planned", "submitted", "processing", "completed", "failed"}:
            raise ValueError(f"不支持的 clip 计划状态：{clip_plan.status}")

    def _row_to_record(self, row: Any) -> VideoClipPlanRecord:
        """把 sqlite3.Row 转成只读记录。"""

        reference_payload = loads_json_or_empty(row["reference_image_asset_ids_json"])
        reference_items = reference_payload.get("items", [])
        if not isinstance(reference_items, list):
            reference_items = []

        return VideoClipPlanRecord(
            id=int(row["id"]),
            content_id=int(row["content_id"]),
            storyboard_id=int(row["storyboard_id"]),
            clip_index=int(row["clip_index"]),
            source_scene_index=int(row["source_scene_index"]),
            clip_title=str(row["clip_title"]),
            repository_full_name=None if row["repository_full_name"] is None else str(row["repository_full_name"]),
            planned_duration_seconds=int(row["planned_duration_seconds"]),
            output_start_second=int(row["output_start_second"]),
            output_end_second=int(row["output_end_second"]),
            narration=str(row["narration"]),
            subtitle=str(row["subtitle"] or ""),
            visual_design=str(row["visual_design"]),
            motion_design=str(row["motion_design"]),
            transition_to_next=str(row["transition_to_next"] or ""),
            seedance_prompt=str(row["seedance_prompt"]),
            reference_image_asset_ids=[int(item) for item in reference_items if isinstance(item, int) or str(item).isdigit()],
            provider=str(row["provider"]),
            status=str(row["status"]),
            metadata=loads_json_or_empty(row["metadata_json"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
