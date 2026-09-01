from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import requests

from src.services.article_visual_template_service import HtmlVisualDocument
from src.services.rendered_visual_validator import (
    RenderedVisualValidationError,
    RenderedVisualValidator,
)


RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
TRANSIENT_REQUEST_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
)
WAIT_FOR_VALIDATION_EXPRESSION = (
    "window.__visualValidation?.status === 'passed'"
)


class GotenbergScreenshotConfig(Protocol):
    image_gotenberg_base_url: str
    image_gotenberg_timeout_seconds: float
    image_renderer_max_attempts: int
    image_canvas_width: int
    image_canvas_height: int
    image_font_path: Path


class GotenbergScreenshotError(RuntimeError):
    """Gotenberg 请求或输出校验失败，并保留可观测的尝试信息。"""

    def __init__(
        self,
        message: str,
        *,
        attempt_count: int,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.attempt_count = attempt_count
        self.status_code = status_code


@dataclass(frozen=True)
class GotenbergScreenshotResult:
    """通过校验并原子落盘的截图结果。"""

    output_path: Path
    trace_id: str | None
    attempts: int


class GotenbergScreenshotProvider:
    """按固定契约调用 Gotenberg HTML screenshot endpoint。"""

    def __init__(
        self,
        config: GotenbergScreenshotConfig,
        *,
        session: requests.Session | None = None,
        validator: RenderedVisualValidator | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        # 本地/Docker 私网服务不能继承 Windows 系统代理。
        self.session.trust_env = False
        self.validator = validator or RenderedVisualValidator()
        self.sleeper = sleeper

    def render(
        self,
        document: HtmlVisualDocument,
        output_path: Path,
    ) -> GotenbergScreenshotResult:
        """截图、校验 PNG，并只在全部成功后原子替换目标文件。"""

        output_path = Path(output_path)
        part_path = output_path.with_name(f"{output_path.name}.part")
        self._remove_partial_file(part_path)
        attempts = 0

        try:
            payload, trace_id, attempts, status_code = self._request_screenshot(document)
            try:
                self.validator.validate_png_bytes(
                    payload,
                    width=int(self.config.image_canvas_width),
                    height=int(self.config.image_canvas_height),
                )
            except RenderedVisualValidationError as exc:
                raise GotenbergScreenshotError(
                    f"Gotenberg 截图校验失败：{exc}",
                    attempt_count=attempts,
                    status_code=status_code,
                ) from exc

            output_path.parent.mkdir(parents=True, exist_ok=True)
            part_path.write_bytes(payload)
            os.replace(part_path, output_path)
            return GotenbergScreenshotResult(
                output_path=output_path,
                trace_id=trace_id,
                attempts=attempts,
            )
        except GotenbergScreenshotError:
            raise
        except OSError as exc:
            raise GotenbergScreenshotError(
                f"Gotenberg 截图落盘失败：{exc}",
                attempt_count=attempts,
                status_code=None,
            ) from exc
        finally:
            self._remove_partial_file(part_path)

    def _request_screenshot(
        self,
        document: HtmlVisualDocument,
    ) -> tuple[bytes, str | None, int, int]:
        endpoint = (
            f"{self.config.image_gotenberg_base_url.rstrip('/')}"
            "/forms/chromium/screenshot/html"
        )
        font_path = Path(self.config.image_font_path)
        try:
            font_bytes = font_path.read_bytes()
        except OSError as exc:
            raise GotenbergScreenshotError(
                f"Gotenberg 字体资源读取失败：{font_path}：{exc}",
                attempt_count=0,
                status_code=None,
            ) from exc

        files = [
            (
                "files",
                (
                    "index.html",
                    document.html.encode("utf-8"),
                    "text/html; charset=utf-8",
                ),
            ),
            (
                "files",
                ("NotoSansSC-VF.ttf", font_bytes, "font/ttf"),
            ),
        ]
        fields = {
            "width": str(int(self.config.image_canvas_width)),
            "height": str(int(self.config.image_canvas_height)),
            "clip": "true",
            "deviceScaleFactor": "1",
            "format": "png",
            "waitForExpression": WAIT_FOR_VALIDATION_EXPRESSION,
            "failOnConsoleExceptions": "true",
            "failOnResourceLoadingFailed": "true",
        }
        max_attempts = min(int(self.config.image_renderer_max_attempts), 5)
        if max_attempts <= 0:
            raise GotenbergScreenshotError(
                "Gotenberg 最大尝试次数必须在 1 到 5 之间",
                attempt_count=0,
                status_code=None,
            )

        for attempt in range(1, max_attempts + 1):
            try:
                response = self.session.post(
                    endpoint,
                    data=fields,
                    files=files,
                    timeout=self.config.image_gotenberg_timeout_seconds,
                )
            except TRANSIENT_REQUEST_ERRORS as exc:
                if attempt < max_attempts:
                    self.sleeper(2 ** (attempt - 1))
                    continue
                raise GotenbergScreenshotError(
                    f"Gotenberg 请求失败，已尝试 {attempt} 次：{exc}",
                    attempt_count=attempt,
                    status_code=None,
                ) from exc
            except requests.RequestException as exc:
                raise GotenbergScreenshotError(
                    f"Gotenberg 请求失败：{exc}",
                    attempt_count=attempt,
                    status_code=None,
                ) from exc

            status_code = int(response.status_code)
            if status_code in RETRYABLE_STATUS_CODES:
                detail = self._response_detail(response)
                response.close()
                if attempt < max_attempts:
                    self.sleeper(2 ** (attempt - 1))
                    continue
                raise GotenbergScreenshotError(
                    f"Gotenberg 返回错误状态码 {status_code}，"
                    f"已尝试 {attempt} 次：{detail}",
                    attempt_count=attempt,
                    status_code=status_code,
                )

            if status_code >= 400:
                detail = self._response_detail(response)
                response.close()
                raise GotenbergScreenshotError(
                    f"Gotenberg 返回错误状态码 {status_code}：{detail}",
                    attempt_count=attempt,
                    status_code=status_code,
                )

            payload = response.content
            trace_id = self._trace_id(response)
            response.close()
            return payload, trace_id, attempt, status_code

        raise GotenbergScreenshotError(
            f"Gotenberg 请求失败，已尝试 {max_attempts} 次",
            attempt_count=max_attempts,
            status_code=None,
        )

    @staticmethod
    def _trace_id(response: requests.Response) -> str | None:
        for header in ("Gotenberg-Trace", "X-Request-Id", "Trace-Id"):
            value = response.headers.get(header)
            if value:
                return str(value)
        return None

    @staticmethod
    def _response_detail(response: requests.Response) -> str:
        detail = (response.text or "").strip()
        return detail[:500] if detail else "服务未返回错误详情"

    @staticmethod
    def _remove_partial_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "GotenbergScreenshotError",
    "GotenbergScreenshotProvider",
    "GotenbergScreenshotResult",
]
