from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

from src.providers.gotenberg_screenshot_provider import (
    GotenbergScreenshotError,
    GotenbergScreenshotProvider,
)
from src.services.article_visual_template_service import HtmlVisualDocument


def _png_bytes(width: int = 2048, height: int = 1152) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _response(
    status_code: int = 200,
    *,
    content: bytes | None = None,
    text: str = "",
    trace_id: str | None = "trace-123",
) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.content = _png_bytes() if content is None else content
    response.text = text
    response.headers = {"Gotenberg-Trace": trace_id} if trace_id else {}
    return response


def _document() -> HtmlVisualDocument:
    return HtmlVisualDocument(
        html="<!doctype html><html><body>技术总结</body></html>",
        expected_texts=(("headline", "技术总结"),),
        node_ids=(),
        edges=(),
    )


class _Config:
    image_gotenberg_base_url = "http://127.0.0.1:3000/"
    image_gotenberg_timeout_seconds = 37
    image_renderer_max_attempts = 5
    image_canvas_width = 2048
    image_canvas_height = 1152

    def __init__(self, font_path: Path) -> None:
        self.image_font_path = font_path


def _build_provider(
    tmp_path: Path,
    side_effect: object,
) -> tuple[GotenbergScreenshotProvider, Mock, Mock]:
    font_path = tmp_path / "NotoSansSC-VF.ttf"
    font_path.write_bytes(b"font-data")
    session = Mock(spec=requests.Session)
    session.post.side_effect = side_effect
    sleeper = Mock()
    provider = GotenbergScreenshotProvider(
        _Config(font_path), session=session, sleeper=sleeper
    )
    return provider, session, sleeper


def test_success_writes_validated_png_atomically(tmp_path: Path) -> None:
    provider, session, _sleeper = _build_provider(tmp_path, [_response()])
    output_path = tmp_path / "result.png"

    result = provider.render(_document(), output_path)

    assert result.output_path == output_path
    assert result.trace_id == "trace-123"
    assert result.attempts == 1
    assert Image.open(output_path).size == (2048, 1152)
    assert not (tmp_path / "result.png.part").exists()
    assert session.post.call_count == 1


def test_request_uploads_html_font_and_all_strict_fields(tmp_path: Path) -> None:
    provider, session, _sleeper = _build_provider(tmp_path, [_response()])

    provider.render(_document(), tmp_path / "result.png")

    call = session.post.call_args
    assert call.args == (
        "http://127.0.0.1:3000/forms/chromium/screenshot/html",
    )
    assert call.kwargs["data"] == {
        "width": "2048",
        "height": "1152",
        "clip": "true",
        "deviceScaleFactor": "1",
        "format": "png",
        "waitForExpression": "window.__visualValidation?.status === 'passed'",
        "failOnConsoleExceptions": "true",
        "failOnResourceLoadingFailed": "true",
    }
    assert call.kwargs["timeout"] == 37
    uploaded = call.kwargs["files"]
    assert [field for field, _file in uploaded] == ["files", "files"]
    assert uploaded[0][1] == (
        "index.html",
        _document().html.encode("utf-8"),
        "text/html; charset=utf-8",
    )
    assert uploaded[1][1] == (
        "NotoSansSC-VF.ttf",
        b"font-data",
        "font/ttf",
    )


def test_session_never_inherits_environment_proxy(tmp_path: Path) -> None:
    provider, session, _sleeper = _build_provider(tmp_path, [_response()])

    provider.render(_document(), tmp_path / "result.png")

    assert session.trust_env is False


def test_429_retries_then_succeeds(tmp_path: Path) -> None:
    provider, session, sleeper = _build_provider(
        tmp_path,
        [_response(429, text="限流"), _response()],
    )

    result = provider.render(_document(), tmp_path / "result.png")

    assert result.attempts == 2
    assert session.post.call_count == 2
    sleeper.assert_called_once_with(1)


def test_connection_error_retries_with_exponential_backoff(tmp_path: Path) -> None:
    provider, session, sleeper = _build_provider(
        tmp_path,
        [requests.ConnectionError("断开"), requests.Timeout("超时"), _response()],
    )

    result = provider.render(_document(), tmp_path / "result.png")

    assert result.attempts == 3
    assert session.post.call_count == 3
    assert [call.args[0] for call in sleeper.call_args_list] == [1, 2]


def test_transient_failures_stop_after_five_attempts(tmp_path: Path) -> None:
    provider, session, sleeper = _build_provider(
        tmp_path,
        [requests.ConnectionError("断开")] * 5,
    )

    with pytest.raises(GotenbergScreenshotError, match="已尝试 5 次") as caught:
        provider.render(_document(), tmp_path / "result.png")

    assert caught.value.attempt_count == 5
    assert caught.value.status_code is None
    assert session.post.call_count == 5
    assert [call.args[0] for call in sleeper.call_args_list] == [1, 2, 4, 8]


@pytest.mark.parametrize("status_code", [400, 409, 501])
def test_non_retryable_http_error_fails_immediately(
    tmp_path: Path, status_code: int
) -> None:
    provider, session, sleeper = _build_provider(
        tmp_path,
        [_response(status_code, text="页面校验失败")],
    )

    with pytest.raises(
        GotenbergScreenshotError, match=f"错误状态码 {status_code}"
    ) as caught:
        provider.render(_document(), tmp_path / "result.png")

    assert caught.value.attempt_count == 1
    assert caught.value.status_code == status_code
    assert "页面校验失败" in str(caught.value)
    assert session.post.call_count == 1
    sleeper.assert_not_called()


def test_retryable_http_error_exposes_final_status(tmp_path: Path) -> None:
    provider, session, _sleeper = _build_provider(
        tmp_path,
        [_response(503, text="不可用")] * 5,
    )

    with pytest.raises(GotenbergScreenshotError) as caught:
        provider.render(_document(), tmp_path / "result.png")

    assert caught.value.attempt_count == 5
    assert caught.value.status_code == 503
    assert session.post.call_count == 5


def test_non_png_response_is_rejected_and_partial_file_is_cleaned(
    tmp_path: Path,
) -> None:
    provider, _session, _sleeper = _build_provider(
        tmp_path,
        [_response(content=b"not-a-png")],
    )
    output_path = tmp_path / "result.png"
    part_path = tmp_path / "result.png.part"
    part_path.write_bytes(b"stale")

    with pytest.raises(GotenbergScreenshotError, match="不是 PNG") as caught:
        provider.render(_document(), output_path)

    assert caught.value.attempt_count == 1
    assert not output_path.exists()
    assert not part_path.exists()


def test_wrong_dimensions_are_rejected(tmp_path: Path) -> None:
    provider, _session, _sleeper = _build_provider(
        tmp_path,
        [_response(content=_png_bytes(1024, 576))],
    )

    with pytest.raises(GotenbergScreenshotError, match="图片尺寸错误"):
        provider.render(_document(), tmp_path / "result.png")


def test_failed_render_preserves_existing_final_file(tmp_path: Path) -> None:
    provider, _session, _sleeper = _build_provider(
        tmp_path,
        [_response(content=b"invalid")],
    )
    output_path = tmp_path / "result.png"
    output_path.write_bytes(b"existing-valid-file")

    with pytest.raises(GotenbergScreenshotError):
        provider.render(_document(), output_path)

    assert output_path.read_bytes() == b"existing-valid-file"
    assert not (tmp_path / "result.png.part").exists()
