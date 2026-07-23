from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import vega.loop_runtime as loop_runtime_module

from vega.finish_runtime import FinishRuntime
from vega.experimental.goal_evidence import validate_goal_evidence
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput
from vega.runner import RunnerResult


class TrackedChangeWorker:
    """构造最小 tracked diff，避免测试被无 diff 门禁提前终止。"""

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
        readme = repo_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "implemented\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="worker complete",
            command=["assurance-test-worker"],
        )


class ApprovingReviewer:
    """固定返回 approve，用来验证模型意见不能覆盖缺失的确定性证据。"""

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
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "assurance regression approve",
                    "findings": [],
                    "checked_items": ["scope", "diff", "tests"],
                },
                ensure_ascii=False,
            ),
            command=["assurance-test-reviewer"],
        )


class SequencedReviewer:
    """按顺序返回结论，用来验证后续轮次不能继承前序验证证据。"""

    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = verdicts

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
        verdict = self.verdicts.pop(0)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": verdict,
                    "summary": f"assurance regression {verdict}",
                    "findings": (
                        []
                        if verdict == "approve"
                        else [
                            {
                                "severity": "major",
                                "title": "继续验证",
                                "detail": "需要下一轮确认。",
                            }
                        ]
                    ),
                    "checked_items": ["scope", "diff", "tests"],
                },
                ensure_ascii=False,
            ),
            command=["assurance-sequenced-reviewer"],
        )


def test_zero_selected_verification_commands_cannot_auto_succeed(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path)

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=ApprovingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    verification = _read_json(run_dir / "iterations" / "01" / "verification-result.json")
    finish = _finish(workspace, run_dir)

    assert verification["selected_command_count"] == 0
    assert verification["command_count"] == 0
    assert state["iterations"][0]["verification_status"] == "skipped"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "verification_unverified"
    assert finish["finish_status"] != "ready_to_commit"
    assert finish["verification_passed"] is False


def test_no_verify_cannot_auto_succeed_when_project_has_detectable_tests(
    tmp_path: Path,
) -> None:
    workspace, repo = _init_repo(tmp_path, with_python_tests=True)

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=ApprovingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    iteration_dir = run_dir / "iterations" / "01"
    finish = _finish(workspace, run_dir)

    assert "python -m pytest -q" in (run_dir / "project-context.md").read_text(
        encoding="utf-8"
    )
    assert not (iteration_dir / "verification-result.json").exists()
    assert state["iterations"][0]["verification_status"] == "skipped"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "verification_unverified"
    assert finish["finish_status"] != "ready_to_commit"
    assert finish["verification_passed"] is False


def test_unstructured_external_test_log_cannot_auto_succeed(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path, with_python_tests=True)
    reviewer = ApprovingReviewer()
    runtime = LoopAutomationRuntime(workspace, reviewer_runner=reviewer)
    run_dir = runtime.start(_brief(repo), "assist", verify=False)
    repo.joinpath("README.md").write_text(
        "# Demo\nimplemented\n",
        encoding="utf-8",
        newline="\n",
    )
    test_log = workspace / "provided-test-log.txt"
    test_log.parent.mkdir(parents=True, exist_ok=True)
    test_log.write_text(
        "FAILED tests/test_demo.py::test_broken\n1 failed\n",
        encoding="utf-8",
        newline="\n",
    )

    runtime.continue_assist(
        run_dir.name,
        repo,
        test_log=test_log,
        verify=True,
    )

    state = _read_json(run_dir / "state.json")
    iteration_dir = run_dir / "iterations" / "01"
    finish = _finish(workspace, run_dir)

    assert "1 failed" in (iteration_dir / "test-summary.md").read_text(encoding="utf-8")
    assert not (iteration_dir / "verification-result.json").exists()
    assert state["iterations"][0]["verification_status"] == "skipped"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "verification_unverified"
    assert finish["finish_status"] != "ready_to_commit"
    assert finish["verification_passed"] is False


def test_finish_recomputes_unverified_success_as_needs_human(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path, with_python_tests=True)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=ApprovingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)
    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    state["status"] = "success"
    state["current_step"] = "done"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    finish = _finish(workspace, run_dir)

    assert finish["loop_status"] == "success"
    assert finish["artifact_integrity"]["valid"] is True
    assert finish["verification_passed"] is False
    assert finish["finish_status"] == "needs_human"


