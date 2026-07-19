from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from vega.change_plan_runtime import ChangePlanRuntime
from vega.decision import DecisionStore
from vega.execution_control import ExecutionRecoveryInspection, OwnedProcessResult
from vega.gate_runtime import GateRuntime
from vega.goal_runtime import GoalRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput
from vega.recovery_runtime import render_recovery_report
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewRuntime
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.tool_broker import ToolBroker
from vega.verification import VerificationRunResult, run_project_verification
from vega.workspace_check import capture_review_workspace, run_workspace_check


FAKE_SECRET = "sk-runtime-fake-secret-123456"


class QueueRunner:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
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
        self.prompts.append(prompt)
        output = self.outputs.pop(0)
        return RunnerResult(
            status="success",
            output=output,
            command=[f"runner --token={FAKE_SECRET}"],
        )


class TrackedChangeRunner(QueueRunner):
    """模拟成功 worker 写入 tracked 文件，避免把历史 diff 当作 auto 成果。"""

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
            "# Demo\nworker completed\n",
            encoding="utf-8",
            newline="\n",
        )
        return result


class MutatingReviewer:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        repo_path.joinpath("reviewer-created.txt").write_text(
            "reviewer changed the workspace\n",
            encoding="utf-8",
        )
        return RunnerResult(
            status="success",
            output=_review_json("approve"),
            command=["mutating-reviewer"],
        )


class IgnoredFileMutatingReviewer:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        repo_path.joinpath("reviewer.tmp").write_text(
            "reviewer changed ignored workspace state\n",
            encoding="utf-8",
        )
        return RunnerResult(
            status="success",
            output=_review_json("approve"),
            command=["ignored-file-mutating-reviewer"],
        )


@pytest.mark.parametrize("location", ["outside", "sensitive"])
def test_reflect_rejects_unsafe_test_log_paths(tmp_path: Path, location: str) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    if location == "outside":
        test_log = tmp_path / "external" / "test.log"
    else:
        test_log = repo / ".env"
    test_log.parent.mkdir(parents=True, exist_ok=True)
    test_log.write_text(f"api_key={FAKE_SECRET}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        ReflectRuntime(workspace).run(repo, test_log=test_log)

    assert FAKE_SECRET not in _read_tree(workspace)


def test_review_workspace_snapshot_does_not_open_sensitive_untracked_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".env").write_text(f"API_KEY={FAKE_SECRET}\n", encoding="utf-8")
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path.name == ".env":
            raise AssertionError("sensitive file content must not be opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    snapshot = capture_review_workspace(repo)

    assert ".env" in snapshot.untracked_files
    assert len(snapshot.untracked_manifest_sha256) == 64


def test_workspace_check_redacts_sensitive_untracked_filename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(f"api_key={FAKE_SECRET}.txt").write_text("noise\n", encoding="utf-8")
    output_dir = tmp_path / "workspace-check"

    run_workspace_check(repo, output_dir)

    artifacts = _read_tree(output_dir)
    assert FAKE_SECRET not in artifacts
    assert "[REDACTED]" in artifacts


def test_workspace_check_uses_placeholder_for_sensitive_untracked_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".env").write_text(f"API_KEY={FAKE_SECRET}\n", encoding="utf-8")
    output_dir = tmp_path / "workspace-check"

    run_workspace_check(repo, output_dir)

    artifacts = _read_tree(output_dir)
    payload = json.loads(
        output_dir.joinpath("workspace-check.json").read_text(encoding="utf-8")
    )
    assert ".env" not in artifacts
    assert FAKE_SECRET not in artifacts
    assert "?? .env" not in artifacts
    assert "<sensitive-path:environment_file>" in artifacts
    assert payload["new_untracked_files"] == ["<sensitive-path:environment_file>"]
    assert "?? <sensitive-path:environment_file>" in payload["raw_status"]


def test_verification_redacts_secret_from_command_and_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                f'    - python -c "print(\'api_key={FAKE_SECRET}\')"',
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = workspace / "verification"

    result = run_project_verification(workspace, repo, output_dir)

    assert result.command_count == 1
    assert not result.has_failures
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    command_result = payload["results"][0]
    assert command_result["command"] == command_result["configured_command"]
    assert command_result["command"] == command_result["executed_command"]
    assert not repo.joinpath(".tmp", "vega-verification").exists()
    artifacts = _read_tree(output_dir)
    assert FAKE_SECRET not in artifacts
    assert "[REDACTED]" in artifacts


