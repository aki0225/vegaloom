from __future__ import annotations

import ast
import importlib
import subprocess
from pathlib import Path


PROFILE_PATH = Path("src/vega/project_profile.py")


def test_tracked_profile_binds_one_resolved_revision_and_repository_scope(
    tmp_path: Path,
) -> None:
    tree = ast.parse(PROFILE_PATH.read_text(encoding="utf-8"))
    build_profile = _function(tree, "build_project_profile")
    rendered = ast.unparse(build_profile)

    assert _imports_repository_identity(tree)
    assert "resolved_revision = " in rendered
    assert "resolve_git_revision(repo, tracked_revision or 'HEAD')" in rendered
    assert "_tracked_files(repo, resolved_revision)" in rendered
    assert rendered.count("tracked_revision=resolved_revision") >= 3

    identity = importlib.import_module("vega.repository_identity")
    repo = tmp_path / "checkout-a" / "backend"
    repo.mkdir(parents=True)
    _git(repo, "init")
    repo.joinpath("pyproject.toml").write_text(
        "[project]\nname = \"demo\"\n",
        encoding="utf-8",
    )
    _git(repo, "add", "pyproject.toml")
    _git(
        repo,
        "-c",
        "user.name=Vega Test",
        "-c",
        "user.email=vega@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    expected_head = _git(repo, "rev-parse", "HEAD").strip()
    repo.joinpath("pyproject.toml").write_text(
        "[project]\nname = \"dirty\"\n",
        encoding="utf-8",
    )

    assert identity.resolve_git_revision(repo, "HEAD") == expected_head

    same_name_repo = tmp_path / "checkout-b" / "backend"
    same_name_repo.mkdir(parents=True)
    first_scope = identity.repository_scope(repo)
    second_scope = identity.repository_scope(same_name_repo)

    assert first_scope.startswith("backend@")
    assert second_scope.startswith("backend@")
    assert first_scope != second_scope
    assert str(repo.resolve()) not in first_scope


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


def _imports_repository_identity(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "repository_identity" or node.level != 1:
            continue
        imported = {alias.name for alias in node.names}
        if {"repository_scope", "resolve_git_revision"}.issubset(imported):
            return True
    return False


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"函数缺失：{name}")
