from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.wechat_draft_creation_service import WechatDraftCreationService
from src.services.wechat_title_service import validate_wechat_title
from src.tasks.summary_task import SummaryTask


def test_wechat_title_keeps_the_complete_text_within_official_limit() -> None:
    title = "GitHub 周榜：AI Agent 正在从写代码转向管代码"

    assert validate_wechat_title(title) == title


def test_wechat_title_accepts_exactly_thirty_two_characters() -> None:
    title = "标" * 32

    assert validate_wechat_title(title) == title


def test_wechat_title_rejects_overlong_text_instead_of_silently_truncating() -> None:
    title = "标" * 33

    with pytest.raises(ValueError, match="不能超过 32 个字"):
        validate_wechat_title(title)


def test_draft_title_no_longer_uses_the_legacy_twenty_eight_character_limit() -> None:
    title = "标" * 30
    service = WechatDraftCreationService()

    assert service._draft_title(config=SimpleNamespace(), title=title) == title


def test_summary_contract_rejects_a_title_that_wechat_cannot_accept() -> None:
    task = object.__new__(SummaryTask)
    payload = {
        "title": "标" * 33,
        "digest": "摘要",
        "opening_markdown": "开篇",
        "weekly_theme_markdown": "主线",
        "closing_markdown": "结尾",
    }

    with pytest.raises(ValueError, match="不能超过 32 个字"):
        task._normalize_global_output(payload, [])
