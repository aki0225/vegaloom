from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vega.execution_control import (  # noqa: E402
    ExecutionLease,
    RunnerExecutionContext,
    request_stop_for_run,
    run_owned_process,
)
from vega.finish_runtime import FinishRuntime  # noqa: E402
from vega.gate_runtime import GateRuntime  # noqa: E402
from vega.experimental.goal_runtime import GoalRuntime  # noqa: E402
from vega.loop_runtime import LoopAutomationRuntime  # noqa: E402
from vega.models import BriefInput  # noqa: E402
from vega.project_config import check_project_config  # noqa: E402
from vega.reflect_runtime import ReflectRuntime  # noqa: E402
from vega.runner import RunnerResult  # noqa: E402
from vega.run_utils import create_run_dir  # noqa: E402
from vega.run_status import latest_run_dir, run_status_payload  # noqa: E402


@dataclass
class EvalCase:
    name: str
    success: bool
    reason: str
    artifacts: list[str] = field(default_factory=list)


class StaticReviewer:
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
        self.calls += 1
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "dogfood 静态 reviewer 通过。",
                    "findings": [],
                    "checked_items": ["dogfood"],
                },
                ensure_ascii=False,
            ),
            command=["static-reviewer"],
        )


class StaticWorker:
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
        self.calls += 1
        return RunnerResult(status="success", output="dogfood worker completed", command=["static-worker"])


class TrackedWorker:
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
        self.calls += 1
        readme = repo_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "\nDogfood tracked change.\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="dogfood worker created tracked README diff",
            command=["tracked-worker"],
        )


class PollutingWorker:
    def __init__(self, file_count: int) -> None:
        self.file_count = file_count

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        for index in range(self.file_count):
            repo_path.joinpath(f"worker-noise-{index:03d}.tmp").write_text(
                "worker noise\n",
                encoding="utf-8",
            )
        return RunnerResult(status="success", output="pollution created", command=["polluting-worker"])


def _case_registry() -> list[tuple[str, object]]:
    return [
        ("core_loop_without_memory", case_core_loop_without_memory),
        ("explicit_memory_lesson", case_explicit_memory_lesson),
        ("config_check_invalid_verification", case_config_check),
        ("execution_control", case_execution_control),
        ("workspace_pollution_guard", case_workspace_pollution_guard),
        ("prompt_budget_guard", case_prompt_budget_guard),
        ("large_scope_gate", case_large_scope_gate),
        ("goal_p0_lifecycle", case_goal_p0_lifecycle),
    ]


