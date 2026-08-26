from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_codex_adapter import SupervisorAgentCodexAdapter
from vega.execution_control import ExecutionController, RunnerExecutionContext
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import LoopAutomationState
from vega.agent_runtime import SupervisorAgentRuntime
from vega.cli_entrypoint import app
from vega.run_status import run_status_payload
from vega.runner import RunnerResult


def test_change_run_starts_in_isolated_worktree_and_approves_contract(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    source_head = _git(repo, "rev-parse", "HEAD")
    source_status = _git(repo, "status", "--short")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)

    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    metadata = json.loads(
        (started.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])

    assert started.state.run_kind == "change"
    assert started.state.phase == "awaiting_approval"
    assert managed_repo != repo
    assert managed_repo.is_dir()
    assert _git(managed_repo, "rev-parse", "HEAD") == source_head
    assert _git(repo, "rev-parse", "HEAD") == source_head
    assert _git(repo, "status", "--short") == source_status
    assert (started.run_dir / "change-contract.json").is_file()
    assert (started.run_dir / "execution-plan.json").is_file()

    approved = runtime.approve(started.run_dir.name, actor="user")

    assert approved.state.phase == "ready"
    assert approved.state.approved_contract_digest
    assert approved.state.accepted_checkpoint_sha == source_head
    assert approved.state.current_work_item == "WI-01"
    assert approved.plan.approval_is_current()
    assert len(approved.plan.work_items) == 2


def test_agent_start_cli_accepts_change_contract_without_duplicate_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract_path = tmp_path / "contract.json"
    plan_path = tmp_path / "execution-plan.json"
    contract_path.write_text(
        _contract().model_dump_json(indent=2),
        encoding="utf-8",
    )
    plan_path.write_text(
        _execution_plan().model_dump_json(indent=2),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "start",
            "--repo",
            str(repo),
            "--contract",
            str(contract_path),
            "--execution-plan",
            str(plan_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Agent 已创建" in result.output
    run_dirs = list((workspace / "runs").iterdir())
    assert len(run_dirs) == 1
    state = json.loads(
        (run_dirs[0] / "agent-state.json").read_text(encoding="utf-8")
    )
    assert state["data"]["run_kind"] == "change"


def test_change_run_advances_two_work_items_on_candidate_commits(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    source_head = _git(repo, "rev-parse", "HEAD")
    source_status = _git(repo, "status", "--short")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    reviewer = _ReviewerRunner()
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_WorkerRunner(["src/one.py", "src/two.py"]),
        loop_runtime=LoopAutomationRuntime(
            workspace,
            reviewer_runner=reviewer,
        ),
        finish_runtime=FinishRuntime(workspace),
    )

    result = adapter.run(approved.run_dir.name, timeout_seconds=60)
    metadata = json.loads(
        (result.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    candidate_artifacts = sorted((result.run_dir / "candidates").glob("*.json"))
    candidates = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in candidate_artifacts
    ]
    candidates.sort(key=lambda item: item["created_at"])

    assert result.state.phase == "completed"
    assert result.state.terminal_status == "ready_to_commit"
    assert result.state.active_candidate_sha is None
    assert result.state.accepted_checkpoint_sha == _git(
        managed_repo,
        "rev-parse",
        "HEAD",
    )
    assert [item.status for item in result.plan.work_items] == [
        "completed",
        "completed",
    ]
    assert [item["work_item_id"] for item in candidates] == ["WI-01", "WI-02"]
    assert candidates[0]["parent_sha"] == source_head
    assert candidates[1]["parent_sha"] == candidates[0]["candidate_sha"]
    assert _git(managed_repo, "rev-list", "--count", f"{source_head}..HEAD") == "2"
    assert _git(repo, "rev-parse", "HEAD") == source_head
    assert _git(repo, "status", "--short") == source_status
    assert reviewer.calls == 2
    status = runtime.status(result.run_dir.name)
    assert "- 证据健康：`passed`" in status
    assert "已过期" not in status
    status_payload = run_status_payload(workspace, result.run_dir.name)
    assert status_payload["agent_run_kind"] == "change"
    assert status_payload["accepted_checkpoint_sha"] == result.state.accepted_checkpoint_sha
    assert any(
        "Accepted Checkpoint" in step
        for step in status_payload["next_steps"]
    )
    assert any(
        path.endswith("change-contract.json")
        for path in status_payload["key_artifacts"]
    )
    child_states = [
        LoopAutomationState.model_validate_json(
            (path / "state.json").read_text(encoding="utf-8")
        )
        for path in (workspace / "runs").iterdir()
        if path != result.run_dir
        and (path / "state.json").is_file()
        and (path / "loop-plan.md").is_file()
    ]
    comparison_bases = [
        state.comparison_base_sha
        for state in child_states
        if state.input_source.startswith("agent-task-brief:")
    ]
    assert comparison_bases == [
        source_head,
        candidates[0]["candidate_sha"],
    ]


def test_failed_candidate_returns_to_parent_for_repair(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    source_head = _git(repo, "rev-parse", "HEAD")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = _contract().model_copy(
        update={
            "task_id": "task-repair",
            "goal": "修复第一个模块",
        }
    )
    execution_plan = ExecutionPlan(
        task_id="task-repair",
        contract_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="更新第一个模块",
                likely_files=["src/one.py"],
                verification=["python -m pytest tests/test_one.py -q"],
            )
        ],
    )
    started = runtime.start_change(
        repo,
        contract=contract,
        execution_plan=execution_plan,
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    reviewer = _ReviewerRunner(["request_changes", "approve"])
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_WorkerRunner(["src/one.py", "src/one.py"]),
        loop_runtime=LoopAutomationRuntime(
            workspace,
            reviewer_runner=reviewer,
        ),
        finish_runtime=FinishRuntime(workspace),
    )

    first = adapter.run(approved.run_dir.name, timeout_seconds=60)
    metadata = json.loads(
        (first.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])

    assert first.state.phase == "ready"
    assert first.state.allowed_actions[0] == "repair"
    assert first.state.active_candidate_sha is None
    assert first.state.accepted_checkpoint_sha == source_head
    assert _git(managed_repo, "rev-parse", "HEAD") == source_head
    assert _git(managed_repo, "status", "--short") == "M src/one.py"
    first_candidates = list((first.run_dir / "candidates").glob("*.json"))
    assert len(first_candidates) == 1

    completed = adapter.run(first.run_dir.name, timeout_seconds=60)

    assert completed.state.phase == "completed"
    assert completed.state.accepted_checkpoint_sha == _git(
        managed_repo,
        "rev-parse",
        "HEAD",
    )
    assert _git(managed_repo, "status", "--short") == ""
    assert len(list((completed.run_dir / "candidates").glob("*.json"))) == 2


class _WorkerRunner:
    def __init__(self, targets: list[str]) -> None:
        self.targets = targets
        self.calls = 0
        self.path_calls: dict[str, int] = {}

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
        relative = self.targets[self.calls]
        target = repo_path / relative
        self.calls += 1
        self.path_calls[relative] = self.path_calls.get(relative, 0) + 1
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-worker"], 60)
        target.write_text(
            f"value = {self.path_calls[relative]}\n",
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


class _ReviewerRunner:
    def __init__(self, verdicts: list[str] | None = None) -> None:
        self.calls = 0
        self.verdicts = verdicts or ["approve"]

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, timeout_seconds, execution_context
        assert sandbox == "read-only"
        self.calls += 1
        reviewed_files = [
            line
            for line in _git(
                repo_path,
                "show",
                "--format=",
                "--name-only",
                "HEAD",
            ).splitlines()
            if line
        ]
        verdict = self.verdicts[min(self.calls - 1, len(self.verdicts) - 1)]
        findings = (
            [
                {
                    "severity": "major",
                    "file": reviewed_files[0],
                    "line": 1,
                    "title": "需要补充一次修复",
                    "evidence": "测试 Reviewer 的确定性返回",
                    "recommendation": "继续修改当前 Work Item",
                }
            ]
            if verdict == "request_changes"
            else []
        )
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": verdict,
                    "summary": (
                        "当前 Candidate 需要继续修改"
                        if verdict == "request_changes"
                        else "当前 Candidate 未发现阻断问题"
                    ),
                    "findings": findings,
                    "reviewed_files": reviewed_files,
                    "checked_items": (
                        ["scope", "tests"]
                        if verdict == "approve"
                        else []
                    ),
                },
                ensure_ascii=False,
            ),
            command=["fake-reviewer"],
        )


