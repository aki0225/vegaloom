from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path

import pytest


_REQUIRED_LANGGRAPH_MODULES = (
    "langgraph",
    "langgraph.checkpoint.sqlite",
)


def _langgraph_dependency_errors() -> list[str]:
    errors: list[str] = []
    for module_name in _REQUIRED_LANGGRAPH_MODULES:
        try:
            spec = importlib.util.find_spec(module_name)
        except Exception as exc:
            errors.append(
                f"{module_name}（发现失败：{type(exc).__name__}: {exc}）"
            )
            continue
        if spec is None:
            errors.append(f"{module_name}（不可发现）")
            continue
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            errors.append(
                f"{module_name}（导入失败：{type(exc).__name__}: {exc}）"
            )
    return errors


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--require-langgraph",
        action="store_true",
        default=False,
        help="要求安装 LangGraph 可选依赖；缺失时测试会话直接失败。",
    )


def pytest_sessionstart(session) -> None:
    if not session.config.getoption("--require-langgraph"):
        return
    dependency_errors = _langgraph_dependency_errors()
    if dependency_errors:
        raise pytest.UsageError(
            "--require-langgraph 已启用，但当前环境的 Gate 所需模块不可用："
            f"{'; '.join(dependency_errors)}。请安装或修复 `vegaloom[langgraph]`。"
        )


def pytest_configure(config) -> None:
    if config.option.basetemp is not None:
        return

    repo_root = Path(__file__).resolve().parents[1]
    temp_root = repo_root / ".tmp" / "pytest" / "runs"
    temp_root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(temp_root / f"pytest-{os.getpid()}")
