from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.database.database_manager import DatabaseManager
from src.repositories.json_utils import dumps_json_or_none, loads_json_or_empty


@dataclass(frozen=True)
class MediaAssetInput:
    """准备写入 media_assets 的媒体资产。"""

    content_id: int
    repository_id: int | None
    asset_type: str
    provider: str
    path: str
    mime_type: str
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MediaAssetRecord:
    """media_assets 表的一条记录。"""

    id: int
    content_id: int | None
    asset_type: str
    provider: str
    path: str
    mime_type: str | None
    status: str
    metadata: dict[str, Any]


class MediaAssetRepository:
    """负责保存图片、音频、视频等媒体资产记录。"""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    def create(self, asset: MediaAssetInput) -> MediaAssetRecord:
        """插入一条媒体资产记录。"""
        self._validate_asset_input(asset)

        with self.database_manager.connection() as conn:
            created_id = self._insert_with_connection(conn, asset)
            rows = self._select_by_ids_with_connection(conn, [created_id])

        if not rows:
            raise RuntimeError("创建 media_assets 记录后无法读取该记录")
        return self._row_to_record(rows[0])

    def create_and_replace_images(
        self,
        assets: list[MediaAssetInput],
        replace_asset_ids: list[int],
    ) -> list[MediaAssetRecord]:
        """在一个事务中先插入整批新图，再把对应旧图标记为 replaced。"""

        if not assets:
            # 没有完整的新批次时绝不能改变旧资产状态。
            return []
        for asset in assets:
            self._validate_asset_input(asset)
            if asset.asset_type != "image":
                raise ValueError("create_and_replace_images 只接受 image 资产")

        with self.database_manager.connection() as conn:
            created_ids = [
                self._insert_with_connection(conn, asset) for asset in assets
            ]
            self._mark_replaced_with_connection(conn, replace_asset_ids)
            rows = self._select_by_ids_with_connection(conn, created_ids)
            if len(rows) != len(created_ids):
                raise RuntimeError(
                    "批量创建 media_assets 后读取数量不一致："
                    f"expected={len(created_ids)} actual={len(rows)}"
                )
        return [self._row_to_record(row) for row in rows]

    def count_by_content_id(self, content_id: int, asset_type: str) -> int:
        """统计指定内容已生成的某类媒体资产数量。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM media_assets
                WHERE content_id = ?
                  AND asset_type = ?
                  AND status != 'replaced'
                """,
                (content_id, asset_type),
            ).fetchone()

        return int(row["total"]) if row is not None else 0

    def list_by_content_id(self, content_id: int, asset_type: str) -> list[MediaAssetRecord]:
        """读取指定内容下的某类媒体资产列表。"""
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
                FROM media_assets
                WHERE content_id = ?
                  AND asset_type = ?
                  AND status != 'replaced'
                ORDER BY id ASC
                """,
                (content_id, asset_type),
            ).fetchall()

        return [
            MediaAssetRecord(
                id=int(row["id"]),
                content_id=None if row["content_id"] is None else int(row["content_id"]),
                asset_type=str(row["asset_type"]),
                provider=str(row["provider"]),
                path=str(row["path"]),
                mime_type=None if row["mime_type"] is None else str(row["mime_type"]),
                status=str(row["status"]),
                metadata=loads_json_or_empty(row["metadata_json"]),
            )
            for row in rows
        ]

    def get_by_id(self, asset_id: int) -> MediaAssetRecord | None:
        """按 ID 读取一条媒体资产记录。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
                FROM media_assets
                WHERE id = ?
                """,
                (asset_id,),
            ).fetchone()

        if row is None:
            return None

        return MediaAssetRecord(
            id=int(row["id"]),
            content_id=None if row["content_id"] is None else int(row["content_id"]),
            asset_type=str(row["asset_type"]),
            provider=str(row["provider"]),
            path=str(row["path"]),
            mime_type=None if row["mime_type"] is None else str(row["mime_type"]),
            status=str(row["status"]),
            metadata=loads_json_or_empty(row["metadata_json"]),
        )

    def list_for_content(self, content_id: int) -> list[MediaAssetRecord]:
        """读取指定内容下的全部媒体资产。"""
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
                FROM media_assets
                WHERE content_id = ?
                  AND status != 'replaced'
                ORDER BY id ASC
                """,
                (content_id,),
            ).fetchall()

        return [
            MediaAssetRecord(
                id=int(row["id"]),
                content_id=None if row["content_id"] is None else int(row["content_id"]),
                asset_type=str(row["asset_type"]),
                provider=str(row["provider"]),
                path=str(row["path"]),
                mime_type=None if row["mime_type"] is None else str(row["mime_type"]),
                status=str(row["status"]),
                metadata=loads_json_or_empty(row["metadata_json"]),
            )
            for row in rows
        ]

    def list_recent(self, limit: int = 300) -> list[MediaAssetRecord]:
        """读取资源库中最近写入的实际媒体资产。

        工作台的“媒体素材”是跨内容的资源库，不应只展示当前最新文章的资产。
        ``replaced`` 是被新素材替换的历史版本，不再作为可用资源展示；失败、生成中
        和已完成的记录都会保留，供界面明确呈现任务状态。
        """

        normalized_limit = min(max(int(limit), 1), 500)
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
                FROM media_assets
                WHERE status != 'replaced'
                ORDER BY id DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()

        return [
            MediaAssetRecord(
                id=int(row["id"]),
                content_id=None if row["content_id"] is None else int(row["content_id"]),
                asset_type=str(row["asset_type"]),
                provider=str(row["provider"]),
                path=str(row["path"]),
                mime_type=None if row["mime_type"] is None else str(row["mime_type"]),
                status=str(row["status"]),
                metadata=loads_json_or_empty(row["metadata_json"]),
            )
            for row in rows
        ]

    def summarize_by_content_ids(self, content_ids: list[int]) -> dict[int, dict[str, int]]:
        """按 ``content_id`` 汇总当前可用的媒体资产。

        执行历史只关心每篇推文所归属的图片、音频与视频，不应把不同
        ``content_id`` 的素材混在一张卡片里。这里刻意沿用素材库的默认
        语义：已被新一轮素材替换的 ``replaced`` 记录不再计入可用资源。
        """

        normalized_ids = sorted({int(content_id) for content_id in content_ids if int(content_id) > 0})
        if not normalized_ids:
            return {}

        placeholders = ",".join("?" for _ in normalized_ids)
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    content_id,
                    COUNT(*) AS total_asset_count,
                    SUM(CASE WHEN asset_type = 'image' THEN 1 ELSE 0 END) AS image_count,
                    SUM(CASE WHEN asset_type = 'audio' THEN 1 ELSE 0 END) AS audio_count,
                    SUM(CASE WHEN asset_type IN ('video', 'video_clip') THEN 1 ELSE 0 END) AS video_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_asset_count
                FROM media_assets
                WHERE content_id IN ({placeholders})
                  AND status != 'replaced'
                  AND asset_type IN ('image', 'audio', 'video', 'video_clip')
                GROUP BY content_id
                """,
                tuple(normalized_ids),
            ).fetchall()

        return {
            int(row["content_id"]): {
                "total_asset_count": int(row["total_asset_count"] or 0),
                "image_count": int(row["image_count"] or 0),
                "audio_count": int(row["audio_count"] or 0),
                "video_count": int(row["video_count"] or 0),
                "failed_asset_count": int(row["failed_asset_count"] or 0),
            }
            for row in rows
            if row["content_id"] is not None
        }

    def list_upload_candidates(self, asset_types: list[str]) -> list[MediaAssetRecord]:
        """读取还没有 remote_url 的媒体资产。"""
        normalized_types = [asset_type.strip() for asset_type in asset_types if asset_type.strip()]
        if not normalized_types:
            return []

        placeholders = ",".join("?" for _ in normalized_types)
        with self.database_manager.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
                FROM media_assets
                WHERE asset_type IN ({placeholders})
                  AND status != 'replaced'
                ORDER BY id ASC
                """,
                tuple(normalized_types),
            ).fetchall()

        candidates: list[MediaAssetRecord] = []
        for row in rows:
            metadata = loads_json_or_empty(row["metadata_json"])
            remote_url = str(metadata.get("remote_url", "")).strip()
            if remote_url.startswith("http://") or remote_url.startswith("https://"):
                continue
            candidates.append(
                MediaAssetRecord(
                    id=int(row["id"]),
                    content_id=None if row["content_id"] is None else int(row["content_id"]),
                    asset_type=str(row["asset_type"]),
                    provider=str(row["provider"]),
                    path=str(row["path"]),
                    mime_type=None if row["mime_type"] is None else str(row["mime_type"]),
                    status=str(row["status"]),
                    metadata=metadata,
                )
            )

        return candidates

    def mark_replaced_by_ids(self, asset_ids: list[int]) -> int:
        """把一批旧媒体资产标记为 replaced，避免继续参与预览、排版和上传。"""
        with self.database_manager.connection() as conn:
            return self._mark_replaced_with_connection(conn, asset_ids)

    @staticmethod
    def _validate_asset_input(asset: MediaAssetInput) -> None:
        if not asset.asset_type.strip():
            raise ValueError("媒体资产类型不能为空")
        if not asset.path.strip():
            raise ValueError("媒体资产路径不能为空")

    def _insert_with_connection(self, conn: Any, asset: MediaAssetInput) -> int:
        cursor = conn.execute(
            """
            INSERT INTO media_assets (
                content_id,
                repository_id,
                asset_type,
                provider,
                path,
                mime_type,
                status,
                metadata_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (
                asset.content_id,
                asset.repository_id,
                asset.asset_type,
                asset.provider,
                asset.path,
                asset.mime_type,
                asset.status,
                dumps_json_or_none(asset.metadata),
            ),
        )
        created_id = cursor.lastrowid
        if created_id is None:
            raise RuntimeError("插入 media_assets 后未返回记录 ID")
        return int(created_id)

    def _mark_replaced_with_connection(
        self,
        conn: Any,
        asset_ids: list[int],
    ) -> int:
        normalized_ids = sorted(
            {int(asset_id) for asset_id in asset_ids if int(asset_id) > 0}
        )
        if not normalized_ids:
            return 0

        placeholders = ",".join("?" for _ in normalized_ids)
        cursor = conn.execute(
            f"""
            UPDATE media_assets
            SET status = 'replaced',
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id IN ({placeholders})
              AND status != 'replaced'
            """,
            tuple(normalized_ids),
        )
        return int(cursor.rowcount)

    def _select_by_ids_with_connection(
        self,
        conn: Any,
        asset_ids: list[int],
    ) -> list[Any]:
        normalized_ids = [int(asset_id) for asset_id in asset_ids]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        rows = conn.execute(
            f"""
            SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
            FROM media_assets
            WHERE id IN ({placeholders})
            ORDER BY id ASC
            """,
            tuple(normalized_ids),
        ).fetchall()
        return list(rows)

    @staticmethod
    def _row_to_record(row: Any) -> MediaAssetRecord:
        return MediaAssetRecord(
            id=int(row["id"]),
            content_id=None
            if row["content_id"] is None
            else int(row["content_id"]),
            asset_type=str(row["asset_type"]),
            provider=str(row["provider"]),
            path=str(row["path"]),
            mime_type=None
            if row["mime_type"] is None
            else str(row["mime_type"]),
            status=str(row["status"]),
            metadata=loads_json_or_empty(row["metadata_json"]),
        )

    def merge_metadata_and_status(
        self,
        asset_id: int,
        metadata_patch: dict[str, Any],
        status: str,
    ) -> MediaAssetRecord:
        """合并媒体资产 metadata，并更新状态。"""
        with self.database_manager.connection() as conn:
            row = conn.execute(
                """
                SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
                FROM media_assets
                WHERE id = ?
                """,
                (asset_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"media_assets 记录不存在：asset_id={asset_id}")

            metadata = loads_json_or_empty(row["metadata_json"])
            metadata.update(metadata_patch)
            conn.execute(
                """
                UPDATE media_assets
                SET status = ?,
                    metadata_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (status, dumps_json_or_none(metadata), asset_id),
            )
            updated = conn.execute(
                """
                SELECT id, content_id, asset_type, provider, path, mime_type, status, metadata_json
                FROM media_assets
                WHERE id = ?
                """,
                (asset_id,),
            ).fetchone()

        if updated is None:
            raise RuntimeError(f"更新 media_assets 后无法读取记录：asset_id={asset_id}")

        return MediaAssetRecord(
            id=int(updated["id"]),
            content_id=None if updated["content_id"] is None else int(updated["content_id"]),
            asset_type=str(updated["asset_type"]),
            provider=str(updated["provider"]),
            path=str(updated["path"]),
            mime_type=None if updated["mime_type"] is None else str(updated["mime_type"]),
            status=str(updated["status"]),
            metadata=loads_json_or_empty(updated["metadata_json"]),
        )
