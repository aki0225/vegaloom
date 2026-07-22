from __future__ import annotations

import os
from pathlib import Path


SELECTIVE_MEMORY_TESTS = (
    Path(__file__).resolve().parent / "experimental" / "selective_memory"
)


def pytest_addoption(parser) -> None:
    group = parser.getgroup("experimental")
    group.addoption(
        "--include-selective-memory",
        action="store_true",
        default=False,
        help="显式收集 Selective Memory 离线实验测试",
    )


def pytest_ignore_collect(collection_path, config) -> bool | None:
    if config.getoption("--include-selective-memory"):
        return None

    path = Path(str(collection_path)).resolve()
    return path == SELECTIVE_MEMORY_TESTS or SELECTIVE_MEMORY_TESTS in path.parents


def pytest_configure(config) -> None:
    if config.option.basetemp is not None:
        return

    repo_root = Path(__file__).resolve().parents[1]
    temp_root = repo_root / ".tmp" / "pytest" / "runs"
    temp_root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(temp_root / f"pytest-{os.getpid()}")
