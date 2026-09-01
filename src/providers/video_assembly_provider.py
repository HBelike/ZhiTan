from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoAssemblyClip:
    """最终长视频中一个已经下载完成的 Seedance 分片。"""

    clip_index: int
    input_path: Path
    duration_seconds: float


@dataclass(frozen=True)
class VideoAssemblyResult:
    """ffmpeg 装配任务成功后的可追溯结果。"""

    output_path: Path
    size_bytes: int
    duration_seconds: float
    ffmpeg_path: str
    clip_count: int


class VideoAssemblyProvider:
    """将多个 Seedance 片段规范化、拼接并与统一旁白混音。

    Seedance 的单段产物可能在编码、帧率或实际时长上略有差异。先逐段规范化再
    concat，可以避免把不兼容流直接拼接导致的花屏、无声或播放失败；最终只保留
    豆包 TTS 的完整旁白，使讲解节奏在所有片段间保持一致。
    """

    def assemble(
        self,
        clips: list[VideoAssemblyClip],
        audio_path: Path | None,
        subtitle_path: Path | None,
        output_path: Path,
        resolution: str,
        timeout_seconds: float,
        require_audio: bool,
        burn_subtitles: bool,
    ) -> VideoAssemblyResult:
        """生成一个可直接上传公众号的 H.264/AAC MP4 文件。"""

        normalized_clips = self._validate_clips(clips)
        normalized_audio_path = self._validate_audio(audio_path=audio_path, require_audio=require_audio)
        normalized_subtitle_path = self._validate_subtitle_path(subtitle_path=subtitle_path, required=burn_subtitles)
        if timeout_seconds <= 0:
            raise ValueError("视频装配 timeout_seconds 必须大于 0")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = self._resolution_to_size(resolution)
        ffmpeg_path = self._resolve_ffmpeg()
        total_duration_seconds = sum(clip.duration_seconds for clip in normalized_clips)

        with tempfile.TemporaryDirectory(prefix=".seedance_assemble_", dir=str(output_path.parent)) as temp_dir:
            work_dir = Path(temp_dir)
            rendered_paths: list[Path] = []
            for clip in normalized_clips:
                rendered_path = work_dir / f"clip_{clip.clip_index:02d}.mp4"
                self._normalize_clip(
                    ffmpeg_path=ffmpeg_path,
                    input_path=clip.input_path,
                    output_path=rendered_path,
                    duration_seconds=clip.duration_seconds,
                    width=width,
                    height=height,
                    timeout_seconds=timeout_seconds,
                )
                rendered_paths.append(rendered_path)

            manifest_path = work_dir / "clips.concat.txt"
            manifest_path.write_text(self._build_concat_manifest(rendered_paths), encoding="utf-8")
            merged_path = work_dir / "merged_with_voiceover.mp4"
            self._merge_tracks(
                ffmpeg_path=ffmpeg_path,
                manifest_path=manifest_path,
                audio_path=normalized_audio_path,
                output_path=merged_path,
                timeout_seconds=timeout_seconds,
            )
            if normalized_subtitle_path is not None and burn_subtitles:
                self._burn_subtitles(
                    ffmpeg_path=ffmpeg_path,
                    input_path=merged_path,
                    subtitle_path=normalized_subtitle_path,
                    output_path=output_path,
                    timeout_seconds=timeout_seconds,
                )
            else:
                merged_path.replace(output_path)

        if not output_path.exists() or not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"视频装配未生成有效文件：{output_path}")

        return VideoAssemblyResult(
            output_path=output_path,
            size_bytes=output_path.stat().st_size,
            duration_seconds=total_duration_seconds,
            ffmpeg_path=ffmpeg_path,
            clip_count=len(normalized_clips),
        )

    def _validate_clips(self, clips: list[VideoAssemblyClip]) -> list[VideoAssemblyClip]:
        """在调用 ffmpeg 前校验片段顺序、文件与时长。"""

        if not clips:
            raise ValueError("视频装配至少需要一个完成的 Seedance 片段")

        normalized = sorted(clips, key=lambda item: item.clip_index)
        expected_indices = list(range(1, len(normalized) + 1))
        actual_indices = [item.clip_index for item in normalized]
        if actual_indices != expected_indices:
            raise ValueError(f"视频片段序号必须从 1 连续递增，当前为：{actual_indices}")

        for clip in normalized:
            if clip.duration_seconds <= 0:
                raise ValueError(f"视频片段时长必须大于 0：clip_index={clip.clip_index}")
            if not clip.input_path.exists() or not clip.input_path.is_file():
                raise FileNotFoundError(f"视频片段文件不存在：clip_index={clip.clip_index} path={clip.input_path}")
        return normalized

    def _validate_audio(self, audio_path: Path | None, require_audio: bool) -> Path | None:
        """验证旁白文件；开发预览允许关闭强制旁白以便单独验收视频链路。"""

        if audio_path is None:
            if require_audio:
                raise RuntimeError("最终视频装配缺少统一旁白音频")
            return None
        if not audio_path.exists() or not audio_path.is_file():
            raise FileNotFoundError(f"旁白音频文件不存在：{audio_path}")
        return audio_path

    def _validate_subtitle_path(self, subtitle_path: Path | None, required: bool) -> Path | None:
        """验证最终成片所用 SRT；开启烧录时字幕缺失必须显式失败。"""

        if subtitle_path is None:
            if required:
                raise RuntimeError("最终视频开启了字幕烧录，但没有可用的 SRT 时间线")
            return None
        if not subtitle_path.exists() or not subtitle_path.is_file():
            raise FileNotFoundError(f"字幕时间线文件不存在：{subtitle_path}")
        return subtitle_path

    def _resolve_ffmpeg(self) -> str:
        """优先使用项目现有 imageio-ffmpeg，避免额外依赖系统级安装。"""

        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError("缺少 imageio-ffmpeg，无法执行视频装配") from exc
        return imageio_ffmpeg.get_ffmpeg_exe()

    def _normalize_clip(
        self,
        ffmpeg_path: str,
        input_path: Path,
        output_path: Path,
        duration_seconds: float,
        width: int,
        height: int,
        timeout_seconds: float,
    ) -> None:
        """裁切单段视频、统一帧率/像素格式并移除模型原生音轨。"""

        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih):color=black,"
            "fps=30,setsar=1,format=yuv420p"
        )
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-t",
            f"{duration_seconds:.3f}",
            "-vf",
            video_filter,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_ffmpeg(command=command, timeout_seconds=timeout_seconds, operation=f"规范化片段 {input_path.name}")

    def _merge_tracks(
        self,
        ffmpeg_path: str,
        manifest_path: Path,
        audio_path: Path | None,
        output_path: Path,
        timeout_seconds: float,
    ) -> None:
        """用 concat demuxer 拼接视频，并在存在旁白时混入单一音轨。"""

        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest_path),
        ]
        if audio_path is not None:
            command.extend(
                [
                    "-i",
                    str(audio_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-shortest",
                ]
            )
        else:
            command.extend(["-map", "0:v:0", "-c:v", "copy", "-an"])
        command.extend(["-movflags", "+faststart", str(output_path)])
        self._run_ffmpeg(command=command, timeout_seconds=timeout_seconds, operation="拼接视频与旁白")

    def _burn_subtitles(
        self,
        ffmpeg_path: str,
        input_path: Path,
        subtitle_path: Path,
        output_path: Path,
        timeout_seconds: float,
    ) -> None:
        """使用 libass 将 UTF-8 中文 SRT 烧录进画面，确保公众号播放器默认可见。"""

        subtitle_filter = (
            "subtitles="
            f"filename='{self._escape_subtitle_filter_path(subtitle_path)}':"
            "charenc=UTF-8:"
            "force_style='FontName=Microsoft YaHei,FontSize=20,Outline=1,Shadow=0,Alignment=2,MarginV=32'"
        )
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_ffmpeg(command=command, timeout_seconds=timeout_seconds, operation="烧录中文字幕")

    def _run_ffmpeg(self, command: list[str], timeout_seconds: float, operation: str) -> None:
        """执行受时限保护的 ffmpeg 子进程，并截断冗长错误信息。"""

        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{operation}超时，已在 {timeout_seconds:.0f} 秒后终止") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "ffmpeg 未返回错误详情").strip()
            if len(detail) > 1200:
                detail = detail[-1200:]
            raise RuntimeError(f"{operation}失败：{detail}") from exc

    def _build_concat_manifest(self, paths: list[Path]) -> str:
        """生成只引用本次临时工作目录文件的 concat 清单。"""

        return "\n".join(f"file '{self._escape_concat_path(path)}'" for path in paths) + "\n"

    def _escape_concat_path(self, path: Path) -> str:
        """将 Windows 路径转换成 ffmpeg concat 可识别的安全形式。"""

        return path.resolve().as_posix().replace("'", "'\\''")

    def _escape_subtitle_filter_path(self, path: Path) -> str:
        """转义 Windows 路径，供 ffmpeg 的 subtitles filter 读取。"""

        return path.resolve().as_posix().replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    def _resolution_to_size(self, resolution: str) -> tuple[int, int]:
        """将项目使用的分辨率枚举转换为 16:9 画布尺寸。"""

        normalized = resolution.strip().lower()
        if normalized == "1080p":
            return 1920, 1080
        if normalized == "480p":
            return 854, 480
        return 1280, 720
