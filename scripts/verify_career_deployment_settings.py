"""验证求职助手本地/容器部署共用配置的环境变量展开规则。"""

from __future__ import annotations

import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.settings import (
    load_attachment_processing_settings,
    load_document_understanding_settings,
    load_legacy_office_conversion_settings,
)


CONFIG_PATH = PROJECT_ROOT / "config" / "career_assistant.yaml"
OVERRIDE_KEYS = (
    "CAREER_REDACTION_ENABLED",
    "CAREER_DOCLING_ENABLED",
    "CAREER_DOCLING_SERVICE_BASE_URL",
    "CAREER_GOTENBERG_ENABLED",
    "CAREER_GOTENBERG_SERVICE_BASE_URL",
)


def main() -> None:
    """分别验证默认开发值与生产容器覆盖值，结束后恢复当前环境。"""

    original_values = {key: os.environ.get(key) for key in OVERRIDE_KEYS}
    try:
        for key in OVERRIDE_KEYS:
            os.environ.pop(key, None)

        local_attachment = load_attachment_processing_settings(CONFIG_PATH)
        local_docling = load_document_understanding_settings(CONFIG_PATH)
        local_gotenberg = load_legacy_office_conversion_settings(CONFIG_PATH)
        assert local_attachment.redaction_enabled is False
        assert local_docling.enabled is True
        assert local_docling.service_base_url == "http://127.0.0.1:5001"
        assert local_gotenberg.enabled is True
        assert local_gotenberg.service_base_url == "http://127.0.0.1:3000"

        os.environ.update(
            {
                "CAREER_REDACTION_ENABLED": "true",
                "CAREER_DOCLING_ENABLED": "false",
                "CAREER_DOCLING_SERVICE_BASE_URL": "http://career-docling:5001",
                "CAREER_GOTENBERG_ENABLED": "false",
                "CAREER_GOTENBERG_SERVICE_BASE_URL": "http://career-gotenberg:3000",
            },
        )
        production_attachment = load_attachment_processing_settings(CONFIG_PATH)
        production_docling = load_document_understanding_settings(CONFIG_PATH)
        production_gotenberg = load_legacy_office_conversion_settings(CONFIG_PATH)
        assert production_attachment.redaction_enabled is True
        assert production_docling.enabled is False
        assert production_docling.service_base_url == "http://career-docling:5001"
        assert production_gotenberg.enabled is False
        assert production_gotenberg.service_base_url == "http://career-gotenberg:3000"
    finally:
        for key, value in original_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("career_deployment_settings_ok")


if __name__ == "__main__":
    main()
