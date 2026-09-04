from __future__ import annotations

import json
import re
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

import vega.agent_reviewer_timeout_retry as reviewer_timeout_retry
from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentWorkItem,
    approve_plan,
)
from vega.agent_persistence import read_agent_trace
from vega.agent_provider_adapter import SupervisorAgentProviderAdapter
from vega.agent_provider_factory import ensure_reviewer_runner
from vega.agent_routing import decide_next_action
from vega.agent_runtime import SupervisorAgentRuntime
from vega.claude_code_runner import ClaudeCodeRunner
from vega.codex_app_server_runner import CodexAppServerRunner
from vega.execution_control import ExecutionController, RunnerExecutionContext
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.project_config import ProjectConfig
from vega.runner import RunnerResult


class _Worker:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, timeout_seconds
        assert sandbox == "workspace-write"
        assert execution_context is not None
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-worker"], 60)
        target = repo_path / "src/one.py"
        self.calls += 1
        target.write_text(
            f"value = {self.calls}\n",
            encoding="utf-8",
            newline="\n",
        )
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "claimed_status": "completed",
                    "summary": "当前 Work Item 已修改",
                    "tests_claimed": [],
                    "remaining_questions": [],
                },
                ensure_ascii=False,
            ),
            command=["fake-worker"],
        )


class _Reviewer:
    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del repo_path, timeout_seconds
        assert sandbox == "read-only"
        assert execution_context is not None
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-reviewer"], 60)
        controller.finish(
            outcome,
            reason=None if outcome == "success" else f"模拟 {outcome}",
            returncode=0 if outcome == "success" else None,
        )
        if outcome != "success":
            return RunnerResult(
                status=outcome,
                output="",
                error=f"模拟 {outcome}",
                command=["fake-reviewer"],
            )
        reviewed_files = _reviewed_files(prompt, execution_context)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "当前 Candidate 未发现阻断问题",
                    "findings": [],
                    "reviewed_files": reviewed_files,
                    "checked_items": ["scope", "tests"],
                },
                ensure_ascii=False,
            ),
            command=["fake-reviewer"],
        )


def test_timeout_retries_once(
    tmp_path: Path,
) -> None:
    workspace, approved = _approved_change(tmp_path)
    reviewer = _Reviewer(["timed_out", "success", "success"])
    worker = _Worker()
    loop = LoopAutomationRuntime(workspace, reviewer_runner=reviewer)

    result = SupervisorAgentProviderAdapter(
        workspace,
        worker_runner=worker,
        loop_runtime=loop,
        finish_runtime=FinishRuntime(workspace),
    ).run(approved.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "completed"
    assert result.state.terminal_status == "ready_to_commit"
    assert worker.calls == 1
    assert reviewer.calls == 3
    trace = read_agent_trace(result.run_dir / "trace.jsonl")
    retry_events = [
        item for item in trace if item["event"] == "verification_retry_committed"
    ]
    assert len(retry_events) == 1
    operation = json.loads(
        next(
            path
            for path in (result.run_dir / "operations").glob("*.json")
            if json.loads(path.read_text(encoding="utf-8")).get("retry_reason")
            == "reviewer_timeout"
        ).read_text(encoding="utf-8")
    )
    assert operation["reviewer_retry_attempt"] == 1
    assert result.state.active_candidate_sha is None


@pytest.mark.parametrize(
    ("runner_status", "retry_attempt", "expected_reason_code"),
    [
        ("timed_out", 0, "review.runner_timed_out"),
        ("timed_out", 1, "review.retry_exhausted"),
        ("error", 0, "gate.review.blocked"),
        ("stopped", 0, "gate.review.blocked"),
        # 集成 Reviewer timeout 不会覆盖 Core child 的成功 runner 状态。
        ("success", 0, "gate.review.blocked"),
    ],
)
def test_only_first_core_reviewer_timeout_is_retryable(
    runner_status: str,
    retry_attempt: int,
    expected_reason_code: str,
) -> None:
    observation = AgentObservation(
        observation_id=f"obs-{runner_status}-{retry_attempt}",
        work_item_id="WI-01",
        child_run="child-01",
        operation_id="operation-01",
        machine_summary="Reviewer 结果需要路由",
        workspace_fingerprint="1" * 64,
        authority="machine_reconcile",
        operation_started=True,
        workspace_explained=True,
        external_side_effects="none",
        verification="passed",
        risk="passed",
        review="blocked",
        reviewer_runner_status=runner_status,  # type: ignore[arg-type]
        reviewer_retry_attempt=retry_attempt,
    )

    decision = decide_next_action(_approved_plan(), observation)

    assert decision.selected_action == "human"
    assert decision.reason_code == expected_reason_code


def test_unconfirmed_reviewer_termination_is_not_quiescent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "state.json").write_text(
        json.dumps(
            {
                "status": "needs_human",
                "current_step": "timed_out",
                "runner_status": "timed_out",
                "verdict": "needs_human",
            }
        ),
        encoding="utf-8",
    )
    record = SimpleNamespace(
        lease=SimpleNamespace(
            step="reviewer",
            iteration=1,
            status="timed_out",
            termination_unconfirmed=True,
        )
    )
    monkeypatch.setattr(
        reviewer_timeout_retry,
        "resolve_run_dir",
        lambda workspace, run: review_dir,
    )
    monkeypatch.setattr(
        reviewer_timeout_retry,
        "find_execution_records",
        lambda child_dir: [record],
    )

    with pytest.raises(ValueError, match="终止未确认"):
        reviewer_timeout_retry._require_review_execution_quiescent(
            tmp_path,
            tmp_path / "child",
            1,
            "review-run",
        )