def test_verification_temp_placeholder_isolates_iterations_and_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - echo first {{vega_verification_temp}}",
                "    - echo second {{vega_verification_temp}}",
                "  max_commands: 2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[tuple[int, int, Path, str]] = []

    def fake_run_owned_process(
        command,
        input_text,
        cwd,
        timeout_seconds,
        context,
        *,
        environment,
    ):
        del input_text, cwd, timeout_seconds
        assert context.iteration is not None
        command_index = int(context.execution_dir.name.rsplit("-", 1)[1])
        expected_temp = (
            repo.resolve()
            / ".tmp"
            / "vega-verification"
            / "isolation-run"
            / f"iteration-{context.iteration}"
            / f"command-{command_index}"
        )
        assert expected_temp.is_dir()
        assert environment == {"VEGA_VERIFICATION_TEMP": str(expected_temp)}
        shell_text = command if isinstance(command, str) else " ".join(command)
        assert "VEGA_VERIFICATION_TEMP" in shell_text
        assert str(expected_temp) not in shell_text
        calls.append((context.iteration, command_index, expected_temp, shell_text))
        return OwnedProcessResult(
            status="success",
            output="ok\n",
            error=None,
            returncode=0,
        )

    monkeypatch.setattr(
        "vega.verification.run_owned_process",
        fake_run_owned_process,
    )

    first = run_project_verification(
        workspace,
        repo,
        workspace / "runs" / "isolation-run" / "iterations" / "01",
        iteration=1,
    )
    second = run_project_verification(
        workspace,
        repo,
        workspace / "runs" / "isolation-run" / "iterations" / "02",
        iteration=2,
    )

    assert [(iteration, command) for iteration, command, _, _ in calls] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    ]
    assert len({path for _, _, path, _ in calls}) == 4
    for result in (first, second):
        payload = json.loads(result.result_path.read_text(encoding="utf-8"))
        for command_result in payload["results"]:
            assert "{{vega_verification_temp}}" in command_result["command"]
            assert command_result["command"] == command_result["configured_command"]
            assert "{{vega_verification_temp}}" not in command_result["executed_command"]
            assert "VEGA_VERIFICATION_TEMP" in command_result["executed_command"]
            assert command_result["verification_temp"] in {
                path.relative_to(repo.resolve()).as_posix()
                for _, _, path, _ in calls
            }


def test_verification_temp_artifacts_redact_sensitive_repo_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / f"repo-{FAKE_SECRET}"
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - echo {{vega_verification_temp}}",
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run_owned_process(
        command,
        input_text,
        cwd,
        timeout_seconds,
        context,
        *,
        environment,
    ):
        del command, input_text, cwd, timeout_seconds, context
        assert FAKE_SECRET in environment["VEGA_VERIFICATION_TEMP"]
        return OwnedProcessResult(
            status="success",
            output="ok\n",
            error=None,
            returncode=0,
        )

    monkeypatch.setattr(
        "vega.verification.run_owned_process",
        fake_run_owned_process,
    )
    result = run_project_verification(
        workspace,
        repo,
        workspace / "runs" / "redaction-run" / "iterations" / "01",
    )

    artifacts = _read_tree(result.result_path.parent)
    assert FAKE_SECRET not in artifacts
    assert "[REDACTED]" in artifacts
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    assert payload["results"][0]["verification_temp"] == (
        Path(".tmp")
        / "vega-verification"
        / "redaction-run"
        / "iteration-1"
        / "command-1"
    ).as_posix()


@pytest.mark.skipif(os.name != "nt", reason="仅覆盖 Windows cmd.exe 引号语义")
def test_windows_verification_preserves_nested_python_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo with spaces & %VEGA_INJECT%"
    monkeypatch.setenv("VEGA_INJECT", "unexpected-expansion")
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                (
                    "    - python -c \"import sys; from pathlib import Path; "
                    "path=Path(sys.argv[1]); "
                    "assert path.is_dir(); print('quoted verification ok')\" "
                    "{{vega_verification_temp}}"
                ),
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output_dir = workspace / "runs" / "windows-run" / "iterations" / "04"
    result = run_project_verification(
        workspace,
        repo,
        output_dir,
        iteration=4,
    )
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    expected_temp = (
        repo.resolve()
        / ".tmp"
        / "vega-verification"
        / "windows-run"
        / "iteration-4"
        / "command-1"
    )
    execution = json.loads(
        output_dir.joinpath(
            "executions",
            "verification-01",
            "execution.json",
        ).read_text(encoding="utf-8")
    )

    assert not result.has_failures
    assert payload["results"][0]["returncode"] == 0
    assert "quoted verification ok" in payload["results"][0]["output"]
    assert payload["results"][0]["command"].count("{{vega_verification_temp}}") == 1
    assert "VEGA_VERIFICATION_TEMP" in payload["results"][0]["executed_command"]
    assert str(expected_temp) not in payload["results"][0]["executed_command"]
    assert payload["results"][0]["verification_temp"] == expected_temp.relative_to(
        repo.resolve()
    ).as_posix()
    assert execution["iteration"] == 4


