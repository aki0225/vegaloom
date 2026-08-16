from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import vega.execution_feedback as execution_feedback
import vega.review_runtime as review_runtime_module
from vega.change_plan_runtime import ChangePlanRuntime
from vega.decision import DecisionStore
from vega.execution_control import (
    ExecutionLease,
    ExecutionRecoveryInspection,
    OwnedProcessResult,
)
from vega.finish_runtime import FinishRuntime
from vega.gate_runtime import GateRuntime
from vega.experimental.goal_runtime import GoalRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput
from vega.project_config import check_project_config
from vega.recovery_runtime import render_recovery_report
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewRuntime
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.experimental.inspection.tool_broker import ToolBroker
from vega.verification import VerificationRunResult, run_project_verification
from vega.workspace_check import capture_review_workspace, run_workspace_check


FAKE_SECRET = "sk-runtime-fake-secret-123456"


def _create_directory_link(link_path: Path, target_path: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("当前 Windows 环境不能创建 junction")
        return
    link_path.symlink_to(target_path, target_is_directory=True)


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


class TerminationUnconfirmedRunner:
    def __init__(self, output: str) -> None:
        self.output = output

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        return RunnerResult(
            status="error",
            output=self.output,
            error="owned process tree 终止未确认",
            command=["termination-unconfirmed-runner"],
            termination_unconfirmed=True,
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


@pytest.mark.parametrize("flow", ["auto", "continue"])
def test_loop_attributes_project_config_failure_without_fake_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flow: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n  commands:\n    - python -c \\\n",
        encoding="utf-8",
    )
    config_check = check_project_config(repo)
    repo.joinpath(".vega.yaml").unlink()
    reviewer = QueueRunner([_review_json("approve")])
    monkeypatch.setattr(
        "vega.verification.check_project_config",
        lambda _: config_check,
    )

    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(["worker complete"]),
        reviewer_runner=reviewer,
    )
    brief = BriefInput(
        mode="bug",
        text="Fix the README behavior.",
        source="test",
        repo_path=str(repo),
    )
    if flow == "auto":
        run_dir = runtime.start(
            brief,
            "auto",
            max_iterations=1,
            verify=True,
        )
    else:
        run_dir = runtime.start(
            brief,
            "assist",
            max_iterations=1,
            verify=True,
        )
        repo.joinpath("README.md").write_text(
            "# Demo\nworker completed\n",
            encoding="utf-8",
            newline="\n",
        )
        run_dir = runtime.continue_assist(run_dir.name, repo, verify=True)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration = state["iterations"][0]
    iteration_dir = run_dir / "iterations" / "01"
    verification = json.loads(
        iteration_dir.joinpath("verification-result.json").read_text(encoding="utf-8")
    )
    trace_items = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = run_dir.joinpath("final-report.md").read_text(encoding="utf-8")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "project_config_invalid"
    assert not any(item.startswith("FAIL:") for item in state["eval_results"])
    assert iteration["verification_status"] == "failed"
    assert iteration["verification_failed_count"] == 0
    assert iteration["verification_failure_kind"] == "project_config_invalid"
    assert verification["failure_kind"] == "project_config_invalid"
    assert verification["commands"] == []
    assert verification["results"] == []
    assert verification["command_count"] == 0
    assert verification["failed_count"] == 0
    assert reviewer.prompts == []
    assert not iteration_dir.joinpath("reflect-run.txt").exists()
    assert not iteration_dir.joinpath("review-verdict.json").exists()
    assert "项目配置预检失败" in report
    assert "未执行任何验证命令" in report
    assert "验证命令失败" not in report
    assert any(
        item.get("event") == "verification_finished"
        and item.get("failure_kind") == "project_config_invalid"
        for item in trace_items
    )
    assert any(
        item.get("event") == "project_config_invalid"
        for item in trace_items
    )
    status_payload = run_status_payload(workspace, run_dir.name)
    assert any("项目配置预检失败" in item for item in status_payload["next_steps"])
    assert any("未执行任何验证命令" in item for item in status_payload["next_steps"])
    assert not any("自动验证失败" in item for item in status_payload["next_steps"])


