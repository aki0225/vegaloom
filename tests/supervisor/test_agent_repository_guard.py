from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega import agent_repository_guard
from vega.agent_repository_guard import (
    AgentRepositoryGuardError,
    acquire_writer_claim,
)


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
