"""离线验证视频关键帧抽取，不调用任何云端模型或付费接口。"""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

import imageio_ffmpeg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.video_frame_sampler import VideoFrameSampler


def main() -> int:
    """生成三秒测试视频并验证均匀抽取三帧。"""

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="video_frame_sampler_") as raw_directory:
        directory = Path(raw_directory)
        video_path = directory / "sample.mp4"
        output_directory = directory / "frames"
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25:duration=3",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ]
        import subprocess

        subprocess.run(command, check=True, capture_output=True)
        frames = VideoFrameSampler().sample(
            input_path=video_path,
            duration_seconds=3,
            output_directory=output_directory,
            frame_count=3,
            timeout_seconds=30,
        )
        if len(frames) != 3 or any(not path.exists() or path.stat().st_size <= 0 for path in frames):
            raise RuntimeError(f"关键帧抽取验证失败：{frames}")
        print({"status": "ok", "frame_count": len(frames), "frame_names": [path.name for path in frames]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