def test_assist_committed_handoff_diff_runs_all_core_gates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    repo.joinpath("src").mkdir()
    repo.joinpath("tests").mkdir()
    repo.joinpath("src", "example.py").write_text(
        "VALUE = 0\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("tests", "test_example.py").write_text(
        "from src.example import VALUE\n\n\ndef test_value():\n    assert VALUE == 0\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "scope:",
                "  allowed_paths:",
                "    - src/example.py",
                "    - tests/test_example.py",
                "verification:",
                "  commands:",
                "    - python -m pytest tests/test_example.py -q",
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", ".vega.yaml", "src/example.py", "tests/test_example.py"],
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
            "add handoff fixture",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    comparison_base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    repo.joinpath("src", "example.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("tests", "test_example.py").write_text(
        "from src.example import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", "src/example.py", "tests/test_example.py"],
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
            "save committed handoff wip",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    changed_files = ("src/example.py", "tests/test_example.py")
    reviewer = QueueRunner(
        [
            json.dumps(
                {
                    "verdict": "approve",
                    "summary": "committed handoff reviewed",
                    "findings": [],
                    "reviewed_files": list(changed_files),
                    "checked_items": ["scope", "tests"],
                }
            )
        ]
    )
    runtime = LoopAutomationRuntime(
        workspace,
        reviewer_runner=reviewer,
    )
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="验证已提交 WIP 的跨机器恢复证据链。",
            source="test-committed-handoff",
            repo_path=str(repo),
        ),
        "assist",
        max_iterations=1,
        verify=True,
        comparison_base_sha=comparison_base,
        comparison_paths=changed_files,
    )

    run_dir = runtime.continue_assist(run_dir.name, repo, verify=True)
    finish_dir = FinishRuntime(workspace).run(run_dir.name)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration = state["iterations"][0]
    scope = json.loads(
        run_dir.joinpath(
            "iterations",
            "01",
            "scope-gate-result.json",
        ).read_text(encoding="utf-8")
    )
    gate = json.loads(
        run_dir.joinpath(
            "iterations",
            "01",
            "risk-gate-result.json",
        ).read_text(encoding="utf-8")
    )
    finish = json.loads(
        finish_dir.joinpath("finish-summary.json").read_text(encoding="utf-8")
    )

    assert state["status"] == "success"
    assert state["comparison_base_sha"] == comparison_base
    assert state["comparison_paths"] == list(changed_files)
    assert scope["committed_changed_files"] == list(changed_files)
    assert scope["changed_files"] == list(changed_files)
    assert scope["staged_changed_files"] == []
    assert scope["unstaged_changed_files"] == []
    assert iteration["verification_status"] == "passed"
    assert iteration["risk_gate_status"] == "success"
    assert iteration["reviewer_status"] == "success"
    assert iteration["verdict"] == "approve"
    assert gate["changed_files"] == list(changed_files)
    assert finish["finish_status"] == "ready_to_commit"
    assert reviewer.prompts


