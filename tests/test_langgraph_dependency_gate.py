from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_dependency_probe(
    tmp_path: Path,
    *,
    require_langgraph: bool,
) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (str(tmp_path), existing_pythonpath)
        if item
    )
    environment["PYTHONUTF8"] = "1"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--basetemp",
        str(tmp_path / "pytest-basetemp"),
    ]
    if require_langgraph:
        command.append("--require-langgraph")
    command.append("tests/test_langgraph_dependency_gate.py")

    return subprocess.run(
        command,
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _write_fake_langgraph(tmp_path: Path, sqlite_source: str) -> None:
    package_root = tmp_path / "langgraph"
    checkpoint_root = package_root / "checkpoint"
    checkpoint_root.mkdir(parents=True)
    package_root.joinpath("__init__.py").write_text("", encoding="utf-8")
    checkpoint_root.joinpath("__init__.py").write_text("", encoding="utf-8")
    checkpoint_root.joinpath("sqlite.py").write_text(
        sqlite_source,
        encoding="utf-8",
        newline="\n",
    )


def test_require_langgraph_exits_nonzero_when_dependency_is_missing(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "sitecustomize.py"
    blocker.write_text(
        "\n".join(
            [
                "import importlib.util",
                "_original_find_spec = importlib.util.find_spec",
                "def _blocked_find_spec(name, package=None):",
                "    if name == 'langgraph' or name.startswith('langgraph.'):",
                "        return None",
                "    return _original_find_spec(name, package)",
                "importlib.util.find_spec = _blocked_find_spec",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )

    completed = _run_dependency_probe(tmp_path, require_langgraph=True)

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "--require-langgraph 已启用" in output
    assert "langgraph.checkpoint.sqlite" in output
    assert "不可发现" in output


def test_require_langgraph_exits_nonzero_when_import_fails(
    tmp_path: Path,
) -> None:
    _write_fake_langgraph(
        tmp_path,
        "raise RuntimeError('模拟 sqlite 导入失败')\n",
    )

    completed = _run_dependency_probe(tmp_path, require_langgraph=True)

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "--require-langgraph 已启用" in output
    assert "langgraph.checkpoint.sqlite" in output
    assert "导入失败：RuntimeError: 模拟 sqlite 导入失败" in output


def test_require_langgraph_allows_importable_dependencies(
    tmp_path: Path,
) -> None:
    _write_fake_langgraph(tmp_path, "SQLITE_AVAILABLE = True\n")

    completed = _run_dependency_probe(tmp_path, require_langgraph=True)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_without_require_langgraph_does_not_import_optional_dependencies(
    tmp_path: Path,
) -> None:
    _write_fake_langgraph(
        tmp_path,
        "raise RuntimeError('未传 flag 时不应导入')\n",
    )

    completed = _run_dependency_probe(tmp_path, require_langgraph=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
