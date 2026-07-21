from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from vega.finish_runtime import FinishRuntime
from vega.gate_runtime import GATE_ARTIFACTS, GateRuntime
from vega.goal_runtime import GoalRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewRuntime
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
    """为 auto loop 构造本轮 worker 可归因的 tracked diff。"""

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
            "# Demo\nchanged\n",
            encoding="utf-8",
            newline="\n",
        )
        return result


def test_finish_rejects_workspace_changes_after_approved_review(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    loop_run = _create_successful_loop_run(workspace, repo)

    repo.joinpath("README.md").write_text(
        "# Demo\nchanged after review\n",
        encoding="utf-8",
        newline="\n",
    )
    FinishRuntime(workspace).run(loop_run.name)

    state = _read_json(loop_run / "state.json")
    summary = _read_json(loop_run / "finish-summary.json")
    trace = _read_jsonl(loop_run / "trace.jsonl")
    assert state["status"] == "success"
    assert state["current_step"] == "done"
    assert trace[-1]["event"] == "run_finished"
    assert trace[-1]["status"] == "success"
    assert summary["finish_status"] == "needs_human"
    assert summary["loop_status"] == "success"
    assert summary["evidence_freshness"]["fresh"] is False
    assert "workspace_changed_since_review" in summary["evidence_freshness"]["issues"]


def test_goal_complete_revalidates_expired_finish_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    child_loop = _create_successful_loop_run(workspace, repo)
    FinishRuntime(workspace).run(child_loop.name)
    goal = GoalRuntime(workspace)
    goal_run = goal.start(
        repo,
        "\n".join(
            [
                "# Goal",
                "",
                "Objective: 验证证据新鲜度",
                "",
                "Success conditions:",
                "- 当前仓库仍与 reviewer 通过时一致",
            ]
        ),
        "test",
        None,
    )
    goal.step(goal_run.name)
    goal.attach(goal_run.name, "01", child_loop.name, "finish", "已生成 finish")

    repo.joinpath("README.md").write_text(
        "# Demo\nchanged before checkpoint\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="缺少可完成证据"):
        goal.checkpoint_done(goal_run.name, "01", note="证据已过期")

    repo.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )
    goal.checkpoint_done(goal_run.name, "01", note="证据当前有效")
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged after checkpoint\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="完成证据已失效"):
        goal.complete(goal_run.name, "目标完成")