@pytest.mark.parametrize("flow", ["auto", "continue"])
def test_loop_pauses_with_structured_artifacts_when_verification_workspace_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flow: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    reviewer = QueueRunner([_review_json("approve")])
    monkeypatch.setattr(
        "vega.verification.capture_runtime_workspace",
        lambda *_: (_ for _ in ()).throw(
            RuntimeError("workspace capture failed")
        ),
    )

    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(["worker complete"]),
        reviewer_runner=reviewer,
    )
    brief = BriefInput(
        mode="bug",
        text="Fix the README behavior.",
        source="test",
        repo_path=str(repo),
    )
    if flow == "auto":
        run_dir = runtime.start(
            brief,
            "auto",
            max_iterations=1,
            verify=True,
        )
    else:
        run_dir = runtime.start(
            brief,
            "assist",
            max_iterations=1,
            verify=True,
        )
        repo.joinpath("README.md").write_text(
            "# Demo\nworker completed\n",
            encoding="utf-8",
            newline="\n",
        )
        run_dir = runtime.continue_assist(run_dir.name, repo, verify=True)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration = state["iterations"][0]
    iteration_dir = run_dir / "iterations" / "01"
    verification = json.loads(
        iteration_dir.joinpath("verification-result.json").read_text(encoding="utf-8")
    )
    trace_items = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert state["status"] == "needs_human"
    assert state["current_step"] == "verification_workspace_capture_failed"
    assert iteration["verification_status"] == "failed"
    assert iteration["verification_failure_kind"] == "workspace_capture_failed"
    assert verification["failure_kind"] == "workspace_capture_failed"
    assert verification["workspace_fingerprint"] is None
    assert verification["workspace_capture_error_type"] == "RuntimeError"
    assert iteration_dir.joinpath("verification-summary.md").exists()
    assert iteration_dir.joinpath("test-summary.md").exists()
    assert not iteration_dir.joinpath("reflect-run.txt").exists()
    assert not iteration_dir.joinpath("review-verdict.json").exists()
    assert reviewer.prompts == []
    assert any(
        item.get("event") == "verification_workspace_capture_failed"
        for item in trace_items
    )
    assert trace_items[-1]["event"] == "run_paused"
    assert "工作区指纹采集失败" in iteration_dir.joinpath(
        "verification-summary.md"
    ).read_text(encoding="utf-8")
    status_payload = run_status_payload(workspace, run_dir.name)
    assert any("工作区指纹采集失败" in item for item in status_payload["next_steps"])


