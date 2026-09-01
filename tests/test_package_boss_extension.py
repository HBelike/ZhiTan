"""浏览器扩展公开打包配置测试。"""

from __future__ import annotations

import pytest

from scripts.package_boss_extension import (
    build_manifest,
    normalize_app_origin,
    render_service_worker,
)


def test_package_origin_is_added_without_private_domain() -> None:
    manifest = build_manifest("https://jobs.example.com")
    assert "https://jobs.example.com/*" in manifest["host_permissions"]
    assert "https://jobs.example.com/*" in manifest["content_scripts"][0]["matches"]

    worker = render_service_worker("https://jobs.example.com").decode("utf-8")
    assert 'const PACKAGED_APP_ORIGIN = "https://jobs.example.com"' in worker


def test_source_defaults_only_include_local_application_origins() -> None:
    manifest = build_manifest(None)
    application_patterns = {
        pattern
        for pattern in manifest["content_scripts"][0]["matches"]
        if "zhipin.com" not in pattern and "xiaohongshu.com" not in pattern
    }
    assert application_patterns == {"http://127.0.0.1/*", "http://localhost/*"}


def test_invalid_package_origin_is_rejected() -> None:
    with pytest.raises(ValueError, match="https"):
        normalize_app_origin("ftp://example.com")
