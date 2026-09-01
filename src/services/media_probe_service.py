from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoProbeResult:
    """本地视频文件的可复现媒体探测结果。"""

    duration_seconds: float
    width: int | None
    height: int | None
    ffmpeg_path: str


class MediaProbeService:
    """通过项目内置 ffmpeg 探测已下载媒体，避免依赖计划时长。"""

    _duration_pattern = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
    _video_stream_pattern = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})", re.IGNORECASE)

    def probe_video(self, input_path: Path, timeout_seconds: float) -> VideoProbeResult:
        """读取视频真实时长和画面尺寸。"""

        return self.probe_media(input_path=input_path, timeout_seconds=timeout_seconds)

    def probe_media(self, input_path: Path, timeout_seconds: float) -> VideoProbeResult:
        """读取 Seedance 实际输出时长；失败时阻止后续旁白与装配。"""

        if timeout_seconds <= 0:
            raise ValueError("媒体探测超时时间必须大于 0")
        if not input_path.exists() or not input_path.is_file():
            raise FileNotFoundError(f"待探测视频不存在：{input_path}")

        ffmpeg_path = self._resolve_ffmpeg()
        try:
            completed = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-i", str(input_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"视频时长探测超时：{input_path.name}") from exc

        output = "\n".join(item for item in [completed.stdout, completed.stderr] if item)
        duration_match = self._duration_pattern.search(output)
        if duration_match is None:
            raise RuntimeError(f"无法从媒体文件读取时长：{input_path.name}")

        hours, minutes, seconds = duration_match.groups()
        duration_seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if duration_seconds <= 0:
            raise RuntimeError(f"媒体文件时长无效：{input_path.name}")

        video_stream_match = self._video_stream_pattern.search(output)
        width: int | None = None
        height: int | None = None
        if video_stream_match is not None:
            width, height = (int(value) for value in video_stream_match.groups())

        return VideoProbeResult(
            duration_seconds=round(duration_seconds, 3),
            width=width,
            height=height,
            ffmpeg_path=ffmpeg_path,
        )

    def _resolve_ffmpeg(self) -> str:
        """复用 imageio-ffmpeg 提供的可移植二进制，便于将来容器化部署。"""

        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError("缺少 imageio-ffmpeg，无法探测视频时长") from exc
        return imageio_ffmpeg.get_ffmpeg_exe()
