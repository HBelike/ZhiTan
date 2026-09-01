from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none, loads_json_or_empty


@dataclass(frozen=True)
class VideoStoryboardInput:
    """准备写入 video_storyboards 的短视频制作蓝图。"""

    content_id: int
    title: str
    progressive_script: str
    seedance_prompt: str
    architecture_image_prompts: list[dict[str, Any]]
    storyboard: dict[str, Any]
    status: str


@dataclass(frozen=True)
class VideoStoryboardRecord:
    """video_storyboards 表的一条记录。"""

    id: int
    content_id: int
    title: str
    progressive_script: str
    seedance_prompt: str
    architecture_image_prompts: list[dict[str, Any]]
    storyboard: dict[str, Any]
    status: str
    created_at: str
    updated_at: str


class VideoStoryboardRepository:
    """负责短视频分镜蓝图的持久化读写。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def upsert(self, storyboard: VideoStoryboardInput) -> VideoStoryboardRecord:
        """按 content_id 幂等创建或更新短视频蓝图。"""
        self._validate(storyboard)
        with self.database_manager.connection() as conn:
            conn.execute(
                """
                INSERT INTO video_storyboards (
                    content_id,
                    title,
                    progressive_script,
                    seedance_prompt,
                    architecture_image_prompts_json,
                    storyboard_json,
                    status,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(content_id) DO UPDATE SET
                    title = excluded.title,
                    progressive_script = excluded.progressive_script,
                    seedance_prompt = excluded.seedance_prompt,
                    architecture_image_prompts_json = excluded.architecture_image_prompts_json,
                    storyboard_json = excluded.storyboard_json,
                    status = excluded.status,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    storyboard.content_id,
                    storyboard.title,
                    storyboard.progressive_script,
                    storyboard.seedance_prompt,
                    dumps_json_or_none({"items": storyboard.architecture_image_prompts}),
                    dumps_json_or_none(storyboard.storyboard),
                    storyboard.status,
                ),
            )
            row = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    title,
                    progressive_script,
                    seedance_prompt,
                    architecture_image_prompts_json,
                    storyboard_json,
                    status,
                    created_at,
                    updated_at
                FROM video_storyboards
                WHERE content_id = ?
                """,
                (storyboard.content_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError(f"写入 video_storyboards 后无法读取记录：content_id={storyboard.content_id}")
        return self._row_to_record(row)

    def latest_for_content(self, content_id: int) -> VideoStoryboardRecord | None:
        """按内容 ID 读取短视频蓝图。"""
        if content_id <= 0:
            raise ValueError("content_id 必须大于 0")

        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    content_id,
                    title,
                    progressive_script,
                    seedance_prompt,
                    architecture_image_prompts_json,
                    storyboard_json,
                    status,
                    created_at,
                    updated_at
                FROM video_storyboards
                WHERE content_id = ?
                """,
                (content_id,),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_record(row)

    def _validate(self, storyboard: VideoStoryboardInput) -> None:
        """写入数据库前做边界检查。"""
        if storyboard.content_id <= 0:
            raise ValueError("content_id 必须大于 0")
        if not storyboard.title.strip():
            raise ValueError("视频蓝图标题不能为空")
        if not storyboard.progressive_script.strip():
            raise ValueError("渐进式口播脚本不能为空")
        if not storyboard.seedance_prompt.strip():
            raise ValueError("Seedance 主 prompt 不能为空")
        if storyboard.status not in {"ready", "failed"}:
            raise ValueError(f"不支持的视频蓝图状态：{storyboard.status}")

    def _row_to_record(self, row: Any) -> VideoStoryboardRecord:
        """把 sqlite3.Row 转成只读记录。"""
        architecture_payload = loads_json_or_empty(row["architecture_image_prompts_json"])
        architecture_items = architecture_payload.get("items", [])
        if not isinstance(architecture_items, list):
            architecture_items = []

        return VideoStoryboardRecord(
            id=int(row["id"]),
            content_id=int(row["content_id"]),
            title=str(row["title"]),
            progressive_script=str(row["progressive_script"]),
            seedance_prompt=str(row["seedance_prompt"]),
            architecture_image_prompts=[
                item for item in architecture_items if isinstance(item, dict)
            ],
            storyboard=loads_json_or_empty(row["storyboard_json"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
