from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Literal


SOURCE_PATH = Path("src/vega/project_profile.py")
REQUIRED_ASSIGNMENTS = {
    "NodePackageManager",
    "NODE_PACKAGE_MANAGER_BY_LOCKFILE",
    "NODE_TEST_COMMANDS",
    "NODE_LINT_COMMANDS",
}
REQUIRED_FUNCTIONS = {
    "_detect_node_package_manager",
    "_read_declared_node_package_manager",
    "_detect_package_managers",
    "_detect_test_commands",
    "_detect_lint_commands",
    "_directory_exists",
    "_dedupe",
}


def test_node_package_manager_is_selected_once_and_fails_closed(
    tmp_path: Path,
) -> None:
    namespace, tree = _load_selection_contract()
    detect = namespace["_detect_node_package_manager"]

    namespace["_read_project_file"] = lambda *args, **kwargs: json.dumps(
        {"packageManager": "yarn@4.9.2"}
    )
    selected = detect(
        tmp_path,
        ["package.json", "package-lock.json", "pnpm-lock.yaml"],
        tracked_revision=None,
    )
    assert selected == "yarn"

    namespace["_read_project_file"] = lambda *args, **kwargs: json.dumps(
        {"packageManager": "bun@1.2.3"}
    )
    assert (
        detect(
            tmp_path,
            ["package.json", "pnpm-lock.yaml"],
            tracked_revision=None,
        )
        is None
    )

    namespace["_read_project_file"] = lambda *args, **kwargs: json.dumps({})
    assert (
        detect(
            tmp_path,
            ["package.json", "package-lock.json", "pnpm-lock.yaml"],
            tracked_revision=None,
        )
        is None
    )
    assert detect(tmp_path, ["package.json"], tracked_revision=None) == "npm"

    assert namespace["_detect_package_managers"](
        ["package.json", "pnpm-lock.yaml"],
        node_package_manager="pnpm",
    ) == ["pnpm"]
    assert namespace["_detect_test_commands"](
        tmp_path,
        ["package.json"],
        node_package_manager="yarn",
    ) == ["yarn test"]
    assert namespace["_detect_lint_commands"](
        ["package.json"],
        node_package_manager="yarn",
    ) == ["yarn lint"]

    build_profile = _function(tree, "build_project_profile")
    rendered = ast.unparse(build_profile)
    assert "_detect_node_package_manager" in rendered
    assert rendered.count("node_package_manager=node_package_manager") >= 3


def _load_selection_contract() -> tuple[dict[str, object], ast.Module]:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    selected_nodes: list[ast.stmt] = [
        ast.parse("from __future__ import annotations").body[0]
    ]
    found_assignments: set[str] = set()
    found_functions: set[str] = set()

    for node in tree.body:
        target_name = _assignment_name(node)
        if target_name in REQUIRED_ASSIGNMENTS:
            selected_nodes.append(node)
            found_assignments.add(target_name)
        if isinstance(node, ast.FunctionDef) and node.name in REQUIRED_FUNCTIONS:
            selected_nodes.append(node)
            found_functions.add(node.name)

    assert found_assignments == REQUIRED_ASSIGNMENTS
    assert found_functions == REQUIRED_FUNCTIONS

    module = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Path": Path,
        "Literal": Literal,
        "json": json,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace, tree


def _assignment_name(node: ast.stmt) -> str | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        return target.id if isinstance(target, ast.Name) else None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"函数缺失：{name}")
