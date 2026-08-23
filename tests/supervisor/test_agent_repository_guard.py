from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega import agent_repository_guard
from vega.agent_repository_guard import (
    AgentRepositoryGuardError,
    acquire_writer_claim,
    mark_writer_claim_releasing,
)
from vega.agent_contract import AgentState
from vega.agent_persistence import save_agent_state
from vega.repository_identity import repository_scope


def test_writer_claim_race_does_not_remove_actual_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    control_dir = agent_repository_guard._prepare_control_dir(repo)
    claim_path = control_dir / "writer-claim.json"
    owner = {
        "schema_version": 1,
        "run_id": "owner-run",
        "child_run": "owner-child",
    }
    original_open = agent_repository_guard.os.open

    def competing_open(path: Path, flags: int, mode: int = 0o777) -> int:
        if not flags & agent_repository_guard.os.O_EXCL:
            return original_open(path, flags, mode)
        Path(path).write_text(
            json.dumps(owner, ensure_ascii=False),
            encoding="utf-8",
        )
        raise FileExistsError(path)

    monkeypatch.setattr(agent_repository_guard.os, "open", competing_open)

    with pytest.raises(AgentRepositoryGuardError, match="owner-run"):
        acquire_writer_claim(
            repo,
            run_dir=repo,
            task_id="task",
            child_run="competing-child",
            operation_id="competing-operation",
        )

    assert json.loads(claim_path.read_text(encoding="utf-8")) == owner


def test_writer_claim_reuse_requires_same_run_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    first_run = tmp_path / "workspace-a" / "runs" / "same-run"
    copied_run = tmp_path / "workspace-b" / "runs" / "same-run"
    first_run.mkdir(parents=True)
    copied_run.mkdir(parents=True)
    claim = {
        "task_id": "same-task",
        "child_run": "same-child",
        "operation_id": "same-operation",
    }
    acquire_writer_claim(repo, run_dir=first_run, **claim)
    mark_writer_claim_releasing(
        repo,
        run_id="same-run",
        operation_id="same-operation",
    )
    acquire_writer_claim(repo, run_dir=first_run, **claim)

    with pytest.raises(AgentRepositoryGuardError, match="same-run"):
        acquire_writer_claim(repo, run_dir=copied_run, **claim)

    claim_path = agent_repository_guard._prepare_control_dir(repo) / "writer-claim.json"
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    assert payload["run_dir"] == str(first_run.resolve(strict=True))
    assert payload["status"] == "releasing"


def test_repository_can_replace_released_claim_after_owner_worktree_removed(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Vega Test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "vega@example.invalid"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
    worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    owner_run = tmp_path / "workspace" / "runs" / "owner-run"
    owner_run.mkdir(parents=True)
    acquire_writer_claim(
        worktree,
        run_dir=owner_run,
        task_id="task-owner",
        child_run="child-owner",
        operation_id="operation-owner",
    )
    save_agent_state(
        owner_run / "agent-state.json",
        AgentState(
            run_id="owner-run",
            task_id="task-owner",
            repository_id=repository_scope(worktree),
            phase="ready",
            current_work_item="W1",
        ),
    )
    mark_writer_claim_releasing(
        worktree,
        run_id="owner-run",
        operation_id="operation-owner",
    )
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    replacement_run = tmp_path / "workspace" / "runs" / "replacement-run"
    replacement_run.mkdir()

    acquire_writer_claim(
        repo,
        run_dir=replacement_run,
        task_id="task-replacement",
        child_run="child-replacement",
        operation_id="operation-replacement",
    )

    claim_path = agent_repository_guard._prepare_control_dir(repo) / "writer-claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    assert claim["run_id"] == "replacement-run"
    assert claim["operation_id"] == "operation-replacement"
