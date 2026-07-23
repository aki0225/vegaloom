from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _architecture_module() -> ModuleType:
    script = PROJECT_ROOT / "scripts" / "check_architecture_growth.py"
    spec = importlib.util.spec_from_file_location("check_architecture_growth", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _finding(module: ModuleType, path: str, name: str, complexity: int):
    return module.ComplexityFinding(
        path=path,
        qualname=name,
        complexity=complexity,
        limit=10,
    )


def test_complexity_gate_rejects_new_c901() -> None:
    module = _architecture_module()
    current = _finding(module, "src/vega/new_runtime.py", "run", 11)

    issues = module._complexity_issues({}, {current.key: current}, {})

    assert issues == [
        "新增 C901：src/vega/new_runtime.py:run complexity=11>10"
    ]


def test_complexity_gate_rejects_existing_growth() -> None:
    module = _architecture_module()
    previous = _finding(module, "src/vega/loop_runtime.py", "Runtime.run", 20)
    current = _finding(module, "src/vega/loop_runtime.py", "Runtime.run", 21)

    issues = module._complexity_issues(
        {previous.key: previous},
        {current.key: current},
        {},
    )

    assert issues == [
        "C901 复杂度增长：src/vega/loop_runtime.py:Runtime.run 20->21"
    ]


def test_complexity_gate_accepts_reduction_after_rename() -> None:
    module = _architecture_module()
    previous = _finding(module, "src/vega/goal_evidence.py", "validate", 20)
    current = _finding(module, "src/vega/loop_evidence.py", "validate", 15)

    issues = module._complexity_issues(
        {previous.key: previous},
        {current.key: current},
        {"src/vega/loop_evidence.py": "src/vega/goal_evidence.py"},
    )

    assert issues == []


@pytest.mark.parametrize(
    ("base", "current", "expected"),
    [
        ({}, {"src/vega/new_runtime.py": 501}, ["新增模块超过 500 行：src/vega/new_runtime.py=501"]),
        (
            {"src/vega/loop_runtime.py": 3485},
            {"src/vega/loop_runtime.py": 3486},
            ["既有大模块继续增长：src/vega/loop_runtime.py 3485->3486"],
        ),
        (
            {"src/vega/small.py": 499},
            {"src/vega/small.py": 501},
            ["模块越过 500 行门槛：src/vega/small.py 499->501"],
        ),
        (
            {"src/vega/small.py": 300},
            {"src/vega/small.py": 400},
            [],
        ),
    ],
)
def test_module_size_gate_is_incremental(
    base: dict[str, int],
    current: dict[str, int],
    expected: list[str],
) -> None:
    module = _architecture_module()

    assert module._module_size_issues(base, current, {}) == expected


def test_core_import_gate_rejects_experimental_dependency(tmp_path: Path) -> None:
    module = _architecture_module()
    package = tmp_path / "src" / "vega"
    package.mkdir(parents=True)
    package.joinpath("core_runtime.py").write_text(
        "from .experimental.assurance import AdequacyResult\n",
        encoding="utf-8",
    )

    issues = module._core_import_issues(tmp_path)

    assert issues == [
        "核心模块静态依赖实验模块：src/vega/core_runtime.py:1"
    ]


def test_cli_may_lazy_import_explicit_experimental_command(tmp_path: Path) -> None:
    module = _architecture_module()
    package = tmp_path / "src" / "vega"
    package.mkdir(parents=True)
    package.joinpath("cli.py").write_text(
        "def command():\n"
        "    from .experimental.goal_runtime import GoalRuntime\n"
        "    return GoalRuntime\n",
        encoding="utf-8",
    )

    assert module._core_import_issues(tmp_path) == []


def test_cli_cannot_eagerly_import_experimental_module(tmp_path: Path) -> None:
    module = _architecture_module()
    package = tmp_path / "src" / "vega"
    package.mkdir(parents=True)
    package.joinpath("cli.py").write_text(
        "from .experimental.goal_runtime import GoalRuntime\n",
        encoding="utf-8",
    )

    assert module._core_import_issues(tmp_path) == [
        "核心模块静态依赖实验模块：src/vega/cli.py:1"
    ]


def test_removed_internal_module_gate_rejects_compatibility_shim(tmp_path: Path) -> None:
    module = _architecture_module()
    package = tmp_path / "src" / "vega"
    package.mkdir(parents=True)
    package.joinpath("assurance.py").write_text(
        "from .experimental.assurance import AdequacyResult\n",
        encoding="utf-8",
    )

    assert module._removed_internal_module_issues(tmp_path) == [
        "已移除的内部模块不得恢复兼容层："
        "src/vega/assurance.py；稳定入口是 CLI"
    ]


def test_repository_architecture_matches_its_own_head() -> None:
    module = _architecture_module()

    assert module.check_architecture_growth(PROJECT_ROOT, "HEAD") == []
