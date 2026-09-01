from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.config.config_manager import AppConfig
from src.providers.seedream_provider import SeedreamApiError, SeedreamProvider


def _provider(tmp_path: Path) -> SeedreamProvider:
    """构造只包含 Seedream Provider 所需字段的测试配置。"""

    return SeedreamProvider(
        AppConfig(
            project_root=tmp_path,
            config_path=tmp_path / "app.yaml",
            raw={
                "image": {
                    "base_url": "https://ark.example.test/api/v3",
                    "generations_endpoint": "/images/generations",
                    "model": "seedream-test",
                    "timeout_seconds": 30,
                }
            },
        )
    )


def _response(*, status_code: int, payload: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = payload if payload is not None else {}
    response.headers = {}
    return response


def _session(*, post_result: object = None, get_result: object = None) -> MagicMock:
    session = MagicMock()
    session.__enter__.return_value = session
    if isinstance(post_result, BaseException):
        session.post.side_effect = post_result
    elif post_result is not None:
        session.post.return_value = post_result
    if isinstance(get_result, BaseException):
        session.get.side_effect = get_result
    elif get_result is not None:
        session.get.return_value = get_result
    return session


def test_generation_retries_without_environment_proxy_after_proxy_failure(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    proxied_session = _session(post_result=requests.exceptions.ProxyError("代理断开"))
    direct_session = _session(post_result=_response(status_code=200, payload={"data": [{}]}))

    with (
        patch("src.providers.seedream_provider.requests.Session", side_effect=[proxied_session, direct_session]),
        patch("src.providers.seedream_provider.time.sleep") as sleep,
    ):
        result = provider._request_generation(
            endpoint="https://ark.example.test/api/v3/images/generations",
            headers={"Authorization": "Bearer test"},
            payload={"prompt": "测试"},
        )

    assert result == {"data": [{}]}
    assert proxied_session.trust_env is True
    assert direct_session.trust_env is False
    sleep.assert_called_once()


def test_generation_stops_after_five_transient_failures(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    sessions = [
        _session(post_result=requests.exceptions.ConnectionError(f"连接失败 {index}"))
        for index in range(1, 6)
    ]

    with (
        patch("src.providers.seedream_provider.requests.Session", side_effect=sessions) as session_factory,
        patch("src.providers.seedream_provider.time.sleep") as sleep,
        pytest.raises(SeedreamApiError, match="已尝试 5 次"),
    ):
        provider._request_generation(
            endpoint="https://ark.example.test/api/v3/images/generations",
            headers={},
            payload={},
        )

    assert session_factory.call_count == 5
    assert sleep.call_count == 4


def test_generation_retries_5xx_but_does_not_retry_regular_4xx(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    server_error = _session(post_result=_response(status_code=503, text="busy"))
    success = _session(post_result=_response(status_code=200, payload={"data": [{"url": "ok"}]}))

    with (
        patch("src.providers.seedream_provider.requests.Session", side_effect=[server_error, success]) as factory,
        patch("src.providers.seedream_provider.time.sleep") as sleep,
    ):
        result = provider._request_generation(endpoint="https://ark.example.test", headers={}, payload={})

    assert result["data"][0]["url"] == "ok"
    assert factory.call_count == 2
    sleep.assert_called_once()

    bad_request = _session(post_result=_response(status_code=400, text="bad request"))
    with (
        patch("src.providers.seedream_provider.requests.Session", return_value=bad_request) as factory,
        pytest.raises(SeedreamApiError, match="错误状态码 400"),
    ):
        provider._request_generation(endpoint="https://ark.example.test", headers={}, payload={})

    assert factory.call_count == 1


def test_image_download_retries_directly_after_proxy_failure(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    output_path = tmp_path / "image.png"
    proxied_session = _session(get_result=requests.exceptions.ProxyError("代理断开"))
    response = _response(status_code=200)
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"real-image-bytes"]
    direct_session = _session(get_result=response)

    with (
        patch("src.providers.seedream_provider.requests.Session", side_effect=[proxied_session, direct_session]),
        patch("src.providers.seedream_provider.time.sleep"),
    ):
        provider._download_image("https://image.example.test/signed", output_path)

    assert output_path.read_bytes() == b"real-image-bytes"
    assert proxied_session.trust_env is True
    assert direct_session.trust_env is False
