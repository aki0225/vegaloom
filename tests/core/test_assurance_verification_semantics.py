from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import vega.loop_runtime as loop_runtime_module
from vega.finish_runtime import FinishRuntime
from vega.loop_integrity import LoopArtifactIntegrity, latest_verification_failed
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput, LoopAutomationState, LoopIterationState
from vega.runner import RunnerResult
from vega.workspace_check import capture_review_workspace


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
                    "reviewed_files": ["README.md"],
                    "checked_items": ["scope", "diff", "tests"],
                },
                ensure_ascii=False,
            ),
            command=["assurance-test-reviewer"],
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


def test_legacy_failed_verification_is_unverified_not_trusted_failure() -> None:
    state = LoopAutomationState(
        run_id="legacy-verification",
        task_mode="bug",
        automation_mode="auto",
        repo_path="repo",
        input_source="inline-text",
        current_iteration=1,
        iterations=[
            LoopIterationState(
                iteration=1,
                verification_status="failed",
                verification_failed_count=1,
            )
        ],
    )
    integrity = LoopArtifactIntegrity(
        valid=True,
        issues=(),
        verification_results=(
            {
                "iteration": 1,
                "failed_count": 1,
            },
        ),
    )

    assert latest_verification_failed(state, integrity) is False


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


def test_workspace_change_after_verification_cannot_auto_succeed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo = _init_repo(tmp_path, with_verification_config=True)
    original_verification = loop_runtime_module.run_project_verification
    verification_fingerprint = ""

    def verify_then_mutate(*args, **kwargs):
        nonlocal verification_fingerprint
        result = original_verification(*args, **kwargs)
        verification_fingerprint = capture_review_workspace(repo).fingerprint
        readme = repo / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "changed after verification\n",
            encoding="utf-8",
            newline="\n",
        )
        return result

    monkeypatch.setattr(
        loop_runtime_module,
        "run_project_verification",
        verify_then_mutate,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=ApprovingReviewer(),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)
    state = _read_json(run_dir / "state.json")
    finish = _finish(workspace, run_dir)
    review_context = _read_json(
        workspace / "runs" / state["iterations"][0]["review_run"] / "review-context.json"
    )
    verification = _read_json(
        run_dir / "iterations" / "01" / "verification-result.json"
    )

    assert verification_fingerprint
    assert state["status"] != "success"
    assert finish["verification_passed"] is False
    assert finish["finish_status"] != "ready_to_commit"
    assert verification["workspace_fingerprint"] == verification_fingerprint
    assert (
        review_context["reviewer_end_workspace_fingerprint"]
        != verification_fingerprint
    )


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
