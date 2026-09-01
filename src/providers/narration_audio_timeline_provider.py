from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioFitResult:
    """单段旁白拟合至视频时长后的可追溯结果。"""

    output_path: Path
    source_duration_seconds: float
    target_duration_seconds: float
    speed_ratio: float
    ffmpeg_path: str


class NarrationAudioTimelineProvider:
    """将每段 TTS 音频对齐到对应镜头，禁止通过硬裁切截断旁白。"""

    def fit_to_visual_duration(
        self,
        input_path: Path,
        output_path: Path,
        source_duration_seconds: float,
        visual_duration_seconds: float,
        tail_padding_seconds: float,
        max_tts_speed_ratio: float,
        timeout_seconds: float,
    ) -> AudioFitResult:
        """短音频补静音，略长音频无感加速，过长则交由上游重写而非静默截断。"""

        if not input_path.exists() or not input_path.is_file():
            raise FileNotFoundError(f"旁白原始音频不存在：{input_path}")
        if source_duration_seconds <= 0 or visual_duration_seconds <= 0:
            raise ValueError("旁白和视频时长必须大于 0")
        if tail_padding_seconds < 0 or tail_padding_seconds >= visual_duration_seconds:
            raise ValueError("旁白尾部静音配置无效")
        if max_tts_speed_ratio < 1:
            raise ValueError("最大 TTS 加速比例不能小于 1")

        speech_slot_seconds = visual_duration_seconds - tail_padding_seconds
        speed_ratio = max(1.0, source_duration_seconds / speech_slot_seconds)
        if speed_ratio > max_tts_speed_ratio:
            raise ValueError(
                "旁白真实时长超过镜头可容纳范围，必须缩短文案后重新合成："
                f"source={source_duration_seconds:.3f}s target={speech_slot_seconds:.3f}s ratio={speed_ratio:.3f}"
            )

        ffmpeg_path = self._resolve_ffmpeg()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filters: list[str] = []
        if speed_ratio > 1.0005:
            filters.append(f"atempo={speed_ratio:.6f}")
        filters.append(f"apad=pad_dur={visual_duration_seconds:.3f}")
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-filter:a",
            ",".join(filters),
            "-t",
            f"{visual_duration_seconds:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]
        self._run_ffmpeg(command, timeout_seconds, f"拟合旁白音频 {input_path.name}")
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"旁白拟合没有生成有效文件：{output_path}")

        return AudioFitResult(
            output_path=output_path,
            source_duration_seconds=source_duration_seconds,
            target_duration_seconds=visual_duration_seconds,
            speed_ratio=round(speed_ratio, 5),
            ffmpeg_path=ffmpeg_path,
        )

    def concat_audio(self, audio_paths: list[Path], output_path: Path, timeout_seconds: float) -> str:
        """按镜头顺序无损拼接已统一为 AAC 的音频段。"""

        if not audio_paths:
            raise ValueError("至少需要一个旁白音频片段")
        for path in audio_paths:
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"待拼接旁白不存在：{path}")

        ffmpeg_path = self._resolve_ffmpeg()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".narration_concat_", dir=str(output_path.parent)) as temp_dir:
            manifest_path = Path(temp_dir) / "audio.concat.txt"
            manifest_path.write_text(self._build_concat_manifest(audio_paths), encoding="utf-8")
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
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
            self._run_ffmpeg(command, timeout_seconds, "拼接分段旁白")

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"旁白拼接没有生成有效文件：{output_path}")
        return ffmpeg_path

    @staticmethod
    def _build_concat_manifest(paths: list[Path]) -> str:
        """生成只指向临时工作目录外明确文件的 ffmpeg concat 清单。"""

        def escape(path: Path) -> str:
            return path.resolve().as_posix().replace("'", "'\\''")

        return "\n".join(f"file '{escape(path)}'" for path in paths) + "\n"

    @staticmethod
    def _resolve_ffmpeg() -> str:
        """复用 imageio-ffmpeg 的可移植二进制，保持开发与容器部署一致。"""

        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError("缺少 imageio-ffmpeg，无法处理旁白时间线") from exc
        return imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def _run_ffmpeg(command: list[str], timeout_seconds: float, operation: str) -> None:
        """执行有超时保护的 ffmpeg 子进程，并将诊断信息限制在安全长度。"""

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
