

import json
import re
import subprocess
from pathlib import Path

import pytest

from vega.execution_control import (
    RunnerExecutionContext,
)
from vega.experimental.goal_runtime import GoalRuntime
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import (
    BriefInput,
)
from vega.reflect_runtime import ReflectRuntime
from vega.run_status import run_status_payload
from vega.runner import RunnerResult

_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_PATTERN.sub("", value)


def _write_goal_file(tmp_path: Path) -> Path:
    goal_file = tmp_path / "goal.md"
    goal_file.write_text(
        "\n".join(
            [
                "# Goal",
                "",
                "Objective: 分阶段收口 Vega goal 状态层。",
                "",
                "Non-goals:",
                "- 不调用 worker",
                "- 不自动改代码",
                "- 不自动 commit",
                "",
                "Success conditions:",
                "- pytest 通过",
                "- ruff 通过",
            ]
        ),
        encoding="utf-8",
    )
    return goal_file


class StaticRunner:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "sandbox": sandbox,
                "timeout_seconds": timeout_seconds,
                "execution_context": execution_context,
            }
        )
        output = self.outputs.pop(0) if self.outputs else "{}"
        return RunnerResult(status="success", output=output, command=["fake-runner"])


class TrackedChangeRunner(StaticRunner):
    """模拟 worker 产生可归因的 tracked diff，供 auto 主链测试使用。"""

    def __init__(self, outputs: list[str]) -> None:
        super().__init__(outputs)
        self.change_count = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        self.change_count += 1
        readme = repo_path / "README.md"
        readme.write_text(
            f"{readme.read_text(encoding='utf-8').rstrip()}\n"
            f"worker tracked change {self.change_count}\n",
            encoding="utf-8",
            newline="\n",
        )
        return result


def _create_successful_loop_run(workspace: Path, repo_dir: Path) -> Path:
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('goal verification passed')\"",
                "  max_commands: 1",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add goal verification")
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
    )
    return runtime.start(
        BriefInput(
            mode="feature",
            text="为 Goal checkpoint 生成可校验 loop 证据",
            source="test",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=True,
    )


def _init_clean_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    repo_dir.joinpath("AGENTS.md").write_text(
        "# AGENTS.md\n\n- 测试必须说明结果。\n",
        encoding="utf-8",
        newline="\n",
    )
    repo_dir.joinpath("README.md").write_text("# Demo\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_changed_git_repo(repo_dir: Path) -> None:
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )


def _commit_repo_paths(repo_dir: Path, *paths: str, message: str = "test update") -> None:
    subprocess.run(
        ["git", "add", "--", *paths],
        cwd=repo_dir,
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
            message,
        ],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )



def test_goal_step_only_writes_checkpoint_plan_without_worker_or_repo_changes(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    before_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    goal_file = _write_goal_file(tmp_path)
    run_dir = GoalRuntime(tmp_path).start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)

    stepped = GoalRuntime(tmp_path).step(run_dir.name)

    assert stepped == run_dir
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "running"
    assert state["current_step"] == "checkpoint_planned"
    assert state["checkpoint_count"] == 1
    checkpoint_plan = run_dir / "checkpoints" / "01" / "checkpoint-plan.md"
    assert checkpoint_plan.exists()
    assert not run_dir.joinpath("worker-prompt.md").exists()
    assert not run_dir.joinpath("loop-run.txt").exists()
    after_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after_status == before_status


def test_goal_attach_records_checkpoint_evidence_without_child_execution(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, note="人工复盘已完成")

    attached = runtime.attach(
        run_dir.name,
        checkpoint="01",
        child_run=reflect_run.name,
        evidence_type="reflect",
        note="人工复盘已完成",
    )

    state = json.loads(attached.joinpath("goal-state.json").read_text(encoding="utf-8"))
    checkpoint = state["checkpoint_records"][0]
    assert checkpoint["checkpoint"] == "01"
    assert checkpoint["refs"][0]["run"] == reflect_run.name
    assert checkpoint["refs"][0]["type"] == "reflect"
    assert checkpoint["refs"][0]["note"] == "人工复盘已完成"
    assert checkpoint["refs"][0]["validated"] is True
    assert checkpoint["refs"][0]["completion_eligible"] is False
    assert attached.joinpath("checkpoints", "01", "checkpoint-evidence.json").exists()
    assert not attached.joinpath("worker-prompt.md").exists()


