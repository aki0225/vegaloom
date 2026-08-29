from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.finish_runtime import FinishRuntime
from vega.loop_evidence import validate_loop_artifact_integrity
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput, LoopIterationState
from vega.risk_gate_evidence import validate_iteration_risk_gate_artifacts
from vega.runner import RunnerResult


class PathWorker:
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
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
            target.read_text(encoding="utf-8") + "VALUE = 2\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="worker done",
            command=["path-worker"],
        )


class RequiredReviewReviewer:
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
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
        del prompt, repo_path, sandbox, timeout_seconds, execution_context
        self.calls += 1
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "当前证据下未发现明显问题，仍需人工确认。",
                    "findings": [],
                    "risk_disclosures": [
                        {
                            "risk_id": "payment",
                            "assessment": "no_obvious_issue",
                            "locations": [
                                {
                                    "file": self.relative_path,
                                    "line": 1,
                                }
                            ],
                            "change_summary": "修改了支付处理逻辑。",
                            "evidence": "已检查完整 diff、项目规则和验证摘要。",
                            "residual_risk": "人工确认重复请求不会造成重复扣款。",
                        }
                    ],
                    "reviewed_files": [self.relative_path],
                    "checked_items": ["需求覆盖", "支付风险", "验证证据"],
                },
                ensure_ascii=False,
            ),
            command=["required-review-reviewer"],
        )


class CountingReviewer:
    def __init__(self) -> None:
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
        del prompt, repo_path, sandbox, timeout_seconds, execution_context
        self.calls += 1
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "未发现明显问题。",
                    "findings": [],
                    "checked_items": ["需求覆盖"],
                },
                ensure_ascii=False,
            ),
            command=["counting-reviewer"],
        )


def test_auto_named_required_review_runs_reviewer_once_and_stays_human(
    tmp_path: Path,
) -> None:
    relative_path = "src/payments/charge.py"
    workspace, repo = _init_repo(
        tmp_path,
        config=_named_required_review_config(),
        files={relative_path: "VALUE = 1\n"},
    )
    worker = PathWorker(relative_path)
    reviewer = RequiredReviewReviewer(relative_path)

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(
        _brief(repo),
        "auto",
        max_iterations=2,
        verify=True,
    )

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    iteration_dir = run_dir / "iterations" / "01"
    gate_result = _read_json(iteration_dir / "risk-gate-result.json")
    local_verdict = _read_json(iteration_dir / "review-verdict.json")
    child_review_state = _read_json(
        workspace / "runs" / iteration["review_run"] / "state.json"
    )
    child_verdict = _read_json(
        workspace / "runs" / iteration["review_run"] / "review-verdict.json"
    )

    assert worker.calls == 1
    assert reviewer.calls == 1
    assert len(state["iterations"]) == 1
    assert state["status"] == "needs_human"
    assert iteration["verdict"] == "needs_human"
    assert local_verdict["verdict"] == "needs_human"
    assert child_review_state["status"] == "needs_human"
    assert child_review_state["verdict"] == "needs_human"
    assert child_verdict["verdict"] == "needs_human"
    assert local_verdict["risk_disclosures"][0]["risk_id"] == "payment"
    assert gate_result["required_reviews"][0]["id"] == "payment"
    assert {
        reason["code"]
        for reason in gate_result["reasons"]
    }.issuperset({"required_risk_review"})
    assert "high_risk_paths" not in {
        reason["code"]
        for reason in gate_result["reasons"]
    }

    eval_results = run_loop_eval(
        run_dir,
        state["artifacts"],
        require_terminal=False,
    )
    assert not [item for item in eval_results if item.startswith("FAIL:")]

    integrity = validate_loop_artifact_integrity(
        workspace,
        repo,
        run_dir,
    )
    assert integrity.valid, integrity.issues
    assert len(integrity.review_verdicts) == 1
    assert integrity.review_verdicts[0].verdict == "needs_human"

    FinishRuntime(workspace).run(run_dir.name)
    finish = _read_json(run_dir / "finish-summary.json")
    assert finish["finish_status"] == "needs_human"
    assert finish["loop_status"] == "needs_human"
    assert finish["latest_verdict"]["verdict"] == "needs_human"
    assert finish["artifact_integrity"]["valid"] is True
    assert finish["evidence_freshness"]["fresh"] is True
    assert finish["verification_passed"] is True
    assert finish["first_screen"]["verification"]["trusted_passed"] is True
    assert not any(
        "自动验证结论未知" in note
        for note in finish["handoff_notes"]
    )
    assert (
        "人工逐项检查高风险命中、Reviewer 关键位置和剩余风险。"
        in finish["first_screen"]["next_steps"]
    )
    assert (
        "补充并运行至少一条受信的项目验证命令。"
        not in finish["first_screen"]["next_steps"]
    )
    assert not any(
        "工作区发生变化" in note or "快照缺失" in note
        for note in finish["handoff_notes"]
    )


