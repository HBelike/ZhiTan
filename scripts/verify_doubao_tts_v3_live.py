"""对新版豆包语音 TTS 发起一次最小真实连通性验证。

该脚本从项目根目录 .env 读取 DOUBAO_TTS_API_KEY，不会打印密钥、请求头或音频内容。
每次执行都会调用一次云端服务并消耗少量额度，仅用于人工验收配置是否可用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.config_manager import ConfigManager
from src.providers.doubao_tts_provider import DoubaoTtsProvider


def parse_arguments() -> argparse.Namespace:
    """读取可选测试文案与输出路径，避免将密钥作为命令行参数传递。"""
    parser = argparse.ArgumentParser(description="验证豆包语音 V3 API Key 是否可用")
    parser.add_argument(
        "--text",
        default="你好，这是豆包语音 API Key 的连通性测试。",
        help="用于合成的短文本；默认值仅用于最小验证。",
    )
    parser.add_argument(
        "--output",
        default="outputs/audio/doubao_tts_v3_smoke_test.mp3",
        help="相对于项目根目录的音频输出路径。",
    )
    return parser.parse_args()


def main() -> int:
    """执行一次真实合成，并输出不包含敏感信息的验收元数据。"""
    arguments = parse_arguments()
    config = ConfigManager(project_root=PROJECT_ROOT).load()
    provider = DoubaoTtsProvider(config=config)
    if not provider.has_credentials():
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": f"{config.audio_api_key_env} 或音色配置缺失",
                },
                ensure_ascii=False,
            )
        )
        return 2

    output_path = PROJECT_ROOT / arguments.output
    result = provider.synthesize(arguments.text, output_path)
    print(
        json.dumps(
            {
                "status": "ok",
                "output_path": str(result.output_path),
                "audio_bytes": result.output_path.stat().st_size,
                "voice_type": result.voice_type,
                "chunk_count": result.chunk_count,
                "protocol": result.raw_response.get("protocol"),
                "usage": result.raw_response.get("usage"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
