from __future__ import annotations

import json
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = PROJECT_ROOT / "browser-extension" / "job-library"
PUBLIC_ROOT = PROJECT_ROOT / "web-ui" / "public"


def test_downloadable_extension_matches_manifest_and_has_flat_root() -> None:
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))
    archive = PUBLIC_ROOT / "downloads" / f"find-job-boss-helper-v{manifest['version']}.zip"

    assert archive.is_file()
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert {
            "manifest.json",
            "content-script.js",
            "service-worker.js",
            "boss-data.js",
            "boss-greeting.js",
            "xiaohongshu-data.js",
            "xiaohongshu-page.js",
        }.issubset(names)
        packaged_manifest = json.loads(package.read("manifest.json").decode("utf-8"))
    assert packaged_manifest["version"] == manifest["version"]


def test_installation_guide_covers_both_supported_browsers() -> None:
    guide = (PUBLIC_ROOT / "boss-extension-guide.html").read_text(encoding="utf-8")
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert "Google Chrome" in guide
    assert "Microsoft Edge" in guide
    assert "chrome://extensions" in guide
    assert "edge://extensions" in guide
    assert 'href="/interviews/jobs"' in guide
    assert f"find-job-boss-helper-v{manifest['version']}.zip" in guide
    assert "加载已解压的扩展程序" in guide