def test_human_risk_standalone_review_cannot_complete_goal_checkpoint(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    auth_path = repo / "src" / "auth.py"
    auth_path.parent.mkdir()
    auth_path.write_text(
        "def can_access():\n    return False\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", "src/auth.py"],
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
            "添加鉴权示例",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    auth_path.write_text(
        "def can_access():\n    return True\n",
        encoding="utf-8",
        newline="\n",
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(
        workspace,
        runner=StaticRunner([_review_json("approve")]),
    ).run(repo, reflect_run.name)

    review_state = _read_json(review_run / "state.json")
    review_context = _read_json(review_run / "review-context.json")
    assert review_state["runner_status"] == "success"
    assert review_state["status"] == "needs_human"
    assert review_state["current_step"] == "risk_gate_needs_human"
    assert review_context["risk_gate"]["result"]["recommendation"] == "human-review"

    goal = GoalRuntime(workspace)
    goal_run = goal.start(repo, _goal_text(), "test", None)
    goal.step(goal_run.name)
    goal.attach(goal_run.name, "01", review_run.name, "review", "高风险 review")

    evidence = _read_json(goal_run / "goal-state.json")["checkpoint_records"][0]["refs"][0]
    assert evidence["validated"] is True
    assert evidence["completion_eligible"] is False
    assert "review_risk_gate_requires_human" in evidence["validation_summary"]
    with pytest.raises(ValueError, match="缺少可完成证据"):
        goal.checkpoint_done(goal_run.name, "01", note="不应完成")


def test_gate_records_failure_for_reflect_from_another_repository(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source_repo = tmp_path / "source-repo"
    target_repo = tmp_path / "target-repo"
    _init_changed_git_repo(source_repo)
    _init_changed_git_repo(target_repo)
    reflect_run = ReflectRuntime(workspace).run(source_repo)

    gate_run = GateRuntime(workspace).run(target_repo, reflect_run.name)

    _assert_failed_gate_run(gate_run, "source_repo_mismatch")


def test_gate_records_failure_for_stale_reflect_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    reflect_run = ReflectRuntime(workspace).run(repo)
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged after reflect\n",
        encoding="utf-8",
        newline="\n",
    )

    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    _assert_failed_gate_run(gate_run, "workspace_changed_since_reflect")


def test_gate_records_failure_for_reflect_with_mismatched_directory_identity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    reflect_run = ReflectRuntime(workspace).run(repo)
    state_path = reflect_run / "state.json"
    state = _read_json(state_path)
    state["run_id"] = "forged-reflect-run"
    _write_json(state_path, state)

    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    _assert_failed_gate_run(gate_run, "source_run_id_mismatch")


def test_gate_records_failure_for_failed_reflect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    reflect_run = ReflectRuntime(workspace).run(repo)
    state_path = reflect_run / "state.json"
    state = _read_json(state_path)
    state["status"] = "failed"
    _write_json(state_path, state)

    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    _assert_failed_gate_run(gate_run, "source_reflect_not_success")


def test_gate_records_failure_for_tampered_reflection(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    reflect_run = ReflectRuntime(workspace).run(repo)
    reflect_run.joinpath("reflection.md").write_text(
        "# forged reflection\n",
        encoding="utf-8",
    )

    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    _assert_failed_gate_run(gate_run, "reflection_hash_mismatch")


def test_gate_records_failure_for_non_string_changed_files_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    reflect_run = ReflectRuntime(workspace).run(repo)
    state_path = reflect_run / "state.json"
    evidence_path = reflect_run / "review-evidence.json"
    state = _read_json(state_path)
    evidence = _read_json(evidence_path)
    forged_changed_files = [*evidence["changed_files"], {"path": "README.md"}]
    state["changed_files"] = forged_changed_files
    evidence["changed_files"] = forged_changed_files
    evidence["changed_files_sha256"] = _sha256_json(forged_changed_files)
    evidence["snapshot_id"] = _sha256_json(
        {key: value for key, value in evidence.items() if key != "snapshot_id"}
    )
    state["review_snapshot_id"] = evidence["snapshot_id"]
    _write_json(state_path, state)
    _write_json(evidence_path, evidence)

    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    _assert_failed_gate_run(gate_run, "evidence_changed_files_invalid")


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("state_run_id", "state.run_id"),
        ("contract_run_id", "contract.run_id"),
        ("contract_repo", "state.repo_path"),
    ],
)
def test_goal_load_rejects_mismatched_root_identity(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    runtime = GoalRuntime(workspace)
    goal_run = runtime.start(repo, _goal_text(), "test", None)

    if mutation == "state_run_id":
        for name in ("goal-state.json", "state.json"):
            path = goal_run / name
            payload = _read_json(path)
            payload["run_id"] = "forged-goal-run"
            _write_json(path, payload)
    else:
        contract_path = goal_run / "goal-contract.json"
        contract = _read_json(contract_path)
        if mutation == "contract_run_id":
            contract["run_id"] = "forged-goal-run"
        else:
            contract["repo_path"] = str(tmp_path / "other-repo")
        _write_json(contract_path, contract)

    with pytest.raises(ValueError, match=expected):
        runtime.step(goal_run.name)


def test_goal_gate_evidence_rejects_tampered_gate_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir(exist_ok=True)
    test_log = workspace / "tests.log"
    test_log.write_text("1 passed\n", encoding="utf-8")
    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)
    gate_result_path = gate_run / "gate-result.json"
    gate_result = _read_json(gate_result_path)
    assert gate_result["recommendation"] == "self-check"
    gate_result["changed_files"] = [*gate_result["changed_files"], "forged.py"]
    _write_json(gate_result_path, gate_result)

    runtime = GoalRuntime(workspace)
    goal_run = runtime.start(repo, _goal_text(), "test", None)
    runtime.step(goal_run.name)
    runtime.attach(goal_run.name, "01", gate_run.name, "gate", "gate 已完成")

    state = _read_json(goal_run / "goal-state.json")
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["completion_eligible"] is False
    assert "gate_result_changed_files_mismatch" in evidence["validation_summary"]
    with pytest.raises(ValueError, match="缺少可完成证据"):
        runtime.checkpoint_done(goal_run.name, "01", note="不应放行")


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        ("failed_state", "source_reflect_not_success"),
        ("tampered_reflection", "reflection_hash_mismatch"),
    ],
)
def test_goal_gate_evidence_revalidates_reflect_integrity(
    tmp_path: Path,
    mutation: str,
    expected_issue: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    workspace.mkdir(exist_ok=True)
    test_log = workspace / "tests.log"
    test_log.write_text("1 passed\n", encoding="utf-8")
    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)
    assert _read_json(gate_run / "state.json")["status"] == "success"
    assert _read_json(gate_run / "gate-result.json")["recommendation"] == "self-check"

    if mutation == "failed_state":
        state_path = reflect_run / "state.json"
        reflect_state = _read_json(state_path)
        reflect_state["status"] = "failed"
        _write_json(state_path, reflect_state)
    else:
        reflect_run.joinpath("reflection.md").write_text(
            "# forged reflection\n",
            encoding="utf-8",
        )

    runtime = GoalRuntime(workspace)
    goal_run = runtime.start(repo, _goal_text(), "test", None)
    runtime.step(goal_run.name)
    runtime.attach(goal_run.name, "01", gate_run.name, "gate", "gate 曾通过")

    state = _read_json(goal_run / "goal-state.json")
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["completion_eligible"] is False
    assert expected_issue in evidence["validation_summary"]
    with pytest.raises(ValueError, match="缺少可完成证据"):
        runtime.checkpoint_done(goal_run.name, "01", note="reflect 已失效")


