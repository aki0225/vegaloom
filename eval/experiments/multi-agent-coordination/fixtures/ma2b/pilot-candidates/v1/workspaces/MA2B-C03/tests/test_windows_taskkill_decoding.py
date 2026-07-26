from __future__ import annotations

from types import SimpleNamespace

import pytest

import vega.execution_control as execution_control


def test_taskkill_replaces_undecodable_localized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_kwargs: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed_kwargs.update(kwargs)
        if kwargs.get("errors") != "replace":
            raise UnicodeDecodeError(
                "utf-8",
                b"\xb3",
                0,
                1,
                "invalid start byte",
            )
        return SimpleNamespace(
            returncode=5,
            stdout="",
            stderr="localized output \ufffd",
        )

    monkeypatch.setattr(execution_control.subprocess, "run", fake_run)

    result = execution_control._run_windows_taskkill(
        4242,
        force=False,
        timeout=1,
    )

    assert observed_kwargs["text"] is True
    assert observed_kwargs["errors"] == "replace"
    assert result == "taskkill 退出码 5：localized output \ufffd"