def test_goal_finish_evidence_requires_ready_to_commit_summary(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    FinishRuntime(tmp_path).run(child_loop.name)

    runtime.attach(run_dir.name, "01", child_loop.name, "finish", "finish 已生成")

    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["kind"] == "loop"
    assert evidence["completion_eligible"] is True
    assert "ready_to_commit" in evidence["validation_summary"]


def test_goal_finish_evidence_rejects_forged_valid_summary_after_artifact_tamper(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    FinishRuntime(tmp_path).run(child_loop.name)

    child_loop.joinpath("iterations", "01", "review-verdict.json").unlink()
    summary_path = child_loop / "finish-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["finish_status"] = "ready_to_commit"
    summary["artifact_integrity"]["valid"] = True
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    runtime.attach(run_dir.name, "01", child_loop.name, "finish", "finish 已生成")

    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["completion_eligible"] is False
    assert "finish_artifact_integrity_mismatch" in evidence["validation_summary"]
    assert "finish_status_mismatch" in evidence["validation_summary"]



def test_goal_rejects_parallel_checkpoint_and_missing_evidence(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    try:
        runtime.step(run_dir.name)
    except ValueError as exc:
        assert "尚未完成" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("同一时间只能存在一个 active checkpoint")

    try:
        runtime.checkpoint_done(run_dir.name, "01", note="没有证据")
    except ValueError as exc:
        assert "没有挂载任何证据" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("没有证据的 checkpoint 不能完成")


def test_goal_attach_validates_child_run_repo_and_kind(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    other_repo = tmp_path / "other-repo"
    _init_changed_git_repo(repo_dir)
    _init_changed_git_repo(other_repo)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    try:
        runtime.attach(run_dir.name, "01", "missing-run", "loop")
    except FileNotFoundError as exc:
        assert "run 不存在" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("不存在的 child run 不能作为证据")

    reflect_run = ReflectRuntime(tmp_path).run(other_repo, note="其他仓库证据")
    try:
        runtime.attach(run_dir.name, "01", reflect_run.name, "reflect")
    except ValueError as exc:
        assert "不属于同一仓库" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("其他仓库的 child run 不能挂载")

    same_repo_reflect = ReflectRuntime(tmp_path).run(repo_dir, note="同仓库复盘")
    try:
        runtime.attach(run_dir.name, "01", same_repo_reflect.name, "review")
    except ValueError as exc:
        assert "证据类型与 child run 不匹配" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("证据类型必须与 child run kind 一致")


def test_goal_manual_evidence_requires_explicit_override_and_becomes_immutable(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    evidence_file = repo_dir / "manual-check.md"
    evidence_file.write_text("# 人工验证\n\n- 已完成验收。\n", encoding="utf-8")
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    runtime.attach(
        run_dir.name,
        "01",
        str(evidence_file),
        "manual",
        "人工检查验收记录",
    )

    try:
        runtime.checkpoint_done(run_dir.name, "01", note="人工确认")
    except ValueError as exc:
        assert "--allow-manual-evidence" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("manual evidence 必须显式 override")

    runtime.checkpoint_done(
        run_dir.name,
        "01",
        note="人工确认 manual evidence 足以完成本阶段",
        allow_manual_evidence=True,
    )
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["checkpoint_records"][0]["completion_mode"] == "manual_override"

    try:
        runtime.attach(run_dir.name, "01", str(evidence_file), "manual", "重复证据")
    except ValueError as exc:
        assert "证据不可再修改" in str(exc) or "状态不允许 attach" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("完成后的 checkpoint 证据必须不可变")


def test_goal_manual_evidence_rejects_sensitive_path_without_persisting_it(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    secret = "sk-manual-secret-123456"
    sensitive_file = repo_dir / ".env"
    sensitive_file.write_text(f"API_KEY={secret}\n", encoding="utf-8")
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    with pytest.raises(ValueError, match="environment_file"):
        runtime.attach(
            run_dir.name,
            "01",
            str(sensitive_file),
            "manual",
            "人工检查敏感文件",
        )

    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in run_dir.rglob("*")
        if path.is_file()
    )
    assert str(sensitive_file) not in persisted
    assert secret not in persisted
    assert not run_dir.joinpath("checkpoints", "01", "checkpoint-evidence.json").exists()



def test_goal_complete_revalidates_checkpoint_evidence(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    runtime.attach(run_dir.name, "01", child_loop.name, "loop", "loop 已通过")
    runtime.checkpoint_done(run_dir.name, "01", note="阶段完成")
    child_state_path = child_loop / "state.json"
    child_state = json.loads(child_state_path.read_text(encoding="utf-8"))
    child_state["status"] = "needs_human"
    child_state_path.write_text(json.dumps(child_state, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="checkpoint 完成证据已失效"):
        runtime.complete(run_dir.name, "准备完成 goal")

    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "checkpoint_done"
    assert not run_dir.joinpath("goal-final-report.md").exists()



def test_goal_recover_turns_running_goal_into_needs_human(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    recovered = runtime.recover(run_dir.name, "CLI 中断")

    state = json.loads(recovered.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "recovered"
    assert recovered.joinpath("recovery-report.md").exists()
    assert "CLI 中断" in recovered.joinpath("recovery-report.md").read_text(encoding="utf-8")



def test_goal_status_highlights_latest_checkpoint_plan(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    payload = run_status_payload(tmp_path, run_dir.name)
    artifacts = "\n".join(payload["key_artifacts"])

    assert payload["kind"] == "goal"
    assert "checkpoints\\01\\checkpoint-plan.md" in artifacts or (
        "checkpoints/01/checkpoint-plan.md" in artifacts
    )


def _review_json(verdict: str) -> str:
    findings = []
    if verdict == "request_changes":
        findings = [
            {
                "severity": "major",
                "file": "README.md",
                "line": 1,
                "title": "README 缺少验证说明",
                "evidence": "diff 未体现测试或验证结果。",
                "recommendation": "补充验证说明或测试日志。",
            }
        ]
    return json.dumps(
        {
            "verdict": verdict,
            "summary": "测试 reviewer 结论",
            "findings": findings,
            "reviewed_files": ["README.md"],
            "checked_items": ["需求覆盖", "测试覆盖"],
        },
        ensure_ascii=False,
    )
