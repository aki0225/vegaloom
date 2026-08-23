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


def test_linked_worktree_can_replace_released_claim_from_original_owner(
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
        repo,
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
            repository_id=repository_scope(repo),
            phase="ready",
            current_work_item="W1",
        ),
    )
    mark_writer_claim_releasing(
        repo,
        run_id="owner-run",
        operation_id="operation-owner",
    )
    replacement_run = tmp_path / "workspace" / "runs" / "replacement-run"
    replacement_run.mkdir()

    acquire_writer_claim(
        worktree,
        run_dir=replacement_run,
        task_id="task-replacement",
        child_run="child-replacement",
        operation_id="operation-replacement",
    )

    claim_path = agent_repository_guard._prepare_control_dir(repo) / "writer-claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    assert claim["run_id"] == "replacement-run"
    assert claim["operation_id"] == "operation-replacement"