def select_case_names(requested_names: list[str], available_names: list[str]) -> list[str]:
    selected_names = list(dict.fromkeys(requested_names)) or list(available_names)
    unknown_cases = [name for name in selected_names if name not in available_names]
    if unknown_cases:
        raise ValueError(f"未知 dogfood case：{', '.join(unknown_cases)}")
    return selected_names


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Vega 轻量 dogfood eval。")
    parser.add_argument(
        "--runner",
        choices=["none"],
        default="none",
        help="当前 dogfood 使用确定性本地 fake runner，避免默认启动真实 Codex 会话。",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=PROJECT_ROOT,
        help="Vega workspace，临时仓库会创建在该目录 runs/ 下。",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="只运行指定 dogfood case；可重复传入。默认按既定顺序运行全部 case。",
    )
    args = parser.parse_args()

    case_registry = _case_registry()
    case_by_name = dict(case_registry)
    try:
        selected_names = select_case_names(
            args.case,
            [name for name, _ in case_registry],
        )
    except ValueError as exc:
        parser.error(str(exc))

    workspace = args.workspace.resolve()
    _, base_dir = create_run_dir(
        workspace,
        f"dogfood-eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    )

    cases = [case_by_name[name](workspace, base_dir) for name in selected_names]
    summary = {
        "base_dir": str(base_dir),
        "runner": args.runner,
        "success_count": sum(1 for case in cases if case.success),
        "case_count": len(cases),
        "cases": [case.__dict__ for case in cases],
    }
    (base_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (base_dir / "EVAL-REPORT.md").write_text(render_report(summary), encoding="utf-8")
    print(f"dogfood eval 完成：{base_dir}")
    print(f"成功：{summary['success_count']}/{summary['case_count']}")
    return 0 if summary["success_count"] == summary["case_count"] else 1


def case_core_loop_without_memory(workspace: Path, base_dir: Path) -> EvalCase:
    case_workspace = base_dir / "core-loop-workspace"
    case_workspace.mkdir()
    repo = base_dir / "core-loop-repo"
    init_repo(
        repo,
        verification_command="python -c \"print('core loop verification passed')\"",
    )
    ledger_path = case_workspace / "memory" / "ledger.jsonl"
    runtime = LoopAutomationRuntime(
        case_workspace,
        worker_runner=TrackedWorker(),
        reviewer_runner=StaticReviewer(),
        timeout_seconds=60,
    )
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="验证普通 bug loop 不依赖 Memory Proposal 也能完成。",
            source="dogfood",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=True,
    )
    FinishRuntime(case_workspace).run(run_dir.name)
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    finish_summary = json.loads((run_dir / "finish-summary.json").read_text(encoding="utf-8"))
    run_dirs = [path for path in (case_workspace / "runs").iterdir() if path.is_dir()]
    memory_artifacts = [str(path / "memory-proposals.jsonl") for path in run_dirs if (path / "memory-proposals.jsonl").exists()]
    success = (
        state.get("status") == "success"
        and state.get("memory_proposals") == []
        and finish_summary.get("finish_status") == "ready_to_commit"
        and finish_summary.get("memory_proposals") == []
        and not memory_artifacts
        and not ledger_path.exists()
        and (run_dir / "final-report.md").exists()
        and (run_dir / "finish-report.md").exists()
    )
    return EvalCase(
        name="core_loop_without_memory",
        success=success,
        reason=(
            "bug loop、隔离 review 和 finish 在零 Memory Proposal 时完整通过"
            if success
            else f"无 Memory 主流程失败：status={state.get('status')}, artifacts={memory_artifacts}"
        ),
        artifacts=[str(run_dir), str(run_dir / "finish-summary.json")],
    )


def case_explicit_memory_lesson(workspace: Path, base_dir: Path) -> EvalCase:
    case_workspace = base_dir / "explicit-memory-workspace"
    case_workspace.mkdir()
    repo = base_dir / "explicit-memory-repo"
    init_repo(repo)
    write_text(
        repo / ".vega.yaml",
        "version: 1\nmemory:\n  default_tags:\n    - dogfood-lesson\n",
    )
    write_text(repo / "README.md", "# Dogfood Repo\n\nchanged\n")
    lesson = "README 文档结构变化后必须重新执行链接检查，避免目录锚点失效。"
    run_dir = ReflectRuntime(case_workspace).run(
        repo,
        note="验证显式经验候选",
        lesson=lesson,
    )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    proposal_lines = [
        json.loads(line)
        for line in (run_dir / "memory-proposals.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    proposal = proposal_lines[0] if proposal_lines else {}
    success = (
        state.get("status") == "success"
        and len(state.get("memory_proposals", [])) == 1
        and len(proposal_lines) == 1
        and proposal.get("content") == lesson
        and proposal.get("type") == "lesson_candidate"
        and "dogfood-lesson" in proposal.get("tags", [])
        and "memory_proposal_written" in trace_text
        and not (case_workspace / "memory" / "ledger.jsonl").exists()
    )
    return EvalCase(
        name="explicit_memory_lesson",
        success=success,
        reason=(
            "只有显式 lesson 生成候选，且未自动写入长期 ledger"
            if success
            else "显式经验候选的内容、标签或写入边界不符合预期"
        ),
        artifacts=[str(run_dir / "memory-proposals.jsonl"), str(run_dir / "trace.jsonl")],
    )


def case_config_check(workspace: Path, base_dir: Path) -> EvalCase:
    repo = base_dir / "config-check-repo"
    init_repo(repo)
    write_text(
        repo / ".vega.yaml",
        "version: 1\nverification:\n  commands:\n    - python -c \\\n",
    )
    result = check_project_config(repo)
    success = result.status == "failed" and any(
        issue.code == "truncated_verification_command" for issue in result.issues
    )
    case_dir = base_dir / "config-check"
    case_dir.mkdir()
    write_text(case_dir / "config-check.json", result.model_dump_json(indent=2) + "\n")
    return EvalCase(
        name="config_check_invalid_verification",
        success=success,
        reason="坏验证命令能被预检拦截" if success else "坏验证命令未被预检拦截",
        artifacts=[str(case_dir / "config-check.json")],
    )


def case_execution_control(workspace: Path, base_dir: Path) -> EvalCase:
    case_dir = base_dir / "execution-control"
    case_dir.mkdir()
    context = RunnerExecutionContext(
        execution_dir=case_dir / "executions" / "worker",
        run_id=case_dir.name,
        step="worker",
        iteration=1,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    holder: dict[str, object] = {}
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=case_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def run_controlled() -> None:
        holder["result"] = run_owned_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            "",
            case_dir,
            20,
            context,
        )

    thread = threading.Thread(target=run_controlled)
    thread.start()
    try:
        _wait_for_execution_child(context.execution_dir / "execution.json")
        request_stop_for_run(case_dir, "dogfood stop request")
        thread.join(timeout=10)
        result = holder.get("result")
        lease = ExecutionLease.model_validate_json(
            context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
        )
        success = (
            not thread.is_alive()
            and getattr(result, "status", None) == "stopped"
            and lease.status == "stopped"
            and unrelated.poll() is None
            and context.execution_dir.joinpath("stop-request.json").exists()
        )
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=5)
        if thread.is_alive():
            request_stop_for_run(case_dir, "dogfood 清理")
            thread.join(timeout=5)

    return EvalCase(
        name="execution_control",
        success=success,
        reason=(
            "stop request 只停止 recorded owned process，heartbeat 和 execution 证据完整"
            if success
            else "execution control 未能安全停止 owned process"
        ),
        artifacts=[str(case_dir)],
    )


def case_workspace_pollution_guard(workspace: Path, base_dir: Path) -> EvalCase:
    repo = base_dir / "pollution-repo"
    init_repo(repo)
    write_text(
        repo / ".vega.yaml",
        "\n".join(
            [
                "version: 1",
                "budget:",
                "  max_new_files: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('verification should not run')\"",
            ]
        )
        + "\n",
    )
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=PollutingWorker(file_count=3),
        reviewer_runner=StaticReviewer(),
        timeout_seconds=60,
    )
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="模拟 worker 生成过多临时文件时应停止。",
            source="dogfood",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
    )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    iteration = state["iterations"][0] if state.get("iterations") else {}
    success = (
        state.get("status") == "needs_human"
        and iteration.get("workspace_status") == "failed"
        and (run_dir / "iterations" / "01" / "workspace-check.md").exists()
    )
    return EvalCase(
        name="workspace_pollution_guard",
        success=success,
        reason="worker 污染能在 verification/review 前停止" if success else "worker 污染未被正确拦截",
        artifacts=[str(run_dir)],
    )


