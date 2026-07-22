from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import vega.finish_runtime as finish_runtime
import vega.goal_evidence as goal_evidence
from vega.finish_runtime import FinishRuntime
from vega.gate_runtime import render_gate_report
from vega.goal_runtime import GoalRuntime
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput, GateResult
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


def test_finish_reuses_single_terminal_artifact_integrity_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    workspace, repo, run_dir = _create_successful_loop(tmp_path, verify=True)
    original = goal_evidence.validate_loop_artifact_integrity
    integrity_calls = 0

    def counted_integrity(*args, **kwargs):
        nonlocal integrity_calls
        integrity_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        goal_evidence,
        "validate_loop_artifact_integrity",
        counted_integrity,
    )
    monkeypatch.setattr(
        finish_runtime,
        "validate_loop_artifact_integrity",
        counted_integrity,
        raising=False,
    )

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert integrity_calls == 1
    assert summary["finish_status"] == "ready_to_commit"
    assert summary["artifact_integrity"]["valid"] is True
    assert summary["evidence_freshness"]["fresh"] is True
    assert summary["verification_passed"] is True
    assert summary["artifact_integrity"]["risk_gate_result_count"] == 1

    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    state["iterations"][-1]["review_run"] = ""
    _write_json(state_path, state)
    integrity_calls = 0

    freshness = goal_evidence.validate_loop_evidence_freshness(
        workspace,
        repo,
        run_dir,
    )

    assert integrity_calls == 0
    assert freshness.fresh is False
    assert "trusted_review_missing" in freshness.issues

    integrity_calls = 0
    no_review_snapshot = goal_evidence.validate_loop_evidence_snapshot(
        workspace,
        repo,
        run_dir,
    )

    assert integrity_calls == 1
    assert no_review_snapshot.evidence_freshness.fresh is False
    assert "trusted_review_missing" in no_review_snapshot.evidence_freshness.issues

    state["iterations"][-1]["review_run"] = "missing-review-run"
    _write_json(state_path, state)
    integrity_calls = 0

    missing_review_freshness = goal_evidence.validate_loop_evidence_freshness(
        workspace,
        repo,
        run_dir,
    )

    assert integrity_calls == 0
    assert missing_review_freshness.fresh is False
    assert "trusted_review_missing" in missing_review_freshness.issues
    assert missing_review_freshness.review_run == "missing-review-run"

    integrity_calls = 0
    missing_review_snapshot = goal_evidence.validate_loop_evidence_snapshot(
        workspace,
        repo,
        run_dir,
    )

    assert integrity_calls == 1
    assert missing_review_snapshot.evidence_freshness.fresh is False
    assert (
        "trusted_review_missing"
        in missing_review_snapshot.evidence_freshness.issues
    )
    assert (
        missing_review_snapshot.evidence_freshness.review_run
        == "missing-review-run"
    )


def test_finish_rejects_unbound_iteration_verdict(tmp_path: Path) -> None:
    workspace, repo, run_dir = _create_successful_loop(tmp_path, verify=False)
    forged_dir = run_dir / "iterations" / "99"
    forged_dir.mkdir(parents=True)
    forged_dir.joinpath("review-verdict.json").write_text(
        _review_json("request_changes"),
        encoding="utf-8",
    )

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert summary["latest_verdict"]["verdict"] == "approve"
    assert summary["artifact_integrity"]["valid"] is False
    assert any(
        issue.startswith("unbound_review_verdict:")
        for issue in summary["artifact_integrity"]["issues"]
    )
    assert Path(summary["repo_path"]) == repo.resolve()


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        ("missing", "iteration_01_verification_result_missing"),
        ("invalid_json", "iteration_01_verification_result_invalid_json"),
        ("schema", "iteration_01_verification_commands_schema_invalid"),
        ("state_mismatch", "iteration_01_verification_iteration_status_mismatch"),
    ],
)
def test_finish_fails_closed_for_invalid_verification_artifact(
    tmp_path: Path,
    mutation: str,
    expected_issue: str,
) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=True)
    result_path = run_dir / "iterations" / "01" / "verification-result.json"
    if mutation == "missing":
        result_path.unlink()
    elif mutation == "invalid_json":
        result_path.write_text('{"commands":', encoding="utf-8")
    else:
        payload = _read_json(result_path)
        if mutation == "schema":
            payload["commands"] = "python -m pytest -q"
        else:
            payload["failed_count"] = 1
            payload["results"][0]["status"] = "failed"
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert summary["artifact_integrity"]["valid"] is False
    assert expected_issue in summary["artifact_integrity"]["issues"]
    assert summary["verification_results"] == []


def test_finish_rejects_missing_child_review_artifact(tmp_path: Path) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    state = _read_json(run_dir / "state.json")
    review_run = state["iterations"][0]["review_run"]
    workspace.joinpath("runs", review_run, "review-verdict.json").unlink()

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert summary["latest_verdict"] is None
    assert "iteration_01_child_review_verdict_missing" in summary["artifact_integrity"]["issues"]


