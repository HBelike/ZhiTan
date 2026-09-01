from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config.config_manager import AppConfig
from src.providers.doubao_tts_provider import DoubaoTtsProvider
from src.repositories.generated_content_repository import GeneratedContentForVideo
from src.repositories.media_asset_repository import MediaAssetRecord


@dataclass(frozen=True)
class VideoMaterialReadiness:
    """视频生成前的素材检查结果。"""

    ready_for_submission: bool
    missing_requirements: list[str]
    warnings: list[str]
    public_image_urls: list[str]
    selected_image_assets: list[MediaAssetRecord]
    selected_audio_asset: MediaAssetRecord | None
    existing_video_assets: list[MediaAssetRecord]
    existing_video_task_assets: list[MediaAssetRecord]
    manifest: dict[str, Any]


class VideoMaterialService:
    """检查 Seedance 视频生成所需的图片、音频、脚本和 API 配置。"""

    def build_readiness(
        self,
        config: AppConfig,
        content: GeneratedContentForVideo,
        media_assets: list[MediaAssetRecord],
        video_credentials_ready: bool,
    ) -> VideoMaterialReadiness:
        """生成可记录到 task_runs 的视频素材清单。"""
        required_image_count = config.video_required_image_count
        active_media_assets = [asset for asset in media_assets if asset.status != "replaced"]
        image_assets = self._sort_image_assets([asset for asset in active_media_assets if asset.asset_type == "image"])
        audio_assets = [asset for asset in active_media_assets if asset.asset_type == "audio"]
        existing_video_assets = [asset for asset in active_media_assets if asset.asset_type == "video"]
        existing_video_task_assets = [asset for asset in active_media_assets if asset.asset_type == "video_task"]

        selected_image_assets = image_assets[:required_image_count]
        public_image_urls = self._extract_public_image_urls(selected_image_assets)
        selected_audio_asset = self._select_audio_asset(config=config, audio_assets=audio_assets)

        missing_requirements: list[str] = []
        warnings: list[str] = []

        if not content.video_script.strip():
            missing_requirements.append("video_script 为空")
        if not content.voiceover_text.strip():
            missing_requirements.append("voiceover_text 为空")

        if len(image_assets) < required_image_count:
            missing_requirements.append(f"图片素材不足：需要 {required_image_count} 张，当前 {len(image_assets)} 张")
        if len(public_image_urls) < required_image_count:
            missing_requirements.append(
                f"可供 Seedance 读取的公网图片 URL 不足：需要 {required_image_count} 个，当前 {len(public_image_urls)} 个"
            )

        if not audio_assets:
            missing_requirements.append("缺少旁白音频素材")
        elif selected_audio_asset is None:
            missing_requirements.append("缺少可用于后期合成的本地旁白音频文件")

        if not self._audio_credentials_ready(config):
            warnings.append("豆包 TTS 凭证未配置；如果音频已手动补齐，可忽略此警告")

        if not video_credentials_ready:
            missing_requirements.append(f"{config.video_api_key_env} 未配置")

        local_image_count = sum(1 for asset in image_assets if self._has_local_file(config=config, asset=asset))
        public_image_count = len(self._extract_public_image_urls(image_assets))
        local_audio_count = sum(1 for asset in audio_assets if self._has_local_file(config=config, asset=asset))

        manifest = {
            "content": {
                "content_id": content.id,
                "week_end": content.week_end,
                "title": content.title,
                "video_script_length": len(content.video_script),
                "voiceover_text_length": len(content.voiceover_text),
            },
            "video_config": {
                "provider": config.video_provider,
                "model": config.video_model,
                "required_image_count": required_image_count,
                "clip_duration_seconds": config.video_clip_duration_seconds,
                "target_duration_seconds": config.video_duration_seconds,
                "resolution": config.video_resolution,
                "aspect_ratio": config.video_aspect_ratio,
                "generation_type": config.video_generation_type,
                "generate_audio": config.video_generate_audio,
            },
            "assets": {
                "image_asset_count": len(image_assets),
                "selected_image_asset_ids": [asset.id for asset in selected_image_assets],
                "public_image_url_count": public_image_count,
                "local_image_file_count": local_image_count,
                "audio_asset_count": len(audio_assets),
                "local_audio_file_count": local_audio_count,
                "selected_audio_asset_id": None if selected_audio_asset is None else selected_audio_asset.id,
                "existing_video_asset_count": len(existing_video_assets),
                "existing_video_task_count": len(existing_video_task_assets),
            },
            "seedance_submission": {
                "public_image_urls": public_image_urls,
                "video_credentials_ready": video_credentials_ready,
            },
            "checks": {
                "missing_requirements": missing_requirements,
                "warnings": warnings,
            },
        }

        return VideoMaterialReadiness(
            ready_for_submission=not missing_requirements,
            missing_requirements=missing_requirements,
            warnings=warnings,
            public_image_urls=public_image_urls,
            selected_image_assets=selected_image_assets,
            selected_audio_asset=selected_audio_asset,
            existing_video_assets=existing_video_assets,
            existing_video_task_assets=existing_video_task_assets,
            manifest=manifest,
        )

    def build_video_prompt(self, title: str, video_script: str) -> str:
        """把文章视频脚本压缩成 Seedance 可用的视频生成 prompt。"""
        script_excerpt = video_script.strip().replace("\r", " ").replace("\n", " ")
        if len(script_excerpt) > 900:
            script_excerpt = script_excerpt[:900].rstrip() + "..."

        return (
            "科技教学科普视频，深色代码背景，清晰信息图层，镜头缓慢推进，"
            "突出开源项目、GitHub 周榜、工程实践、代码结构和知识卡片。"
            f"标题：{title.strip()}。"
            f"脚本摘要：{script_excerpt}"
        )

    def _sort_image_assets(self, image_assets: list[MediaAssetRecord]) -> list[MediaAssetRecord]:
        """优先按 prompt_index 排序，其次按 asset_id 排序。"""
        return sorted(
            image_assets,
            key=lambda asset: (
                int(asset.metadata.get("prompt_index", 9999) or 9999),
                self._image_provider_priority(asset.provider),
                asset.id,
            ),
        )

    def _image_provider_priority(self, provider: str) -> int:
        """同一 prompt_index 下优先使用真实项目图，其次 AI 生图，最后本地兜底图。"""

        priorities = {
            "github_repository_asset": 0,
            "seedream": 1,
            "local_tech_card": 2,
        }
        return priorities.get(provider, 9)

    def _extract_public_image_urls(self, image_assets: list[MediaAssetRecord]) -> list[str]:
        """从图片资产里提取可供远端视频 API 访问的公网 URL。"""
        urls: list[str] = []
        for asset in image_assets:
            candidates = [
                asset.metadata.get("remote_url"),
                asset.metadata.get("source_url"),
                asset.metadata.get("url"),
                asset.path,
            ]
            for candidate in candidates:
                if not isinstance(candidate, str):
                    continue
                normalized = candidate.strip()
                if normalized.startswith("http://") or normalized.startswith("https://"):
                    urls.append(normalized)
                    break
        return urls

    def _select_audio_asset(
        self,
        config: AppConfig,
        audio_assets: list[MediaAssetRecord],
    ) -> MediaAssetRecord | None:
        """选择一个可用于后期合成的本地音频文件。"""
        for asset in sorted(audio_assets, key=lambda item: item.id):
            if self._has_local_file(config=config, asset=asset):
                return asset
        return None

    def _has_local_file(self, config: AppConfig, asset: MediaAssetRecord) -> bool:
        """判断媒体资产是否有位于 outputs 目录内的本地文件。"""
        if asset.path.startswith("http://") or asset.path.startswith("https://"):
            return False

        raw_path = Path(asset.path)
        candidate = raw_path if raw_path.is_absolute() else config.project_root / raw_path
        try:
            resolved_candidate = candidate.resolve()
            resolved_outputs_dir = (config.project_root / "outputs").resolve()
            resolved_candidate.relative_to(resolved_outputs_dir)
        except (OSError, ValueError):
            return False

        return resolved_candidate.exists() and resolved_candidate.is_file()

    def _audio_credentials_ready(self, config: AppConfig) -> bool:
        """通过新版豆包语音 Provider 检查 API Key 与音色是否齐全。"""
        return DoubaoTtsProvider(config=config).has_credentials()
