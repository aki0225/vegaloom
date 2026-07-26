from __future__ import annotations

import ast
from pathlib import Path

import pytest
import typer


CLI_PATH = Path("src/vega/cli.py")
CLI_SUPPORT_PATH = Path("src/vega/cli_support.py")


def test_config_check_rejects_regular_file_before_project_config(
    tmp_path: Path,
) -> None:
    helper_name, guard = _load_repo_directory_guard()
    repo_file = tmp_path / "README.md"
    repo_file.write_text("# not a directory\n", encoding="utf-8")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    with pytest.raises(typer.BadParameter, match="目标仓库路径必须是目录"):
        guard(repo_file)

    assert guard(repo_dir) == repo_dir.resolve()

    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    config_check = _function(tree, "config_check")
    calls = [
        (node.lineno, node.func.id)
        for node in ast.walk(config_check)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    guard_lines = [line for line, name in calls if name == helper_name]
    config_lines = [line for line, name in calls if name == "check_project_config"]

    assert guard_lines
    assert config_lines
    assert min(guard_lines) < min(config_lines)


def _load_repo_directory_guard():
    candidates = [
        (CLI_SUPPORT_PATH, "require_repo_directory"),
        (CLI_PATH, "_require_repo_directory"),
    ]
    for path, name in candidates:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        try:
            function = _function(tree, name)
        except AssertionError:
            continue
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
            "_safe_path_display": lambda value: str(value),
        }
        exec(compile(module, str(path), "exec"), namespace)
        return name, namespace[name]
    raise AssertionError("仓库目录校验 helper 缺失")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"函数缺失：{name}")
