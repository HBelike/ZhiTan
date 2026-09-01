from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChineseOverlayResult:
    """中文覆盖层处理结果。"""

    title: str
    labels: dict[str, str]
    metadata: dict[str, Any]


class ChineseImageOverlayService:
    """给 AI 底图叠加稳定的中文技术课件标签。"""

    def apply_project_overlay(
        self,
        image_path: Path,
        repository_full_name: str,
        index: int,
    ) -> ChineseOverlayResult:
        """在图片内部叠加中文标题、模块节点和流程箭头。"""

        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:
            raise RuntimeError("缺少 Pillow，无法为图片叠加中文标签") from exc

        profile = self._profile_for_repository(repository_full_name=repository_full_name, index=index)
        image = Image.open(image_path).convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        width, height = image.size
        scale = width / 2048
        title_font = self._font(size=int(78 * scale), bold=True)
        subtitle_font = self._font(size=int(34 * scale), bold=False)
        node_font = self._font(size=int(38 * scale), bold=True)
        small_font = self._font(size=int(28 * scale), bold=True)

        self._draw_top_title(
            draw=draw,
            width=width,
            scale=scale,
            title=profile["title"],
            subtitle=f"本周项目 {index:02d} · 中文技术图解",
            title_font=title_font,
            subtitle_font=subtitle_font,
        )
        self._draw_main_backdrop(draw=draw, width=width, height=height, scale=scale)
        self._draw_flow_diagram(
            draw=draw,
            width=width,
            height=height,
            scale=scale,
            profile=profile,
            node_font=node_font,
            small_font=small_font,
        )

        composed = Image.alpha_composite(image, overlay).convert("RGB")
        composed.save(image_path, format="PNG", optimize=True)
        return ChineseOverlayResult(
            title=profile["title"],
            labels={
                "left_top": profile["left_top"],
                "left_bottom": profile["left_bottom"],
                "center": profile["center"],
                "right_top": profile["right_top"],
                "right_bottom": profile["right_bottom"],
                "bottom": profile["bottom"],
            },
            metadata={
                "chinese_overlay": True,
                "overlay_language": "zh-CN",
                "overlay_title": profile["title"],
            },
        )

    def _draw_top_title(
        self,
        draw: Any,
        width: int,
        scale: float,
        title: str,
        subtitle: str,
        title_font: Any,
        subtitle_font: Any,
    ) -> None:
        """绘制顶部中文标题区。"""

        left = int(116 * scale)
        top = int(96 * scale)
        right = width - int(116 * scale)
        bottom = int(300 * scale)
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=int(38 * scale),
            fill=(5, 16, 31, 196),
            outline=(45, 212, 225, 210),
            width=max(2, int(3 * scale)),
        )
        draw.text((left + int(48 * scale), top + int(34 * scale)), title, font=title_font, fill=(245, 253, 255, 255))
        draw.text(
            (left + int(52 * scale), top + int(126 * scale)),
            subtitle,
            font=subtitle_font,
            fill=(163, 244, 225, 245),
        )

    def _draw_main_backdrop(self, draw: Any, width: int, height: int, scale: float) -> None:
        """绘制主内容遮罩，盖住模型底图里可能残留的乱码或英文小字。"""

        left = int(72 * scale)
        top = int(344 * scale)
        right = width - int(72 * scale)
        bottom = height - int(112 * scale)
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=int(48 * scale),
            fill=(3, 12, 24, 172),
            outline=(20, 184, 166, 190),
            width=max(2, int(3 * scale)),
        )

    def _draw_flow_diagram(
        self,
        draw: Any,
        width: int,
        height: int,
        scale: float,
        profile: dict[str, str],
        node_font: Any,
        small_font: Any,
    ) -> None:
        """绘制中文模块节点和流程箭头。"""

        center = self._node_box(width * 0.34, height * 0.44, width * 0.66, height * 0.58)
        left_top = self._node_box(width * 0.09, height * 0.38, width * 0.29, height * 0.48)
        left_bottom = self._node_box(width * 0.09, height * 0.55, width * 0.29, height * 0.65)
        right_top = self._node_box(width * 0.71, height * 0.38, width * 0.91, height * 0.48)
        right_bottom = self._node_box(width * 0.71, height * 0.55, width * 0.91, height * 0.65)
        bottom = self._node_box(width * 0.28, height * 0.76, width * 0.72, height * 0.87)

        self._draw_arrow(draw, self._right_mid(left_top), self._left_mid(center), scale)
        self._draw_arrow(draw, self._right_mid(left_bottom), self._left_mid(center), scale)
        self._draw_arrow(draw, self._right_mid(center), self._left_mid(right_top), scale)
        self._draw_arrow(draw, self._right_mid(center), self._left_mid(right_bottom), scale)
        self._draw_arrow(draw, self._bottom_mid(center), self._top_mid(bottom), scale)

        self._draw_node(draw, left_top, profile["left_top"], small_font, scale, accent=(34, 211, 238))
        self._draw_node(draw, left_bottom, profile["left_bottom"], small_font, scale, accent=(34, 197, 94))
        self._draw_node(draw, center, profile["center"], node_font, scale, accent=(20, 184, 166), is_center=True)
        self._draw_node(draw, right_top, profile["right_top"], small_font, scale, accent=(96, 165, 250))
        self._draw_node(draw, right_bottom, profile["right_bottom"], small_font, scale, accent=(52, 211, 153))
        self._draw_node(draw, bottom, profile["bottom"], small_font, scale, accent=(250, 204, 21))

    def _draw_node(
        self,
        draw: Any,
        box: tuple[int, int, int, int],
        text: str,
        font: Any,
        scale: float,
        accent: tuple[int, int, int],
        is_center: bool = False,
    ) -> None:
        """绘制单个中文节点。"""

        fill_alpha = 248 if is_center else 238
        outline_alpha = 255 if is_center else 238
        draw.rounded_rectangle(
            box,
            radius=int((34 if is_center else 26) * scale),
            fill=(6, 24, 41, fill_alpha),
            outline=(*accent, outline_alpha),
            width=max(2, int((5 if is_center else 3) * scale)),
        )
        self._center_text(draw=draw, box=box, text=text, font=font, fill=(244, 253, 255, 255))

    def _draw_arrow(self, draw: Any, start: tuple[int, int], end: tuple[int, int], scale: float) -> None:
        """绘制科技感箭头。"""

        width = max(3, int(5 * scale))
        color = (45, 212, 225, 230)
        draw.line((start, end), fill=color, width=width)
        ex, ey = end
        sx, sy = start
        direction = 1 if ex >= sx else -1
        arrow = int(20 * scale)
        draw.polygon(
            [(ex, ey), (ex - direction * arrow, ey - arrow // 2), (ex - direction * arrow, ey + arrow // 2)],
            fill=color,
        )

    def _center_text(self, draw: Any, box: tuple[int, int, int, int], text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
        """把中文文本居中画入节点。"""

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = box[0] + (box[2] - box[0] - text_width) / 2
        y = box[1] + (box[3] - box[1] - text_height) / 2 - bbox[1]
        draw.text((x, y), text, font=font, fill=fill)

    def _profile_for_repository(self, repository_full_name: str, index: int) -> dict[str, str]:
        """根据仓库名生成中文标题和模块标签。"""

        normalized = repository_full_name.lower()
        if "skills" in normalized:
            return {
                "title": "技能调用链",
                "left_top": "需求输入",
                "left_bottom": "工具集合",
                "center": "技能模块",
                "right_top": "能力复用",
                "right_bottom": "智能体行为",
                "bottom": "适合：技能库沉淀",
            }
        if "graphify" in normalized:
            return {
                "title": "代码知识图谱",
                "left_top": "源码输入",
                "left_bottom": "文档输入",
                "center": "图谱引擎",
                "right_top": "节点关系",
                "right_bottom": "检索上下文",
                "bottom": "适合：代码理解",
            }
        if "build-your-own" in normalized:
            return {
                "title": "动手构建路线",
                "left_top": "目标技术",
                "left_bottom": "学习资料",
                "center": "实现路径",
                "right_top": "核心组件",
                "right_bottom": "实践步骤",
                "bottom": "适合：工程学习",
            }
        if "hermes-agent" in normalized:
            return {
                "title": "智能体循环",
                "left_top": "任务输入",
                "left_bottom": "记忆状态",
                "center": "推理核心",
                "right_top": "决策结果",
                "right_bottom": "动作执行",
                "bottom": "适合：智能体实验",
            }
        if "opencode" in normalized:
            return {
                "title": "编码代理流程",
                "left_top": "终端指令",
                "left_bottom": "文件上下文",
                "center": "编码代理",
                "right_top": "代码补丁",
                "right_bottom": "执行反馈",
                "bottom": "适合：命令行开发",
            }
        return {
            "title": f"项目 {index} 技术图解",
            "left_top": "输入层",
            "left_bottom": "上下文",
            "center": "核心模块",
            "right_top": "输出层",
            "right_bottom": "结果反馈",
            "bottom": "适合：工程实践",
        }

    def _font(self, size: int, bold: bool = False) -> Any:
        """加载中文字体，失败时回退到 Pillow 默认字体。"""

        from PIL import ImageFont

        candidates = [
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
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

    def _node_box(self, left: float, top: float, right: float, bottom: float) -> tuple[int, int, int, int]:
        return int(left), int(top), int(right), int(bottom)

    def _left_mid(self, box: tuple[int, int, int, int]) -> tuple[int, int]:
        return box[0], int((box[1] + box[3]) / 2)

    def _right_mid(self, box: tuple[int, int, int, int]) -> tuple[int, int]:
        return box[2], int((box[1] + box[3]) / 2)

    def _top_mid(self, box: tuple[int, int, int, int]) -> tuple[int, int]:
        return int((box[0] + box[2]) / 2), box[1]

    def _bottom_mid(self, box: tuple[int, int, int, int]) -> tuple[int, int]:
        return int((box[0] + box[2]) / 2), box[3]
