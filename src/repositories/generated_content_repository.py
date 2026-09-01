from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none, loads_json_or_empty


@dataclass(frozen=True)
class GeneratedContentInput:
    """准备写入 generated_contents 的内容生成结果。"""

    week_end: str
    title: str
    digest: str
    article_markdown: str
    video_script: str
    voiceover_text: str
    image_prompts: list[dict[str, Any]]
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class GeneratedContentRecord:
    """generated_contents 表的一条基础记录。"""

    id: int
    week_end: str
    title: str
    status: str


@dataclass(frozen=True)
class GeneratedContentForImage:
    """ImageTask 生成图片时需要读取的内容快照。"""

    id: int
    week_end: str
    title: str
    image_prompts: list[dict[str, Any]]


@dataclass(frozen=True)
class GeneratedContentForVideo:
    """VideoTask 生成视频时需要读取的内容快照。"""

    id: int
    week_end: str
    title: str
    video_script: str
    voiceover_text: str


@dataclass(frozen=True)
class GeneratedContentForStoryboard:
    """ShortVideoPromptTask 生成视频蓝图时需要读取的内容快照。"""

    id: int
    week_end: str
    title: str
    digest: str
    article_markdown: str
    video_script: str
    voiceover_text: str
    image_prompts: list[dict[str, Any]]


@dataclass(frozen=True)
class GeneratedContentPreview:
    """预览页面需要展示的内容快照。"""

    id: int
    week_end: str
    title: str
    digest: str
    article_markdown: str
    video_script: str
    voiceover_text: str
    image_prompts: list[dict[str, Any]]
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GeneratedContentHistoryItem:
    """工作台执行历史的一条轻量内容索引。

    执行历史只负责把已经持久化的推文快照与同一 ``content_id`` 的资源重新关联，
    因此这里刻意不携带提示词、任务日志或模型原始响应。
    """

    id: int
    week_end: str
    title: str
    digest: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GeneratedContentForLayout:
    """ArticleLayoutTask 生成公众号排版稿时需要读取的内容快照。"""

    id: int
    week_end: str
    title: str
    digest: str
    article_markdown: str
    video_script: str
    voiceover_text: str
    image_prompts: list[dict[str, Any]]
    status: str
    created_at: str
    updated_at: str