@pytest.mark.skipif(os.name == "nt", reason="仅覆盖 POSIX shell 变量展开语义")
def test_posix_verification_temp_env_does_not_re_evaluate_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo $(touch injected) `touch injected-two`"
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                (
                    "    - python -c \"import sys; from pathlib import Path; "
                    "path=Path(sys.argv[1]); assert path.is_dir(); print('safe')\" "
                    "{{vega_verification_temp}}"
                ),
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_project_verification(
        workspace,
        repo,
        workspace / "runs" / "posix-run" / "iterations" / "01",
    )

    assert not result.has_failures
    assert not tmp_path.joinpath("injected").exists()
    assert not tmp_path.joinpath("injected-two").exists()


@pytest.mark.parametrize(
    (
        "owned_status",
        "termination_unconfirmed",
        "interruption_status",
        "result_status",
        "summary_badge",
    ),
    [
        ("timed_out", False, "timed_out", "timeout", "TIMEOUT"),
        ("stopped", False, "stopped", "failed", "STOPPED"),
        (
            "error",
            True,
            "termination-unconfirmed",
            "failed",
            "TERMINATION-UNCONFIRMED",
        ),
    ],
)
def test_verification_continues_after_failure_and_short_circuits_on_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_status: str,
    termination_unconfirmed: bool,
    interruption_status: str,
    result_status: str,
    summary_badge: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - echo ordinary-failure",
                "    - echo interruption",
                "    - echo never-run",
                "  max_commands: 3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    responses = [
        OwnedProcessResult(
            status="error",
            output="ordinary failure\n",
            error="外部 runner 退出码：1",
            returncode=1,
        ),
        OwnedProcessResult(
            status=owned_status,
            output="interrupted\n",
            error=f"verification {interruption_status}",
            returncode=None,
            termination_unconfirmed=termination_unconfirmed,
        ),
        OwnedProcessResult(
            status="success",
            output="must not run\n",
            error=None,
            returncode=0,
        ),
    ]
    calls: list[list[str]] = []

    def fake_run_owned_process(command, input_text, cwd, timeout_seconds, context):
        calls.append(command)
        return responses[len(calls) - 1]

    monkeypatch.setattr(
        "vega.verification.run_owned_process",
        fake_run_owned_process,
    )

    result = run_project_verification(workspace, repo, workspace / "verification")
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    summary = result.summary_path.read_text(encoding="utf-8")

    assert len(calls) == 2
    assert result.was_interrupted
    assert result.interruption_status == interruption_status
    assert payload["command_count"] == 2
    assert payload["selected_command_count"] == 3
    assert payload["skipped_commands"] == ["echo never-run"]
    assert [item["status"] for item in payload["results"]] == ["failed", result_status]
    assert payload["results"][0]["interruption_status"] is None
    assert payload["results"][1]["interruption_status"] == interruption_status
    assert summary_badge in summary


