from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
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


@pytest.mark.parametrize("command", ["status", "watch", "explain"])
@pytest.mark.parametrize("kind,artifacts", [
    ("project-profile", ("project-profile.json", "project-profile.md")),
    ("change-plan", ("change-plan.md", "scope-profile.md")),
    ("brief", ("agent-brief.md",)),
    ("reflect", ("reflection.md", "diff-summary.md", "test-summary.md")),
    ("review-pack", ("review-pack.md", "review-prompt.md")),
    ("gate", ("gate-result.json", "gate-report.md")),
    ("engineering-change", ("report.md",)),
])
def test_retired_artifacts_keep_read_only_cli_contract(
    tmp_path: Path,
    monkeypatch,
    command: str,
    kind: str,
    artifacts: tuple[str, ...],
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / f"20260101-120000-{kind}"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "run_id": run_dir.name,
            "status": "success",
            "repo_path": "<repo>",
            "input_source": "inline",
            "source_run": "historical-brief",
            "current_step": "done",
        }),
        encoding="utf-8",
    )
    for artifact in artifacts:
        content = (json.dumps({"repo_name": "repo", "repo_path": "<repo>"})
                   if artifact.endswith(".json") else "# 历史规划材料\n")
        (run_dir / artifact).write_text(content, encoding="utf-8")
    original = {path.name: path.read_bytes() for path in run_dir.iterdir()}
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(app, [command, "--run", run_dir.name, "--json"])

    assert result.exit_code == (2 if command == "explain" else 0), result.output
    payload = json.loads(result.output)
    if command == "explain":
        assert "explain 只支持 ChangeRun" in payload["message"]
    else:
        assert payload["run_id"] == run_dir.name
        assert payload["status"] == "success"
        if command == "status":
            assert payload["kind"] == kind
            assert payload["explanation"] is None
            assert payload["selected_run"]["selection_source"] == "explicit"
            for artifact in artifacts:
                assert str((run_dir / artifact).resolve()) in payload["key_artifacts"]
            guidance = "\n".join(payload["next_steps"])
            assert "仅供只读检查" in guidance
            assert 'vega change "<需求>"' in guidance
            assert all(retired not in guidance for retired in (
                "vega decision", "vega brief", "vega loop", "vega reflect", "vega review",
            ))
        else:
            assert payload["current_step"] == "done"
            assert payload["progress"] == []
    assert {path.name: path.read_bytes() for path in run_dir.iterdir()} == original


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
