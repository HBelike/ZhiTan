from __future__ import annotations

from unittest.mock import patch

from src.career_assistant.settings import load_model_gateway_settings


def test_deepseek_provider_uses_environment_key_and_paid_models_default_off() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = load_model_gateway_settings()

    assert settings.provider_credential_env["deepseek"] == "DEEPSEEK_API_KEY"
    assert settings.allow_paid_profiles is False


def test_local_environment_can_explicitly_enable_paid_deepseek_profile() -> None:
    with patch.dict("os.environ", {"CAREER_ALLOW_PAID_PROFILES": "true"}, clear=True):
        settings = load_model_gateway_settings()

    assert settings.allow_paid_profiles is True
