from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _deterministic_cli_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    # GitHub Actions 注入的 GITHUB_ACTIONS / FORCE_COLOR 会让 typer/rich 强制 ANSI 渲染，
    # CLI 中文校验消息被样式码打断，子串断言随环境漂移；测试内移除，固定为无终端渲染。
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def pytest_configure(config) -> None:
    if config.option.basetemp is not None:
        return

    repo_root = Path(__file__).resolve().parents[1]
    temp_root = repo_root / ".tmp" / "pytest" / "runs"
    temp_root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(temp_root / f"pytest-{os.getpid()}")
