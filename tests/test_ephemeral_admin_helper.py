from __future__ import annotations

import io

import pytest

from scripts import create_ephemeral_test_admin as helper


def test_helper_requires_explicit_integration_mode(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ZHITAN_EPHEMERAL_TEST_MODE", raising=False)

    assert helper.main() == 2
    assert "ZHITAN_EPHEMERAL_TEST_MODE" in capsys.readouterr().err


def test_password_is_read_only_from_non_tty_stdin(monkeypatch) -> None:
    stdin = io.StringIO("random-password\n")
    monkeypatch.setattr(helper.sys, "stdin", stdin)

    assert helper.read_password_from_stdin() == "random-password"


def test_password_reader_rejects_interactive_input(monkeypatch) -> None:
    stdin = io.StringIO("random-password\n")
    stdin.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setattr(helper.sys, "stdin", stdin)

    with pytest.raises(RuntimeError, match="CI stdin"):
        helper.read_password_from_stdin()
