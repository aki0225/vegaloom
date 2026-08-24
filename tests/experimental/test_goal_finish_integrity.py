from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.experimental.goal_runtime import GoalRuntime
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput
from vega.risk_gate_evidence import render_risk_gate_report_binding, sha256_text
from vega.runner import RunnerResult


class StaticRunner:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs

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
        return RunnerResult(
            status="success",
            output=self.outputs.pop(0),
            command=["test-runner"],
        )


class TrackedChangeRunner(StaticRunner):
    """为 auto loop 构造被 Reflect 和 Finish 绑定的 tracked diff。"""

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        repo_path.joinpath("README.md").write_text(
            "# Demo\nimplemented\n",
            encoding="utf-8",
            newline="\n",
        )
        return result


def test_human_review_risk_gate_cannot_share_success_chain_with_approved_reviewer(
    tmp_path: Path,
) -> None:
    workspace, repo, run_dir = _create_successful_loop(tmp_path, verify=False)
    _rewrite_risk_gate_report_binding(run_dir, recommendation="human-review")
    state = _read_json(run_dir / "state.json")

    results = run_loop_eval(run_dir, state["artifacts"])

    assert "FAIL: risk_gate_human_review_bypassed" in results

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert summary["artifact_integrity"]["valid"] is False
    assert (
        "iteration_01_risk_gate_human_review_bypassed"
        in summary["artifact_integrity"]["issues"]
    )

    goal = GoalRuntime(workspace)
    goal_run = goal.start(
        repo,
        "# Goal\n\nObjective: 验证风险门禁不能绕过人工审查\n",
        "test",
        None,
    )
    goal.step(goal_run.name)
    goal.attach(goal_run.name, "01", run_dir.name, "loop", "loop 证据")
    goal.attach(goal_run.name, "01", run_dir.name, "finish", "finish 证据")

    goal_state = _read_json(goal_run / "goal-state.json")
    refs = goal_state["checkpoint_records"][0]["refs"]
    assert all(item["validated"] is True for item in refs)
    assert all(item["completion_eligible"] is False for item in refs)
    with pytest.raises(ValueError, match="缺少可完成证据"):
        goal.checkpoint_done(goal_run.name, "01", note="不应完成")


def _create_successful_loop(
    tmp_path: Path,
    *,
    verify: bool,
) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
    )
    run_dir = runtime.start(
        BriefInput(
            mode="feature",
            text="验证 Finish artifact 完整性",
            source="test",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=verify,
    )
    return workspace, repo, run_dir


def _review_json(verdict: str) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "summary": "review complete",
            "findings": [],
            "reviewed_files": ["README.md"],
            "checked_items": ["scope", "tests"],
        }
    )


def _init_clean_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    repo.joinpath("tests").mkdir()
    repo.joinpath("AGENTS.md").write_text("# Rules\n\n- Run tests.\n", encoding="utf-8")
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8")
    repo.joinpath("pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["setuptools"]',
                'build-backend = "setuptools.build_meta"',
                "",
                "[tool.pytest.ini_options]",
                'testpaths = ["tests"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    repo.joinpath("tests", "test_ok.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
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
    subprocess.run(
        ["git", "add", "--", "AGENTS.md", "README.md", "pyproject.toml", "tests/test_ok.py"],
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


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _rewrite_risk_gate_report_binding(
    run_dir: Path,
    *,
    source_run: str | None = None,
    iteration: int | None = None,
    recommendation: str | None = None,
) -> None:
    """构造哈希自洽、但业务语义被伪造的风险门禁证据。"""
    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    iteration_state = state["iterations"][0]
    result_path = run_dir / "iterations" / "01" / "risk-gate-result.json"
    report_path = run_dir / "iterations" / "01" / "risk-gate-report.md"
    result = _read_json(result_path)
    if recommendation is not None:
        result["recommendation"] = recommendation
        iteration_state["risk_gate_recommendation"] = recommendation
        if recommendation == "human-review":
            result["risk"] = "high"
            iteration_state["risk_gate_risk"] = "high"
            for reason in result["reasons"]:
                reason["severity"] = "high"
        _write_json(result_path, result)
    result_text = result_path.read_text(encoding="utf-8")

    report_text = report_path.read_text(encoding="utf-8")
    report_body, marker, _ = report_text.partition("## 证据绑定")
    assert marker == "## 证据绑定"
    bound_source_run = (
        source_run if source_run is not None else iteration_state["reflect_run"]
    )
    bound_iteration = iteration if iteration is not None else iteration_state["iteration"]
    rewritten_report = (
        report_body.rstrip()
        + "\n\n"
        + render_risk_gate_report_binding(
            status=iteration_state["risk_gate_status"],
            iteration=bound_iteration,
            source_run=bound_source_run,
            result_sha256=sha256_text(result_text),
            risk=result["risk"],
            recommendation=result["recommendation"],
        )
    )
    report_path.write_text(rewritten_report, encoding="utf-8")
    iteration_state["risk_gate_result_sha256"] = sha256_text(result_text)
    iteration_state["risk_gate_report_sha256"] = sha256_text(rewritten_report)
    _write_json(state_path, state)
