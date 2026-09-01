from __future__ import annotations

from pathlib import Path

import pytest

from scripts.setup_common import PortConflictError, assert_ports_available, generate_urlsafe_secret, render_env


def test_generated_secret_is_url_safe() -> None:
    value = generate_urlsafe_secret()

    assert len(value) >= 40
    assert set(value) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def test_render_env_replaces_values_without_overwriting_existing_file(tmp_path: Path) -> None:
    template = tmp_path / "template.env"
    destination = tmp_path / "instance.env"
    template.write_text("PORT=18081\nPASSWORD=example\n", encoding="utf-8")

    render_env(template, destination, {"PASSWORD": "generated"})

    assert destination.read_text(encoding="utf-8") == "PORT=18081\nPASSWORD=generated\n"
    with pytest.raises(FileExistsError):
        render_env(template, destination, {"PASSWORD": "other"})


def test_port_check_reports_the_variable_name(monkeypatch) -> None:
    class _Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def bind(self, _address) -> None:
            raise OSError("occupied")

    monkeypatch.setattr("scripts.setup_common.socket.socket", lambda *_args: _Socket())

    with pytest.raises(PortConflictError, match="ZHITAN_HTTP_PORT=18081"):
        assert_ports_available({"ZHITAN_HTTP_PORT": ("127.0.0.1", 18081)})
