from __future__ import annotations

from unittest.mock import patch

from src.career_assistant.settings import load_model_gateway_settings


def test_model_policy_defaults_allow_operator_configured_profiles() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = load_model_gateway_settings()

    assert settings.provider_credential_env["deepseek"] == "DEEPSEEK_API_KEY"
    assert settings.allow_paid_profiles is True
    assert settings.enable_local_ollama_profile is True


def test_operator_can_explicitly_disable_paid_and_local_profiles() -> None:
    with patch.dict(
        "os.environ",
        {
            "CAREER_ALLOW_PAID_PROFILES": "false",
            "CAREER_ENABLE_LOCAL_OLLAMA_PROFILE": "false",
        },
        clear=True,
    ):
        settings = load_model_gateway_settings()

    assert settings.allow_paid_profiles is False
    assert settings.enable_local_ollama_profile is False
