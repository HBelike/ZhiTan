from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.providers.deepseek_provider import DeepSeekApiError, DeepSeekMessage, DeepSeekProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = ""

    def json(self) -> dict[str, object]:
        return self._payload


def _build_config(*, model: str = "deepseek-v4-pro") -> SimpleNamespace:
    return SimpleNamespace(
        llm_timeout_seconds=90,
        llm_max_tokens=12_288,
        llm_model=model,
        llm_temperature=0.4,
        llm_response_format_json=True,
        llm_base_url="https://api.deepseek.com",
        llm_chat_completions_endpoint="/chat/completions",
        llm_api_key_env="DEEPSEEK_API_KEY",
    )


class DeepSeekProviderTest(unittest.TestCase):
    @patch("src.providers.deepseek_provider.requests.post")
    def test_v4_structured_request_disables_thinking(self, post_mock) -> None:
        post_mock.return_value = _FakeResponse(
            {
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"title":"ok"}'},
                    }
                ],
            }
        )
        provider = DeepSeekProvider(_build_config())

        with patch.object(provider, "_read_api_key", return_value="test-key"):
            provider._chat_without_trace(
                [DeepSeekMessage(role="user", content="输出 JSON")],
                timeout_seconds=None,
                max_tokens=None,
                retry_empty_content=False,
            )

        request_payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(request_payload["thinking"], {"type": "disabled"})

    def test_length_finish_reason_is_reported_as_truncation(self) -> None:
        provider = DeepSeekProvider(_build_config())

        with self.assertRaisesRegex(DeepSeekApiError, "达到 max_tokens"):
            provider._extract_message_content(
                status_code=200,
                response_payload={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {
                                "content": '{"title":"标题","article_markdown":"未闭合正文',
                            },
                        }
                    ]
                },
            )


if __name__ == "__main__":
    unittest.main()
