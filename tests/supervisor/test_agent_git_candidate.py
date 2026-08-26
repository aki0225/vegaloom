from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
    approve_change_contract,
)
from vega.agent_git_candidate import (
    GitCandidateError,
    freeze_candidate_commit,
    validate_candidate_binding,
)
from vega.agent_git_worktree import prepare_managed_worktree


def test_candidate_commit_isolated_from_user_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "source")
    source_head = _git(repo, "rev-parse", "HEAD")
    source_status = _git(repo, "status", "--short")
    handle = prepare_managed_worktree(
        repo,
        workspace_root=(tmp_path / "managed").resolve(),
        run_id="task-001",
        base_revision=source_head,
    )
    target = handle.worktree_path / "src" / "payments" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("def capture():\n    return 'ok'\n", encoding="utf-8")

    candidate = freeze_candidate_commit(
        handle,
        expected_parent_sha=source_head,
        contract=_contract(),
        execution_plan=_plan(),
        work_item_id="WI-01",
    )

    assert candidate.parent_sha == source_head
    assert candidate.candidate_sha != source_head
    assert candidate.changed_files == ["src/payments/service.py"]
    assert _git(repo, "rev-parse", "HEAD") == source_head
    assert _git(repo, "status", "--short") == source_status
    assert not (repo / "src" / "payments" / "service.py").exists()
    validate_candidate_binding(
        handle,
        candidate=candidate,
        contract=_contract(),
        execution_plan=_plan(),
    )


def test_worker_created_commit_is_rejected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "source")
    source_head = _git(repo, "rev-parse", "HEAD")
    handle = prepare_managed_worktree(
        repo,
        workspace_root=(tmp_path / "managed").resolve(),
        run_id="task-worker-commit",
        base_revision=source_head,
    )
    target = handle.worktree_path / "src" / "payments" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("worker committed\n", encoding="utf-8")
    _git(handle.worktree_path, "add", "--", target.relative_to(handle.worktree_path))
    _git(handle.worktree_path, "commit", "-m", "worker commit")

    with pytest.raises(GitCandidateError, match="改变了 Git HEAD"):
        freeze_candidate_commit(
            handle,
            expected_parent_sha=source_head,
            contract=_contract(),
            execution_plan=_plan(),
            work_item_id="WI-01",
        )


def test_candidate_outside_contract_is_rejected_before_commit(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "source")
    source_head = _git(repo, "rev-parse", "HEAD")
    handle = prepare_managed_worktree(
        repo,
        workspace_root=(tmp_path / "managed").resolve(),
        run_id="task-scope",
        base_revision=source_head,
    )
    target = handle.worktree_path / "deploy" / "release.ps1"
    target.parent.mkdir(parents=True)
    target.write_text("Write-Output release\n", encoding="utf-8")

    with pytest.raises(GitCandidateError, match="Approved Contract"):
        freeze_candidate_commit(
            handle,
            expected_parent_sha=source_head,
            contract=_contract(),
            execution_plan=_plan(),
            work_item_id="WI-01",
        )

    assert _git(handle.worktree_path, "rev-parse", "HEAD") == source_head
    assert _git(handle.worktree_path, "status", "--short") == "?? deploy/"


def test_workspace_drift_invalidates_candidate_binding(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "source")
    source_head = _git(repo, "rev-parse", "HEAD")
    handle = prepare_managed_worktree(
        repo,
        workspace_root=(tmp_path / "managed").resolve(),
        run_id="task-drift",
        base_revision=source_head,
    )
    target = handle.worktree_path / "src" / "payments" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("first\n", encoding="utf-8")
    candidate = freeze_candidate_commit(
        handle,
        expected_parent_sha=source_head,
        contract=_contract(),
        execution_plan=_plan(),
        work_item_id="WI-01",
    )
    target.write_text("changed after candidate\n", encoding="utf-8")

    with pytest.raises(GitCandidateError, match="Workspace 已漂移"):
        validate_candidate_binding(
            handle,
            candidate=candidate,
            contract=_contract(),
            execution_plan=_plan(),
        )


def test_managed_worktree_must_stay_outside_user_worktree(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "source")

    with pytest.raises(GitCandidateError, match="不能位于用户当前工作区内"):
        prepare_managed_worktree(
            repo,
            workspace_root=(repo / ".vega-worktrees").resolve(),
            run_id="task-nested",
        )


def _contract() -> ChangeContract:
    return approve_change_contract(
        ChangeContract(
            task_id="task-payment",
            goal="修复支付幂等",
            acceptance=["重复请求只产生一次有效扣款"],
            invariants=["一笔订单最多一次有效扣款"],
            required_verification=["支付模块回归测试"],
            authority_envelope=ChangeAuthorityEnvelope(
                allowed_paths=["src/payments/**", "tests/payments/**"],
                forbidden_paths=["src/payments/generated/**"],
                max_changed_files=5,
            ),
        ),
        actor="user",
        approved_at="2026-08-25T00:00:00+00:00",
    )


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-payment",
        contract_revision=1,
        plan_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="修复支付幂等",
                likely_files=["src/payments/service.py"],
            )
        ],
    )


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "初始化")
    return path


def _git(repo: Path, *args: object) -> str:
    process = subprocess.run(
        ["git", *(str(arg) for arg in args)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()