def test_verification_propagates_bounded_progress_reporter(
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
                '    - python -c "import time; time.sleep(0.3)"',
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(execution_feedback, "PROGRESS_INTERVAL_SECONDS", 0.03)

    result = run_project_verification(
        workspace,
        repo,
        workspace / "verification",
        progress_reporter=lambda step, elapsed: events.append((step, elapsed)),
    )

    assert not result.has_failures
    assert events[0] == ("verification", 0)
    assert len(events) >= 2


def test_verification_explicit_commands_override_project_defaults(
    tmp_path: Path,
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
                '    - python -c "raise SystemExit(9)"',
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    declared = [
        'python -c "print(\'declared-one\')"',
        'python -c "print(\'declared-two\')"',
    ]

    result = run_project_verification(
        workspace,
        repo,
        workspace / "verification",
        verification_commands=declared,
    )

    assert not result.has_failures
    assert result.command_count == 2
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    assert payload["commands"] == declared
    assert payload["selected_command_count"] == 2
    assert payload["skipped_commands"] == []


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
        assert environment == {
            "PYTHONDONTWRITEBYTECODE": "1",
            "VEGA_VERIFICATION_TEMP": str(expected_temp),
        }
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


@pytest.mark.parametrize("preexisting_kind", ["directory", "link"])
def test_verification_temp_leaf_must_be_created_exclusively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preexisting_kind: str,
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
                "    - echo {{vega_verification_temp}}",
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    leaf = (
        repo
        / ".tmp"
        / "vega-verification"
        / "exclusive-run"
        / "iteration-1"
        / "command-1"
    )
    leaf.parent.mkdir(parents=True)
    preserved = tmp_path / "preserved"
    if preexisting_kind == "directory":
        leaf.mkdir()
        preserved = leaf
    else:
        preserved.mkdir()
        _create_directory_link(leaf, preserved)
    marker = preserved / "do-not-remove.txt"
    marker.write_text("preserve", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "vega.verification.run_owned_process",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="已存在；拒绝复用或清理"):
        run_project_verification(
            workspace,
            repo,
            workspace / "runs" / "exclusive-run" / "iterations" / "01",
        )

    assert calls == []
    assert marker.read_text(encoding="utf-8") == "preserve"


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
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
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

    def fake_run_owned_process(
        command,
        input_text,
        cwd,
        timeout_seconds,
        context,
        *,
        environment,
    ):
        assert environment == {"PYTHONDONTWRITEBYTECODE": "1"}
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
    _init_clean_git_repo(repo)
    reviewer = QueueRunner([_review_json("approve")])

    seen_iterations: list[int] = []

    def fake_verification(
        workspace_path: Path,
        repo_path: Path,
        output_dir: Path,
        *,
        iteration: int,
        progress_reporter=None,
        verification_commands=None,
        comparison_base_sha=None,
        comparison_paths=(),
    ):
        del (
            workspace_path,
            progress_reporter,
            verification_commands,
            comparison_base_sha,
            comparison_paths,
        )
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
        repo.joinpath("README.md").write_text(
            "# Demo\nworker completed\n",
            encoding="utf-8",
            newline="\n",
        )
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


@pytest.mark.parametrize("termination_unconfirmed", [False, True])
def test_loop_continue_rejects_execution_tree_that_has_not_safely_disappeared(
    tmp_path: Path,
    termination_unconfirmed: bool,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    runtime = LoopAutomationRuntime(workspace)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="Fix the README behavior.",
            source="test",
            repo_path=str(repo),
        ),
        "assist",
        verify=False,
    )
    now = datetime.now(UTC)
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    execution_path.parent.mkdir(parents=True)
    execution_path.write_text(
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=os.getpid(),
            child_pid=os.getpid(),
            termination_unconfirmed=termination_unconfirmed,
            command=["worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="拒绝 continue"):
        runtime.continue_assist(run_dir.name, repo, verify=False)

    assert not run_dir.joinpath("iterations", "01").exists()


def test_worker_termination_unconfirmed_skips_output_and_workspace_followup(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    marker = "WORKER_OUTPUT_MUST_NOT_BE_CONSUMED"
    reviewer = QueueRunner([_review_json("approve")])

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TerminationUnconfirmedRunner(marker),
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
    iteration_dir = run_dir / "iterations" / "01"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "worker_termination_unconfirmed"
    assert state["iterations"][0]["worker_status"] == "failed"
    assert marker not in iteration_dir.joinpath("worker-output.txt").read_text(
        encoding="utf-8"
    )
    assert not iteration_dir.joinpath("workspace-check.json").exists()
    assert not iteration_dir.joinpath("verification-result.json").exists()
    assert reviewer.prompts == []


def test_reviewer_termination_unconfirmed_does_not_parse_or_persist_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    reflect_run = ReflectRuntime(workspace).run(repo)
    marker = "REVIEWER_APPROVE_MUST_NOT_BE_CONSUMED"
    reviewer_output = json.dumps(
        {
            "verdict": "approve",
            "summary": marker,
            "findings": [],
            "checked_items": ["scope"],
        }
    )
    capture_calls = 0
    original_capture = review_runtime_module.capture_runtime_workspace

    def capture_once_before_runner(*args, **kwargs):
        nonlocal capture_calls
        capture_calls += 1
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(
        review_runtime_module,
        "capture_runtime_workspace",
        capture_once_before_runner,
    )

    review_run = ReviewRuntime(
        workspace,
        runner=TerminationUnconfirmedRunner(reviewer_output),
    ).run(repo, reflect_run.name)

    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    verdict = json.loads(
        review_run.joinpath("review-verdict.json").read_text(encoding="utf-8")
    )
    runner_output = review_run.joinpath("review-runner-output.txt").read_text(
        encoding="utf-8"
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "termination_unconfirmed"
    assert verdict["verdict"] == "needs_human"
    assert "终止未确认" in verdict["summary"]
    assert marker not in runner_output
    # Review 输入和授权各捕获一次；终止未确认后不得再捕获第三次结束快照。
    assert capture_calls == 2


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
            "reviewed_files": ["README.md"],
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
