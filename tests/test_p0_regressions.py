from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.finish_runtime import FinishRuntime
from vega.gate_runtime import GateRuntime
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewRuntime
from vega.runner import RunnerResult


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


def _init_repo(tmp_path: Path, config: str | None = None) -> tuple[Path, Path]:
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


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