def test_goal_rejects_unverified_loop_and_finish_evidence(tmp_path: Path) -> None:
    workspace, repo = _init_repo(tmp_path, with_python_tests=True)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=ApprovingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)
    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    state["status"] = "success"
    state["current_step"] = "done"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _finish(workspace, run_dir)

    loop_evidence = validate_goal_evidence(
        workspace,
        repo,
        run_dir.name,
        "loop",
        "验证 Goal 不信任缺失验证证据的 Loop 顶层状态",
    )
    finish_evidence = validate_goal_evidence(
        workspace,
        repo,
        run_dir.name,
        "finish",
        "验证 Goal 会重算 Finish 的验证结论",
    )

    assert loop_evidence.completion_eligible is False
    assert "verification=unverified" in (loop_evidence.validation_summary or "")
    assert finish_evidence.completion_eligible is False
    assert "finish_status=needs_human" in (finish_evidence.validation_summary or "")


def test_latest_skipped_verification_cannot_reuse_previous_pass(
    tmp_path: Path,
) -> None:
    workspace, repo = _init_repo(tmp_path, with_verification_config=True)
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=SequencedReviewer(["request_changes", "approve"]),
    )
    run_dir = runtime.start(_brief(repo), "auto", max_iterations=1, verify=True)
    first_state = _read_json(run_dir / "state.json")
    assert first_state["iterations"][0]["verification_status"] == "passed"
    assert first_state["status"] == "needs_human"

    runtime.continue_assist(run_dir.name, repo, verify=False)

    state = _read_json(run_dir / "state.json")
    finish = _finish(workspace, run_dir)
    loop_evidence = validate_goal_evidence(
        workspace,
        repo,
        run_dir.name,
        "loop",
        "验证最新轮次不能继承前序验证通过证据",
    )

    assert len(state["iterations"]) == 2
    assert state["iterations"][0]["verification_status"] == "passed"
    assert state["iterations"][1]["verification_status"] == "skipped"
    assert state["iterations"][1]["verdict"] == "approve"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "verification_unverified"
    assert finish["verification_passed"] is False
    assert finish["finish_status"] == "needs_human"
    assert loop_evidence.completion_eligible is False
    assert "verification=unverified" in (loop_evidence.validation_summary or "")


@pytest.mark.parametrize(
    "mutation",
    ["missing", "tampered", "incomplete", "interrupted"],
)
def test_all_completion_layers_reject_broken_structured_verification(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace, repo = _init_repo(tmp_path, with_verification_config=True)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=ApprovingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)
    state = _read_json(run_dir / "state.json")
    assert state["status"] == "success"
    assert state["iterations"][0]["verification_status"] == "passed"

    result_path = run_dir / "iterations" / "01" / "verification-result.json"
    if mutation == "missing":
        result_path.unlink()
    elif mutation == "tampered":
        result = _read_json(result_path)
        result["command_count"] = 0
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "incomplete":
        result = _read_json(result_path)
        result["selected_command_count"] = result["command_count"] + 1
        result["skipped_commands"] = ["python -c \"print('never run')\""]
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        result = _read_json(result_path)
        result["interruption_status"] = "timed_out"
        result["interruption_command"] = result["commands"][0]
        result["interruption_reason"] = "forced timeout"
        result["results"][0]["interruption_status"] = "timed_out"
        result["results"][0]["interruption_reason"] = "forced timeout"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    eval_results = run_loop_eval(run_dir, state["artifacts"])
    finish = _finish(workspace, run_dir)
    loop_evidence = validate_goal_evidence(
        workspace,
        repo,
        run_dir.name,
        "loop",
        "验证 Loop 不信任损坏的结构化验证证据",
    )
    finish_evidence = validate_goal_evidence(
        workspace,
        repo,
        run_dir.name,
        "finish",
        "验证 Finish 不信任损坏的结构化验证证据",
    )

    assert any(
        item.startswith(
            "FAIL: success loop 的最新 verification 缺少受信结构化通过证据"
        )
        for item in eval_results
    )
    assert finish["artifact_integrity"]["valid"] is False
    assert finish["verification_passed"] is False
    assert finish["finish_status"] == "needs_human"
    assert loop_evidence.completion_eligible is False
    assert finish_evidence.completion_eligible is False


