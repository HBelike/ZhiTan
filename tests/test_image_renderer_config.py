from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from src.config.config_manager import ConfigManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_deterministic_image_renderer_config_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAREER_GOTENBERG_SERVICE_BASE_URL", raising=False)

    config = ConfigManager(PROJECT_ROOT).load()

    assert config.image_renderer == "gotenberg_html"
    assert config.image_gotenberg_base_url == "http://127.0.0.1:3000"
    assert config.image_gotenberg_timeout_seconds == 60
    assert config.image_renderer_max_attempts == 5
    assert config.image_canvas_width == 2048
    assert config.image_canvas_height == 1152
    assert config.image_template_version == "article_visual_v1"
    assert config.image_renderer_version == "gotenberg_html_v1"
    assert config.image_font_path.name == "NotoSansSC-VF.ttf"
    assert config.image_font_path.is_file()
    assert config.image_font_version == "noto-cjk-2.004-523d033d"
    assert config.image_concept_background_enabled is False


def test_gotenberg_environment_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CAREER_GOTENBERG_SERVICE_BASE_URL",
        "http://career-gotenberg:3000",
    )

    assert (
        ConfigManager(PROJECT_ROOT).load().image_gotenberg_base_url
        == "http://career-gotenberg:3000"
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "unknown", "image.renderer.name"),
        ("timeout_seconds", 0, "image.renderer.timeout_seconds"),
        ("max_attempts", 4, "image.renderer.max_attempts"),
        ("width", 1920, "image.renderer.width"),
        ("height", 1080, "image.renderer.height"),
        ("template_version", "", "image.renderer.template_version"),
        ("renderer_version", "", "image.renderer.renderer_version"),
        ("font_version", "", "image.renderer.font_version"),
    ],
)
def test_invalid_image_renderer_config_is_rejected(
    field: str,
    value: object,
    message: str,
) -> None:
    manager = ConfigManager(PROJECT_ROOT)
    raw = deepcopy(manager.load().raw)
    raw["image"]["renderer"][field] = value

    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        manager._validate(raw)


def test_missing_image_renderer_font_is_rejected() -> None:
    manager = ConfigManager(PROJECT_ROOT)
    raw = deepcopy(manager.load().raw)
    raw["image"]["renderer"]["font_path"] = "assets/fonts/missing.ttf"

    with pytest.raises(ValueError, match=r"image\.renderer\.font_path"):
        manager._validate(raw)
