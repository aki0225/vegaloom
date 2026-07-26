from __future__ import annotations

import ast
from pathlib import Path

import pytest
import typer


CLI_PATH = Path("src/vega/cli.py")
CLI_SUPPORT_PATH = Path("src/vega/cli_support.py")


def test_repo_directory_validation_moves_to_shared_cli_support(
    tmp_path: Path,
) -> None:
    cli_source = CLI_PATH.read_text(encoding="utf-8")
    cli_tree = ast.parse(cli_source)
    support_tree = ast.parse(CLI_SUPPORT_PATH.read_text(encoding="utf-8"))

    helper = _function(support_tree, "require_repo_directory")
    assert not _has_function(cli_tree, "_require_repo_directory")
    assert _imports_shared_helper(cli_tree)
    assert _call_count(cli_tree, "require_repo_directory") >= 10
    assert len(cli_source.splitlines()) <= 1006

    guard = _compile_helper(helper)
    repo_file = tmp_path / "README.md"
    repo_file.write_text("# not a directory\n", encoding="utf-8")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    with pytest.raises(typer.BadParameter, match="目标仓库路径必须是目录"):
        guard(repo_file)

    assert guard(repo_dir) == repo_dir.resolve()


def _compile_helper(function: ast.FunctionDef):
    module = ast.Module(
        body=[
            ast.parse("from __future__ import annotations").body[0],
            function,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {
        "Path": Path,
        "typer": typer,
        "redact_text": lambda value: str(value),
    }
    exec(compile(module, str(CLI_SUPPORT_PATH), "exec"), namespace)
    return namespace["require_repo_directory"]


def _imports_shared_helper(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "cli_support" or node.level != 1:
            continue
        if any(alias.name == "require_repo_directory" for alias in node.names):
            return True
    return False


def _call_count(tree: ast.Module, name: str) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


def _has_function(tree: ast.Module, name: str) -> bool:
    return any(
        isinstance(node, ast.FunctionDef) and node.name == name
        for node in tree.body
    )


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"函数缺失：{name}")