def test_finish_rejects_corrupt_local_review_verdict(tmp_path: Path) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    run_dir.joinpath("iterations", "01", "review-verdict.json").write_text(
        '{"verdict":',
        encoding="utf-8",
    )

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert summary["latest_verdict"] is None
    assert (
        "iteration_01_local_review_verdict_invalid_json"
        in summary["artifact_integrity"]["issues"]
    )


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        ("missing_result", "iteration_01_risk_gate_result_missing"),
        ("missing_report", "iteration_01_risk_gate_report_missing"),
        ("tampered_result", "iteration_01_risk_gate_result_hash_mismatch"),
    ],
)
def test_finish_fails_closed_for_missing_or_tampered_loop_risk_gate_artifact(
    tmp_path: Path,
    mutation: str,
    expected_issue: str,
) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    iteration_dir = run_dir / "iterations" / "01"
    result_path = iteration_dir / "risk-gate-result.json"
    report_path = iteration_dir / "risk-gate-report.md"
    if mutation == "missing_result":
        result_path.unlink()
    elif mutation == "missing_report":
        report_path.unlink()
    else:
        payload = _read_json(result_path)
        payload["reasons"][0]["message"] = "篡改后的风险原因"
        _write_json(result_path, payload)

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert summary["artifact_integrity"]["valid"] is False
    assert expected_issue in summary["artifact_integrity"]["issues"]
    assert summary["artifact_integrity"]["risk_gate_result_count"] == 0


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


@pytest.mark.parametrize(
    ("binding_source_run", "binding_iteration", "expected_issue"),
    [
        ("forged-reflect-run", None, "risk_gate_report_source_mismatch"),
        (None, 99, "risk_gate_report_iteration_mismatch"),
    ],
)
def test_risk_gate_report_binding_must_match_source_and_iteration(
    tmp_path: Path,
    binding_source_run: str | None,
    binding_iteration: int | None,
    expected_issue: str,
) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    _rewrite_risk_gate_report_binding(
        run_dir,
        source_run=binding_source_run,
        iteration=binding_iteration,
    )
    state = _read_json(run_dir / "state.json")

    results = run_loop_eval(run_dir, state["artifacts"])

    assert f"FAIL: {expected_issue}" in results

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert summary["artifact_integrity"]["valid"] is False
    assert f"iteration_01_{expected_issue}" in summary["artifact_integrity"]["issues"]