def test_goal_accepts_legacy_gate_result_without_embedded_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    workspace.mkdir(exist_ok=True)
    test_log = workspace / "tests.log"
    test_log.write_text("1 passed\n", encoding="utf-8")
    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)
    gate_result_path = gate_run / "gate-result.json"
    gate_result = _read_json(gate_result_path)
    for key in ("run_id", "repo_path", "source_run"):
        gate_result.pop(key)
    _write_json(gate_result_path, gate_result)

    runtime = GoalRuntime(workspace)
    goal_run = runtime.start(repo, _goal_text(), "test", None)
    runtime.step(goal_run.name)
    runtime.attach(goal_run.name, "01", gate_run.name, "gate", "legacy gate")

    state = _read_json(goal_run / "goal-state.json")
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["validated"] is True
    assert "artifact_integrity=valid" in evidence["validation_summary"]
    assert "gate_result_run_id_mismatch" not in evidence["validation_summary"]
    assert "gate_result_repo_mismatch" not in evidence["validation_summary"]
    assert "gate_result_source_mismatch" not in evidence["validation_summary"]


@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_goal_complete_rejects_missing_or_tampered_checkpoint_json(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    child_loop = _create_successful_loop_run(workspace, repo)
    runtime = GoalRuntime(workspace)
    goal_run = runtime.start(repo, _goal_text(), "test", None)
    runtime.step(goal_run.name)
    runtime.attach(goal_run.name, "01", child_loop.name, "loop", "loop 已通过")
    runtime.checkpoint_done(goal_run.name, "01", note="checkpoint 已完成")
    evidence_path = goal_run / "checkpoints" / "01" / "checkpoint-evidence.json"
    if mutation == "missing":
        evidence_path.unlink()
    else:
        evidence = _read_json(evidence_path)
        evidence["completed_note"] = "forged note"
        _write_json(evidence_path, evidence)

    with pytest.raises(ValueError, match="checkpoint 01 evidence JSON"):
        runtime.complete(goal_run.name, "目标完成")


def test_goal_finish_evidence_rejects_tampered_summary_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    child_loop = _create_successful_loop_run(workspace, repo)
    FinishRuntime(workspace).run(child_loop.name)
    summary_path = child_loop / "finish-summary.json"
    summary = _read_json(summary_path)
    summary["run_id"] = "forged-loop-run"
    _write_json(summary_path, summary)

    runtime = GoalRuntime(workspace)
    goal_run = runtime.start(repo, _goal_text(), "test", None)
    runtime.step(goal_run.name)
    runtime.attach(goal_run.name, "01", child_loop.name, "finish", "finish 已生成")

    state = _read_json(goal_run / "goal-state.json")
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["completion_eligible"] is False
    assert "finish_run_id_mismatch" in evidence["validation_summary"]


def _create_successful_loop_run(workspace: Path, repo: Path) -> Path:
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('freshness verification passed')\"",
                "  max_commands: 1",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", ".vega.yaml"],
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
            "add verification config",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
    )
    return runtime.start(
        BriefInput(
            mode="feature",
            text="生成可校验的新鲜证据",
            source="test",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=True,
    )


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


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256_json(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _goal_text() -> str:
    return "\n".join(
        [
            "# Goal",
            "",
            "Objective: 验证证据身份",
            "",
            "Success conditions:",
            "- 所有 checkpoint 证据保持一致",
        ]
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_failed_gate_run(run_dir: Path, expected_issue: str) -> None:
    state = _read_json(run_dir / "state.json")
    result = _read_json(run_dir / "gate-result.json")
    trace = _read_jsonl(run_dir / "trace.jsonl")
    eval_text = (run_dir / "eval.md").read_text(encoding="utf-8")

    assert all((run_dir / name).is_file() for name in GATE_ARTIFACTS)
    assert state["status"] == "failed"
    assert state["current_step"] == "risk_evaluation_failed"
    assert state["artifacts"] == GATE_ARTIFACTS
    assert result["status"] == "failed"
    assert result["code"] == "risk_evaluation_failed"
    assert expected_issue in result["diagnostic"]
    assert expected_issue in eval_text
    assert trace[-1]["event"] == "run_finished"
    assert trace[-1]["status"] == "failed"