def case_prompt_budget_guard(workspace: Path, base_dir: Path) -> EvalCase:
    repo = base_dir / "prompt-budget-repo"
    init_repo(repo)
    write_text(
        repo / ".vega.yaml",
        "version: 1\nprompt_budget:\n  worker_max_chars: 1000\n",
    )
    worker = StaticWorker()
    reviewer = StaticReviewer()
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
        timeout_seconds=30,
    )
    run_dir = runtime.start(
        BriefInput(
            mode="feature",
            text="为 dogfood README 增加一段使用说明，但 prompt 超预算时不得启动 worker。",
            source="dogfood",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    iteration_dir = run_dir / "iterations" / "01"
    success = (
        state["status"] == "needs_human"
        and state["current_step"] == "worker_context_budget"
        and worker.calls == 0
        and reviewer.calls == 0
        and (iteration_dir / "worker-prompt-metrics.json").exists()
        and (iteration_dir / "worker-context-budget-report.md").exists()
    )
    return EvalCase(
        name="prompt_budget_guard",
        success=success,
        reason="prompt 超预算时未启动 worker/reviewer" if success else "prompt 预算门禁未按预期停止",
        artifacts=[
            str(iteration_dir / "worker-prompt-metrics.json"),
            str(iteration_dir / "worker-context-budget-report.md"),
            str(run_dir / "state.json"),
        ],
    )


def case_large_scope_gate(workspace: Path, base_dir: Path) -> EvalCase:
    repo = base_dir / "large-scope-repo"
    init_repo(repo)
    write_text(
        repo / ".vega.yaml",
        "\n".join(
            [
                "version: 1",
                "budget:",
                "  max_changed_files: 1",
                "budget_profiles:",
                "  refactor:",
                "    max_changed_files: 10",
            ]
        )
        + "\n",
    )
    write_text(repo / "a.txt", "a\n")
    write_text(repo / "b.txt", "b\n")
    reflect_run = ReflectRuntime(workspace).run(repo, note="dogfood 大范围变更")
    default_gate = GateRuntime(workspace).run(repo, reflect_run.name)
    scoped_gate = GateRuntime(workspace).run(repo, reflect_run.name, scope_profile="refactor")
    default_codes = _gate_codes(default_gate)
    scoped_codes = _gate_codes(scoped_gate)
    success = "budget_changed_files" in default_codes and "budget_changed_files" not in scoped_codes
    return EvalCase(
        name="large_scope_gate",
        success=success,
        reason="scope profile 能放宽预算但保留 gate 证据" if success else "scope profile 未按预期影响预算",
        artifacts=[str(reflect_run), str(default_gate), str(scoped_gate)],
    )


def case_goal_p0_lifecycle(workspace: Path, base_dir: Path) -> EvalCase:
    repo = base_dir / "goal-p0-repo"
    init_repo(
        repo,
        verification_command="python -c \"print('goal loop verification passed')\"",
    )
    write_text(
        repo / "README.md",
        "# Dogfood Repo\n\nGoal P0 lifecycle evidence.\n",
    )
    run(["git", "add", "--", "README.md"], repo)
    run(
        [
            "git",
            "-c",
            "user.email=dogfood@example.com",
            "-c",
            "user.name=Dogfood",
            "commit",
            "-m",
            "add goal fixture",
        ],
        repo,
    )
    ledger_path = workspace / "memory" / "ledger.jsonl"
    before_ledger = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else None
    runtime = GoalRuntime(workspace)
    goal_text = "\n".join(
        [
            "# Goal",
            "",
            "Objective: dogfood 验证 Goal P0 生命周期。",
            "",
            "Non-goals:",
            "- 不调用 worker",
            "- 不自动改代码",
            "- 不自动 commit",
            "",
            "Success conditions:",
            "- 生成 checkpoint plan",
            "- status/latest 能识别 goal run",
        ]
    )
    run_dir = runtime.start(repo, goal_text, "dogfood", "refactor")
    runtime.step(run_dir.name)
    child_loop = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedWorker(),
        reviewer_runner=StaticReviewer(),
        timeout_seconds=60,
    ).start(
        BriefInput(
            mode="feature",
            text="为 Goal P0 dogfood 生成成功 loop 证据",
            source="dogfood-goal",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=True,
    )
    before_status = _git_status(repo)
    runtime.attach(run_dir.name, "01", child_loop.name, "loop", "自动 loop 已通过")
    runtime.checkpoint_done(run_dir.name, "01", "checkpoint 01 完成")
    runtime.pause(run_dir.name, "dogfood 暂停验证")
    runtime.resume(run_dir.name)
    runtime.step(run_dir.name)
    runtime.recover(run_dir.name, "dogfood 模拟 CLI 中断")

    stopped_run = runtime.start(repo, goal_text, "dogfood-stop", None)
    runtime.stop(stopped_run.name, "dogfood 停止验证")

    completed_run = runtime.start(repo, goal_text, "dogfood-complete", "refactor")
    runtime.step(completed_run.name)
    runtime.attach(completed_run.name, "01", child_loop.name, "loop", "复用已验证 child loop")
    runtime.checkpoint_done(completed_run.name, "01", "checkpoint 完成")
    runtime.complete(completed_run.name, "success conditions 已由 dogfood 断言核对")

    state = json.loads((run_dir / "goal-state.json").read_text(encoding="utf-8"))
    completed_state = json.loads((completed_run / "goal-state.json").read_text(encoding="utf-8"))
    trace_text = (run_dir / "goal-trace.jsonl").read_text(encoding="utf-8")
    latest_goal = latest_run_dir(workspace, "goal")
    status_payload = run_status_payload(workspace, run_dir.name)
    required_artifacts = [
        "state.json",
        "goal-state.json",
        "goal-trace.jsonl",
        "goal-contract.md",
        "goal-contract.json",
        "progress.md",
        "checkpoints/01/checkpoint-plan.md",
        "checkpoints/01/checkpoint-evidence.json",
        "checkpoints/01/checkpoint-report.md",
        "checkpoints/02/checkpoint-plan.md",
    ]
    missing_artifacts = [item for item in required_artifacts if not (run_dir / item).exists()]
    forbidden_artifacts = [
        "worker-prompt.md",
        "loop-run.txt",
        "memory-proposals.jsonl",
    ]
    forbidden_present = [item for item in forbidden_artifacts if (run_dir / item).exists()]
    success = (
        not missing_artifacts
        and not forbidden_present
        and _read_optional_text(ledger_path) == before_ledger
        and _git_status(repo) == before_status
        and state.get("status") == "needs_human"
        and state.get("current_step") == "recovered"
        and any(
            item.get("checkpoint") == "01" and item.get("status") == "done"
            for item in state.get("checkpoint_records", [])
        )
        and status_payload["kind"] == "goal"
        and status_payload["status"] == "needs_human"
        and latest_goal is not None
        and latest_goal.name == completed_run.name
        and (stopped_run / "stop-report.md").exists()
        and completed_state.get("status") == "success"
        and (completed_run / "goal-final-report.md").exists()
        and (completed_run / "goal-eval.md").exists()
        and all(item.startswith("PASS:") for item in completed_state.get("eval_results", []))
        and "goal_checkpoint_planned" in trace_text
        and "goal_checkpoint_evidence_attached" in trace_text
        and "goal_checkpoint_done" in trace_text
        and "goal_recovered" in trace_text
    )
    reason = "Goal P0 生命周期校验证据、收口完成态，且未在 child loop 已有 diff 基础上继续修改目标仓库"
    if not success:
        reason = (
            "Goal P0 生命周期验收失败："
            f"missing={missing_artifacts}, forbidden={forbidden_present}, "
            f"state={state.get('status')}/{state.get('current_step')}"
        )
    return EvalCase(
        name="goal_p0_lifecycle",
        success=success,
        reason=reason,
        artifacts=[str(run_dir), str(stopped_run), str(completed_run), str(child_loop)],
    )


def render_report(summary: dict) -> str:
    lines = [
        "# Dogfood Eval Report",
        "",
        f"- base dir：`{summary['base_dir']}`",
        f"- runner：`{summary['runner']}`",
        f"- 成功率：`{summary['success_count']}/{summary['case_count']}`",
        "",
        "## Cases",
        "",
    ]
    for case in summary["cases"]:
        badge = "PASS" if case["success"] else "FAIL"
        lines.extend(
            [
                f"### {case['name']}",
                "",
                f"- 结果：`{badge}`",
                f"- 原因：{case['reason']}",
                "- 产物：",
            ]
        )
        lines.extend(f"  - `{item}`" for item in case["artifacts"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def init_repo(
    repo: Path,
    *,
    verification_command: str | None = None,
) -> None:
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    write_text(repo / "README.md", "# Dogfood Repo\n")
    if verification_command is not None:
        write_text(
            repo / ".vega.yaml",
            "\n".join(
                [
                    "version: 1",
                    "verification:",
                    "  commands:",
                    f"    - {verification_command}",
                    "  max_commands: 1",
                    "",
                ]
            ),
        )
    run(["git", "init"], repo)
    run(["git", "config", "core.autocrlf", "false"], repo)
    run(["git", "add", "."], repo)
    run(["git", "-c", "user.email=dogfood@example.com", "-c", "user.name=Dogfood", "commit", "-m", "init"], repo)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        raise RuntimeError(f"命令失败：{' '.join(command)}\n{output}")


def _git_status(repo: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        raise RuntimeError(f"git status 失败：{output}")
    return result.stdout


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _gate_codes(run_dir: Path) -> set[str]:
    payload = json.loads((run_dir / "gate-result.json").read_text(encoding="utf-8"))
    return {reason["code"] for reason in payload["reasons"]}


def _wait_for_execution_child(path: Path, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            try:
                lease = ExecutionLease.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.02)
                continue
            if lease.child_pid is not None and lease.status == "running":
                return
        time.sleep(0.02)
    raise RuntimeError(f"等待 execution child 启动超时：{path}")


if __name__ == "__main__":
    raise SystemExit(main())
