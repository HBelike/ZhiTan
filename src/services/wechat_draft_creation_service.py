from __future__ import annotations

import html
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config.config_manager import AppConfig
from src.providers.wechat_client import WechatClient, WechatDraft, WechatMaterial, WechatUploadedImage
from src.repositories.article_layout_repository import ArticleLayoutRecord
from src.repositories.media_asset_repository import MediaAssetRecord
from src.services.article_layout_service import LOCAL_WECHAT_IMAGE_SCHEME, resolve_expected_project_image_count
from src.services.wechat_title_service import validate_wechat_title


@dataclass(frozen=True)
class WechatDraftCreationResult:
    """微信草稿创建服务的输出结果。"""

    draft_media_id: str
    cover_media_id: str
    video_media_id: str | None
    uploaded_image_count: int
    final_article_html_length: int
    response: dict[str, Any]


class WechatDraftCreationService:
    """把排版稿和媒体素材上传到微信，并创建微信公众号草稿。"""

    def create_draft(
        self,
        config: AppConfig,
        layout: ArticleLayoutRecord,
        media_assets: list[MediaAssetRecord],
    ) -> WechatDraftCreationResult:
        """调用微信真实 API 创建草稿。"""
        client = WechatClient(config=config)
        token = client.get_access_token()

        cover_asset = self._find_cover_asset(layout=layout, media_assets=media_assets)
        cover_material = client.add_permanent_material(
            access_token=token.access_token,
            material_type=self._wechat_config(config).get("cover_material_type", "image"),
            file_path=self._resolve_upload_path(config=config, asset=cover_asset),
            title=self._draft_title(config=config, title=layout.title),
            introduction=layout.digest,
        )

        uploaded_images = self._upload_article_images(
            config=config,
            client=client,
            access_token=token.access_token,
            expected_image_count=resolve_expected_project_image_count(layout.payload),
            media_assets=media_assets,
        )
        final_article_html = self._replace_article_image_urls(
            article_html=layout.article_html,
            uploaded_images=uploaded_images,
        )

        video_material = self._try_upload_video_material(
            config=config,
            client=client,
            access_token=token.access_token,
            layout=layout,
            media_assets=media_assets,
        )

        draft_article = self._build_draft_article(
            config=config,
            layout=layout,
            thumb_media_id=cover_material.media_id,
            content_html=final_article_html,
        )
        draft = client.add_draft(
            access_token=token.access_token,
            article=draft_article,
        )

        response = self._build_response_payload(
            token_expires_in=token.expires_in,
            cover_material=cover_material,
            uploaded_images=uploaded_images,
            video_material=video_material,
            draft=draft,
        )
        return WechatDraftCreationResult(
            draft_media_id=draft.media_id,
            cover_media_id=cover_material.media_id,
            video_media_id=None if video_material is None else video_material.media_id,
            uploaded_image_count=len(uploaded_images),
            final_article_html_length=len(final_article_html),
            response=response,
        )

    def _upload_article_images(
        self,
        config: AppConfig,
        client: WechatClient,
        access_token: str,
        expected_image_count: int,
        media_assets: list[MediaAssetRecord],
    ) -> list[dict[str, Any]]:
        """上传正文图片，并记录原始 URL 到微信 URL 的映射。"""
        uploaded_images: list[dict[str, Any]] = []
        image_assets = self._select_article_image_assets(
            media_assets=media_assets,
            expected_image_count=expected_image_count,
        )

        for asset in image_assets:
            upload_result = client.upload_article_image(
                access_token=access_token,
                image_path=self._resolve_upload_path(config=config, asset=asset),
            )
            uploaded_images.append(
                {
                    "asset_id": asset.id,
                    "repository_full_name": asset.metadata.get("repository_full_name"),
                    "original_url_candidates": self._image_url_candidates(asset),
                    "wechat_url": upload_result.url,
                    "raw_response": self._safe_response(upload_result),
                }
            )
        return uploaded_images

    def _try_upload_video_material(
        self,
        config: AppConfig,
        client: WechatClient,
        access_token: str,
        layout: ArticleLayoutRecord,
        media_assets: list[MediaAssetRecord],
    ) -> WechatMaterial | None:
        """如存在本地视频文件，则上传为微信永久视频素材。"""
        if not config.video_submit_enabled:
            return None

        video_assets = sorted(
            [asset for asset in media_assets if asset.asset_type == "video" and asset.status != "replaced"],
            key=lambda item: item.id,
        )
        if not video_assets:
            return None

        video_asset = video_assets[0]
        material_type = str(self._wechat_config(config).get("video_material_type", "video"))
        return client.add_permanent_material(
            access_token=access_token,
            material_type=material_type,
            file_path=self._resolve_upload_path(config=config, asset=video_asset),
            title=self._draft_title(config=config, title=layout.title),
            introduction=layout.digest or self._draft_title(config=config, title=layout.title),
        )

    def _replace_article_image_urls(
        self,
        article_html: str,
        uploaded_images: list[dict[str, Any]],
    ) -> str:
        """把正文里原图片 URL 替换成微信 uploadimg 返回的 URL。"""
        final_html = article_html
        for item in uploaded_images:
            wechat_url = str(item["wechat_url"])
            for original_url in item.get("original_url_candidates", []):
                if not original_url:
                    continue
                final_html = final_html.replace(original_url, wechat_url)
                final_html = final_html.replace(html.escape(original_url, quote=True), html.escape(wechat_url, quote=True))
        return final_html

    def _build_draft_article(
        self,
        config: AppConfig,
        layout: ArticleLayoutRecord,
        thumb_media_id: str,
        content_html: str,
    ) -> dict[str, Any]:
        """构建微信 draft/add 的单篇图文参数。"""
        wechat_config = self._wechat_config(config)
        author = os.getenv(str(wechat_config.get("author_env", "WECHAT_AUTHOR")), "").strip()
        if not author:
            author = str(wechat_config.get("default_author", "GitHub 技术雷达"))

        content_source_url = os.getenv(str(wechat_config.get("content_source_url_env", "WECHAT_CONTENT_SOURCE_URL")), "").strip()
        article = {
            "title": self._draft_title(config=config, title=layout.title),
            "digest": self._truncate(layout.digest, 120),
            "content": content_html,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 1 if bool(wechat_config.get("need_open_comment", False)) else 0,
            "only_fans_can_comment": 1 if bool(wechat_config.get("only_fans_can_comment", False)) else 0,
        }
        if author:
            article["author"] = self._truncate(author, 8)
        if content_source_url:
            article["content_source_url"] = content_source_url
        return article

    def _build_response_payload(
        self,
        token_expires_in: int,
        cover_material: WechatMaterial,
        uploaded_images: list[dict[str, Any]],
        video_material: WechatMaterial | None,
        draft: WechatDraft,
    ) -> dict[str, Any]:
        """生成可写入 draft_records.response_json 的安全响应。"""
        return {
            "wechat_api": {
                "token_expires_in": token_expires_in,
                "cover_media_id": cover_material.media_id,
                "cover_url": cover_material.url,
                "draft_media_id": draft.media_id,
                "video_media_id": None if video_material is None else video_material.media_id,
                "video_url": None if video_material is None else video_material.url,
            },
            "uploaded_article_images": uploaded_images,
            "raw_responses": {
                "cover_material": cover_material.raw_response,
                "video_material": None if video_material is None else video_material.raw_response,
                "draft": draft.raw_response,
            },
        }

    def _find_cover_asset(
        self,
        layout: ArticleLayoutRecord,
        media_assets: list[MediaAssetRecord],
    ) -> MediaAssetRecord:
        """根据 layout.cover_asset_id 找到封面素材。"""
        if layout.cover_asset_id is None:
            raise ValueError("缺少封面图 cover_asset_id，无法创建微信草稿")
        for asset in media_assets:
            if asset.id == layout.cover_asset_id:
                if asset.status == "replaced":
                    raise ValueError(f"封面图素材已被替换：asset_id={layout.cover_asset_id}")
                return asset
        raise ValueError(f"封面图素材不存在：asset_id={layout.cover_asset_id}")

    def _select_article_image_assets(
        self,
        media_assets: list[MediaAssetRecord],
        expected_image_count: int,
    ) -> list[MediaAssetRecord]:
        """按本次排版项目数选择有效正文图片。"""

        image_assets = [
            asset
            for asset in media_assets
            if asset.asset_type == "image" and asset.status != "replaced"
        ]
        return sorted(image_assets, key=lambda item: (self._image_provider_priority(item.provider), item.id))[
            :expected_image_count
        ]

    def _image_provider_priority(self, provider: str) -> int:
        """微信上传时使用与排版一致的图片优先级。"""

        priorities = {
            "github_repository_asset": 0,
            "seedream": 1,
            "local_tech_card": 2,
        }
        return priorities.get(provider, 9)

    def _resolve_upload_path(self, config: AppConfig, asset: MediaAssetRecord) -> Path:
        """解析可上传到微信的本地文件路径，并限制在项目 outputs 目录内。"""
        if asset.path.startswith("http://") or asset.path.startswith("https://"):
            raise ValueError(f"微信上传需要本地文件，当前是远程路径：asset_id={asset.id}")

        raw_path = Path(asset.path)
        candidate = raw_path if raw_path.is_absolute() else config.project_root / raw_path
        resolved_candidate = candidate.resolve()
        resolved_outputs_dir = (config.project_root / "outputs").resolve()
        try:
            resolved_candidate.relative_to(resolved_outputs_dir)
        except ValueError as exc:
            raise ValueError(f"媒体文件不在 outputs 目录内，拒绝上传：asset_id={asset.id}") from exc

        if not resolved_candidate.exists() or not resolved_candidate.is_file():
            raise ValueError(f"媒体文件不存在，无法上传微信：asset_id={asset.id} path={resolved_candidate}")
        return resolved_candidate

    def _image_url_candidates(self, asset: MediaAssetRecord) -> list[str]:
        """生成正文图片替换时可匹配的原 URL 列表。"""
        candidates: list[str] = [f"{LOCAL_WECHAT_IMAGE_SCHEME}://{asset.id}"]
        remote_url = str(asset.metadata.get("remote_url", "")).strip()
        if remote_url:
            candidates.append(remote_url)
        if asset.path.startswith("http://") or asset.path.startswith("https://"):
            candidates.append(asset.path)
        return candidates

    def _safe_response(self, upload_result: WechatUploadedImage) -> dict[str, Any]:
        """保存 uploadimg 响应，避免未来把大字段或敏感字段带进数据库。"""
        return {
            key: value
            for key, value in upload_result.raw_response.items()
            if key not in {"access_token"}
        }

    def _wechat_config(self, config: AppConfig) -> dict[str, Any]:
        """读取 wechat 配置段。"""
        raw_wechat_config = config.raw.get("wechat", {})
        if not isinstance(raw_wechat_config, dict):
            return {}
        return raw_wechat_config

    def _draft_title(self, config: AppConfig, title: str) -> str:
        """保留完整标题，并在提交前执行微信字段长度校验。"""

        del config
        return validate_wechat_title(title)

    def _truncate(self, text: str, max_length: int) -> str:
        """按微信字段长度做 UTF-8 字节安全截断。"""
        normalized = text.strip()
        if len(normalized.encode("utf-8")) <= max_length:
            return normalized

        ellipsis = "…"
        budget = max_length - len(ellipsis.encode("utf-8"))
        if budget <= 0:
            return ""

        result_chars: list[str] = []
        used_bytes = 0
        for char in normalized:
            char_bytes = len(char.encode("utf-8"))
            if used_bytes + char_bytes > budget:
                break
            result_chars.append(char)
            used_bytes += char_bytes
        return "".join(result_chars).rstrip() + ellipsis