class GeneratedContentRepository:
    """负责保存和读取 SummaryTask 生成的图文与视频脚本内容。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def create(self, content: GeneratedContentInput) -> GeneratedContentRecord:
        """插入一条生成内容记录。"""
        if not content.title.strip():
            raise ValueError("生成内容标题不能为空")
        if not content.article_markdown.strip():
            raise ValueError("生成内容正文不能为空")

        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO generated_contents (
                    week_end,
                    title,
                    digest,
                    article_markdown,
                    video_script,
                    voiceover_text,
                    image_prompts_json,
                    raw_response_json,
                    status,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """,
                (
                    content.week_end,
                    content.title,
                    content.digest,
                    content.article_markdown,
                    content.video_script,
                    content.voiceover_text,
                    dumps_json_or_none({"items": content.image_prompts}),
                    dumps_json_or_none(content.raw_response),
                ),
            )
            row = conn.execute(
                """
                SELECT id, week_end, title, status
                FROM generated_contents
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

        if row is None:
            raise RuntimeError("创建 generated_contents 记录后无法读取该记录")

        return GeneratedContentRecord(
            id=int(row["id"]),
            week_end=str(row["week_end"]),
            title=str(row["title"]),
            status=str(row["status"]),
        )

    def latest_for_image_generation(self) -> GeneratedContentForImage | None:
        """读取最新一条有图片 prompt 的内容记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, week_end, title, image_prompts_json
                FROM generated_contents
                WHERE image_prompts_json IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return GeneratedContentForImage(
            id=int(row["id"]),
            week_end=str(row["week_end"]),
            title=str(row["title"]),
            image_prompts=self._normalize_image_prompts(row["image_prompts_json"], require_prompt=True),
        )

    def get_for_image_generation(self, content_id: int) -> GeneratedContentForImage | None:
        """按 content_id 读取有图片 prompt 的内容记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, week_end, title, image_prompts_json
                FROM generated_contents
                WHERE id = ?
                  AND image_prompts_json IS NOT NULL
                LIMIT 1
                """,
                (content_id,),
            ).fetchone()

        if row is None:
            return None

        return GeneratedContentForImage(
            id=int(row["id"]),
            week_end=str(row["week_end"]),
            title=str(row["title"]),
            image_prompts=self._normalize_image_prompts(row["image_prompts_json"], require_prompt=True),
        )

    def latest_for_video_generation(self) -> GeneratedContentForVideo | None:
        """读取最新一条有视频脚本的内容记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, week_end, title, video_script, voiceover_text
                FROM generated_contents
                WHERE video_script IS NOT NULL
                  AND TRIM(video_script) != ''
                  AND voiceover_text IS NOT NULL
                  AND TRIM(voiceover_text) != ''
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return GeneratedContentForVideo(
            id=int(row["id"]),
            week_end=str(row["week_end"]),
            title=str(row["title"]),
            video_script=str(row["video_script"] or "").strip(),
            voiceover_text=str(row["voiceover_text"] or "").strip(),
        )

    def update_media_plan(
            self,
            content_id: int,
            *,
            video_script: str,
            voiceover_text: str,
    ) -> None:
        """保存 ShortVideoPromptTask 生成的统一视频脚本和可配音文本。"""

        normalized_video_script = video_script.strip()
        normalized_voiceover_text = voiceover_text.strip()
        if not normalized_video_script:
            raise ValueError("视频脚本不能为空")
        if not normalized_voiceover_text:
            raise ValueError("配音文本不能为空")

        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE generated_contents
                SET video_script = ?,
                    voiceover_text = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (normalized_video_script, normalized_voiceover_text, content_id),
            )

        if cursor.rowcount != 1:
            raise RuntimeError(f"未找到 generated_contents 记录：content_id={content_id}")

    def latest_for_storyboard_generation(self) -> GeneratedContentForStoryboard | None:
        """读取最新一条可生成短视频蓝图的内容记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    week_end,
                    title,
                    digest,
                    article_markdown,
                    video_script,
                    voiceover_text,
                    image_prompts_json
                FROM generated_contents
                WHERE article_markdown IS NOT NULL
                  AND TRIM(article_markdown) != ''
                  AND image_prompts_json IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return GeneratedContentForStoryboard(
            id=int(row["id"]),
            week_end=str(row["week_end"]),
            title=str(row["title"]),
            digest=str(row["digest"] or ""),
            article_markdown=str(row["article_markdown"] or ""),
            video_script=str(row["video_script"] or ""),
            voiceover_text=str(row["voiceover_text"] or ""),
            image_prompts=self._normalize_image_prompts(row["image_prompts_json"], require_prompt=True),
        )

    def latest_for_preview(self) -> GeneratedContentPreview | None:
        """读取最新一条内容，供审核预览使用。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    week_end,
                    title,
                    digest,
                    article_markdown,
                    video_script,
                    voiceover_text,
                    image_prompts_json,
                    status,
                    created_at,
                    updated_at
                FROM generated_contents
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return self._row_to_preview(row)

    def get_for_preview(self, content_id: int) -> GeneratedContentPreview | None:
        """按 content_id 读取一条历史推文快照。

        工作台素材和执行历史都必须显式以 ``content_id`` 为边界；不能再通过
        “最新一条”间接拿到其他任务的文章或媒体。
        """

        if content_id <= 0:
            raise ValueError("content_id 必须大于 0")

        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    week_end,
                    title,
                    digest,
                    article_markdown,
                    video_script,
                    voiceover_text,
                    image_prompts_json,
                    status,
                    created_at,
                    updated_at
                FROM generated_contents
                WHERE id = ?
                LIMIT 1
                """,
                (content_id,),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_preview(row)

    def list_recent_for_history(self, limit: int = 50) -> list[GeneratedContentHistoryItem]:
        """读取可在执行历史中回看的推文快照索引。

        只返回已经有正文的生成内容。正文和同 ID 的媒体资产才是本次执行历史要
        长期展示的两类数据；任务运行日志不参与该列表。
        """

        normalized_limit = min(max(int(limit), 1), 100)
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, week_end, title, digest, status, created_at, updated_at
                FROM generated_contents
                WHERE article_markdown IS NOT NULL
                  AND TRIM(article_markdown) != ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()

        return [
            GeneratedContentHistoryItem(
                id=int(row["id"]),
                week_end=str(row["week_end"]),
                title=str(row["title"]),
                digest=str(row["digest"] or ""),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def _row_to_preview(self, row: Any) -> GeneratedContentPreview:
        """将查询结果转换为审核与历史页面共用的只读推文快照。"""

        return GeneratedContentPreview(
            id=int(row["id"]),
            week_end=str(row["week_end"]),
            title=str(row["title"]),
            digest=str(row["digest"] or ""),
            article_markdown=str(row["article_markdown"] or ""),
            video_script=str(row["video_script"] or ""),
            voiceover_text=str(row["voiceover_text"] or ""),
            image_prompts=self._normalize_image_prompts(row["image_prompts_json"], require_prompt=False),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def latest_approved_for_layout(self) -> GeneratedContentForLayout | None:
        """读取最近一条已通过人工审核、可进入公众号排版阶段的内容。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    week_end,
                    title,
                    digest,
                    article_markdown,
                    video_script,
                    voiceover_text,
                    image_prompts_json,
                    status,
                    created_at,
                    updated_at
                FROM generated_contents
                WHERE status = 'approved'
                  AND article_markdown IS NOT NULL
                  AND TRIM(article_markdown) != ''
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return GeneratedContentForLayout(
            id=int(row["id"]),
            week_end=str(row["week_end"]),
            title=str(row["title"]),
            digest=str(row["digest"] or ""),
            article_markdown=str(row["article_markdown"] or ""),
            video_script=str(row["video_script"] or ""),
            voiceover_text=str(row["voiceover_text"] or ""),
            image_prompts=self._normalize_image_prompts(row["image_prompts_json"], require_prompt=False),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def exists(self, content_id: int) -> bool:
        """判断内容记录是否存在。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM generated_contents
                WHERE id = ?
                """,
                (content_id,),
            ).fetchone()

        return row is not None

    def update_status(self, content_id: int, status: str) -> None:
        """更新生成内容状态。"""
        normalized_status = status.strip()
        if not normalized_status:
            raise ValueError("内容状态不能为空")

        with self.database_manager.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE generated_contents
                SET status = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (normalized_status, content_id),
            )

        if cursor.rowcount != 1:
            raise RuntimeError(f"未找到 generated_contents 记录：content_id={content_id}")

    def _normalize_image_prompts(
        self,
        image_prompts_json: str | None,
        require_prompt: bool,
    ) -> list[dict[str, Any]]:
        """把 SummaryTask 保存的图片 prompt JSON 转成稳定的列表结构。"""
        payload = loads_json_or_empty(image_prompts_json)
        items = payload.get("items", [])
        if not isinstance(items, list):
            return []

        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            repository_full_name = str(item.get("repository_full_name", "")).strip()
            prompt = str(item.get("prompt", "")).strip()
            if not repository_full_name:
                continue
            if require_prompt and not prompt:
                continue
            normalized_item: dict[str, Any] = {
                "repository_full_name": repository_full_name,
                "prompt": prompt,
            }
            for optional_field in (
                "rank",
                "summary_text",
                "project_summary_text",
                "project_analysis_markdown",
                "prompt_source",
                "visual_title",
                "raw_prompt",
                "prompt_stage",
                "visual_spec",
                "visual_brief",
                "video_brief",
            ):
                value = item.get(optional_field)
                if value is not None:
                    normalized_item[optional_field] = value
            normalized_items.append(normalized_item)
        return normalized_items
