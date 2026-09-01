from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError


class RenderedVisualValidationError(ValueError):
    """渲染产物无法作为目标技术配图使用。"""


class RenderedVisualValidator:
    """校验浏览器返回的位图格式和固定画布尺寸。"""

    def validate_png_bytes(self, payload: bytes, width: int, height: int) -> None:
        if not payload:
            raise RenderedVisualValidationError("Gotenberg 响应不是 PNG：内容为空")

        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if image.format != "PNG":
                    raise RenderedVisualValidationError("Gotenberg 响应不是 PNG")
                if image.size != (width, height):
                    raise RenderedVisualValidationError(
                        "图片尺寸错误："
                        f"expected={width}x{height} "
                        f"actual={image.width}x{image.height}"
                    )
        except RenderedVisualValidationError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError) as exc:
            raise RenderedVisualValidationError("Gotenberg 响应不是 PNG") from exc


__all__ = ["RenderedVisualValidationError", "RenderedVisualValidator"]
