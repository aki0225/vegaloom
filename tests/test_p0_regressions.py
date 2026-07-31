from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from vega import workspace_check as workspace_check_module
from vega import reflect_runtime as reflect_runtime_module
from vega import review_runtime as review_runtime_module
from vega.brief_runtime import BriefRuntime
from vega.finish_runtime import FinishRuntime
from vega.gate_runtime import GateRuntime
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput, LoopIterationState
from vega.project_config import ScopeConfig
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewRuntime
from vega.runner import RunnerResult
from vega.scope_gate import evaluate_scope_gate, validate_iteration_scope_gate_artifacts


class CountingWorker:
    def __init__(self, line: str | None = None) -> None:
        self.line = line
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds, execution_context
        self.calls += 1
        if self.line is not None:
            readme = repo_path / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + self.line + "\n",
                encoding="utf-8",
                newline="\n",
            )
        return RunnerResult(status="success", output="worker done", command=["counting-worker"])


class PathWorker:
    def __init__(self, relative_path: str, line: str) -> None:
        self.relative_path = relative_path
        self.line = line
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds, execution_context
        self.calls += 1
        target = repo_path / self.relative_path
        target.write_text(
            target.read_text(encoding="utf-8") + self.line + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(status="success", output="worker done", command=["path-worker"])


class CommitThenModifyWorker:
    def __init__(self, committed_path: str, remaining_path: str) -> None:
        self.committed_path = committed_path
        self.remaining_path = remaining_path
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds, execution_context
        self.calls += 1
        committed = repo_path / self.committed_path
        committed.write_text(
            committed.read_text(encoding="utf-8") + "committed by worker\n",
            encoding="utf-8",
            newline="\n",
        )
        _git(repo_path, "add", self.committed_path)
        _git_commit(repo_path, "worker commit")
        remaining = repo_path / self.remaining_path
        remaining.write_text(
            remaining.read_text(encoding="utf-8") + "remaining worker diff\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="worker committed one path and left another path dirty",
            command=["commit-then-modify-worker"],
        )


class IndexHidingWorker:
    def __init__(
        self,
        hidden_path: str,
        visible_path: str,
        index_flag: str,
    ) -> None:
        self.hidden_path = hidden_path
        self.visible_path = visible_path
        self.index_flag = index_flag
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds, execution_context
        self.calls += 1
        _git(repo_path, "update-index", self.index_flag, "--", self.hidden_path)
        hidden = repo_path / self.hidden_path
        hidden.write_text(
            hidden.read_text(encoding="utf-8") + "hidden worker diff\n",
            encoding="utf-8",
            newline="\n",
        )
        visible = repo_path / self.visible_path
        visible.write_text(
            visible.read_text(encoding="utf-8") + "visible worker diff\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="worker hid one path with an index flag",
            command=["index-hiding-worker"],
        )


class IgnoredContentMutatingWorker:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds, execution_context
        ignored = repo_path / "cache.tmp"
        original_stat = ignored.stat()
        ignored.write_text("bravo\n", encoding="utf-8", newline="\n")
        os.utime(
            ignored,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        readme = repo_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="worker changed ignored content",
            command=["ignored-content-mutating-worker"],
        )


class GitControlMutatingWorker:
    def __init__(self, target: str) -> None:
        self.target = target

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds, execution_context
        if self.target == "exclude":
            exclude = repo_path / ".git" / "info" / "exclude"
            exclude.write_text(
                exclude.read_text(encoding="utf-8") + "\nworker-hidden.tmp\n",
                encoding="utf-8",
                newline="\n",
            )
            repo_path.joinpath("worker-hidden.tmp").write_text(
                "hidden by worker\n",
                encoding="utf-8",
                newline="\n",
            )
        else:
            _git(repo_path, "config", "core.excludesFile", "worker-ignore-rules")
        readme = repo_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output=f"worker changed git {self.target}",
            command=["git-control-mutating-worker"],
        )


class CountingReviewer:
    def __init__(self, verdicts: list[str] | None = None) -> None:
        self.verdicts = verdicts or ["approve"]
        self.calls = 0
        self.prompts: list[str] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del repo_path, sandbox, timeout_seconds, execution_context
        self.calls += 1
        self.prompts.append(prompt)
        verdict = self.verdicts.pop(0) if self.verdicts else "approve"
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": verdict,
                    "summary": f"probe {verdict}",
                    "findings": [],
                    "checked_items": ["scope", "diff"],
                },
                ensure_ascii=False,
            ),
            command=["counting-reviewer"],
        )


