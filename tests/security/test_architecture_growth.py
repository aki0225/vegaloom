from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REMOVED_INTERNAL_MODULE_NAMES = (
    "agent_graph",
    "adapter_runtime",
    "assurance",
    "context_loader",
    "eval",
    "goal_evidence",
    "goal_runtime",
    "llm_client",
    "loop_spec",
    "memory",
    "reviewer",
    "runtime",
    "state",
    "tool_broker",
)


def test_candidate_pipeline_does_not_import_codex_modules() -> None:
    path = PROJECT_ROOT / "src" / "vega" / "agent_candidate_pipeline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any("codex" in module for module in imported_modules)


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


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("from . import experimental\n", id="relative-package"),
        pytest.param("from vega import experimental\n", id="absolute-package"),
    ],
)
def test_core_import_gate_rejects_experimental_package_import_variants(
    tmp_path: Path,
    source: str,
) -> None:
    module = _architecture_module()
    package = tmp_path / "src" / "vega"
    package.mkdir(parents=True)
    package.joinpath("core_runtime.py").write_text(source, encoding="utf-8")

    assert module._core_import_issues(tmp_path) == [
        "核心模块静态依赖实验模块：src/vega/core_runtime.py:1"
    ]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import vega.experimental_tools\n", id="experimental-tools"),
        pytest.param("import vega.experimentalish\n", id="experimentalish"),
    ],
)
def test_core_import_gate_allows_non_experimental_namespace_prefixes(
    tmp_path: Path,
    source: str,
) -> None:
    module = _architecture_module()
    package = tmp_path / "src" / "vega"
    package.mkdir(parents=True)
    package.joinpath("core_runtime.py").write_text(source, encoding="utf-8")

    assert module._core_import_issues(tmp_path) == []


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


def test_assist_and_auto_share_one_post_worker_executor() -> None:
    source = (PROJECT_ROOT / "src" / "vega" / "loop_runtime.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    runtime = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LoopAutomationRuntime"
    )
    methods = {
        node.name: node
        for node in runtime.body
        if isinstance(node, ast.FunctionDef)
    }

    def called_attributes(method_name: str) -> set[str]:
        return {
            node.func.attr
            for node in ast.walk(methods[method_name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

    for entrypoint in ("_continue_assist_locked", "_run_auto_iterations"):
        calls = called_attributes(entrypoint)
        assert "_run_post_worker_stages" in calls
        assert "_run_review" not in calls

    assert {
        "_run_post_worker_scope_gate",
        "_run_post_worker_verification",
        "_run_post_worker_reflect",
        "_run_post_worker_review",
    } <= called_attributes("_run_post_worker_stages")


def test_agent_operation_identity_has_one_implementation_owner() -> None:
    expected_owner = "src/vega/agent_operation.py"
    identity_functions = {
        "operation_ref",
        "child_summary_ref",
        "reserve_operation_identity",
        "bound_operation_kind",
    }
    owners: dict[str, list[str]] = {
        function_name: [] for function_name in identity_functions
    }

    for path in sorted((PROJECT_ROOT / "src" / "vega").glob("agent_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in owners:
                owners[node.name].append(relative)

    assert owners == {
        function_name: [expected_owner]
        for function_name in identity_functions
    }


@pytest.mark.parametrize("module_name", REMOVED_INTERNAL_MODULE_NAMES)
@pytest.mark.parametrize(
    "shape",
    [
        pytest.param("module", id="module"),
        pytest.param("package", id="package"),
    ],
)
def test_removed_internal_module_gate_rejects_compatibility_shim(
    tmp_path: Path,
    module_name: str,
    shape: str,
) -> None:
    module = _architecture_module()
    package = tmp_path / "src" / "vega"
    package.mkdir(parents=True)
    if shape == "module":
        shim_path = package / f"{module_name}.py"
        reported_path = f"src/vega/{module_name}.py"
    else:
        shim_path = package / module_name / "__init__.py"
        shim_path.parent.mkdir()
        reported_path = f"src/vega/{module_name}/"
    shim_path.write_text("# compatibility shim\n", encoding="utf-8")

    assert module._removed_internal_module_issues(tmp_path) == [
        "已移除的内部模块不得恢复兼容层："
        f"{reported_path}；稳定入口是 CLI"
    ]


def test_removed_internal_module_gate_rejects_namespace_package_shim(
    tmp_path: Path,
) -> None:
    module = _architecture_module()
    namespace_package = tmp_path / "src" / "vega" / "memory"
    namespace_package.mkdir(parents=True)
    namespace_package.joinpath("backend.py").write_text(
        "# namespace compatibility shim\n",
        encoding="utf-8",
    )

    assert module._removed_internal_module_issues(tmp_path) == [
        "已移除的内部模块不得恢复兼容层："
        "src/vega/memory/；稳定入口是 CLI"
    ]


def test_repository_architecture_matches_its_own_head() -> None:
    module = _architecture_module()

    assert module.check_architecture_growth(PROJECT_ROOT, "HEAD") == []
