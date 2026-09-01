from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from src.observability.langsmith_runtime import trace_operation


@dataclass(frozen=True)
class EdgeTtsResult:
    """edge-tts 在线语音合成结果。"""

    output_path: Path
    voice: str


class EdgeTtsProvider:
    """使用 edge-tts 生成中文旁白音频。

    这是无需 API Key 的免费兜底方案，但它会访问 Microsoft 在线语音服务。
    如果后续配置了豆包 TTS，AudioTask 会优先使用豆包。
    """

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
    ) -> EdgeTtsResult:
        """把文本合成为 MP3 文件。"""

        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("edge-tts 合成文本不能为空")

        return trace_operation(
            run_name="media.audio.edge_tts.synthesize",
            run_type="llm",
            inputs={"input_characters": len(normalized_text)},
            metadata={
                "component": "edge_tts_provider",
                "provider": "edge-tts",
                "model": voice,
            },
            execute=lambda: self._synthesize_without_trace(
                text=normalized_text,
                output_path=output_path,
                voice=voice,
                rate=rate,
                volume=volume,
            ),
            summarize=self._trace_summary,
            tags=("media", "audio", "tts"),
        )

    def _synthesize_without_trace(
        self,
        *,
        text: str,
        output_path: Path,
        voice: str,
        rate: str,
        volume: str,
    ) -> EdgeTtsResult:
        """执行一次真实语音合成；正文与落盘路径只在业务进程内流转。"""

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("缺少 edge-tts，无法使用免费在线语音兜底") from exc

        async def _save() -> None:
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate,
                volume=volume,
            )
            await communicate.save(str(output_path))

        asyncio.run(_save())

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"edge-tts 未生成有效文件：{output_path}")

        return EdgeTtsResult(output_path=output_path, voice=voice)

    @staticmethod
    def _trace_summary(result: EdgeTtsResult) -> dict[str, int | str]:
        """仅上报音频文件规模和完成状态，不上传正文或文件路径。"""

        return {
            "output_bytes": result.output_path.stat().st_size,
            "status": "completed",
        }
