from __future__ import annotations

from pathlib import Path
from typing import Any

from src.providers.doubao_tts_provider import DoubaoTtsApiError, DoubaoTtsProvider
from src.providers.edge_tts_provider import EdgeTtsProvider
from src.providers.local_speech_provider import LocalSpeechProvider
from src.repositories.generated_content_repository import GeneratedContentForVideo, GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetInput, MediaAssetRecord, MediaAssetRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class AudioTask(BaseTask):
    """把 SummaryTask 产出的旁白文本合成为音频资产。"""

    task_name = "AudioTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取最新内容，生成或复用旁白音频，并写入 media_assets。"""

        if not context.config.audio_enabled:
            self.logger.info("audio.enabled=false，AudioTask 跳过全部音频生成")
            return {
                "skipped": True,
                "skip_reason": "audio.enabled=false",
                "disabled_by_config": True,
                "network_called": False,
            }

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)

        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可生成音频的 generated_contents，请先运行 SummaryTask")
        if not content.voiceover_text:
            raise RuntimeError(f"content_id={content.id} voiceover_text 为空，无法生成音频")

        existing_audio_assets = media_asset_repository.list_by_content_id(content.id, "audio")
        reusable_audio_asset = self._find_reusable_audio_asset(
            project_root=context.config.project_root,
            existing_audio_assets=existing_audio_assets,
        )
        if reusable_audio_asset is not None:
            self.logger.info(
                "旁白音频已经存在且可复用，AudioTask 跳过重复生成：content_id=%s asset_id=%s",
                content.id,
                reusable_audio_asset.id,
            )
            return {
                "content_id": content.id,
                "week_end": content.week_end,
                "text_length": len(content.voiceover_text),
                "audio_asset_count": len(existing_audio_assets),
                "reusable_audio_asset_id": reusable_audio_asset.id,
                "provider": reusable_audio_asset.provider,
                "skipped": True,
                "skip_reason": "audio_asset_already_exists",
                "network_called": False,
            }

        provider = DoubaoTtsProvider(config=context.config)
        if not provider.has_credentials():
            return self._handle_doubao_unavailable(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                existing_audio_assets=existing_audio_assets,
                unavailable_reason="doubao_tts_credentials_missing",
            )

        output_path = self._build_output_path(
            output_dir=context.config.audio_output_dir,
            content=content,
            encoding=context.config.audio_encoding,
        )
        try:
            tts_result = provider.synthesize(text=content.voiceover_text, output_path=output_path)
        except DoubaoTtsApiError as exc:
            self.logger.warning(
                "豆包 TTS 调用失败，将按配置尝试本地语音兜底：content_id=%s error=%s",
                content.id,
                exc,
            )
            return self._handle_doubao_unavailable(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                existing_audio_assets=existing_audio_assets,
                unavailable_reason=f"doubao_tts_request_failed: {exc}",
            )
        asset = media_asset_repository.create(
            MediaAssetInput(
                content_id=content.id,
                repository_id=None,
                asset_type="audio",
                provider=context.config.audio_provider,
                path=str(tts_result.output_path),
                mime_type=self._mime_type_for_encoding(context.config.audio_encoding),
                status="created",
                metadata={
                    "reqid": tts_result.reqid,
                    "voice_type": tts_result.voice_type,
                    "chunk_count": tts_result.chunk_count,
                    "text_length": len(content.voiceover_text),
                    "encoding": context.config.audio_encoding,
                    "rate": context.config.audio_rate,
                    "raw_response": tts_result.raw_response,
                },
            )
        )
        self.logger.info("豆包旁白音频生成完成：content_id=%s asset_id=%s path=%s", content.id, asset.id, asset.path)

        return {
            "content_id": content.id,
            "week_end": content.week_end,
            "text_length": len(content.voiceover_text),
            "audio_asset_count": len(existing_audio_assets) + 1,
            "asset_id": asset.id,
            "path": asset.path,
            "provider": context.config.audio_provider,
            "voice_type": tts_result.voice_type,
            "chunk_count": tts_result.chunk_count,
            "skipped": False,
            "network_called": True,
        }

    def _handle_doubao_unavailable(
        self,
        context: TaskContext,
        content: GeneratedContentForVideo,
        media_asset_repository: MediaAssetRepository,
        existing_audio_assets: list[MediaAssetRecord],
        unavailable_reason: str,
    ) -> dict[str, Any]:
        """豆包 TTS 凭证缺失或调用暂时失败时，尝试免费兜底语音方案。"""

        if context.config.audio_local_fallback_enabled:
            fallback_errors: list[str] = []

            system_result = self._try_windows_system_speech(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                existing_audio_assets=existing_audio_assets,
            )
            if system_result["created"]:
                return {**system_result["payload"], "doubao_unavailable_reason": unavailable_reason}
            fallback_errors.extend(system_result["errors"])

            edge_result = self._try_edge_tts(
                context=context,
                content=content,
                media_asset_repository=media_asset_repository,
                existing_audio_assets=existing_audio_assets,
            )
            if edge_result["created"]:
                return {**edge_result["payload"], "doubao_unavailable_reason": unavailable_reason}
            fallback_errors.extend(edge_result["errors"])

            if not context.config.audio_skip_when_api_key_missing:
                raise RuntimeError("免费语音兜底全部失败：" + "；".join(fallback_errors))

            return {
                "content_id": content.id,
                "week_end": content.week_end,
                "text_length": len(content.voiceover_text),
                "audio_asset_count": len(existing_audio_assets),
                "reusable_audio_asset_id": None,
                "provider": context.config.audio_provider,
                "voice_type": context.config.audio_default_voice_type,
                "skipped": True,
                "skip_reason": "local_audio_fallback_failed",
                "doubao_unavailable_reason": unavailable_reason,
                "local_fallback_errors": fallback_errors,
                "network_called": False,
            }

        if context.config.audio_skip_when_api_key_missing:
            self.logger.warning("豆包 TTS 凭证未配置完整，AudioTask 跳过真实音频生成")
            return {
                "content_id": content.id,
                "week_end": content.week_end,
                "text_length": len(content.voiceover_text),
                "audio_asset_count": len(existing_audio_assets),
                "reusable_audio_asset_id": None,
                "provider": context.config.audio_provider,
                "voice_type": context.config.audio_default_voice_type,
                "skipped": True,
                "skip_reason": "doubao_tts_credentials_missing",
                "doubao_unavailable_reason": unavailable_reason,
                "network_called": False,
            }

        raise RuntimeError(f"豆包 TTS 不可用，且未启用本地语音兜底：{unavailable_reason}")

    def _try_windows_system_speech(
        self,
        context: TaskContext,
        content: GeneratedContentForVideo,
        media_asset_repository: MediaAssetRepository,
        existing_audio_assets: list[MediaAssetRecord],
    ) -> dict[str, Any]:
        """尝试使用 Windows 本机语音合成。"""

        local_provider = LocalSpeechProvider()
        if not local_provider.is_supported():
            return {"created": False, "errors": ["windows_system_speech_not_supported"]}

        try:
            output_path = self._build_output_path(
                output_dir=context.config.audio_output_dir,
                content=content,
                encoding="wav",
            )
            speech_result = local_provider.synthesize(
                text=content.voiceover_text,
                output_path=output_path,
                rate=0,
            )
            asset = media_asset_repository.create(
                MediaAssetInput(
                    content_id=content.id,
                    repository_id=None,
                    asset_type="audio",
                    provider="local_system_speech",
                    path=str(speech_result.output_path),
                    mime_type="audio/wav",
                    status="created",
                    metadata={
                        "voice_engine": speech_result.voice_engine,
                        "text_length": len(content.voiceover_text),
                        "encoding": "wav",
                        "fallback": True,
                    },
                )
            )
            self.logger.info(
                "本机旁白音频生成完成：content_id=%s asset_id=%s path=%s",
                content.id,
                asset.id,
                asset.path,
            )
            return {
                "created": True,
                "errors": [],
                "payload": {
                    "content_id": content.id,
                    "week_end": content.week_end,
                    "text_length": len(content.voiceover_text),
                    "audio_asset_count": len(existing_audio_assets) + 1,
                    "asset_id": asset.id,
                    "path": asset.path,
                    "provider": "local_system_speech",
                    "voice_type": "windows_system_speech",
                    "local_fallback_used": True,
                    "skipped": False,
                    "network_called": False,
                },
            }
        except Exception as exc:
            self.logger.warning("本机语音兜底失败，将尝试 edge-tts：content_id=%s error=%s", content.id, exc)
            return {"created": False, "errors": [f"windows_system_speech_failed: {exc}"]}

    def _try_edge_tts(
        self,
        context: TaskContext,
        content: GeneratedContentForVideo,
        media_asset_repository: MediaAssetRepository,
        existing_audio_assets: list[MediaAssetRecord],
    ) -> dict[str, Any]:
        """尝试使用 edge-tts 免费在线语音合成。"""

        try:
            output_path = self._build_output_path(
                output_dir=context.config.audio_output_dir,
                content=content,
                encoding="mp3",
            )
            edge_result = EdgeTtsProvider().synthesize(
                text=content.voiceover_text,
                output_path=output_path,
            )
            asset = media_asset_repository.create(
                MediaAssetInput(
                    content_id=content.id,
                    repository_id=None,
                    asset_type="audio",
                    provider="edge_tts_free",
                    path=str(edge_result.output_path),
                    mime_type="audio/mpeg",
                    status="created",
                    metadata={
                        "voice": edge_result.voice,
                        "text_length": len(content.voiceover_text),
                        "encoding": "mp3",
                        "fallback": True,
                        "requires_api_key": False,
                    },
                )
            )
            self.logger.info(
                "edge-tts 旁白音频生成完成：content_id=%s asset_id=%s path=%s",
                content.id,
                asset.id,
                asset.path,
            )
            return {
                "created": True,
                "errors": [],
                "payload": {
                    "content_id": content.id,
                    "week_end": content.week_end,
                    "text_length": len(content.voiceover_text),
                    "audio_asset_count": len(existing_audio_assets) + 1,
                    "asset_id": asset.id,
                    "path": asset.path,
                    "provider": "edge_tts_free",
                    "voice_type": edge_result.voice,
                    "local_fallback_used": True,
                    "skipped": False,
                    "network_called": True,
                    "requires_api_key": False,
                },
            }
        except Exception as exc:
            self.logger.warning("edge-tts 免费语音兜底失败：content_id=%s error=%s", content.id, exc)
            return {"created": False, "errors": [f"edge_tts_failed: {exc}"]}

    def _find_reusable_audio_asset(
        self,
        project_root: Path,
        existing_audio_assets: list[MediaAssetRecord],
    ) -> MediaAssetRecord | None:
        """选择一条仍然可用的音频资产。"""

        for asset in sorted(existing_audio_assets, key=lambda item: item.id):
            if self._asset_is_reusable(project_root=project_root, asset=asset):
                return asset
        return None

    def _asset_is_reusable(self, project_root: Path, asset: MediaAssetRecord) -> bool:
        """判断音频资产是否可复用。"""

        remote_url = str(asset.metadata.get("remote_url", "")).strip()
        if remote_url.startswith("http://") or remote_url.startswith("https://"):
            return True
        if asset.path.startswith("http://") or asset.path.startswith("https://"):
            return True

        raw_path = Path(asset.path)
        candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
        try:
            return candidate.exists() and candidate.is_file()
        except OSError:
            return False

    def _build_output_path(self, output_dir: Path, content: GeneratedContentForVideo, encoding: str) -> Path:
        """为旁白音频生成稳定的本地保存路径。"""

        normalized_encoding = encoding.strip().lower() or "mp3"
        return output_dir / content.week_end / f"{content.id}_voiceover.{normalized_encoding}"

    def _mime_type_for_encoding(self, encoding: str) -> str:
        """根据编码格式返回 MIME 类型。"""

        normalized_encoding = encoding.strip().lower()
        if normalized_encoding == "mp3":
            return "audio/mpeg"
        if normalized_encoding == "wav":
            return "audio/wav"
        if normalized_encoding == "ogg_opus":
            return "audio/ogg"
        if normalized_encoding == "pcm":
            return "audio/L16"
        return "application/octet-stream"