def _contract() -> ChangeContract:
    return ChangeContract(
        task_id="task-change-run",
        goal="完成两个顺序修改",
        acceptance=["两个模块均按要求更新"],
        required_verification=["python -m compileall -q src"],
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/**", "tests/**"],
            forbidden_paths=["src/generated/**"],
            max_changed_files=4,
        ),
    )


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-change-run",
        contract_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="更新第一个模块",
                likely_files=["src/one.py"],
                verification=["python -m pytest tests/test_one.py -q"],
            ),
            ExecutionWorkItem(
                work_item_id="WI-02",
                objective="更新第二个模块",
                depends_on=["WI-01"],
                likely_files=["src/two.py"],
                verification=["python -m pytest tests/test_two.py -q"],
            ),
        ],
    )


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    (path / ".gitignore").write_text(
        ".vega/\n__pycache__/\n*.pyc\n",
        encoding="utf-8",
    )
    (path / ".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "scope:",
                "  allowed_paths:",
                "    - src/**",
                "    - tests/**",
                "verification:",
                "  commands:",
                "    - python -m compileall -q src",
                "  max_commands: 4",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (path / "src").mkdir()
    (path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (path / "src" / "one.py").write_text("value = 0\n", encoding="utf-8")
    (path / "src" / "two.py").write_text("value = 0\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_one.py").write_text(
        "from src.one import value\n\n\ndef test_one():\n    assert value >= 1\n",
        encoding="utf-8",
    )
    (path / "tests" / "test_two.py").write_text(
        "from src.two import value\n\n\ndef test_two():\n    assert value == 1\n",
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "初始化")
    return path


def _git(repo: Path, *args: object) -> str:
    process = subprocess.run(
        ["git", *(str(arg) for arg in args)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()
