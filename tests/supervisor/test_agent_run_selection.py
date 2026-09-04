from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from vega.agent_contract import AgentState
from vega.agent_persistence import save_agent_state
from vega.agent_run_selection import (
    ACTIVE_CHANGE_PHASES,
    ChangeRunSelectionError,
    list_repository_change_runs,
    resolve_repository_root,
    select_repository_change_run,
)
from vega.agent_runtime_logic import update_state


def test_selector_uses_source_repo_from_subdirectory_and_prefers_active_run(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    other_repo = _git_repo(tmp_path / "other-repo")
    subdirectory = repo / "src" / "feature"
    subdirectory.mkdir(parents=True)
    active = _change_run(
        repo,
        "run-active",
        source_repo=repo,
        phase="needs_human",
        updated_at="2026-09-04T08:00:00+00:00",
    )
    terminal = _change_run(
        repo,
        "run-terminal",
        source_repo=repo,
        phase="completed",
        updated_at="2026-09-04T09:00:00+00:00",
    )
    _change_run(
        repo,
        "run-other-repo",
        source_repo=other_repo,
        phase="ready",
        updated_at="2026-09-04T10:00:00+00:00",
    )
    os.utime(active, (1, 1))
    os.utime(terminal, (2_000_000_000, 2_000_000_000))

    selected = select_repository_change_run(subdirectory)

    assert resolve_repository_root(subdirectory) == repo
    assert selected is not None
    assert selected.run_dir == active
    assert [item.run_dir.name for item in list_repository_change_runs(repo)] == [
        "run-terminal",
        "run-active",
    ]


def test_selector_rejects_multiple_active_change_runs(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    first = _change_run(
        repo,
        "run-first",
        source_repo=repo,
        phase="planning",
        updated_at="2026-09-04T08:00:00+00:00",
    )
    second = _change_run(
        repo,
        "run-second",
        source_repo=repo,
        phase="finalizing",
        updated_at="2026-09-04T09:00:00+00:00",
    )

    with pytest.raises(ChangeRunSelectionError, match="多个未完成") as caught:
        select_repository_change_run(repo)

    assert [item.run_dir for item in caught.value.candidates] == [second, first]


def test_selector_uses_state_timestamp_for_terminal_runs(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    older = _change_run(
        repo,
        "run-older",
        source_repo=repo,
        phase="stopped",
        updated_at="2026-09-04T08:00:00+00:00",
    )
    newer = _change_run(
        repo,
        "run-newer",
        source_repo=repo,
        phase="completed",
        updated_at="2026-09-04T09:00:00+00:00",
    )
    os.utime(older, (2_000_000_000, 2_000_000_000))
    os.utime(newer, (1, 1))

    selected = select_repository_change_run(repo)

    assert selected is not None
    assert selected.run_dir == newer


def test_selector_fails_closed_for_matching_corrupt_change_run(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    other_repo = _git_repo(tmp_path / "other-repo")
    matching = _change_run(
        repo,
        "run-corrupt",
        source_repo=repo,
        phase="ready",
        updated_at="2026-09-04T08:00:00+00:00",
    )
    unrelated = _change_run(
        repo,
        "run-unrelated",
        source_repo=other_repo,
        phase="ready",
        updated_at="2026-09-04T08:00:00+00:00",
    )
    (matching / "agent-state.json").write_text(
        '{"kind":"agent_state","data":{},"digest":"broken"}\n',
        encoding="utf-8",
        newline="\n",
    )
    (unrelated / "agent-state.json").write_text(
        '{"kind":"agent_state","data":{},"digest":"broken"}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ChangeRunSelectionError, match="agent-state.json"):
        select_repository_change_run(repo)


def test_update_state_always_refreshes_updated_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = AgentState(
        run_id="run-1",
        task_id="task-1",
        repository_id="repo-1",
        run_kind="change",
        phase="ready",
        contract_revision=1,
        approved_contract_digest="b" * 64,
        execution_plan_revision=1,
        accepted_checkpoint_sha="a" * 40,
        updated_at="2026-09-04T08:00:00+00:00",
    )
    monkeypatch.setattr(
        "vega.agent_runtime_logic.utc_now",
        lambda: "2026-09-04T09:00:00+00:00",
    )

    updated = update_state(
        state,
        phase="needs_human",
        updated_at="2000-01-01T00:00:00+00:00",
    )

    assert updated.phase == "needs_human"
    assert updated.updated_at == "2026-09-04T09:00:00+00:00"


def test_active_phase_contract_is_explicit() -> None:
    assert ACTIVE_CHANGE_PHASES == {
        "planning",
        "awaiting_approval",
        "ready",
        "acting",
        "observing",
        "needs_human",
        "finalizing",
    }


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=path,
        check=True,
    )
    return path.resolve()


def _change_run(
    workspace: Path,
    run_id: str,
    *,
    source_repo: Path,
    phase: str,
    updated_at: str,
) -> Path:
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True)
    state_payload: dict[str, object] = {
        "run_id": run_id,
        "task_id": f"task-{run_id}",
        "repository_id": f"repo-{run_id}",
        "run_kind": "change",
        "phase": phase,
        "accepted_checkpoint_sha": "a" * 40,
        "updated_at": updated_at,
    }
    if phase != "planning":
        state_payload.update(
            {
                "contract_revision": 1,
                "execution_plan_revision": 1,
            }
        )
    if phase not in {"planning", "awaiting_approval"}:
        state_payload["approved_contract_digest"] = "b" * 64
    if phase == "acting":
        state_payload.update(
            {
                "active_child_run": "child-1",
                "active_operation_id": "operation-1",
            }
        )
    if phase == "completed":
        state_payload["terminal_status"] = "ready_to_commit"
    save_agent_state(
        run_dir / "agent-state.json",
        AgentState.model_validate(state_payload),
    )
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "repo_path": str(workspace),
        "base_revision": "0" * 40,
        "task_card": None,
        "task_card_sha256": None,
        "change_run": {
            "schema_version": 1,
            "run_id": run_id,
            "source_repo_path": str(source_repo),
            "worktree_path": str(workspace / ".vega-worktrees" / run_id),
            "branch": f"vega/{run_id}",
            "base_revision": "0" * 40,
        },
    }
    (run_dir / "agent-run.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return run_dir.resolve()