@pytest.mark.parametrize(
    ("flow", "interruption_status", "report_name", "current_step", "worker_status"),
    [
        ("auto", "timed_out", "timeout-report.md", "timed_out", "success"),
        (
            "continue",
            "termination-unconfirmed",
            "runner-error-report.md",
            "verification_termination_unconfirmed",
            "skipped",
        ),
    ],
)
def test_loop_persists_verification_interruption_before_reflect_and_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flow: str,
    interruption_status: str,
    report_name: str,
    current_step: str,
    worker_status: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    if flow == "auto":
        _init_clean_git_repo(repo)
    else:
        _init_changed_git_repo(repo)
    reviewer = QueueRunner([_review_json("approve")])

    seen_iterations: list[int] = []

    def fake_verification(
        workspace_path: Path,
        repo_path: Path,
        output_dir: Path,
        *,
        iteration: int,
    ):
        del workspace_path
        seen_iterations.append(iteration)
        output_dir.mkdir(parents=True, exist_ok=True)
        result_status = "timeout" if interruption_status == "timed_out" else "failed"
        result_path = output_dir / "verification-result.json"
        summary_path = output_dir / "verification-summary.md"
        result_path.write_text(
            json.dumps(
                {
                    "repo_path": str(repo_path.resolve()),
                    "commands": ["python -m pytest -q"],
                    "results": [
                        {
                            "command": "python -m pytest -q",
                            "status": result_status,
                            "returncode": None,
                            "duration_seconds": 1.0,
                            "output": f"verification {interruption_status}",
                            "interruption_status": interruption_status,
                            "interruption_reason": f"verification {interruption_status}",
                        }
                    ],
                    "command_count": 1,
                    "failed_count": 1,
                    "selected_command_count": 1,
                    "skipped_commands": [],
                    "interruption_status": interruption_status,
                    "interruption_command": "python -m pytest -q",
                    "interruption_reason": f"verification {interruption_status}",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        summary_path.write_text(
            f"# 验证摘要\n\n- 中断状态：`{interruption_status}`\n",
            encoding="utf-8",
        )
        return VerificationRunResult(
            summary_path=summary_path,
            result_path=result_path,
            command_count=1,
            failed_count=1,
            interruption_status=interruption_status,
            interruption_command="python -m pytest -q",
            interruption_reason=f"verification {interruption_status}",
        )

    def fail_if_reflect_runs(*args, **kwargs):
        raise AssertionError("verification 中断后不得运行 reflect")

    monkeypatch.setattr(
        "vega.loop_runtime.run_project_verification",
        fake_verification,
    )
    monkeypatch.setattr(
        "vega.loop_runtime.ReflectRuntime.run",
        fail_if_reflect_runs,
    )

    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(["worker complete"]),
        reviewer_runner=reviewer,
    )
    brief_input = BriefInput(
        mode="bug",
        text="Fix the README behavior.",
        source="test",
        repo_path=str(repo),
    )
    run_dir = runtime.start(
        brief_input,
        "auto" if flow == "auto" else "assist",
        max_iterations=1,
        verify=True,
    )
    if flow == "continue":
        run_dir = runtime.continue_assist(run_dir.name, repo, verify=True)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration_dir = run_dir / "iterations" / "01"
    trace_items = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert state["status"] == "needs_human"
    assert state["current_step"] == current_step
    assert state["iterations"][0]["worker_status"] == worker_status
    assert state["iterations"][0]["verification_status"] == "failed"
    assert state["iterations"][0]["verification_failed_count"] == 1
    assert seen_iterations == [1]
    assert iteration_dir.joinpath(report_name).exists()
    assert iteration_dir.joinpath("verification-result.json").exists()
    assert iteration_dir.joinpath("verification-summary.md").exists()
    assert iteration_dir.joinpath("test-summary.md").exists()
    assert not iteration_dir.joinpath("reflect-run.txt").exists()
    assert not iteration_dir.joinpath("review-verdict.json").exists()
    assert reviewer.prompts == []
    assert "机器验证失败或中断" in run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    )
    assert any(item.get("event") == "verification_interrupted" for item in trace_items)
    assert trace_items[-1]["event"] == "run_paused"
    if interruption_status == "termination-unconfirmed":
        next_steps = run_status_payload(workspace, run_dir.name)["next_steps"]
        assert any("终止未确认" in item for item in next_steps)
        assert any("不允许重复 stop 或自动 recover" in item for item in next_steps)


def test_review_blocks_untracked_config_without_leaking_content(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    marker = "UNTRACKED_CONFIG_MUST_NOT_REACH_REVIEWER"
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "runner:",
                "  reviewer: none",
                "verification:",
                "  commands:",
                f"    - echo {marker}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    reflect_run = ReflectRuntime(workspace).run(repo)
    reviewer_output = json.dumps(
        {
            "verdict": "approve",
            "summary": f"api_key={FAKE_SECRET}",
            "findings": [],
            "checked_items": ["scope"],
        }
    )
    runner = QueueRunner([reviewer_output])

    review_run = ReviewRuntime(workspace, runner=runner).run(repo, reflect_run.name)

    assert runner.prompts == []
    artifacts = _read_tree(review_run)
    assert marker not in review_run.joinpath("review-prompt.md").read_text(encoding="utf-8")
    assert FAKE_SECRET not in artifacts
    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_stale"
    context = json.loads(
        review_run.joinpath("review-context.json").read_text(encoding="utf-8")
    )
    assert "source_untracked_files_present" in context["evidence_issues"]


def test_review_workspace_change_during_runner_forces_needs_human(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=MutatingReviewer()).run(
        repo,
        reflect_run.name,
    )

    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    verdict = json.loads(
        review_run.joinpath("review-verdict.json").read_text(encoding="utf-8")
    )
    context = json.loads(
        review_run.joinpath("review-context.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "needs_human"
    assert verdict["verdict"] == "needs_human"
    assert "workspace_changed_during_review" in context["evidence_issues"]


def test_review_workspace_change_detects_ignored_file_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".gitignore").write_text("*.tmp\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", ".gitignore"],
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
            "ignore temp files",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=IgnoredFileMutatingReviewer()).run(
        repo,
        reflect_run.name,
    )

    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    context = json.loads(
        review_run.joinpath("review-context.json").read_text(encoding="utf-8")
    )
    assert repo.joinpath("reviewer.tmp").exists()
    assert state["status"] == "needs_human"
    assert context["workspace_changed_during_review"] is True
    assert "workspace_changed_during_review" in context["evidence_issues"]


def test_file_read_rejects_windows_alternate_data_stream_syntax(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8")
    stream_path = Path(str(repo / "README.md") + ":.env")
    stream_path.write_text(f"CUSTOM_VALUE={FAKE_SECRET}\n", encoding="utf-8")

    result = ToolBroker(repo).file_read("README.md:.env")

    assert result.status == "error"
    assert FAKE_SECRET not in str(result.model_dump())
    assert "alternate_data_stream" in (result.error or "")


def test_human_control_and_planning_artifacts_redact_secrets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    workspace.mkdir()
    _init_changed_git_repo(repo)

    decision_run = workspace / "runs" / "decision-run"
    decision_run.mkdir(parents=True)
    DecisionStore(decision_run).append(
        decision_type="custom",
        decision="approved",
        reason=f"api_key={FAKE_SECRET}",
        actor=f"token={FAKE_SECRET}",
        references=[f"secret={FAKE_SECRET}"],
    )

    recovery_report = render_recovery_report(
        run_id="recovery-run",
        repo_path=str(repo),
        previous_step="worker",
        previous_iteration=1,
        reason=f"api_key={FAKE_SECRET}",
        inspection=ExecutionRecoveryInspection(True, f"token={FAKE_SECRET}"),
    )
    assert FAKE_SECRET not in recovery_report

    ChangePlanRuntime(workspace).run(
        repo,
        f"修复问题，api_key={FAKE_SECRET}",
        f"token={FAKE_SECRET}",
    )

    goal_text = "\n".join(
        [
            "# Goal",
            "",
            f"Objective: 收口目标，api_key={FAKE_SECRET}",
            "",
            "Non-goals:",
            "- 不调用 worker",
            "",
            "Success conditions:",
            "- pytest 通过",
        ]
    )
    goal_runtime = GoalRuntime(workspace)
    goal_run = goal_runtime.start(
        repo,
        goal_text,
        f"token={FAKE_SECRET}",
        None,
    )
    goal_runtime.pause(goal_run.name, f"api_key={FAKE_SECRET}")
    goal_runtime.resume(goal_run.name)
    goal_runtime.stop(goal_run.name, f"token={FAKE_SECRET}")

    assert FAKE_SECRET not in _read_tree(workspace)
    assert "[REDACTED]" in _read_tree(workspace)


def test_gate_artifacts_redact_sensitive_untracked_filenames(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(f"api_key={FAKE_SECRET}.txt").write_text(
        "untracked\n",
        encoding="utf-8",
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    artifacts = _read_tree(gate_run)
    assert FAKE_SECRET not in artifacts
    assert "[REDACTED]" in artifacts


def test_loop_eval_failure_overrides_success_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    worker = TrackedChangeRunner(["worker complete"])
    reviewer = QueueRunner([_review_json("approve")])
    monkeypatch.setattr(
        "vega.loop_runtime.run_loop_eval",
        lambda run_dir, artifacts, **kwargs: ["FAIL: forced integration failure"],
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(
        BriefInput(
            mode="bug",
            text="Fix the README behavior.",
            source="test",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    trace_items = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert state["status"] == "failed"
    assert "FAIL: forced integration failure" in state["eval_results"]
    terminal_statuses = [
        item["status"]
        for item in trace_items
        if item.get("event") == "run_finished"
    ]
    assert terminal_statuses == ["failed"]
    assert trace_items[-1]["event"] == "run_finished"


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
        "# Rules\n\n- Run tests.\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", "AGENTS.md", "README.md"],
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


def _init_changed_git_repo(repo: Path) -> None:
    _init_clean_git_repo(repo)
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_tree(root: Path) -> str:
    if not root.exists():
        return ""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file()
    )