def test_risk_gate_recomputation_rejects_fully_synchronized_semantic_downgrade(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    _forge_risk_gate_semantic_downgrade(run_dir)
    state = _read_json(run_dir / "state.json")

    results = run_loop_eval(run_dir, state["artifacts"])

    assert "FAIL: risk_gate_recomputed_result_mismatch" in results
    assert not [item for item in results if item.startswith("FAIL: risk_gate_trace")]
    assert "FAIL: risk_gate_result_risk_inconsistent" not in results
    assert "FAIL: risk_gate_result_recommendation_inconsistent" not in results

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert (
        "iteration_01_risk_gate_recomputed_result_mismatch"
        in summary["artifact_integrity"]["issues"]
    )


def test_loop_iteration_sequence_cannot_be_renumbered(tmp_path: Path) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    _renumber_iteration_evidence(run_dir, new_iteration=99)
    state = _read_json(run_dir / "state.json")

    results = run_loop_eval(run_dir, state["artifacts"])

    assert "FAIL: iteration 编号不连续：期望 01，实际 99" in results
    assert not [item for item in results if item.startswith("FAIL: risk_gate")]

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert "iteration_99_sequence_mismatch" in summary["artifact_integrity"]["issues"]


def test_finish_persists_diagnostic_for_corrupt_loop_state(tmp_path: Path) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    run_dir.joinpath("state.json").write_text('{"status":', encoding="utf-8")

    with pytest.raises(ValueError, match="state.json 已损坏"):
        FinishRuntime(workspace).run(run_dir.name)

    diagnostic = _read_json(run_dir / "finish-diagnostic.json")
    assert diagnostic["status"] == "failed"
    assert "state.json 已损坏" in diagnostic["message"]
    assert "不是 loop automation run" not in diagnostic["message"]


def test_finish_rejects_loop_state_from_another_run_directory(tmp_path: Path) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    state["run_id"] = "another-loop-run"
    _write_json(state_path, state)

    with pytest.raises(ValueError, match="state.run_id"):
        FinishRuntime(workspace).run(run_dir.name)

    diagnostic = _read_json(run_dir / "finish-diagnostic.json")
    assert diagnostic["status"] == "failed"
    assert "疑似移植了其他证据链" in diagnostic["message"]


def test_finish_rejects_review_state_from_another_run_directory(tmp_path: Path) -> None:
    workspace, _, run_dir = _create_successful_loop(tmp_path, verify=False)
    loop_state = _read_json(run_dir / "state.json")
    review_run = loop_state["iterations"][0]["review_run"]
    review_state_path = workspace / "runs" / review_run / "state.json"
    review_state = _read_json(review_state_path)
    review_state["run_id"] = "another-review-run"
    _write_json(review_state_path, review_state)

    FinishRuntime(workspace).run(run_dir.name)

    summary = _read_json(run_dir / "finish-summary.json")
    assert summary["finish_status"] == "needs_human"
    assert "review_run_id_mismatch" in summary["evidence_freshness"]["issues"]
    assert (
        "iteration_01_child_review_run_id_mismatch"
        in summary["artifact_integrity"]["issues"]
    )


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


def _forge_risk_gate_semantic_downgrade(run_dir: Path) -> None:
    """同步改写可变证据，验证校验器会回到 Reflect 重算真实风险。"""
    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    iteration_state = state["iterations"][0]
    iteration_dir = run_dir / "iterations" / "01"
    result_path = iteration_dir / "risk-gate-result.json"
    report_path = iteration_dir / "risk-gate-report.md"
    result = _read_json(result_path)
    result["risk"] = "low"
    result["recommendation"] = "self-check"
    for reason in result["reasons"]:
        reason["severity"] = "low"
    _write_json(result_path, result)
    result_text = result_path.read_text(encoding="utf-8")
    result_model = GateResult.model_validate(result)
    report_text = (
        render_gate_report(result_model).rstrip()
        + "\n\n## 本轮关联\n\n"
        + f"- source reflect：`{iteration_state['reflect_run']}`\n"
        + f"- iteration：`{iteration_state['iteration']:02d}`\n"
        + "- 此结果由 loop 在启动隔离 reviewer 前生成。\n\n"
        + render_risk_gate_report_binding(
            status="success",
            iteration=iteration_state["iteration"],
            source_run=iteration_state["reflect_run"],
            result_sha256=sha256_text(result_text),
            risk=result_model.risk,
            recommendation=result_model.recommendation,
        )
    )
    report_path.write_text(report_text, encoding="utf-8")
    iteration_state["risk_gate_risk"] = result_model.risk
    iteration_state["risk_gate_recommendation"] = result_model.recommendation
    iteration_state["risk_gate_result_sha256"] = sha256_text(result_text)
    iteration_state["risk_gate_report_sha256"] = sha256_text(report_text)
    _write_json(state_path, state)

    trace_path = run_dir / "trace.jsonl"
    trace_items = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gate_event = next(
        item
        for item in trace_items
        if item.get("event") == "risk_gate_finished" and item.get("iteration") == 1
    )
    gate_event["risk"] = result_model.risk
    gate_event["recommendation"] = result_model.recommendation
    gate_event["result_sha256"] = iteration_state["risk_gate_result_sha256"]
    gate_event["report_sha256"] = iteration_state["risk_gate_report_sha256"]
    trace_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in trace_items) + "\n",
        encoding="utf-8",
    )


def _renumber_iteration_evidence(run_dir: Path, *, new_iteration: int) -> None:
    """同步修改 iteration 目录、state、风险证据与 trace，保留唯一的序列违规。"""
    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    iteration_state = state["iterations"][0]
    old_dir = run_dir / "iterations" / "01"
    new_dir = run_dir / "iterations" / f"{new_iteration:02d}"
    old_dir.rename(new_dir)
    iteration_state["iteration"] = new_iteration
    state["current_iteration"] = new_iteration

    result_path = new_dir / "risk-gate-result.json"
    report_path = new_dir / "risk-gate-report.md"
    result = _read_json(result_path)
    result["iteration"] = new_iteration
    _write_json(result_path, result)
    result_text = result_path.read_text(encoding="utf-8")
    report_body, marker, _ = report_path.read_text(encoding="utf-8").partition("## 证据绑定")
    assert marker == "## 证据绑定"
    report_text = (
        report_body.rstrip()
        + "\n\n"
        + render_risk_gate_report_binding(
            status=iteration_state["risk_gate_status"],
            iteration=new_iteration,
            source_run=iteration_state["reflect_run"],
            result_sha256=sha256_text(result_text),
            risk=iteration_state["risk_gate_risk"],
            recommendation=iteration_state["risk_gate_recommendation"],
        )
    )
    report_path.write_text(report_text, encoding="utf-8")
    iteration_state["risk_gate_result_sha256"] = sha256_text(result_text)
    iteration_state["risk_gate_report_sha256"] = sha256_text(report_text)
    _write_json(state_path, state)

    trace_path = run_dir / "trace.jsonl"
    trace_items = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gate_event = next(
        item
        for item in trace_items
        if item.get("event") == "risk_gate_finished" and item.get("iteration") == 1
    )
    gate_event["iteration"] = new_iteration
    gate_event["result_sha256"] = iteration_state["risk_gate_result_sha256"]
    gate_event["report_sha256"] = iteration_state["risk_gate_report_sha256"]
    trace_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in trace_items) + "\n",
        encoding="utf-8",
    )