@pytest.mark.parametrize(
    ("provider", "runner_type"),
    [
        ("codex", CodexAppServerRunner),
        ("claude", ClaudeCodeRunner),
    ],
)
def test_retry_role_is_independent(
    tmp_path: Path,
    provider: str,
    runner_type: type[object],
) -> None:
    workspace = tmp_path / "w"
    workspace.mkdir()
    loop = LoopAutomationRuntime(workspace)
    state = AgentState(
        run_id="agent-role",
        task_id="task-role",
        repository_id="repo@example",
        run_kind="change",
        phase="needs_human",
        goal_revision=1,
        plan_revision=1,
        approved_plan_digest="a" * 64,
        contract_revision=1,
        approved_contract_digest="b" * 64,
        execution_plan_revision=1,
        accepted_checkpoint_sha="c" * 40,
        current_work_item="WI-01",
    )
    role = "reviewer:WI-01:candidate-123456789abc:retry-1"

    ensure_reviewer_runner(
        loop,
        ProjectConfig(),
        agent_run_dir=workspace,
        state=state,
        provider=provider,  # type: ignore[arg-type]
        persistent_session=True,
        role_key=role,
    )

    assert isinstance(loop.reviewer_runner, runner_type)
    assert loop.reviewer_runner.role_key == role


def _approved_change(
    tmp_path: Path,
) -> tuple[Path, object]:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "w"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = ChangeContract(
        task_id="task-reviewer-timeout",
        goal="完成受控修改",
        acceptance=["目标文件按要求更新"],
        required_verification=["python -m compileall -q src"],
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/**"],
            max_changed_files=1,
            max_review_rounds=4,
            max_verification_retries=1,
        ),
    )
    plan = ExecutionPlan(
        task_id=contract.task_id,
        contract_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="更新 src/one.py",
                likely_files=["src/one.py"],
                verification=["python -m compileall -q src"],
            )
        ],
    )
    started = runtime.start_change(
        repo,
        contract=contract,
        execution_plan=plan,
    )
    return workspace, runtime.approve(started.run_dir.name, actor="user")


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    (path / ".gitignore").write_text(".vega/\n__pycache__/\n", encoding="utf-8")
    (path / ".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "scope:",
                "  allowed_paths:",
                "    - src/**",
                "verification:",
                "  commands:",
                "    - python -m compileall -q src",
                "  max_commands: 2",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (path / "src").mkdir()
    (path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (path / "src" / "one.py").write_text("value = 0\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "测试：初始化仓库")
    return path


def _reviewed_files(
    prompt: str,
    execution_context: RunnerExecutionContext,
) -> list[str]:
    context_path = execution_context.execution_root / "review-context.json"
    if context_path.is_file():
        payload = json.loads(context_path.read_text(encoding="utf-8"))
        return list(payload["changed_files"])
    changed_files = re.findall(r"^\+\+\+ b/(.+)$", prompt, flags=re.MULTILINE)
    assert changed_files
    return sorted(dict.fromkeys(changed_files))


def _approved_plan() -> AgentPlan:
    return approve_plan(
        AgentPlan(
            task_id="task-reviewer-timeout",
            user_goal="完成受控修改",
            success_conditions=["验证和审查通过"],
            work_items=[
                AgentWorkItem(
                    work_item_id="WI-01",
                    objective="更新目标文件",
                    allowed_paths=["src/**"],
                    verification=["python -m compileall -q src"],
                )
            ],
        ),
        actor="user",
        approved_at="2026-09-04T00:00:00+00:00",
    )


def _git(repo: Path, *args: str) -> None:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
