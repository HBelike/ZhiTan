from __future__ import annotations

import subprocess
from pathlib import Path


class VideoFrameSampler:
    """从本地视频均匀抽取关键帧，供视觉质检模型检查而不上传整段视频。"""

    def sample(
        self,
        input_path: Path,
        duration_seconds: float,
        output_directory: Path,
        frame_count: int,
        timeout_seconds: float,
    ) -> list[Path]:
        """生成固定数量 JPEG 关键帧；失败时保留明确异常供任务重试或人工审核。"""

        if not input_path.exists() or not input_path.is_file():
            raise FileNotFoundError(f"待抽帧视频不存在：{input_path}")
        if duration_seconds <= 0:
            raise ValueError("抽帧前必须提供大于 0 的真实视频时长")
        if frame_count <= 0:
            raise ValueError("frame_count 必须大于 0")
        if timeout_seconds <= 0:
            raise ValueError("抽帧超时必须大于 0")

        output_directory.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = self._resolve_ffmpeg()
        sampled_paths: list[Path] = []
        for index in range(1, frame_count + 1):
            timestamp = min(duration_seconds - 0.04, duration_seconds * index / (frame_count + 1))
            timestamp = max(0.0, timestamp)
            output_path = output_directory / f"frame_{index:02d}.jpg"
            command = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(input_path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(960,iw)':-2",
                "-q:v",
                "3",
                str(output_path),
            ]
            self._run_ffmpeg(command, timeout_seconds, f"抽取第 {index} 帧")
            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError(f"抽帧未生成有效图片：{output_path}")
            sampled_paths.append(output_path)
        return sampled_paths

    @staticmethod
    def _resolve_ffmpeg() -> str:
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError("缺少 imageio-ffmpeg，无法抽取视频关键帧") from exc
        return imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def _run_ffmpeg(command: list[str], timeout_seconds: float, operation: str) -> None:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{operation}超时，已在 {timeout_seconds:.0f} 秒后终止") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "ffmpeg 未返回错误详情").strip()[-1200:]
            raise RuntimeError(f"{operation}失败：{detail}")
