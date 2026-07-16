from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config) -> None:
    if config.option.basetemp is not None:
        return

    repo_root = Path(__file__).resolve().parents[1]
    temp_root = repo_root / ".tmp" / "pytest" / "runs"
    temp_root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(temp_root / f"pytest-{os.getpid()}")
