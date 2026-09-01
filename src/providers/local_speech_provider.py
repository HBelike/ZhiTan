from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalSpeechResult:
    """本机语音合成结果。"""

    output_path: Path
    voice_engine: str


class LocalSpeechProvider:
    """使用 Windows 自带 System.Speech 生成本地旁白音频。

    这是豆包 TTS 的免费兜底方案。它不追求商业级音色，但能让完整内容生产链路
    在没有 TTS API Key 时仍然跑到 Web UI 审核阶段。
    """

    def is_supported(self) -> bool:
        """判断当前机器是否具备本地语音合成能力。"""

        return platform.system().lower() == "windows" and shutil.which("powershell") is not None

    def synthesize(self, text: str, output_path: Path, rate: int = 0) -> LocalSpeechResult:
        """把文本合成为 WAV 文件。"""

        if not self.is_supported():
            raise RuntimeError("当前系统不支持 Windows System.Speech 本地语音合成")

        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("本地语音合成文本不能为空")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        text_path = output_path.with_suffix(".voiceover.txt")
        script_path = output_path.with_suffix(".speech.ps1")
        text_path.write_text(normalized_text, encoding="utf-8")

        script = """
param(
    [string]$TextPath,
    [string]$OutputPath,
    [int]$Rate
)
$ErrorActionPreference = 'Stop'
$text = Get-Content -LiteralPath $TextPath -Raw -Encoding UTF8
$voice = New-Object -ComObject SAPI.SpVoice
$stream = New-Object -ComObject SAPI.SpFileStream
$format = New-Object -ComObject SAPI.SpAudioFormat
$format.Type = 22
$stream.Format = $format
$stream.Open($OutputPath, 3, $false)
$voice.AudioOutputStream = $stream
try { $voice.Rate = [Math]::Max(-10, [Math]::Min(10, $Rate)) } catch { }
try { $voice.Volume = 100 } catch { }
$null = $voice.Speak($text)
$stream.Close()
"""
        script_path.write_text(script, encoding="utf-8")
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    str(text_path),
                    str(output_path),
                    str(rate),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(f"本地语音合成失败：{stderr}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("本地语音合成超时") from exc
        finally:
            try:
                text_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"本地语音合成未生成有效文件：{output_path}")

        _ = completed
        return LocalSpeechResult(output_path=output_path, voice_engine="windows_system_speech")