def test_assist_named_required_review_runs_reviewer_and_stays_human(
    tmp_path: Path,
) -> None:
    relative_path = "src/payments/charge.py"
    workspace, repo = _init_repo(
        tmp_path,
        config=_named_required_review_config(),
        files={relative_path: "VALUE = 1\n"},
    )
    reviewer = RequiredReviewReviewer(relative_path)
    runtime = LoopAutomationRuntime(
        workspace,
        reviewer_runner=reviewer,
    )
    run_dir = runtime.start(
        _brief(repo),
        "assist",
        max_iterations=2,
        verify=False,
    )
    target = repo / relative_path
    target.write_text(
        target.read_text(encoding="utf-8") + "VALUE = 2\n",
        encoding="utf-8",
        newline="\n",
    )

    runtime.continue_assist(
        run_dir.name,
        repo,
        verify=False,
    )

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    gate_result = _read_json(
        run_dir / "iterations" / "01" / "risk-gate-result.json"
    )
    verdict = _read_json(
        run_dir / "iterations" / "01" / "review-verdict.json"
    )

    assert reviewer.calls == 1
    assert len(state["iterations"]) == 1
    assert state["status"] == "needs_human"
    assert iteration["worker_status"] == "skipped"
    assert iteration["verdict"] == "needs_human"
    assert verdict["verdict"] == "needs_human"
    assert gate_result["required_reviews"][0]["matched_files"] == [relative_path]
    assert {
        reason["code"]
        for reason in gate_result["reasons"]
    }.issuperset({"required_risk_review"})
    assert "high_risk_paths" not in {
        reason["code"]
        for reason in gate_result["reasons"]
    }


@pytest.mark.parametrize("mode", ["auto", "assist"])
def test_budget_only_human_review_still_skips_reviewer(
    tmp_path: Path,
    mode: str,
) -> None:
    workspace, repo = _init_repo(
        tmp_path,
        config="\n".join(
            [
                "version: 1",
                "budget:",
                "  max_diff_lines: 0",
            ]
        )
        + "\n",
        files={"README.md": "# Demo\n"},
    )
    reviewer = CountingReviewer()
    worker = PathWorker("README.md")
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )
    run_dir = runtime.start(
        _brief(repo),
        mode,
        max_iterations=2,
        verify=False,
    )
    if mode == "assist":
        readme = repo / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "manual change\n",
            encoding="utf-8",
            newline="\n",
        )
        runtime.continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    iteration_dir = run_dir / "iterations" / "01"
    gate_result = _read_json(iteration_dir / "risk-gate-result.json")

    assert reviewer.calls == 0
    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_needs_human"
    assert iteration["verdict"] is None
    assert gate_result["required_reviews"] == []
    assert any(
        reason["code"] == "budget_diff_lines"
        for reason in gate_result["reasons"]
    )
    assert not (iteration_dir / "review-verdict.json").exists()
    if mode == "auto":
        assert worker.calls == 1
    else:
        assert worker.calls == 0


@pytest.mark.parametrize("mode", ["auto", "assist"])
def test_named_required_review_does_not_bypass_budget_human_review(
    tmp_path: Path,
    mode: str,
) -> None:
    relative_path = "src/payments/charge.py"
    workspace, repo = _init_repo(
        tmp_path,
        config=(
            _named_required_review_config()
            + "budget:\n"
            + "  max_diff_lines: 0\n"
        ),
        files={relative_path: "VALUE = 1\n"},
    )
    reviewer = RequiredReviewReviewer(relative_path)
    worker = PathWorker(relative_path)
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )
    run_dir = runtime.start(
        _brief(repo),
        mode,
        max_iterations=2,
        verify=False,
    )
    if mode == "assist":
        target = repo / relative_path
        target.write_text(
            target.read_text(encoding="utf-8") + "VALUE = 2\n",
            encoding="utf-8",
            newline="\n",
        )
        runtime.continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    iteration_dir = run_dir / "iterations" / "01"
    gate_result = _read_json(iteration_dir / "risk-gate-result.json")
    reason_codes = {
        reason["code"]
        for reason in gate_result["reasons"]
    }

    assert reviewer.calls == 0
    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_needs_human"
    assert iteration["verdict"] is None
    assert gate_result["required_reviews"][0]["id"] == "payment"
    assert reason_codes.issuperset(
        {"required_risk_review", "budget_diff_lines"}
    )
    assert not (iteration_dir / "review-verdict.json").exists()
    forged_iteration = LoopIterationState.model_validate(iteration).model_copy(
        update={
            "reviewer_status": "success",
            "review_run": "forged-review",
            "verdict": "needs_human",
        }
    )
    forged_integrity = validate_iteration_risk_gate_artifacts(
        iteration_dir,
        forged_iteration,
        workspace=workspace,
        repo_path=repo,
        trace_path=run_dir / "trace.jsonl",
    )
    assert "risk_gate_human_review_bypassed" in forged_integrity.issues


def _named_required_review_config() -> str:
    return (
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                '    - python -c "print(\'verification passed\')"',
                "  max_commands: 1",
                "risk:",
                "  require_human_review:",
                "    - charge.py",
                "  required_reviews:",
                "    - id: payment",
                "      label: Payment",
                "      paths:",
                "        - src/payments/**",
            ]
        )
        + "\n"
    )


def _brief(repo: Path) -> BriefInput:
    return BriefInput(
        mode="feature",
        text="调整支付处理逻辑，并保留人工高风险审查。",
        source="test",
        repo_path=str(repo),
    )


def _init_repo(
    tmp_path: Path,
    *,
    config: str,
    files: dict[str, str],
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- Run minimal verification.\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath(".vega.yaml").write_text(
        config,
        encoding="utf-8",
        newline="\n",
    )
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    subprocess.run(
        ["git", "add", "--", "AGENTS.md", ".vega.yaml", *files],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
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
    workspace.mkdir()
    return workspace, repo


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
