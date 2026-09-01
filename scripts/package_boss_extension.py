"""生成可供生产页面下载的浏览器扩展 ZIP。"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = PROJECT_ROOT / "browser-extension" / "job-library"
OUTPUT_ROOT = PROJECT_ROOT / "web-ui" / "public" / "downloads"
PACKAGE_FILES = (
    "manifest.json",
    "content-script.js",
    "service-worker.js",
    "boss-data.js",
    "boss-greeting.js",
    "xiaohongshu-data.js",
    "xiaohongshu-page.js",
    "assessment-capture.js",
)
PACKAGED_ORIGIN_DECLARATION = "const PACKAGED_APP_ORIGIN = ''"


def normalize_app_origin(value: str | None) -> str | None:
    """校验用于生产扩展包的 HTTPS 应用 Origin。"""

    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ZHITAN_APP_ORIGIN 必须是无路径的 https Origin")
    return f"https://{parsed.netloc.casefold()}"


def build_manifest(app_origin: str | None) -> dict[str, object]:
    """读取源码 manifest，并按需加入生产应用权限。"""

    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))
    normalized_origin = normalize_app_origin(app_origin)
    if normalized_origin:
        pattern = f"{normalized_origin}/*"
        manifest["host_permissions"] = [*manifest["host_permissions"], pattern]
        manifest["content_scripts"][0]["matches"] = [
            *manifest["content_scripts"][0]["matches"],
            pattern,
        ]
    return manifest


def render_service_worker(app_origin: str | None) -> bytes:
    """渲染仅供 ZIP 使用的 Service Worker。"""

    normalized_origin = normalize_app_origin(app_origin)
    source = (EXTENSION_ROOT / "service-worker.js").read_text(encoding="utf-8")
    replacement = f"const PACKAGED_APP_ORIGIN = {json.dumps(normalized_origin or '')}"
    if source.count(PACKAGED_ORIGIN_DECLARATION) != 1:
        raise ValueError("扩展 Service Worker 缺少唯一的生产 Origin 注入点")
    return source.replace(PACKAGED_ORIGIN_DECLARATION, replacement).encode("utf-8")


def package_extension(app_origin: str | None = None) -> Path:
    configured_origin = os.environ.get("ZHITAN_APP_ORIGIN") if app_origin is None else app_origin
    manifest = build_manifest(configured_origin)
    version = str(manifest["version"]).strip()
    if not version:
        raise ValueError("扩展 manifest 缺少版本号")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_ROOT / f"find-job-boss-helper-v{version}.zip"
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for relative_path in PACKAGE_FILES:
            source = EXTENSION_ROOT / relative_path
            if not source.is_file():
                raise FileNotFoundError(f"扩展文件不存在：{source}")
            archive_entry = zipfile.ZipInfo(relative_path, date_time=(2026, 8, 24, 0, 0, 0))
            archive_entry.compress_type = zipfile.ZIP_DEFLATED
            archive_entry.external_attr = 0o644 << 16
            if relative_path == "manifest.json":
                content = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            elif relative_path == "service-worker.js":
                content = render_service_worker(configured_origin)
            else:
                content = source.read_bytes()
            package.writestr(archive_entry, content)
    return output_path


if __name__ == "__main__":
    print(package_extension())
