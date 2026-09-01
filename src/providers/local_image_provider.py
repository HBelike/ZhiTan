from __future__ import annotations

import math
import random
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LocalImageResult:
    """本地生成的科技风卡片图结果。"""

    output_path: Path
    metadata: dict[str, Any]


class LocalTechCardImageProvider:
    """用 Pillow 在本地生成科技教学风项目卡片图。

    这个 provider 是免费兜底方案：当 Seedream API Key 尚未配置时，
    ImageTask 仍然可以生成 5 张可预览、可用于本地视频合成的 PNG 图片。
    """

    def __init__(self, width: int = 1600, height: int = 900) -> None:
        self.width = width
        self.height = height

    def generate_card(
        self,
        repository_full_name: str,
        prompt: str,
        output_path: Path,
        index: int,
        total: int,
    ) -> LocalImageResult:
        """生成单张项目卡片图，并写入 output_path。"""

        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:
            raise RuntimeError("缺少 Pillow，无法生成本地科技风卡片图") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)

        image = Image.new("RGB", (self.width, self.height), (12, 18, 30))
        draw = ImageDraw.Draw(image)
        seed = sum(ord(char) for char in repository_full_name) + index * 97
        rng = random.Random(seed)

        self._draw_background(draw=draw, rng=rng)
        self._draw_glow_nodes(draw=draw, rng=rng)
        self._draw_content(draw=draw, repository_full_name=repository_full_name, prompt=prompt, index=index, total=total)

        image.save(output_path, format="PNG", optimize=True)
        return LocalImageResult(
            output_path=output_path,
            metadata={
                "style": "local_tech_teaching_card",
                "width": self.width,
                "height": self.height,
                "fallback": True,
            },
        )

    def _draw_background(self, draw: Any, rng: random.Random) -> None:
        """绘制深色科技教学风背景。"""

        for y in range(self.height):
            ratio = y / max(self.height - 1, 1)
            red = int(10 + 12 * ratio)
            green = int(18 + 28 * ratio)
            blue = int(34 + 45 * ratio)
            draw.line([(0, y), (self.width, y)], fill=(red, green, blue))

        grid_color = (52, 87, 116)
        for x in range(-self.height, self.width, 72):
            draw.line([(x, 0), (x + self.height, self.height)], fill=grid_color, width=1)
        for y in range(70, self.height, 70):
            draw.line([(0, y), (self.width, y)], fill=(35, 55, 79), width=1)

        for _ in range(42):
            x = rng.randint(0, self.width)
            y = rng.randint(0, self.height)
            radius = rng.randint(1, 3)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(117, 214, 255))

        accent = (134, 214, 102)
        draw.rounded_rectangle((84, 70, self.width - 84, self.height - 70), radius=34, outline=(69, 101, 122), width=2)
        draw.line((120, 146, self.width - 120, 146), fill=(56, 87, 110), width=2)
        draw.rounded_rectangle((120, 100, 314, 128), radius=14, fill=(24, 50, 67), outline=accent, width=1)

    def _draw_glow_nodes(self, draw: Any, rng: random.Random) -> None:
        """绘制类似架构图节点的装饰元素。"""

        node_points = []
        for _ in range(8):
            node_points.append((rng.randint(980, 1460), rng.randint(210, 720)))

        for start, end in zip(node_points, node_points[1:]):
            draw.line([start, end], fill=(72, 135, 155), width=2)

        for x, y in node_points:
            radius = rng.randint(16, 28)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(22, 49, 68), outline=(120, 219, 255), width=2)
            inner = max(5, radius // 3)
            draw.ellipse((x - inner, y - inner, x + inner, y + inner), fill=(136, 229, 112))

        for angle in range(0, 360, 20):
            cx = 1280 + int(math.cos(math.radians(angle)) * 86)
            cy = 430 + int(math.sin(math.radians(angle)) * 86)
            draw.line((1280, 430, cx, cy), fill=(34, 89, 118), width=1)

    def _draw_content(
        self,
        draw: Any,
        repository_full_name: str,
        prompt: str,
        index: int,
        total: int,
    ) -> None:
        """绘制项目名称、解释文本和标签。"""

        repo_name = repository_full_name.split("/")[-1] or repository_full_name
        owner = repository_full_name.split("/")[0] if "/" in repository_full_name else "GitHub"

        title_font = self._font(size=66, bold=True)
        repo_font = self._font(size=36, bold=True)
        body_font = self._font(size=28)
        small_font = self._font(size=22, bold=True)
        code_font = self._font(size=24)

        draw.text((146, 95), "GitHub Weekly Radar", font=small_font, fill=(172, 234, 123))
        draw.text((146, 202), repo_name[:28], font=title_font, fill=(245, 250, 255))
        draw.text((150, 286), f"{owner} / 本周 Top {index} of {total}", font=repo_font, fill=(124, 209, 255))

        summary = self._clean_prompt(prompt)
        lines = self._wrap_text(summary, font=body_font, max_width=720, max_lines=6)
        y = 372
        for line in lines:
            draw.text((154, y), line, font=body_font, fill=(222, 232, 224))
            y += 43

        pills = ["Star 增速", "工程价值", "开源项目", "可复用技术"]
        x = 154
        for pill in pills:
            width = self._text_width(draw=draw, text=pill, font=small_font) + 34
            draw.rounded_rectangle((x, 670, x + width, 712), radius=21, fill=(28, 70, 62), outline=(132, 219, 105), width=1)
            draw.text((x + 17, 679), pill, font=small_font, fill=(196, 240, 160))
            x += width + 14

        code_lines = [
            "agent.scan(github.weekly())",
            "rank.by(star_growth, velocity)",
            "explain(project.value())",
            "render(article + video)",
        ]
        card_x, card_y, card_w, card_h = 925, 565, 500, 190
        draw.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + card_h), radius=22, fill=(10, 26, 38), outline=(80, 130, 152), width=2)
        for offset, line in enumerate(code_lines):
            draw.text((card_x + 28, card_y + 24 + offset * 38), line, font=code_font, fill=(143, 228, 255))

    def _font(self, size: int, bold: bool = False) -> Any:
        """加载中文字体，失败时回退到 Pillow 默认字体。"""

        from PIL import ImageFont

        candidates = [
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for candidate in candidates:
            path = Path(candidate)
            if not path.exists():
                continue
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _clean_prompt(self, prompt: str) -> str:
        """把生图 prompt 压缩成适合卡片展示的解释文本。"""

        compact = " ".join(prompt.replace("\r", " ").replace("\n", " ").split())
        if len(compact) > 210:
            return compact[:210].rstrip() + "..."
        return compact or "这个项目在本周 GitHub 热度增长明显，适合用作新技术观察样本。"

    def _wrap_text(self, text: str, font: Any, max_width: int, max_lines: int) -> list[str]:
        """按像素宽度为中英文混排文本换行。"""

        from PIL import Image, ImageDraw

        probe = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(probe)
        lines: list[str] = []
        current = ""

        for char in text:
            candidate = current + char
            if self._text_width(draw=draw, text=candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if lines and len("".join(lines)) < len(text):
            lines[-1] = textwrap.shorten(lines[-1] + "...", width=max(len(lines[-1]), 8), placeholder="...")
        return lines

    def _text_width(self, draw: Any, text: str, font: Any) -> int:
        """返回文本绘制宽度。"""

        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])
