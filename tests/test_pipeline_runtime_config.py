from __future__ import annotations

from pathlib import Path

import pytest

from src.config.config_manager import ConfigManager
from src.platform_access.runtime_config import apply_pipeline_config, normalize_pipeline_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_config_defaults_preserve_current_media_behavior() -> None:
    normalized = normalize_pipeline_config({"top_n": 6})

    assert normalized["image_generation_enabled"] is True
    assert normalized["video_generation_enabled"] is False
    assert normalized["audio_generation_enabled"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_generation_enabled", "false"),
        ("video_generation_enabled", 1),
        ("audio_generation_enabled", "true"),
    ],
)
def test_pipeline_config_rejects_non_boolean_media_switches(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="必须是布尔值"):
        normalize_pipeline_config({field: value})


def test_pipeline_config_applies_media_switches_to_existing_task_gates() -> None:
    base_config = ConfigManager(project_root=PROJECT_ROOT).load()

    runtime_config = apply_pipeline_config(
        base_config,
        {
            "top_n": 6,
            "image_generation_enabled": False,
            "video_generation_enabled": True,
            "audio_generation_enabled": True,
        },
    )

    assert runtime_config.image_paid_generation_enabled is False
    assert runtime_config.video_submit_enabled is True
    assert runtime_config.audio_enabled is True


def test_workbench_video_switch_takes_priority_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_SUBMIT_ENABLED", "true")
    base_config = ConfigManager(project_root=PROJECT_ROOT).load()

    runtime_config = apply_pipeline_config(
        base_config,
        {
            "video_generation_enabled": False,
        },
    )

    assert runtime_config.video_submit_enabled is False


def test_workbench_audio_switch_takes_priority_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIO_ENABLED", "true")
    base_config = ConfigManager(project_root=PROJECT_ROOT).load()

    runtime_config = apply_pipeline_config(
        base_config,
        {
            "audio_generation_enabled": False,
        },
    )

    assert runtime_config.audio_enabled is False
