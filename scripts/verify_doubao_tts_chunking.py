"""验证豆包 TTS 长文分段与本地音频拼接，不会调用任何云端 API。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.config_manager import ConfigManager
from src.providers.doubao_tts_provider import DoubaoTtsProvider


def _create_tone(path: Path, frequency: int) -> None:
    """生成短测试音频，模拟多个 TTS 分段。"""

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=24000",
        "-t",
        "0.2",
        "-c:a",
        "libmp3lame",
        "-y",
        str(path),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def main() -> None:
    """执行分段上限和 ffmpeg 拼接验证。"""

    config = ConfigManager(project_root=PROJECT_ROOT).load()
    provider = DoubaoTtsProvider(config=config)
    text = "第一段旁白。" + "这是没有标点的超长文本" * 300
    chunks = provider._split_text_by_utf8_bytes(text, config.audio_max_input_utf8_bytes)
    if not chunks or any(len(chunk.encode("utf-8")) > config.audio_max_input_utf8_bytes for chunk in chunks):
        raise RuntimeError("豆包 TTS 分段未遵守 UTF-8 字节上限")

    with tempfile.TemporaryDirectory(prefix="verify_doubao_tts_") as temporary_directory:
        root = Path(temporary_directory)
        first = root / "first.mp3"
        second = root / "second.mp3"
        output = root / "combined.mp3"
        _create_tone(first, 440)
        _create_tone(second, 660)
        provider._concat_audio_chunks([first, second], output)
        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("豆包 TTS 音频拼接未生成有效输出")
        payload = {
            "status": "ok",
            "text_chunk_count": len(chunks),
            "max_chunk_utf8_bytes": max(len(chunk.encode("utf-8")) for chunk in chunks),
            "combined_audio_size_bytes": output.stat().st_size,
        }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
