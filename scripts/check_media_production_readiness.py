"""检查周榜媒体工作流的生产前置条件，不发起任何外部请求。

本脚本只判断配置和环境变量是否存在，绝不输出任何凭证值。它用于在
手动开启 ``video.submit_enabled`` 之前，提前发现语音、对象存储或
Seedance 配置缺失的问题。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 允许使用 ``python scripts/check_media_production_readiness.py`` 直接执行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.config_manager import AppConfig, ConfigManager
from src.providers.doubao_tts_provider import DoubaoTtsProvider
from src.providers.seedance_video_provider import SeedanceVideoProvider
from src.providers.storage_provider import create_storage_provider


@dataclass
class ReadinessReport:
    """生产前检查的结构化结果，方便终端与 CI 读取。"""

    video_submit_enabled: bool
    audio_enabled: bool
    ready_for_real_seedance_submission: bool = False
    safe_local_mode: bool = False
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """转换为不会泄漏密钥内容的 JSON 对象。"""

        return {
            "video_submit_enabled": self.video_submit_enabled,
            "audio_enabled": self.audio_enabled,
            "ready_for_real_seedance_submission": self.ready_for_real_seedance_submission,
            "safe_local_mode": self.safe_local_mode,
            "checks": self.checks,
            "warnings": self.warnings,
            "blockers": self.blockers,
        }


def build_report(config: AppConfig) -> ReadinessReport:
    """依据当前配置判断本地安全模式或真实云端提交是否已经就绪。"""

    tts_provider = DoubaoTtsProvider(config=config)
    video_provider = SeedanceVideoProvider(config=config)
    storage_provider = create_storage_provider(config=config)
    storage_ready = storage_provider.can_upload()
    public_storage_required = config.video_reference_images_enabled

    report = ReadinessReport(
        video_submit_enabled=config.video_submit_enabled,
        audio_enabled=config.audio_enabled,
    )
    report.checks = {
        "seedance_api_key_present": video_provider.has_api_key(),
        "doubao_tts_credentials_present": tts_provider.has_credentials(),
        "public_storage_ready": storage_ready,
        "public_storage_required": public_storage_required,
        "reference_images_enabled": config.video_reference_images_enabled,
        "video_assembly_enabled": config.video_assembly_enabled,
        "voiceover_required_for_assembly": config.video_assembly_require_voiceover,
    }

    if not config.video_submit_enabled:
        report.safe_local_mode = True
        report.warnings.append("当前 VIDEO_SUBMIT_ENABLED=false：不会创建 Seedance 付费任务。")
        if not config.audio_enabled:
            report.warnings.append("当前 AUDIO_ENABLED=false：不会调用豆包或本地语音生成链路。")
        elif not tts_provider.has_credentials():
            report.warnings.append(
                "豆包语音 V3 API Key 未配置：开发期将依配置回退到本地语音；上线前需配置 DOUBAO_TTS_API_KEY。"
            )
        if public_storage_required and not storage_ready:
            report.warnings.append(
                f"{config.storage_provider} 存储尚不能提供公网图片 URL：本地验证不受影响，真实 Seedance 提交前必须补齐。"
            )
        return report

    if not video_provider.has_api_key():
        report.blockers.append(f"缺少 {config.video_api_key_env}，无法创建 Seedance 视频任务。")
    if public_storage_required and not storage_ready:
        reason = storage_provider.unavailable_reason() or "尚未配置可访问的公网媒体存储。"
        report.blockers.append(f"参考图不能被 Seedance 访问：{reason}")
    if config.video_assembly_require_voiceover and not config.audio_enabled:
        report.blockers.append("成片装配要求统一旁白，但 AUDIO_ENABLED=false。")
    elif config.video_assembly_require_voiceover and not tts_provider.has_credentials():
        report.blockers.append(
            "成片装配要求统一旁白，但豆包语音 V3 API Key 未配置：请配置 DOUBAO_TTS_API_KEY。"
        )
    if not config.video_assembly_enabled:
        report.warnings.append("视频装配已关闭：远程分片完成后不会自动生成可发布的最终 MP4。")

    report.ready_for_real_seedance_submission = not report.blockers
    return report


def main() -> int:
    """加载工程配置并打印脱敏后的就绪检查结果。"""

    config = ConfigManager(project_root=PROJECT_ROOT).load()
    report = build_report(config)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ready_for_real_seedance_submission or report.safe_local_mode else 1


if __name__ == "__main__":
    raise SystemExit(main())