def test_auto_stops_before_worker_when_tracked_baseline_is_dirty(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\npreexisting change\n",
        encoding="utf-8",
        newline="\n",
    )
    worker = CountingWorker("worker change")
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    workspace_check = _read_json(run_dir / "iterations" / "01" / "workspace-check.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_baseline_dirty"
    assert worker.calls == 0
    assert reviewer.calls == 0
    assert workspace_check["baseline_tracked_changes_present"] is True
    assert workspace_check["baseline_tracked_files"] == ["README.md"]


def test_auto_records_terminal_artifacts_when_ignored_inventory_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(tmp_path)
    worker = CountingWorker("worker change")
    reviewer = CountingReviewer()

    def timeout_ignored_paths(repo_path: Path) -> tuple[list[str], bool]:
        del repo_path
        raise subprocess.TimeoutExpired(
            cmd=["git", "ls-files"],
            timeout=30,
        )

    monkeypatch.setattr(
        workspace_check_module,
        "_ignored_paths",
        timeout_ignored_paths,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    iteration_dir = run_dir / "iterations" / "01"
    trace = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_baseline_unavailable"
    assert worker.calls == 0
    assert reviewer.calls == 0
    assert iteration_dir.joinpath("workspace-check.json").is_file()
    assert iteration_dir.joinpath("workspace-check.md").is_file()
    assert run_dir.joinpath("final-report.md").is_file()
    assert run_dir.joinpath("eval.md").is_file()
    assert any(item["event"] == "workspace_baseline_blocked" for item in trace)
    assert trace[-1]["event"] == "run_paused"
    assert trace[-1]["status"] == "needs_human"


def test_auto_blocks_mm_baseline_when_net_diff_is_empty(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\nstaged only\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "README.md")
    # index 保留新增行，但工作区又把它撤回到 HEAD 内容；`git diff HEAD` 会为空。
    repo.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    worker = CountingWorker("worker change")

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=CountingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    workspace_check = _read_json(run_dir / "iterations" / "01" / "workspace-check.json")
    assert _git(repo, "diff", "HEAD", "--").strip() == ""
    assert _git(repo, "status", "--short").startswith("MM README.md")
    assert state["current_step"] == "workspace_baseline_dirty"
    assert worker.calls == 0
    assert workspace_check["baseline_tracked_files"] == ["README.md"]


def test_auto_does_not_start_worker_when_head_changes_after_loop_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(tmp_path)
    worker = CountingWorker("worker change")
    original_run = BriefRuntime.run

    def brief_then_commit(
        runtime: BriefRuntime,
        brief_input: BriefInput,
    ) -> Path:
        result = original_run(runtime, brief_input)
        repo.joinpath("README.md").write_text(
            "# Demo\ncommit between start and worker\n",
            encoding="utf-8",
            newline="\n",
        )
        _git(repo, "add", "README.md")
        _git_commit(repo, "concurrent commit")
        return result

    monkeypatch.setattr("vega.loop_runtime.BriefRuntime.run", brief_then_commit)

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=CountingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    workspace_check = _read_json(run_dir / "iterations" / "01" / "workspace-check.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_head_changed"
    assert worker.calls == 0
    assert workspace_check["baseline_head_changed"] is True
    assert workspace_check["baseline_head_sha"] == state["initial_head_sha"]


def test_loop_start_rejects_head_change_while_loading_project_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(tmp_path)
    worker = CountingWorker("worker change")
    from vega.loop_runtime import load_project_config as original_load_project_config

    def load_then_commit(repo_path: Path):
        config = original_load_project_config(repo_path)
        repo.joinpath("README.md").write_text(
            "# Demo\ncommit during policy load\n",
            encoding="utf-8",
            newline="\n",
        )
        _git(repo, "add", "README.md")
        _git_commit(repo, "policy load race")
        return config

    monkeypatch.setattr(
        "vega.loop_runtime.load_project_config",
        load_then_commit,
    )

    with pytest.raises(RuntimeError, match="loop 启动时 HEAD 或项目策略发生变化"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=CountingReviewer(),
        ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    assert worker.calls == 0
    assert not workspace.joinpath("runs").exists()


def test_auto_allows_follow_up_worker_against_its_own_prior_diff(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    worker = CountingWorker("worker change")
    reviewer = CountingReviewer(["request_changes", "request_changes"])

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=2, verify=False)

    state = _read_json(run_dir / "state.json")
    second_workspace_check = _read_json(
        run_dir / "iterations" / "02" / "workspace-check.json"
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] != "workspace_baseline_dirty"
    assert len(state["iterations"]) == 2
    assert worker.calls == 2
    assert reviewer.calls == 2
    assert second_workspace_check["baseline_tracked_changes_present"] is True
    assert any("上一轮 auto" in item for item in second_workspace_check["reasons"])
    assert "FAIL: risk_gate_recomputation_failed" not in state["eval_results"]

    FinishRuntime(workspace).run(run_dir.name)
    finish_summary = _read_json(run_dir / "finish-summary.json")
    assert finish_summary["artifact_integrity"]["valid"] is True
    assert (
        "iteration_01_risk_gate_recomputation_failed"
        not in finish_summary["artifact_integrity"]["issues"]
    )


def test_auto_does_not_review_when_reflect_deterministic_eval_failed(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    worker = CountingWorker("trailing whitespace   ")
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    reflect_run = workspace / "runs" / state["iterations"][0]["reflect_run"]
    reflect_state = _read_json(reflect_run / "state.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "reflect_failed"
    assert reflect_state["status"] == "failed"
    assert reviewer.calls == 0
    assert (run_dir / "iterations" / "01" / "reflect-failure.md").exists()


def test_standalone_review_rejects_failed_reflect_without_running_reviewer(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\ntrailing whitespace   \n",
        encoding="utf-8",
        newline="\n",
    )
    reflect_run = ReflectRuntime(workspace).run(repo, note="staged evidence semantics")
    reviewer = CountingReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(repo, reflect_run.name)

    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_stale"
    assert reviewer.calls == 0
    assert "source_reflect_not_success" in context["evidence_issues"]


def test_standalone_review_rejects_unsafe_index_flags_before_runner(
    tmp_path: Path,
) -> None:
    workspace, repo = _init_repo(
        tmp_path,
        files={"tests/locked.py": "value = 'baseline'\n"},
    )
    repo.joinpath("README.md").write_text(
        "# Demo\nreviewable change\n",
        encoding="utf-8",
        newline="\n",
    )
    reflect_run = ReflectRuntime(workspace).run(repo, note="index flag review guard")
    _git(repo, "update-index", "--assume-unchanged", "--", "tests/locked.py")
    repo.joinpath("tests/locked.py").write_text(
        "value = 'hidden after reflect'\n",
        encoding="utf-8",
        newline="\n",
    )
    reviewer = CountingReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")
    assert state["status"] == "needs_human"
    assert reviewer.calls == 0
    assert "current_unsafe_index_flags_present" in context["evidence_issues"]
    assert context["current_unsafe_index_paths"] == ["tests/locked.py"]


def test_standalone_review_rechecks_policy_after_risk_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(
        tmp_path,
        config="version: 1\n",
        files={"tests/locked.py": "value = 'baseline'\n"},
    )
    repo.joinpath("README.md").write_text(
        "# Demo\nreviewable change\n",
        encoding="utf-8",
        newline="\n",
    )
    reflect_run = ReflectRuntime(workspace).run(repo, note="policy review guard")
    original_evaluate_risk_gate = review_runtime_module._evaluate_review_risk_gate

    def risk_gate_then_mutate_policy(*args: object, **kwargs: object) -> dict:
        result = original_evaluate_risk_gate(*args, **kwargs)
        repo.joinpath(".vega.yaml").write_text(
            "version: 1\nrisk:\n  require_human_review:\n    - README.md\n",
            encoding="utf-8",
            newline="\n",
        )
        _git(repo, "update-index", "--assume-unchanged", "--", "tests/locked.py")
        repo.joinpath("tests/locked.py").write_text(
            "value = 'hidden during risk gate'\n",
            encoding="utf-8",
            newline="\n",
        )
        return result

    monkeypatch.setattr(
        review_runtime_module,
        "_evaluate_review_risk_gate",
        risk_gate_then_mutate_policy,
    )
    reviewer = CountingReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")
    assert state["status"] == "needs_human"
    assert reviewer.calls == 0
    assert "project_policy_changed_before_reviewer" in context["evidence_issues"]
    assert "review_authorization_workspace_changed" in context["evidence_issues"]
    assert "current_unsafe_index_flags_present" in context["evidence_issues"]


def test_auto_enforces_change_budget_before_isolated_reviewer(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "budget:",
            "  max_changed_files: 0",
            "  max_diff_lines: 0",
            "  max_new_files: 0",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    worker = CountingWorker("worker change")
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    gate_result = _read_json(run_dir / "iterations" / "01" / "risk-gate-result.json")
    reason_codes = {item["code"] for item in gate_result["reasons"]}
    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_needs_human"
    assert reviewer.calls == 0
    assert gate_result["recommendation"] == "human-review"
    assert {"budget_changed_files", "budget_diff_lines"}.issubset(reason_codes)


def test_auto_allows_exact_scope_and_writes_bound_evidence(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - .vega.yaml",
            "verification:",
            "  commands:",
            "    - python -c \"print('scope verification passed')\"",
            "  max_commands: 1",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    result = _read_json(run_dir / "iterations" / "01" / "scope-gate-result.json")
    post_result = _read_json(
        run_dir / "iterations" / "01" / "scope-gate-post-verification-result.json"
    )
    pre_review_result = _read_json(
        run_dir / "iterations" / "01" / "scope-gate-pre-review-result.json"
    )
    report = (run_dir / "iterations" / "01" / "scope-gate-report.md").read_text(
        encoding="utf-8"
    )
    trace_events = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert state["status"] == "success"
    assert reviewer.calls == 1
    assert iteration["scope_gate_status"] == "success"
    assert iteration["scope_gate_changed_files"] == ["README.md"]
    assert iteration["scope_gate_violations"] == []
    assert iteration["scope_gate_result_sha256"]
    assert iteration["scope_gate_report_sha256"]
    assert iteration["scope_gate_post_verification_status"] == "success"
    assert iteration["scope_gate_post_verification_result_sha256"]
    assert iteration["scope_gate_post_verification_report_sha256"]
    assert iteration["scope_gate_pre_review_status"] == "success"
    assert iteration["scope_gate_pre_review_result_sha256"]
    assert iteration["scope_gate_pre_review_report_sha256"]
    assert result["status"] == "success"
    assert result["phase"] == "pre_verification"
    assert result["staged_changed_files"] == []
    assert result["unstaged_changed_files"] == ["README.md"]
    assert post_result["status"] == "success"
    assert post_result["phase"] == "post_verification"
    assert pre_review_result["status"] == "success"
    assert pre_review_result["phase"] == "pre_review"
    assert "## 证据绑定" in report
    assert [
        event
        for event in trace_events
        if event["event"] == "scope_gate_finished" and event["iteration"] == 1
    ]
    assert "PASS: pre-verification scope gate artifact 与 iteration 一致" in state[
        "eval_results"
    ]
    assert "PASS: post-verification scope gate artifact 与 iteration 一致" in state[
        "eval_results"
    ]
    assert "PASS: pre-review scope gate artifact 与 iteration 一致" in state["eval_results"]


def test_auto_reuses_loop_risk_gate_for_embedded_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(
        tmp_path,
        config="\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('risk gate verification passed')\"",
                "  max_commands: 1",
                "",
            ]
        ),
    )
    reviewer = CountingReviewer()

    def fail_duplicate_review_risk_gate(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("Loop 内嵌 reviewer 不应重复计算刚生成的风险门禁")

    monkeypatch.setattr(
        review_runtime_module,
        "_evaluate_review_risk_gate",
        fail_duplicate_review_risk_gate,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    context = _read_json(
        run_dir / "iterations" / "01" / "review-context.json"
    )

    assert state["status"] == "success"
    assert reviewer.calls == 1
    assert context["risk_gate"]["status"] == "success"
    assert context["risk_gate"]["source_run"] == state["iterations"][0]["reflect_run"]


def test_auto_reused_risk_gate_rejects_policy_drift_before_review_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(tmp_path, config="version: 1\n")
    reviewer = CountingReviewer()
    original_run_review = LoopAutomationRuntime._run_review

    def mutate_policy_before_review(
        runtime: LoopAutomationRuntime,
        repo_path: Path,
        reflect_run: Path,
        reviewer_name: str,
        loop_run_dir: Path,
        iteration: int,
        config,
        risk_gate_result,
        expected_project_policy_snapshot,
    ) -> Path:
        repo_path.joinpath(".vega.yaml").write_text(
            "version: 1\nrisk:\n  require_human_review:\n    - README.md\n",
            encoding="utf-8",
            newline="\n",
        )
        return original_run_review(
            runtime,
            repo_path,
            reflect_run,
            reviewer_name,
            loop_run_dir,
            iteration,
            config,
            risk_gate_result,
            expected_project_policy_snapshot,
        )

    monkeypatch.setattr(
        LoopAutomationRuntime,
        "_run_review",
        mutate_policy_before_review,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    review_run = state["iterations"][0]["review_run"]
    context = _read_json(workspace / "runs" / review_run / "review-context.json")

    assert state["status"] == "failed"
    assert reviewer.calls == 0
    assert (
        "project_policy_changed_before_review_start"
        in context["evidence_issues"]
    )


def test_auto_scope_failure_blocks_verification_reflect_and_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - README.md",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    reviewer = CountingReviewer()

    def verification_must_not_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("scope gate 失败后不应启动 verification")

    monkeypatch.setattr("vega.loop_runtime.run_project_verification", verification_must_not_run)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    result = _read_json(run_dir / "iterations" / "01" / "scope-gate-result.json")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_failed"
    assert state["current_iteration"] == 1
    assert reviewer.calls == 0
    assert iteration["verification_status"] == "skipped"
    assert iteration["reflect_run"] is None
    assert iteration["scope_gate_status"] == "failed"
    assert result["status"] == "failed"
    assert result["violations"] == [
        {
            "code": "forbidden_path",
            "path": "README.md",
            "matched_patterns": ["README.md"],
        }
    ]
    assert not (run_dir / "iterations" / "01" / "verification-summary.md").exists()
    assert not (run_dir / "iterations" / "01" / "reflect-run.txt").exists()
    assert not (run_dir / "iterations" / "01" / "review-verdict.json").exists()
    assert "PASS: pre-verification scope gate artifact 与 iteration 一致" in state[
        "eval_results"
    ]


def test_auto_worker_commit_cannot_hide_forbidden_path_from_scope_gate(
    tmp_path: Path,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - tests/**",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={"tests/locked.py": "value = 'baseline'\n"},
    )
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CommitThenModifyWorker("tests/locked.py", "README.md"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_check_failed"
    assert reviewer.calls == 0
    assert _git(repo, "rev-parse", "HEAD").strip() != state["initial_head_sha"]


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_auto_worker_index_flags_cannot_hide_forbidden_path_from_scope_gate(
    tmp_path: Path,
    index_flag: str,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - tests/**",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={"tests/locked.py": "value = 'baseline'\n"},
    )
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=IndexHidingWorker(
            "tests/locked.py",
            "README.md",
            index_flag,
        ),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    result = _read_json(run_dir / "iterations" / "01" / "scope-gate-result.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_failed"
    assert reviewer.calls == 0
    assert result["failure_code"] == "scope_index_flags_unsafe"
    assert result["unsafe_index_paths"] == ["tests/locked.py"]
    assert result["changed_files"] == ["README.md"]


def test_auto_worker_cannot_mutate_existing_ignored_content_before_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "verification:",
            "  commands:",
            '    - python -c "print(\'must not run\')"',
            "  max_commands: 1",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={
            ".gitignore": "*.tmp\n",
            "cache.tmp": "alpha\n",
        },
    )
    reviewer = CountingReviewer()

    def verification_must_not_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("ignored 内容变化后不应启动 verification")

    monkeypatch.setattr("vega.loop_runtime.run_project_verification", verification_must_not_run)

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=IgnoredContentMutatingWorker(),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    workspace_check = _read_json(run_dir / "iterations" / "01" / "workspace-check.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_check_failed"
    assert reviewer.calls == 0
    assert workspace_check["baseline_ignored_changed"] is True
    assert workspace_check["git_control_changed"] is False


@pytest.mark.parametrize("target", ["exclude", "config"])
def test_auto_worker_cannot_mutate_git_control_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "verification:",
            "  commands:",
            '    - python -c "print(\'must not run\')"',
            "  max_commands: 1",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    reviewer = CountingReviewer()

    def verification_must_not_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("Git 控制文件变化后不应启动 verification")

    monkeypatch.setattr("vega.loop_runtime.run_project_verification", verification_must_not_run)

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=GitControlMutatingWorker(target),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    workspace_check = _read_json(run_dir / "iterations" / "01" / "workspace-check.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_check_failed"
    assert reviewer.calls == 0
    assert workspace_check["git_control_changed"] is True


def test_assist_continue_cannot_bypass_scope_gate(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  forbidden_paths:",
            "    - README.md",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    reviewer = CountingReviewer()
    runtime = LoopAutomationRuntime(workspace, reviewer_runner=reviewer)
    run_dir = runtime.start(_brief(repo), "assist", verify=False)
    repo.joinpath("README.md").write_text(
        "# Demo\nmanual worker change\n",
        encoding="utf-8",
        newline="\n",
    )

    runtime.continue_assist(run_dir.name, repo, verify=False)

    state = _read_json(run_dir / "state.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_failed"
    assert reviewer.calls == 0
    assert state["iterations"][0]["scope_gate_status"] == "failed"
    assert not (run_dir / "iterations" / "01" / "reflect-run.txt").exists()


def test_assist_continue_rejects_head_change_after_loop_start(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - tests/**",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={"tests/locked.py": "value = 'baseline'\n"},
    )
    reviewer = CountingReviewer()
    runtime = LoopAutomationRuntime(workspace, reviewer_runner=reviewer)
    run_dir = runtime.start(_brief(repo), "assist", verify=False)
    repo.joinpath("tests/locked.py").write_text(
        "value = 'committed after start'\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "tests/locked.py")
    _git_commit(repo, "manual commit")
    repo.joinpath("README.md").write_text(
        "# Demo\nallowed manual diff\n",
        encoding="utf-8",
        newline="\n",
    )

    runtime.continue_assist(run_dir.name, repo, verify=False)

    state = _read_json(run_dir / "state.json")
    workspace_result = _read_json(
        run_dir / "iterations" / "01" / "workspace-check.json"
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_head_changed"
    assert reviewer.calls == 0
    assert workspace_result["baseline_head_changed"] is True
    assert workspace_result["baseline_head_sha"] == state["initial_head_sha"]
    assert workspace_result["current_head_sha"] != state["initial_head_sha"]
    assert not (run_dir / "iterations" / "01" / "scope-gate-result.json").exists()


@pytest.mark.parametrize("mode", ["auto", "assist"])
def test_scope_gate_rechecks_changes_introduced_by_verification(
    tmp_path: Path,
    mode: str,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "verification:",
            "  commands:",
            "    - python mutate.py",
            "  max_commands: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - tests/**",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={
            "tests/locked.py": "value = 'baseline'\n",
            "mutate.py": (
                "from pathlib import Path\n"
                "Path('tests/locked.py').write_text(\"value = 'changed'\\n\", encoding='utf-8')\n"
            ),
        },
    )
    reviewer = CountingReviewer()
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change") if mode == "auto" else None,
        reviewer_runner=reviewer,
    )
    if mode == "auto":
        run_dir = runtime.start(_brief(repo), "auto", max_iterations=1, verify=True)
    else:
        run_dir = runtime.start(_brief(repo), "assist", verify=False)
        repo.joinpath("README.md").write_text(
            "# Demo\nmanual worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        runtime.continue_assist(run_dir.name, repo, verify=True)

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    post_result = _read_json(
        run_dir / "iterations" / "01" / "scope-gate-post-verification-result.json"
    )

    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_post_verification_failed"
    assert reviewer.calls == 0
    assert iteration["scope_gate_status"] == "success"
    assert iteration["scope_gate_post_verification_status"] == "failed"
    assert iteration["verification_status"] == "passed"
    assert iteration["reflect_run"] is None
    assert post_result["phase"] == "post_verification"
    assert post_result["changed_files"] == ["README.md", "tests/locked.py"]
    assert post_result["violations"] == [
        {
            "code": "forbidden_path",
            "path": "tests/locked.py",
            "matched_patterns": ["tests/**"],
        }
    ]
    assert not (run_dir / "iterations" / "01" / "reflect-run.txt").exists()
    assert not (run_dir / "iterations" / "01" / "review-verdict.json").exists()


def test_verification_project_policy_mutation_stops_before_reflect_and_reviewer(
    tmp_path: Path,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "verification:",
            "  commands:",
            "    - python mutate_policy.py",
            "  max_commands: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={
            "mutate_policy.py": (
                "from pathlib import Path\n"
                "Path('.vega.yaml').write_text('version: 1\\n', encoding='utf-8')\n"
            ),
        },
    )
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    assert state["status"] == "needs_human"
    assert state["current_step"] == "project_policy_changed"
    assert reviewer.calls == 0
    assert iteration["scope_gate_status"] == "success"
    assert iteration["scope_gate_post_verification_status"] == "skipped"
    assert iteration["verification_status"] == "passed"
    assert iteration["reflect_run"] is None
    assert (run_dir / "iterations" / "01" / "project-policy-change-report.md").exists()
    assert not (
        run_dir / "iterations" / "01" / "scope-gate-post-verification-result.json"
    ).exists()
    assert not (run_dir / "iterations" / "01" / "reflect-run.txt").exists()


@pytest.mark.parametrize("mode", ["auto", "assist"])
def test_pre_review_scope_gate_blocks_mutation_after_reflect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - tests/**",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={"tests/locked.py": "value = 'baseline'\n"},
    )
    original_run = ReflectRuntime.run

    def reflect_then_mutate(
        runtime: ReflectRuntime,
        repo_path: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        result = original_run(runtime, repo_path, *args, **kwargs)
        repo_path.joinpath("tests/locked.py").write_text(
            "value = 'changed'\n",
            encoding="utf-8",
            newline="\n",
        )
        return result

    monkeypatch.setattr("vega.loop_runtime.ReflectRuntime.run", reflect_then_mutate)
    reviewer = CountingReviewer()
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change") if mode == "auto" else None,
        reviewer_runner=reviewer,
    )
    if mode == "auto":
        run_dir = runtime.start(_brief(repo), "auto", max_iterations=1, verify=False)
    else:
        run_dir = runtime.start(_brief(repo), "assist", verify=False)
        repo.joinpath("README.md").write_text(
            "# Demo\nmanual worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        runtime.continue_assist(run_dir.name, repo, verify=False)

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    review_result = _read_json(run_dir / "iterations" / "01" / "scope-gate-pre-review-result.json")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_pre_review_failed"
    assert state["current_iteration"] == 1
    assert reviewer.calls == 0
    assert iteration["reflect_run"]
    assert iteration["scope_gate_status"] == "success"
    assert iteration["scope_gate_post_verification_status"] == "success"
    assert iteration["scope_gate_pre_review_status"] == "failed"
    assert review_result["phase"] == "pre_review"
    assert review_result["changed_files"] == ["README.md", "tests/locked.py"]
    assert review_result["violations"][0]["path"] == "tests/locked.py"
    fix_prompt = (run_dir / "iterations" / "01" / "fix-prompt.md").read_text(encoding="utf-8")
    assert "scope-gate-pre-review-report.md" in fix_prompt
    assert "scope-gate-pre-review-result.json" in fix_prompt
    assert not (run_dir / "iterations" / "01" / "risk-gate-result.json").exists()
    assert not (run_dir / "iterations" / "01" / "review-verdict.json").exists()


@pytest.mark.parametrize("mode", ["auto", "assist"])
def test_project_policy_mutation_during_reflect_stops_before_pre_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    config = "version: 1\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    original_run = ReflectRuntime.run

    def reflect_then_mutate_policy(
        runtime: ReflectRuntime,
        repo_path: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        result = original_run(runtime, repo_path, *args, **kwargs)
        repo_path.joinpath(".vega.yaml").write_text(
            "version: 1\nbudget:\n  max_changed_files: 99\n",
            encoding="utf-8",
            newline="\n",
        )
        return result

    monkeypatch.setattr(
        "vega.loop_runtime.ReflectRuntime.run",
        reflect_then_mutate_policy,
    )
    reviewer = CountingReviewer()
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change") if mode == "auto" else None,
        reviewer_runner=reviewer,
    )
    if mode == "auto":
        run_dir = runtime.start(_brief(repo), "auto", max_iterations=1, verify=False)
    else:
        run_dir = runtime.start(_brief(repo), "assist", verify=False)
        repo.joinpath("README.md").write_text(
            "# Demo\nmanual worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        runtime.continue_assist(run_dir.name, repo, verify=False)

    state = _read_json(run_dir / "state.json")
    FinishRuntime(workspace).run(run_dir.name)
    finish_summary = _read_json(run_dir / "finish-summary.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "project_policy_changed"
    assert reviewer.calls == 0
    assert state["iterations"][0]["reflect_run"]
    assert (
        "project_policy_changed_since_loop_start"
        in finish_summary["artifact_integrity"]["issues"]
    )
    assert not (
        run_dir / "iterations" / "01" / "scope-gate-pre-review-result.json"
    ).exists()
    assert not (run_dir / "iterations" / "01" / "review-verdict.json").exists()


def test_verification_commit_cannot_hide_forbidden_path_from_post_scope_gate(
    tmp_path: Path,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "verification:",
            "  commands:",
            "    - python mutate_and_commit.py",
            "  max_commands: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - tests/**",
        ]
    ) + "\n"
    mutation_script = "\n".join(
        [
            "from pathlib import Path",
            "import subprocess",
            "target = Path('tests/locked.py')",
            "target.write_text(\"value = 'committed by verification'\\n\", encoding='utf-8')",
            "subprocess.run(['git', 'add', 'tests/locked.py'], check=True)",
            "subprocess.run([",
            "    'git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test',",
            "    'commit', '-m', 'verification commit'",
            "], check=True)",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={
            "tests/locked.py": "value = 'baseline'\n",
            "mutate_and_commit.py": mutation_script,
        },
    )
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("allowed worker diff"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    result = _read_json(
        run_dir / "iterations" / "01" / "scope-gate-post-verification-result.json"
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_post_verification_failed"
    assert reviewer.calls == 0
    assert result["failure_code"] == "scope_head_changed"
    assert result["expected_head_sha"] == state["initial_head_sha"]
    assert result["current_head_sha"] != state["initial_head_sha"]


def test_auto_scope_glob_allows_nested_markdown_file(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - docs/**/*.md",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={"docs/guides/guide.md": "# Guide\n"},
    )
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=PathWorker("docs/guides/guide.md", "worker change"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    result = _read_json(run_dir / "iterations" / "01" / "scope-gate-result.json")
    assert reviewer.calls == 1
    assert result["status"] == "success"
    assert result["changed_files"] == ["docs/guides/guide.md"]


def test_auto_scope_glob_uses_host_semantics_not_git_core_ignorecase(
    tmp_path: Path,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  forbidden_paths:",
            "    - docs/**",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={"Docs/leak.md": "# Leak\n"},
    )
    _git(repo, "config", "core.ignorecase", "true")
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=PathWorker("Docs/leak.md", "worker change"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    result = _read_json(run_dir / "iterations" / "01" / "scope-gate-result.json")
    if os.path.normcase("A") == os.path.normcase("a"):
        state = _read_json(run_dir / "state.json")
        assert state["status"] == "needs_human"
        assert state["current_step"] == "scope_gate_failed"
        assert reviewer.calls == 0
        assert result["status"] == "failed"
        assert result["violations"] == [
            {
                "code": "forbidden_path",
                "path": "Docs/leak.md",
                "matched_patterns": ["docs/**"],
            }
        ]
    else:
        assert result["status"] == "success"
        assert result["violations"] == []


@pytest.mark.parametrize(
    ("mode", "expected_staged", "expected_unstaged"),
    [
        ("staged", ["README.md"], []),
        ("unstaged", [], ["README.md"]),
        ("mm", ["README.md"], ["README.md"]),
    ],
)
def test_scope_gate_checks_staged_unstaged_and_mm_diff_streams(
    tmp_path: Path,
    mode: str,
    expected_staged: list[str],
    expected_unstaged: list[str],
) -> None:
    _, repo = _init_repo(tmp_path)
    readme = repo / "README.md"
    if mode in {"staged", "mm"}:
        readme.write_text("# Demo\nstaged\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "README.md")
    if mode == "unstaged":
        readme.write_text("# Demo\nunstaged\n", encoding="utf-8", newline="\n")
    elif mode == "mm":
        readme.write_text("# Demo\nworking tree\n", encoding="utf-8", newline="\n")

    result = evaluate_scope_gate(
        repo,
        ScopeConfig(forbidden_paths=["README.md"]),
        iteration=1,
        phase="pre_verification",
    )

    assert result.status == "failed"
    assert result.staged_changed_files == expected_staged
    assert result.unstaged_changed_files == expected_unstaged
    assert result.changed_files == ["README.md"]
    assert result.violations[0].code == "forbidden_path"


def test_scope_gate_applies_path_rules_to_untracked_files(tmp_path: Path) -> None:
    _, repo = _init_repo(tmp_path)
    target = repo / "tests" / "conftest.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")

    result = evaluate_scope_gate(
        repo,
        ScopeConfig(
            allowed_paths=["src/**"],
            forbidden_paths=["tests/**"],
        ),
        iteration=1,
        phase="pre_verification",
    )

    assert result.status == "failed"
    assert result.untracked_changed_files == ["tests/conftest.py"]
    assert result.changed_files == ["tests/conftest.py"]
    assert [violation.model_dump() for violation in result.violations] == [
        {
            "code": "forbidden_path",
            "path": "tests/conftest.py",
            "matched_patterns": ["tests/**"],
        }
    ]


def test_scope_gate_checks_both_paths_of_staged_rename(tmp_path: Path) -> None:
    _, repo = _init_repo(tmp_path, files={"tests/case.py": "value = 1\n"})
    source = repo / "tests" / "case.py"
    target = repo / "src" / "case.py"
    target.parent.mkdir()
    source.rename(target)
    _git(repo, "add", "-A")

    result = evaluate_scope_gate(
        repo,
        ScopeConfig(
            allowed_paths=["src/**/*.py"],
            forbidden_paths=["tests/**"],
        ),
        iteration=1,
        phase="pre_verification",
    )

    assert result.status == "failed"
    assert result.staged_changed_files == ["tests/case.py", "src/case.py"]
    assert result.changed_files == ["tests/case.py", "src/case.py"]
    assert [violation.model_dump() for violation in result.violations] == [
        {
            "code": "forbidden_path",
            "path": "tests/case.py",
            "matched_patterns": ["tests/**"],
        }
    ]


def test_scope_gate_fails_closed_without_leaking_credential_like_filename(
    tmp_path: Path,
) -> None:
    credential_like_path = "src/sk-abcdefghijkl.py"
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - src/**/*.py",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={credential_like_path: "value = 1\n"},
    )
    reviewer = CountingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=PathWorker(credential_like_path, "value = 2"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    result = _read_json(run_dir / "iterations" / "01" / "scope-gate-result.json")
    persisted_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in run_dir.rglob("*")
        if path.is_file()
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_failed"
    assert reviewer.calls == 0
    assert result["failure_code"] == "scope_path_identity_unsafe"
    assert result["changed_files"][0].startswith("<redacted-path-")
    assert len(result["changed_paths_sha256"]) == 64
    assert credential_like_path not in persisted_text


def test_scope_gate_rejects_workspace_change_during_git_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )
    original_run_git_bytes = workspace_check_module._run_git_bytes
    status_calls = 0

    def unstable_run_git_bytes(*args: object, **kwargs: object) -> bytes:
        nonlocal status_calls
        payload = original_run_git_bytes(*args, **kwargs)
        command = args[1]
        if isinstance(command, list) and command[:3] == [
            "git",
            "status",
            "--porcelain=v2",
        ]:
            status_calls += 1
            if status_calls == 2:
                return payload + b" M forged-during-snapshot.py\0"
        return payload

    monkeypatch.setattr(
        workspace_check_module,
        "_run_git_bytes",
        unstable_run_git_bytes,
    )

    result = evaluate_scope_gate(
        repo,
        ScopeConfig(allowed_paths=["README.md"]),
        iteration=1,
        phase="pre_verification",
    )

    assert result.status == "failed"
    assert result.failure_code == "scope_evaluation_failed"
    assert "tracked status" in (result.diagnostic or "")


def test_legacy_loop_without_scope_artifacts_remains_compatible(tmp_path: Path) -> None:
    iteration_dir = tmp_path / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    iteration = LoopIterationState(
        iteration=1,
        verification_status="passed",
    )

    legacy = validate_iteration_scope_gate_artifacts(
        iteration_dir,
        iteration,
        phase="pre_verification",
        required=False,
    )
    new_run = validate_iteration_scope_gate_artifacts(
        iteration_dir,
        iteration,
        phase="pre_verification",
        required=True,
    )

    assert legacy.valid is True
    assert legacy.evaluated is False
    assert new_run.valid is False
    assert new_run.issues == ("scope_gate_required_before_downstream_steps",)


def test_legacy_loop_without_scope_evidence_cannot_be_ready_to_commit(
    tmp_path: Path,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "verification:",
            "  commands:",
            "    - python -c \"print('legacy verification passed')\"",
            "  max_commands: 1",
            "",
        ]
    )
    workspace, repo = _init_repo(tmp_path, config=config)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=CountingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)
    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    state.pop("scope_gate_required", None)
    state.pop("scope_policy_sha256", None)
    for key in list(state["iterations"][0]):
        if key.startswith("scope_gate_"):
            state["iterations"][0].pop(key)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for path in run_dir.joinpath("iterations", "01").glob("scope-gate-*"):
        path.unlink()

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert "legacy_scope_gate_unverified" in summary["artifact_integrity"]["issues"]


def test_scope_gate_root_policy_and_trace_bindings_are_enforced(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "verification:",
            "  commands:",
            "    - python -c \"print('scope binding verification passed')\"",
            "  max_commands: 1",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=CountingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    state["scope_policy_sha256"] = "0" * 64
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    run_dir.joinpath("project-policy-snapshot.json").write_text(
        "{}\n",
        encoding="utf-8",
        newline="\n",
    )
    trace_path = run_dir / "trace.jsonl"
    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    original_events = json.loads(json.dumps(events))
    pre_index = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "scope_gate_finished"
        and event.get("phase") == "pre_verification"
    )
    post_index = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "scope_gate_finished"
        and event.get("phase") == "post_verification"
    )
    review_index = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "scope_gate_finished"
        and event.get("phase") == "pre_review"
    )
    events[pre_index]["changed_files"] = ["forged.py"]
    events[pre_index]["violation_codes"] = ["forged"]
    events[pre_index]["failure_code"] = "forged"
    events[post_index], events[review_index] = events[review_index], events[post_index]
    trace_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
        newline="\n",
    )

    results = run_loop_eval(run_dir, state["artifacts"], require_terminal=False)

    assert "FAIL: scope_gate_policy_hash_mismatch" in results
    assert "FAIL: project policy snapshot hash mismatch" in results
    assert "FAIL: project policy snapshot 与 state 不一致" in results
    assert "FAIL: scope_gate_trace_changed_files_mismatch" in results
    assert "FAIL: scope_gate_trace_violation_codes_mismatch" in results
    assert "FAIL: scope_gate_trace_failure_code_mismatch" in results
    assert "FAIL: pre_review_scope_gate_trace_phase_order_invalid" in results

    scope_events = [
        event
        for event in original_events
        if event.get("event") in {"scope_gate_finished", "scope_gate_failed"}
    ]
    moved_events = [
        event
        for event in original_events
        if event.get("event") not in {"scope_gate_finished", "scope_gate_failed"}
    ]
    terminal_index = next(
        index
        for index, event in enumerate(moved_events)
        if event.get("event") == "run_finished"
    )
    moved_events[terminal_index:terminal_index] = scope_events
    trace_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in moved_events),
        encoding="utf-8",
        newline="\n",
    )

    parent_order_results = run_loop_eval(
        run_dir,
        state["artifacts"],
        require_terminal=False,
    )
    FinishRuntime(workspace).run(run_dir.name)
    finish_summary = _read_json(run_dir / "finish-summary.json")

    assert "FAIL: pre_review_scope_gate_trace_parent_order_invalid" in parent_order_results
    assert (
        "project_policy_snapshot_hash_mismatch"
        in finish_summary["artifact_integrity"]["issues"]
    )
    assert (
        "scope_policy_changed_since_loop_start"
        in finish_summary["artifact_integrity"]["issues"]
    )


@pytest.mark.parametrize(
    ("artifact_name", "expected_eval", "expected_integrity_issue"),
    [
        (
            "scope-gate-result.json",
            "FAIL: scope_gate_result_hash_mismatch",
            "iteration_01_scope_gate_result_hash_mismatch",
        ),
        (
            "scope-gate-post-verification-result.json",
            "FAIL: post_verification_scope_gate_result_hash_mismatch",
            "iteration_01_post_verification_scope_gate_result_hash_mismatch",
        ),
        (
            "scope-gate-pre-review-result.json",
            "FAIL: pre_review_scope_gate_result_hash_mismatch",
            "iteration_01_pre_review_scope_gate_result_hash_mismatch",
        ),
    ],
)
def test_scope_gate_artifact_tampering_blocks_eval_and_finish(
    tmp_path: Path,
    artifact_name: str,
    expected_eval: str,
    expected_integrity_issue: str,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=CountingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)
    result_path = run_dir / "iterations" / "01" / artifact_name
    result_path.write_text("{}\n", encoding="utf-8", newline="\n")

    state = _read_json(run_dir / "state.json")
    eval_results = run_loop_eval(run_dir, state["artifacts"], require_terminal=False)
    FinishRuntime(workspace).run(run_dir.name)
    finish_summary = _read_json(run_dir / "finish-summary.json")

    assert expected_eval in eval_results
    assert finish_summary["finish_status"] == "needs_human"
    assert finish_summary["artifact_integrity"]["valid"] is False
    assert expected_integrity_issue in finish_summary["artifact_integrity"]["issues"]


def test_pre_review_scope_gate_recomputation_blocks_later_out_of_scope_mutation(
    tmp_path: Path,
) -> None:
    config = "\n".join(
        [
            "version: 1",
            "scope:",
            "  allowed_paths:",
            "    - README.md",
            "  forbidden_paths:",
            "    - tests/**",
        ]
    ) + "\n"
    workspace, repo = _init_repo(
        tmp_path,
        config=config,
        files={"tests/locked.py": "value = 'baseline'\n"},
    )
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=CountingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)
    repo.joinpath("tests/locked.py").write_text(
        "value = 'changed after review gate'\n",
        encoding="utf-8",
        newline="\n",
    )

    state = _read_json(run_dir / "state.json")
    eval_results = run_loop_eval(run_dir, state["artifacts"], require_terminal=False)
    FinishRuntime(workspace).run(run_dir.name)
    finish_summary = _read_json(run_dir / "finish-summary.json")

    assert "FAIL: pre_review_scope_gate_recomputed_result_mismatch" in eval_results
    assert finish_summary["finish_status"] == "needs_human"
    assert finish_summary["artifact_integrity"]["valid"] is False
    assert (
        "iteration_01_pre_review_scope_gate_recomputed_result_mismatch"
        in finish_summary["artifact_integrity"]["issues"]
    )


def test_auto_writes_bound_failure_report_when_risk_gate_evaluation_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(tmp_path)

    def raise_risk_evaluation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("forced risk gate failure")

    monkeypatch.setattr("vega.loop_runtime.evaluate_risk", raise_risk_evaluation)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker("worker change"),
        reviewer_runner=CountingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    report = (run_dir / "iterations" / "01" / "risk-gate-report.md").read_text(
        encoding="utf-8"
    )
    results = run_loop_eval(
        run_dir,
        state["artifacts"],
        require_terminal=False,
    )

    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_failed"
    assert iteration["risk_gate_status"] == "failed"
    assert iteration["risk_gate_result_sha256"]
    assert iteration["risk_gate_report_sha256"]
    assert "## 证据绑定" in report
    assert "PASS: risk gate artifact 与 iteration 一致" in results
    assert not [item for item in results if item.startswith("FAIL: risk_gate")]


def test_reflect_diff_check_covers_staged_changes(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\nstaged trailing whitespace   \n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "README.md")

    reflect_run = ReflectRuntime(workspace).run(repo, note="staged whitespace")

    state = _read_json(reflect_run / "state.json")
    assert state["status"] == "failed"
    assert "FAIL: git diff --check 存在问题" in state["eval_results"]
    assert "staged trailing whitespace" in (reflect_run / "full-diff.patch").read_text(
        encoding="utf-8"
    )


def test_gate_diff_check_covers_both_diff_streams_when_reflect_misses_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(tmp_path)
    original_run_reflect_eval = reflect_runtime_module._run_reflect_eval

    def ignore_reflect_diff_check(
        run_dir: Path,
        git_data: dict[str, str],
        expected_artifacts: list[str],
    ) -> list[str]:
        return [
            (
                "PASS: git diff --check 通过"
                if result == "FAIL: git diff --check 存在问题"
                else result
            )
            for result in original_run_reflect_eval(
                run_dir,
                git_data,
                expected_artifacts,
            )
        ]

    monkeypatch.setattr(
        reflect_runtime_module,
        "_run_reflect_eval",
        ignore_reflect_diff_check,
    )
    repo.joinpath("README.md").write_text(
        "# Demo\nstaged trailing whitespace   \n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "README.md")
    repo.joinpath("README.md").write_text(
        "# Demo\nunstaged trailing whitespace   \n",
        encoding="utf-8",
        newline="\n",
    )

    reflect_run = ReflectRuntime(workspace).run(repo, note="模拟上游漏检")
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    assert _read_json(reflect_run / "state.json")["status"] == "success"
    gate_result = _read_json(gate_run / "gate-result.json")
    reason = next(
        item
        for item in gate_result["reasons"]
        if item["code"] == "diff_check_failed"
    )
    assert gate_result["risk"] == "high"
    assert gate_result["recommendation"] == "human-review"
    assert reason["severity"] == "high"
    assert "# --- Vega staged diff: index vs HEAD ---" in reason["evidence"]
    assert (
        "# --- Vega unstaged diff: working tree vs index ---"
        in reason["evidence"]
    )
    assert "staged trailing whitespace" in reason["evidence"]
    assert "unstaged trailing whitespace" in reason["evidence"]


def test_gate_counts_staged_diff_lines_against_budget(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "budget:",
            "  max_changed_files: 5",
            "  max_diff_lines: 0",
            "  max_new_files: 0",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    repo.joinpath("README.md").write_text(
        "# Demo\nstaged implementation\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "README.md")

    reflect_run = ReflectRuntime(workspace).run(repo, note="staged budget")
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    reflect_state = _read_json(reflect_run / "state.json")
    gate_result = _read_json(gate_run / "gate-result.json")
    reason_codes = {item["code"] for item in gate_result["reasons"]}
    assert reflect_state["status"] == "success"
    assert gate_result["risk"] == "high"
    assert "budget_diff_lines" in reason_codes


def test_reflect_keeps_both_mm_diff_streams_for_reviewer(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\nstaged only\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "README.md")
    repo.joinpath("README.md").write_text(
        "# Demo\nworking tree only\n",
        encoding="utf-8",
        newline="\n",
    )

    reflect_run = ReflectRuntime(workspace).run(repo, note="MM 双事实流")

    state = _read_json(reflect_run / "state.json")
    patch = (reflect_run / "full-diff.patch").read_text(encoding="utf-8")
    evidence = _read_json(reflect_run / "review-evidence.json")
    assert state["status"] == "success"
    assert state["changed_files"] == ["README.md"]
    assert "# --- Vega staged diff: index vs HEAD ---" in patch
    assert "# --- Vega unstaged diff: working tree vs index ---" in patch
    assert "staged only" in patch
    assert "working tree only" in patch
    assert len(evidence["staged_diff_sha256"]) == 64
    assert len(evidence["unstaged_diff_sha256"]) == 64


def test_isolated_reviewer_receives_both_mm_diff_streams(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\nstaged only\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "README.md")
    repo.joinpath("README.md").write_text(
        "# Demo\nworking tree only\n",
        encoding="utf-8",
        newline="\n",
    )
    reflect_run = ReflectRuntime(workspace).run(repo, note="MM reviewer 输入")
    reviewer = CountingReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(repo, reflect_run.name)

    state = _read_json(review_run / "state.json")
    review_pack = (review_run / "review-pack.md").read_text(encoding="utf-8")
    assert state["status"] == "success"
    assert reviewer.calls == 1
    assert "# --- Vega staged diff: index vs HEAD ---" in review_pack
    assert "# --- Vega unstaged diff: working tree vs index ---" in review_pack
    assert "staged only" in review_pack
    assert "working tree only" in review_pack


def test_gate_counts_both_mm_diff_streams_against_budget(tmp_path: Path) -> None:
    config = "\n".join(
        [
            "version: 1",
            "budget:",
            "  max_changed_files: 5",
            "  max_diff_lines: 2",
            "  max_new_files: 0",
        ]
    ) + "\n"
    workspace, repo = _init_repo(tmp_path, config=config)
    repo.joinpath("README.md").write_text(
        "# Demo\nstaged only\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "README.md")
    repo.joinpath("README.md").write_text(
        "# Demo\nworking tree only\n",
        encoding="utf-8",
        newline="\n",
    )

    reflect_run = ReflectRuntime(workspace).run(repo, note="MM 预算")
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    gate_result = _read_json(gate_run / "gate-result.json")
    reason_codes = {item["code"] for item in gate_result["reasons"]}
    assert _git(repo, "diff", "HEAD", "--numstat").strip() == "1\t0\tREADME.md"
    assert gate_result["risk"] == "high"
    assert "budget_diff_lines" in reason_codes


def _init_repo(
    tmp_path: Path,
    config: str | None = None,
    files: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- Run tests.\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8", newline="\n")
    for relative_path, content in (files or {}).items():
        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    if config is not None:
        repo.joinpath(".vega.yaml").write_text(config, encoding="utf-8", newline="\n")
    _git(repo, "add", ".")
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return workspace, repo


def _brief(repo: Path) -> BriefInput:
    return BriefInput(
        mode="bug",
        text="修复 README 演示问题",
        source="p0-regression",
        repo_path=str(repo),
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _git_commit(repo: Path, message: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