@pytest.mark.parametrize(
    ("mutation", "expected_eval_fragment"),
    [
        ("missing", "iteration_01_verification_result_missing"),
        ("invalid_json", "iteration_01_verification_result_invalid_json"),
        ("workspace_changed", "workspace_changed_since_review"),
    ],
)
def test_success_finalization_fails_closed_when_evidence_changes_before_eval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_eval_fragment: str,
) -> None:
    workspace, repo = _init_repo(tmp_path, with_verification_config=True)
    original_finalize = loop_runtime_module._finalize_loop_eval
    mutation_applied = False

    def mutate_then_finalize(run_dir, state, requested_status, artifacts, trace):
        nonlocal mutation_applied
        if requested_status == "success" and not mutation_applied:
            mutation_applied = True
            verification_path = (
                run_dir / "iterations" / "01" / "verification-result.json"
            )
            if mutation == "missing":
                verification_path.unlink()
            elif mutation == "invalid_json":
                verification_path.write_text("{", encoding="utf-8", newline="\n")
            else:
                readme = repo / "README.md"
                readme.write_text(
                    readme.read_text(encoding="utf-8") + "changed after review\n",
                    encoding="utf-8",
                    newline="\n",
                )
        return original_finalize(
            run_dir,
            state,
            requested_status,
            artifacts,
            trace,
        )

    monkeypatch.setattr(
        loop_runtime_module,
        "_finalize_loop_eval",
        mutate_then_finalize,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=ApprovingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    trace = [
        json.loads(line)
        for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    terminal_events = [item for item in trace if item.get("event") == "run_finished"]

    assert mutation_applied is True
    assert state["status"] == "failed"
    assert state["current_step"] == "completion_eval_failed"
    assert expected_eval_fragment in (run_dir / "eval.md").read_text(encoding="utf-8")
    assert [item["status"] for item in terminal_events] == ["failed"]
    assert trace[-1] == terminal_events[0]


def test_latest_passed_verification_supersedes_previous_failure_for_finish_and_goal(
    tmp_path: Path,
) -> None:
    verification_command = (
        "python -c \"from pathlib import Path; "
        "raise SystemExit(Path('README.md').read_text(encoding='utf-8')"
        ".count('implemented') < 2)\""
    )
    workspace, repo = _init_repo(
        tmp_path,
        with_verification_config=True,
        verification_command=verification_command,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=SequencedReviewer(["request_changes", "approve"]),
    ).start(_brief(repo), "auto", max_iterations=2, verify=True)
    state = _read_json(run_dir / "state.json")
    finish = _finish(workspace, run_dir)
    finish_evidence = validate_goal_evidence(
        workspace,
        repo,
        run_dir.name,
        "finish",
        "验证最新受信通过可覆盖已修复的历史验证失败",
    )

    assert [item["verification_status"] for item in state["iterations"]] == [
        "failed",
        "passed",
    ]
    assert state["status"] == "success"
    assert finish["has_verification_failures"] is True
    assert finish["latest_verification_failed"] is False
    assert finish["verification_passed"] is True
    assert finish["finish_status"] == "ready_to_commit"
    assert any("历史 iteration 曾验证失败" in item for item in finish["handoff_notes"])
    assert finish_evidence.completion_eligible is True


def _init_repo(
    tmp_path: Path,
    *,
    with_python_tests: bool = False,
    with_verification_config: bool = False,
    verification_command: str | None = None,
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- 修改后运行测试。\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8", newline="\n")
    if with_verification_config:
        command = (
            verification_command
            or "python -c \"print('assurance verification passed')\""
        )
        repo.joinpath(".vega.yaml").write_text(
            "\n".join(
                [
                    "version: 1",
                    "verification:",
                    "  commands:",
                    f"    - {command}",
                    "  max_commands: 1",
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
    if with_python_tests:
        repo.joinpath("tests").mkdir()
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
            newline="\n",
        )
        repo.joinpath("tests", "test_ok.py").write_text(
            "def test_ok():\n    assert True\n",
            encoding="utf-8",
            newline="\n",
        )
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
        mode="feature",
        text="修改 README",
        source="assurance-regression",
        repo_path=str(repo),
    )


def _finish(workspace: Path, run_dir: Path) -> dict:
    FinishRuntime(workspace).run(run_dir.name)
    return _read_json(run_dir / "finish-summary.json")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
