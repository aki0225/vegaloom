from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from vega.agent_runtime import SupervisorAgentRuntime
from vega.cli_entrypoint import app
from vega.models import BriefState


def test_explicit_change_run_works_from_independent_non_git_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="验证独立 workspace 的显式状态读取",
    )
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        ["status", "--run", run.run_dir.name, "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == run.run_dir.name
    assert payload["kind"] == "agent"
    assert payload["selected_run"]["selection_source"] == "explicit"


def test_explicit_generic_run_keeps_legacy_workspace_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "standalone-brief"
    run_dir.mkdir(parents=True)
    BriefState(
        run_id=run_dir.name,
        mode="bug",
        status="success",
        repo_path="<repo>",
        input_source="inline",
        current_step="completed",
    ).save(run_dir / "state.json")
    (run_dir / "agent-brief.md").write_text(
        "# Agent Brief\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        ["status", "--run", run_dir.name, "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == run_dir.name
    assert payload["kind"] == "brief"
    assert payload["selected_run"]["selection_source"] == "explicit"
    assert payload["explanation"] is None


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Vega Test"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "vega@example.invalid"],
        cwd=path,
        check=True,
    )
    (path / ".gitignore").write_text(
        "runs/\n.vega-worktrees/\n",
        encoding="utf-8",
        newline="\n",
    )
    (path / "README.md").write_text(
        "fixture\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path.resolve()
