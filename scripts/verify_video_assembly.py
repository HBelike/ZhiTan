"""验证 Seedance 分片 + 统一旁白的本地视频装配，不调用外部付费 API。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.video_assembly_provider import VideoAssemblyClip, VideoAssemblyProvider


def _run(command: list[str]) -> None:
    """执行测试素材生成命令；失败时直接输出 ffmpeg 的明确错误。"""

    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def main() -> int:
    """生成三个彩色片段和一条音频，验证拼接产物可落盘。"""

    provider = VideoAssemblyProvider()
    ffmpeg_path = provider._resolve_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="verify_video_assembly_") as temp_dir:
        root = Path(temp_dir)
        colors = ["0x274c77", "0x6096ba", "0xa3cef1"]
        clips: list[VideoAssemblyClip] = []
        for index, color in enumerate(colors, start=1):
            clip_path = root / f"input_{index}.mp4"
            _run(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:s=640x360:r=30:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(clip_path),
                ]
            )
            clips.append(VideoAssemblyClip(clip_index=index, input_path=clip_path, duration_seconds=1))

        audio_path = root / "voiceover.m4a"
        _run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100:duration=3",
                "-c:a",
                "aac",
                str(audio_path),
            ]
        )

        subtitle_path = root / "subtitles.srt"
        subtitle_path.write_text(
            "1\n00:00:00,050 --> 00:00:00,900\n第一段：真实时长驱动旁白。\n\n"
            "2\n00:00:01,050 --> 00:00:01,900\n第二段：字幕与画面同步。\n\n"
            "3\n00:00:02,050 --> 00:00:02,900\n第三段：最终成片可直接预览。\n",
            encoding="utf-8",
        )

        result = provider.assemble(
            clips=clips,
            audio_path=audio_path,
            subtitle_path=subtitle_path,
            output_path=root / "assembled.mp4",
            resolution="480p",
            timeout_seconds=60,
            require_audio=True,
            burn_subtitles=True,
        )
        if result.size_bytes <= 0 or not result.output_path.exists():
            raise RuntimeError("视频装配验证未产生有效文件")
        print(
            {
                "status": "ok",
                "clip_count": result.clip_count,
                "planned_duration_seconds": result.duration_seconds,
                "size_bytes": result.size_bytes,
                "ffmpeg_path": result.ffmpeg_path,
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
