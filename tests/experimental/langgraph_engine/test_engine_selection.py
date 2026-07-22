from __future__ import annotations

import builtins
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from vega.cli import app
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput, LoopAutomationState


def _init_git_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    path.joinpath("README.md").write_text("# Demo\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Tests",
            "-c",
            "user.email=vega@example.invalid",
            "commit",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _brief(repo: Path) -> BriefInput:
    return BriefInput(
        mode="bug",
        text="修复 README",
        source="test",
        repo_path=str(repo),
    )


def test_new_run_defaults_to_linear_engine(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    run_dir = LoopAutomationRuntime(tmp_path).start(_brief(repo), "assist")

    payload = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert payload["engine"] == "linear"


def test_missing_langgraph_dependency_is_rejected_before_run_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delitem(sys.modules, "vega.loop_graph_runtime", raising=False)
    for module_name in list(sys.modules):
        if module_name == "langgraph" or module_name.startswith("langgraph."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "langgraph" or name.startswith("langgraph."):
            raise ModuleNotFoundError(
                "测试模拟未安装 langgraph",
                name="langgraph",
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(ValueError, match="可选依赖"):
        LoopAutomationRuntime(tmp_path).start(
            _brief(repo),
            "assist",
            engine="langgraph",
        )

    assert not tmp_path.joinpath("runs").exists()


def test_langgraph_rejects_control_root_inside_target_repo_before_run_creation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    with pytest.raises(ValueError, match="control root 与目标 Git 仓库互不包含"):
        LoopAutomationRuntime(repo).start(
            _brief(repo),
            "assist",
            engine="langgraph",
        )

    assert not repo.joinpath("runs").exists()


@pytest.mark.parametrize("engine", ["", "unknown"])
def test_invalid_engine_is_rejected_before_run_creation(
    tmp_path: Path,
    engine: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError, match="engine 只能是"):
        LoopAutomationRuntime(tmp_path).start(
            _brief(repo),
            "assist",
            engine=engine,
        )

    assert not tmp_path.joinpath("runs").exists()


def test_loop_cli_passes_engine_selection_to_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / "cli-engine-loop"
    run_dir.mkdir(parents=True)
    LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="assist",
        repo_path=str(repo),
        input_source="test",
        status="needs_human",
        current_step="waiting_for_worker",
    ).save(run_dir / "state.json")
    captured: dict[str, object] = {}

    def start(*args, **kwargs):
        captured.update(kwargs)
        return run_dir

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("vega.cli._ensure_git_ready", lambda _: None)
    monkeypatch.setattr(
        "vega.cli.LoopAutomationRuntime",
        lambda **_: SimpleNamespace(start=start),
    )

    result = CliRunner().invoke(
        app,
        [
            "loop",
            "bug",
            "--repo",
            str(repo),
            "--text",
            "修复 README",
            "--engine",
            "linear",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["engine"] == "linear"


def test_finish_cli_passes_engine_selection_to_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / "cli-finish-loop"
    run_dir.mkdir(parents=True)
    LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="assist",
        repo_path=str(repo),
        input_source="test",
        status="needs_human",
        current_step="waiting_for_worker",
    ).save(run_dir / "state.json")
    run_dir.joinpath("finish-summary.json").write_text("{}\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def finish_run(run: str, engine: str | None = None) -> Path:
        captured["run"] = run
        captured["engine"] = engine
        return run_dir

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "vega.cli.FinishRuntime",
        lambda **_: SimpleNamespace(run=finish_run),
    )

    result = CliRunner().invoke(
        app,
        [
            "finish",
            "--run",
            run_dir.name,
            "--engine",
            "linear",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"run": run_dir.name, "engine": "linear"}
