from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalSlideshowVideoResult:
    """本地幻灯片式视频合成结果。"""

    output_path: Path
    size_bytes: int
    ffmpeg_path: str


class LocalSlideshowVideoProvider:
    """使用本地 ffmpeg 把图片和旁白合成为 MP4。

    这是 Seedance 的免费兜底方案：画面不会像生成式视频那样有复杂运动，
    但它能稳定产出“科技教学卡片 + 旁白”的审核版视频。
    """

    def create_video(
        self,
        image_paths: list[Path],
        audio_path: Path,
        output_path: Path,
        clip_duration_seconds: int,
        target_duration_seconds: int,
        resolution: str,
    ) -> LocalSlideshowVideoResult:
        """合成单条 MP4 视频。"""

        if not image_paths:
            raise ValueError("本地视频合成至少需要 1 张图片")
        if not audio_path.exists() or not audio_path.is_file():
            raise RuntimeError(f"本地视频合成缺少旁白音频：{audio_path}")

        for image_path in image_paths:
            if not image_path.exists() or not image_path.is_file():
                raise RuntimeError(f"本地视频合成缺少图片素材：{image_path}")

        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError("缺少 imageio-ffmpeg，无法执行本地视频合成") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = output_path.with_suffix(".concat.txt")
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

        duration = self._duration_per_image(
            image_count=len(image_paths),
            clip_duration_seconds=clip_duration_seconds,
            target_duration_seconds=target_duration_seconds,
        )
        manifest_path.write_text(
            self._build_concat_manifest(image_paths=image_paths, duration=duration),
            encoding="utf-8",
        )

        width, height = self._resolution_to_size(resolution)
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,format=yuv420p"
        )

        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest_path),
            "-i",
            str(audio_path),
            "-vf",
            video_filter,
            "-r",
            "30",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or exc.stdout or "").strip()
            tail = stderr[-1200:] if len(stderr) > 1200 else stderr
            raise RuntimeError(f"本地视频合成失败：{tail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("本地视频合成超时") from exc
        finally:
            try:
                manifest_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"本地视频合成未生成有效文件：{output_path}")

        return LocalSlideshowVideoResult(
            output_path=output_path,
            size_bytes=output_path.stat().st_size,
            ffmpeg_path=ffmpeg_path,
        )

    def _duration_per_image(
        self,
        image_count: int,
        clip_duration_seconds: int,
        target_duration_seconds: int,
    ) -> float:
        """计算每张图停留时长。"""

        if image_count <= 0:
            raise ValueError("image_count 必须大于 0")
        if target_duration_seconds > 0:
            return max(2.0, target_duration_seconds / image_count)
        return max(2.0, float(clip_duration_seconds))

    def _build_concat_manifest(self, image_paths: list[Path], duration: float) -> str:
        """生成 ffmpeg concat demuxer 所需的清单文本。"""

        lines: list[str] = []
        for image_path in image_paths:
            escaped_path = self._escape_concat_path(image_path)
            lines.append(f"file '{escaped_path}'")
            lines.append(f"duration {duration:.3f}")

        lines.append(f"file '{self._escape_concat_path(image_paths[-1])}'")
        return "\n".join(lines) + "\n"

    def _escape_concat_path(self, path: Path) -> str:
        """把本地路径转换成 ffmpeg concat 可读取的形式。"""

        return path.resolve().as_posix().replace("'", "'\\''")

    def _resolution_to_size(self, resolution: str) -> tuple[int, int]:
        """把配置中的分辨率转换成 16:9 画布尺寸。"""

        normalized = resolution.strip().lower()
        if normalized == "1080p":
            return 1920, 1080
        if normalized == "720p":
            return 1280, 720
        if normalized == "480p":
            return 854, 480
        return 1280, 720
