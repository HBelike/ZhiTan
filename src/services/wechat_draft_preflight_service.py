from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from src.config.config_manager import AppConfig
from src.repositories.article_layout_repository import ArticleLayoutRecord
from src.repositories.media_asset_repository import MediaAssetRecord
from src.services.article_layout_service import resolve_expected_project_image_count


@dataclass(frozen=True)
class WechatDraftPreflightResult:
    """DeliverTask 调用微信 API 前的检查结果。"""

    can_call_wechat_api: bool
    status: str
    missing_requirements: list[str]
    warnings: list[str]
    payload: dict[str, Any]
    error_message: str | None


class WechatDraftPreflightService:
    """检查公众号草稿创建所需的配置和素材是否已经齐备。"""

    def build(
        self,
        config: AppConfig,
        layout: ArticleLayoutRecord,
        media_assets: list[MediaAssetRecord],
    ) -> WechatDraftPreflightResult:
        """生成一份不会泄露密钥的微信草稿前置检查报告。"""
        wechat_config = self._wechat_config(config)
        missing_requirements: list[str] = []
        warnings: list[str] = []

        if not bool(wechat_config.get("enabled", True)):
            missing_requirements.append("wechat.enabled=false")

        app_id_env = str(wechat_config.get("app_id_env", "WECHAT_APP_ID"))
        app_secret_env = str(wechat_config.get("app_secret_env", "WECHAT_APP_SECRET"))
        if not os.getenv(app_id_env):
            missing_requirements.append(f"{app_id_env} 未配置")
        if not os.getenv(app_secret_env):
            missing_requirements.append(f"{app_secret_env} 未配置")

        if layout.status != "ready":
            missing_requirements.append(f"article_layouts.status 不是 ready：{layout.status}")

        active_media_assets = [asset for asset in media_assets if asset.status != "replaced"]
        image_assets = [asset for asset in active_media_assets if asset.asset_type == "image"]
        video_assets = [asset for asset in active_media_assets if asset.asset_type == "video"]
        public_image_count = sum(1 for asset in image_assets if self._asset_has_public_url(asset))
        public_video_count = sum(1 for asset in video_assets if self._asset_has_public_url(asset))
        local_uploadable_image_count = sum(1 for asset in image_assets if self._asset_has_uploadable_local_file(config, asset))
        local_uploadable_video_count = sum(1 for asset in video_assets if self._asset_has_uploadable_local_file(config, asset))
        expected_image_count = resolve_expected_project_image_count(layout.payload)

        if bool(wechat_config.get("require_cover_asset", True)) and layout.cover_asset_id is None:
            missing_requirements.append("缺少封面图 cover_asset_id")
        if layout.cover_asset_id is not None:
            cover_asset = next((asset for asset in active_media_assets if asset.id == layout.cover_asset_id), None)
            if cover_asset is None:
                missing_requirements.append(f"封面图素材不存在：asset_id={layout.cover_asset_id}")
            elif not self._asset_has_uploadable_local_file(config, cover_asset):
                missing_requirements.append(f"封面图没有可上传的本地文件：asset_id={layout.cover_asset_id}")

        if bool(wechat_config.get("require_public_article_images", True)):
            missing_image_count = int(layout.payload.get("layout_stats", {}).get("missing_image_count", 0))
            if missing_image_count > 0:
                missing_requirements.append(f"正文还有 {missing_image_count} 张项目图未生成或未上传")
            if public_image_count < expected_image_count:
                missing_requirements.append(
                    f"公网项目图不足 {expected_image_count} 张：当前 {public_image_count} 张"
                )
        if (
            bool(wechat_config.get("require_local_uploadable_images", True))
            and local_uploadable_image_count < expected_image_count
        ):
            missing_requirements.append(
                f"可上传到微信的本地项目图不足 {expected_image_count} 张："
                f"当前 {local_uploadable_image_count} 张"
            )

        if bool(wechat_config.get("require_video_asset", True)) and public_video_count < 1:
            missing_requirements.append("缺少已上传并带公网 URL 的视频素材")
        if bool(wechat_config.get("require_local_uploadable_video", True)) and local_uploadable_video_count < 1:
            missing_requirements.append("缺少可上传到微信的本地视频文件")

        if len(layout.article_html) > 900_000:
            warnings.append("article_html 超过 900KB，接入微信 API 前建议压缩正文")

        endpoint_preview = self._endpoint_preview(wechat_config)
        payload = {
            "layout": {
                "layout_id": layout.id,
                "content_id": layout.content_id,
                "title": layout.title,
                "digest_length": len(layout.digest),
                "html_length": len(layout.article_html),
                "cover_asset_id": layout.cover_asset_id,
                "status": layout.status,
            },
            "wechat": {
                "enabled": bool(wechat_config.get("enabled", True)),
                "api_base_url": str(wechat_config.get("api_base_url", "https://api.weixin.qq.com")),
                "app_id_env": app_id_env,
                "app_id_configured": bool(os.getenv(app_id_env)),
                "app_secret_env": app_secret_env,
                "app_secret_configured": bool(os.getenv(app_secret_env)),
                "timeout_seconds": float(wechat_config.get("timeout_seconds", 30)),
                "endpoints": endpoint_preview,
            },
            "assets": {
                "image_count": len(image_assets),
                "expected_image_count": expected_image_count,
                "public_image_count": public_image_count,
                "video_count": len(video_assets),
                "public_video_count": public_video_count,
                "local_uploadable_image_count": local_uploadable_image_count,
                "local_uploadable_video_count": local_uploadable_video_count,
                "media_asset_count": len(active_media_assets),
            },
            "checks": {
                "missing_requirements": missing_requirements,
                "warnings": warnings,
            },
        }

        can_call_wechat_api = not missing_requirements
        status = "ready_for_wechat_api" if can_call_wechat_api else "preflight_blocked"
        error_message = None if can_call_wechat_api else "；".join(missing_requirements)
        return WechatDraftPreflightResult(
            can_call_wechat_api=can_call_wechat_api,
            status=status,
            missing_requirements=missing_requirements,
            warnings=warnings,
            payload=payload,
            error_message=error_message,
        )

    def _wechat_config(self, config: AppConfig) -> dict[str, Any]:
        """读取 wechat 配置段，缺省时返回安全默认值。"""
        raw_wechat_config = config.raw.get("wechat", {})
        if not isinstance(raw_wechat_config, dict):
            return {}
        return raw_wechat_config

    def _endpoint_preview(self, wechat_config: dict[str, Any]) -> dict[str, str]:
        """构建下一步真实 API 调用会用到的 endpoint 预览。"""
        api_base_url = str(wechat_config.get("api_base_url", "https://api.weixin.qq.com")).rstrip("/") + "/"
        draft_config = wechat_config.get("draft", {})
        if not isinstance(draft_config, dict):
            draft_config = {}

        return {
            "token": urljoin(api_base_url, str(draft_config.get("token_endpoint", "/cgi-bin/token")).lstrip("/")),
            "add_draft": urljoin(api_base_url, str(draft_config.get("add_endpoint", "/cgi-bin/draft/add")).lstrip("/")),
            "upload_image": urljoin(
                api_base_url,
                str(draft_config.get("upload_image_endpoint", "/cgi-bin/media/uploadimg")).lstrip("/"),
            ),
            "add_material": urljoin(
                api_base_url,
                str(draft_config.get("add_material_endpoint", "/cgi-bin/material/add_material")).lstrip("/"),
            ),
        }

    def _asset_has_public_url(self, asset: MediaAssetRecord) -> bool:
        """判断媒体资产是否已经有可被微信服务器访问的公网 URL。"""
        remote_url = str(asset.metadata.get("remote_url", "")).strip()
        if remote_url.startswith("http://") or remote_url.startswith("https://"):
            return True
        return asset.path.startswith("http://") or asset.path.startswith("https://")

    def _asset_has_uploadable_local_file(self, config: AppConfig, asset: MediaAssetRecord) -> bool:
        """判断媒体资产是否存在本地文件，且文件位于项目 outputs 范围内。"""
        raw_path = Path(asset.path)
        if str(asset.path).startswith("http://") or str(asset.path).startswith("https://"):
            return False

        candidate = raw_path if raw_path.is_absolute() else config.project_root / raw_path
        try:
            resolved_candidate = candidate.resolve()
            resolved_outputs_dir = (config.project_root / "outputs").resolve()
            resolved_candidate.relative_to(resolved_outputs_dir)
        except (OSError, ValueError):
            return False

        return resolved_candidate.exists() and resolved_candidate.is_file()
