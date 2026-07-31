from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .decision import DecisionStore
from .execution_feedback import render_owned_child_pid_line
from .execution_control import (
    ACTIVE_EXECUTION_STATUSES,
    ExecutionRecord,
    find_execution_records,
)
from .run_status_guidance import (
    classify_assist_initialization_status as _classify_init,
    initialization_next_steps as _initialization_next_steps,
    latest_iteration_file as _latest_iteration_file,
    verification_failure_next_steps as _verification_failure_next_steps,
)
from .run_utils import resolve_run_dir, resolve_runs_root


def latest_run_dir(workspace: Path, kind: str = "all") -> Path | None:
    runs_dir = resolve_runs_root(workspace)
    if runs_dir is None:
        return None
    candidates = _safe_run_dirs(runs_dir)
    if kind == "all":
        child_run_ids = _referenced_child_run_ids(candidates)
        roots = [path for path in candidates if path.name not in child_run_ids]
        if roots:
            candidates = roots
    if kind != "all":
        candidates = [path for path in candidates if _infer_kind(path) == kind or path.name.endswith(f"-{kind}")]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def render_run_status(workspace: Path, run: str) -> str:
    payload = run_status_payload(workspace, run)
    lines = [
        "# Run Status",
        "",
        f"- run：`{payload['run_id']}`",
        f"- 类型：`{payload['kind']}`",
        f"- 状态：`{payload['status']}`",
        f"- 当前步骤：`{payload['current_step']}`",
    ]
    if payload.get("repo_path"):
        lines.append(f"- 仓库：`{payload['repo_path']}`")
    if payload.get("risk"):
        lines.append(f"- 风险等级：`{payload['risk']}`")
    if payload.get("recommendation"):
        lines.append(f"- 建议：`{payload['recommendation']}`")
    if payload.get("decision_count"):
        lines.append(f"- 人工决策：`{payload['decision_count']}` 条")
    execution = payload.get("execution")
    if execution:
        lines.extend([
            f"- execution：`{execution['status']}` / `{execution['step']}`",
            render_owned_child_pid_line(
                execution["status"] in ACTIVE_EXECUTION_STATUSES, execution["child_pid"]
            ),
            f"- 最后心跳：`{execution['last_heartbeat']}`",
        ])
        if execution.get("termination_unconfirmed"):
            lines.append("- owned process tree：`终止未确认`")
    lines.extend(["", "## 下一步", ""])
    lines.extend(f"- {item}" for item in payload["next_steps"])
    lines.extend(["", "## 关键产物", ""])
    artifacts = payload["key_artifacts"]
    if artifacts:
        lines.extend(f"- `{item}`" for item in artifacts)
    else:
        lines.append("- 未识别关键产物。")
    return "\n".join(lines).rstrip() + "\n"


def run_status_payload(workspace: Path, run: str) -> dict[str, Any]:
    run_dir = resolve_run_dir(workspace, run)
    state = _classify_init(workspace, run_dir, _read_state(run_dir))
    kind = _infer_kind(run_dir, state)
    decisions = DecisionStore(run_dir).list()
    execution = _latest_execution_payload(run_dir)
    return {
        "run_id": run_dir.name,
        "run_dir": str(run_dir.resolve()),
        "kind": kind,
        "status": state.get("status", "unknown"),
        "current_step": state.get("current_step", "unknown"),
        "repo_path": state.get("repo_path"),
        "risk": state.get("risk"),
        "recommendation": state.get("recommendation"),
        "decision_count": len(decisions),
        "latest_decisions": [entry.model_dump() for entry in decisions[-3:]],
        "execution": execution,
        "next_steps": next_steps_for_run(workspace, run_dir, state, kind),
        "key_artifacts": key_artifacts_for_run(run_dir, state, kind),
    }


def next_steps_for_run(workspace: Path, run_dir: Path, state: dict[str, Any], kind: str | None = None) -> list[str]:
    run_kind = kind or _infer_kind(run_dir, state)
    execution = _latest_execution_payload(run_dir)
    if (
        run_kind in {"loop", "review"}
        and execution
        and execution.get("termination_unconfirmed")
    ):
        return [
            f"owned process tree 终止未确认；先读取 `{execution['path']}` 和对应错误报告。",
            "人工检查系统进程与目标仓库现场；当前 run 不允许重复 stop 或自动 recover。",
            "确认没有残留进程后，保留当前 run 作为证据，并从新的 run 继续工作。",
        ]
    if (
        run_kind in {"loop", "review"}
        and execution
        and execution["status"] in ACTIVE_EXECUTION_STATUSES
    ):
        return [
            f"当前 `{execution['step']}` 仍在运行，可继续等待并查看 `{execution['path']}`。",
            f"如需停止：`vega stop --run {run_dir.name} --reason \"...\"`。",
            "fresh execution 不能直接 recover；只有 heartbeat 过期、PID 消失或 execution 终态后才能接管。",
        ]
    if run_kind == "loop":
        return _loop_next_steps(run_dir, state)
    if run_kind == "reflect":
        return [
            f"生成隔离审查包：`vega review-pack --repo <repo> --run {run_dir.name}`",
            f"直接调用 reviewer：`vega review --repo <repo> --run {run_dir.name} --runner codex-exec`",
            "如果还没跑测试，先补测试日志后重新 reflect。",
        ]
    if run_kind == "review-pack":
        if state.get("current_step") == "context_budget":
            return [
                f"读取 `{run_dir / 'review-prompt-metrics.md'}` 和 `{run_dir / 'review-context-budget-report.md'}`。",
                "先缩小任务/diff/项目规则输入，或人工确认后调整 `.vega.yaml` 的 prompt_budget。",
                "不要在未检查缺失证据的情况下直接扩大预算并自动重跑。",
            ]
        return [
            f"调用隔离 reviewer：`vega review --repo <repo> --run {state.get('source_run', '<reflect_run>')} --runner codex-exec`",
            f"或手动复制 `{run_dir / 'review-prompt.md'}` 给干净 reviewer 会话。",
        ]
    if run_kind == "review":
        if state.get("current_step") == "context_budget":
            return [
                f"读取 `{run_dir / 'review-prompt-metrics.md'}` 和 `{run_dir / 'review-context-budget-report.md'}`。",
                "当前 reviewer 未启动；请缩小 review 输入或人工确认新的 prompt 预算后重跑。",
            ]
        if state.get("current_step") == "evidence_truncated":
            return [
                f"读取 `{run_dir / 'review-context.json'}` 确认被截断的 sections。",
                f"读取 `{run_dir / 'review-findings.md'}`；当前结果不能视为完整 approve。",
                "请缩小任务/diff 后重跑，或由人工检查完整证据。",
            ]
        verdict = state.get("verdict")
        if verdict == "approve":
            return ["reviewer 已通过；回到主会话整理交付结论，人工检查后再 commit。"]
        if verdict == "request_changes":
            return [
                f"读取 `{run_dir / 'review-findings.md'}`，按 findings 修复后重新 reflect + review。",
            ]
        return [
            f"读取 `{run_dir / 'review-runner-output.txt'}` 和 `{run_dir / 'review-findings.md'}`，人工判断或重跑 reviewer。",
        ]
    if run_kind == "gate":
        recommendation = state.get("recommendation")
        if recommendation == "self-check":
            return ["风险较低，可以主会话自检后整理交付；需要时仍可运行隔离 review。"]
        if recommendation == "isolated-review":
            return [
                f"建议运行隔离 reviewer：`vega review --repo <repo> --run {state.get('source_run', '<reflect_run>')} --runner codex-exec`。",
            ]
        return [
            "风险较高，建议人工确认影响面，再决定是否继续 auto loop 或提交。",
            f"如确认继续，请记录决策：`vega decision approve --run {run_dir.name} --type gate --reason \"...\" --ref gate-result.json`。",
        ]
    if run_kind == "brief":
        return [
            f"把 `{run_dir / 'agent-brief.md'}` 交给主会话 worker 实现。",
            "实现后运行：`vega reflect --repo <repo> --run <brief_run> --test-log <log>`。",
        ]
    if run_kind == "project-profile":
        return [f"把 `{run_dir / 'project-profile.md'}` 作为项目画像参考，继续生成 brief 或 loop。"]
    if run_kind == "change-plan":
        return [
            f"人工审查 `{run_dir / 'change-plan.md'}` 和 `{run_dir / 'scope-profile.md'}`。",
            f"如确认范围合理，记录决策：`vega decision approve --run {run_dir.name} --type custom --reason \"批准 scope\" --ref change-plan.md`。",
            "之后按 phase 拆成多个 `vega do` 或 `vega loop` 任务执行。",
        ]
    if run_kind == "goal":
        return _goal_next_steps(run_dir, state)
    return [
        "读取 report/review/eval 判断是否通过。",
        "如发现跨任务可复用经验，可通过 `vega reflect --lesson \"...\"` 显式生成候选。",
    ]


def key_artifacts_for_run(run_dir: Path, state: dict[str, Any], kind: str | None = None) -> list[str]:
    run_kind = kind or _infer_kind(run_dir, state)
    preferred = {
        "loop": [
            "worker-prompt.md",
            "worker-prompt-metrics.md",
            "worker-context-budget-report.md",
            "project-context.md",
            "final-report.md",
            "recovery-report.md",
            "eval.md",
        ],
        "reflect": ["diff-summary.md", "test-summary.md", "reflection.md", "eval.md"],
        "review-pack": [
            "review-pack.md",
            "review-prompt.md",
            "review-prompt-metrics.md",
            "review-context-budget-report.md",
            "project-context.md",
            "review-context.json",
            "eval.md",
        ],
        "review": [
            "review-findings.md",
            "review-verdict.json",
            "review-runner-output.txt",
            "review-prompt-metrics.md",
            "review-context-budget-report.md",
            "runner-error-report.md",
            "timeout-report.md",
            "stop-report.md",
            "eval.md",
        ],
        "gate": ["gate-report.md", "gate-result.json", "eval.md"],
        "brief": ["agent-brief.md", "project-context.md", "knowledge-context.md", "eval.md"],
        "project-profile": ["project-profile.md", "project-profile.json", "eval.md"],
        "change-plan": ["change-plan.md", "scope-profile.md", "phase-plan.md", "risk.md", "eval.md"],
        "goal": [
            "goal-contract.md",
            "progress.md",
            "goal-state.json",
            "goal-trace.jsonl",
            "goal-final-report.md",
            "goal-eval.md",
            "stop-report.md",
            "recovery-report.md",
        ],
    }.get(run_kind, ["report.md", "review.md", "eval.md"])
    result = [str((run_dir / item).resolve()) for item in preferred if (run_dir / item).exists()]
    if (run_dir / "decisions.jsonl").exists():
        result.append(str((run_dir / "decisions.jsonl").resolve()))
    if run_kind == "loop":
        for item in ["finish-report.md", "finish-summary.json"]:
            path = run_dir / item
            if path.exists():
                result.append(str(path.resolve()))
        # status 面向“下一步怎么做”，多轮 loop 时只展示最新一轮关键产物，避免三四轮后输出被历史文件淹没。
        result.extend(_latest_iteration_artifacts(run_dir, state))
    if run_kind == "review":
        for record in find_execution_records(run_dir):
            result.append(str(record.path.resolve()))
            for name in ["stop-request.json", "process-output.txt"]:
                path = record.path.parent / name
                if path.exists():
                    result.append(str(path.resolve()))
    if run_kind == "goal":
        result.extend(_latest_goal_artifacts(run_dir))
    return result


def _loop_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    status, current_step = state.get("status"), state.get("current_step")
    initialization_steps = _initialization_next_steps(run_dir, state)
    if initialization_steps is not None:
        return initialization_steps
    if (
        status == "needs_human"
        and current_step == "recovered_initialization_incomplete"
    ):
        return [
            f"读取 `{run_dir / 'recovery-report.md'}`，确认初始化中断位置。",
            "原始 brief 尚未绑定到 loop state，不能安全 continue。",
            "保留当前 run 作为中断证据，并从新的 run 重新开始任务。",
        ]
    if status == "needs_human" and current_step == "recovered":
        interruption = _latest_iteration_file(run_dir, "interruption-report.md")
        steps = [
            f"读取 `{run_dir / 'recovery-report.md'}`，确认中断原因和现场。",
        ]
        if interruption.is_file():
            steps.append(f"读取 `{interruption}`，确认被冻结的 iteration 与原执行步骤。")
        steps.extend(
            [
                "先人工检查目标仓库 `git status`，不要直接覆盖或清理未知文件。",
                f"如果工作区已有合理修复，再运行：`vega loop continue --repo <repo> --run {run_dir.name}`。",
            ]
        )
        return steps
    if status == "needs_human" and current_step in {"timed_out", "stopped"}:
        report_name = "timeout-report.md" if current_step == "timed_out" else "stop-report.md"
        report = _latest_iteration_file(run_dir, report_name)
        return [
            f"读取 `{report}` 和对应 execution.json，确认停止位置和目标仓库现场。",
            "先人工检查目标仓库 `git status`，不要自动回滚或覆盖未知文件。",
            f"如现场可继续，运行：`vega loop continue --repo <repo> --run {run_dir.name}`。",
        ]
    if status == "needs_human" and current_step in {"worker_error", "reviewer_error"}:
        report = _latest_iteration_file(run_dir, "runner-error-report.md")
        return [
            f"读取 `{report}`，确认 runner 错误和当前工作区现场。",
            "现有 diff 可能是部分完成结果，未经过完整 verification/review，不能直接视为可交付。",
            f"如部分改动可保留，补齐后运行：`vega loop continue --repo <repo> --run {run_dir.name}`。",
        ]
    if (
        status == "needs_human"
        and current_step == "verification_termination_unconfirmed"
    ):
        report = _latest_iteration_file(run_dir, "runner-error-report.md")
        return [
            f"读取 `{report}` 和 verification execution.json，确认进程树终止未确认的位置。",
            "人工检查系统进程与目标仓库现场；当前 run 不允许重复 stop 或自动 recover。",
            "确认没有残留进程后，保留当前 run 作为证据，并从新的 run 继续工作。",
        ]
    if status == "needs_human" and current_step == "worker_context_budget":
        metrics = _latest_iteration_file(run_dir, "worker-prompt-metrics.md")
        report = _latest_iteration_file(run_dir, "worker-context-budget-report.md")
        return [
            f"读取 `{metrics}` 和 `{report}`，确认主要上下文来源。",
            "缩小任务范围、精简项目规则，或人工确认后调整 `.vega.yaml` 的 prompt_budget。",
            "本轮 worker 未启动，目标仓库没有因为该 attempt 产生自动改动。",
        ]
    if status == "needs_human" and current_step == "project_policy_changed":
        report = _latest_iteration_file(run_dir, "project-policy-change-report.md")
        return [
            f"读取 `{report}`，人工确认 `.vega.yaml` / `.vega.yml` 是否属于本次任务。",
            "策略文件会影响验证、预算和 runner 行为；确认前不要继续自动 verification 或 reviewer。",
            "确认需要保留改动后，建议重新创建 loop，或人工完成后续验证和审查。",
        ]
    latest_iteration = _latest_iteration(state)
    if latest_iteration and latest_iteration.get("workspace_status") == "failed":
        workspace_check = _latest_iteration_file(run_dir, "workspace-check.md")
        fix_prompt = _latest_iteration_file(run_dir, "fix-prompt.md")
        return [
            f"工作区污染检查失败，先读取 `{workspace_check}`。",
            f"按 `{fix_prompt}` 清理或确认新增文件范围后，再运行：`vega loop continue --repo <repo> --run {run_dir.name}`。",
            "Vega 不会自动删除文件；请人工确认哪些文件属于需求产物。",
        ]
    verdict = latest_iteration.get("verdict") if latest_iteration else None
    if latest_iteration and latest_iteration.get("verification_status") == "failed":
        return _verification_failure_next_steps(run_dir, latest_iteration)
    if status == "success":
        if (run_dir / "finish-report.md").exists():
            return [
                f"读取 `{run_dir / 'finish-report.md'}` 作为最终交付与 commit 前 checklist。",
                "人工检查 diff 后再自行 commit；仅在明确有复用价值时再生成经验候选。",
            ]
        return [
            f"运行：`vega finish --run {run_dir.name}` 生成交付总结和 commit 前 checklist。",
            "人工检查 diff 后再自行 commit；仅在明确有复用价值时再生成经验候选。",
        ]
    if verdict == "request_changes":
        fix_prompt = _latest_iteration_file(run_dir, "fix-prompt.md")
        return [
            f"读取 `{fix_prompt}`，让主会话继续修复。",
            f"修完后再次运行：`vega loop continue --repo <repo> --run {run_dir.name}`；如已有外部日志再加 `--test-log <log>`。",
        ]
    if verdict == "needs_human":
        findings = _latest_iteration_file(run_dir, "review-findings.md")
        if (run_dir / "finish-report.md").exists():
            return [
                f"读取 `{run_dir / 'finish-report.md'}` 和 `{findings}`，人工判断是否重跑 reviewer 或继续修复。",
            ]
        return [
            f"读取 `{findings}` 和 `final-report.md`，人工判断是否重跑 reviewer 或继续修复。",
        ]
    return [f"读取 `{run_dir / 'state.json'}` 确认当前 loop 状态。"]


def _goal_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    status = state.get("status")
    if status == "created":
        return [
            f"人工审查 `{run_dir / 'goal-contract.md'}`。",
            f"确认后运行：`vega goal step --run {run_dir.name}` 生成第一个 checkpoint plan。",
        ]
    if status == "running":
        return [
            f"读取 `{run_dir / 'progress.md'}` 和最新 checkpoint plan。",
            f"完成人工执行后运行：`vega goal attach --run {run_dir.name} --checkpoint <n> --ref <child_run> --type <type>`。",
            f"证据挂载完成后运行：`vega goal checkpoint-done --run {run_dir.name} --checkpoint <n>`。",
        ]
    if status == "checkpoint_done":
        return [
            f"读取 `{run_dir / 'progress.md'}` 和最新 `checkpoint-report.md`。",
            f"如继续推进，运行：`vega goal step --run {run_dir.name}` 生成下一个 checkpoint plan。",
            f"如 success conditions 已满足，运行：`vega goal complete --run {run_dir.name} --note \"...\"`。",
            f"如放弃目标，运行：`vega goal stop --run {run_dir.name} --reason \"...\"`。",
        ]
    if status == "paused":
        return [
            f"如确认继续，运行：`vega goal resume --run {run_dir.name}`。",
            f"如方向变化，运行：`vega goal stop --run {run_dir.name} --reason \"...\"`。",
        ]
    if status == "needs_human":
        if state.get("current_step") == "completion_eval_failed":
            return [
                f"读取 `{run_dir / 'goal-eval.md'}` 和 `{run_dir / 'goal-final-report.md'}`。",
                "修复缺失产物或不可信 checkpoint 证据后，再重新执行 goal complete。",
            ]
        return [
            f"读取 `{run_dir / 'recovery-report.md'}` 和 `{run_dir / 'progress.md'}`。",
            "人工检查目标仓库和 checkpoint 产物，再决定 resume、stop 或重开 goal。",
        ]
    if status == "stopped":
        return [
            f"读取 `{run_dir / 'stop-report.md'}` 了解停止原因。",
            "该 goal 不再调度新的 checkpoint；如目标仍有效，建议重开新的 goal。",
        ]
    if status == "success":
        return [
            f"读取 `{run_dir / 'goal-final-report.md'}` 和 `{run_dir / 'goal-eval.md'}`。",
            "Goal 已完成；如需提交代码，仍应人工检查关联 child run 的 diff 和 finish 报告。",
        ]
    return [f"读取 `{run_dir / 'goal-state.json'}` 确认当前 goal 状态。"]


def _read_state(run_dir: Path) -> dict[str, Any]:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return {}
    try:
        raw = state_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"run `{run_dir.name}` 的 state.json 无法读取；已拒绝展示不完整状态。"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"run `{run_dir.name}` 的 state.json 无法解析；已拒绝降级展示损坏状态。"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"run `{run_dir.name}` 的 state.json 顶层必须是 JSON object；"
            "已拒绝降级展示损坏状态。"
        )
    stored_run_id = payload.get("run_id")
    if isinstance(stored_run_id, str) and stored_run_id != run_dir.name:
        raise ValueError(
            "state.json run_id 与 run 目录身份不一致；"
            "为避免展示错误证据链，已拒绝读取。"
        )
    return payload


def _read_state_for_selection(run_dir: Path) -> dict[str, Any]:
    try:
        return _read_state(run_dir)
    except ValueError:
        # latest 先选 run，再由 run_status_payload 对被选中的状态给出明确诊断。
        return {}


def _safe_run_dirs(runs_dir: Path) -> list[Path]:
    """只返回 runs 根目录下、具备状态文件的真实目录。"""
    root = runs_dir
    candidates: list[Path] = []
    try:
        entries = list(runs_dir.iterdir())
    except OSError:
        return candidates
    for path in entries:
        if not path.is_dir():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.parent != root:
            continue
        if not (
            (resolved / "state.json").is_file()
            or (resolved / "goal-state.json").is_file()
            or (resolved / "goal-contract.json").is_file()
        ):
            continue
        candidates.append(resolved)
    return candidates


def _referenced_child_run_ids(run_dirs: list[Path]) -> set[str]:
    referenced: set[str] = set()
    for run_dir in run_dirs:
        state = _read_state_for_selection(run_dir)
        brief_run = state.get("brief_run")
        if isinstance(brief_run, str):
            referenced.add(brief_run)
        for iteration in state.get("iterations") or []:
            if not isinstance(iteration, dict):
                continue
            for key in ("reflect_run", "review_run"):
                value = iteration.get(key)
                if isinstance(value, str):
                    referenced.add(value)
        for checkpoint in state.get("checkpoint_records") or []:
            if not isinstance(checkpoint, dict):
                continue
            for ref in checkpoint.get("refs") or []:
                if isinstance(ref, dict) and isinstance(ref.get("run"), str):
                    referenced.add(ref["run"])
    return referenced


def _infer_kind(run_dir: Path, state: dict[str, Any] | None = None) -> str:
    data = state if state is not None else _read_state_for_selection(run_dir)
    if "automation_mode" in data:
        return "loop"
    if (run_dir / "review-pack.md").exists():
        if (
            (run_dir / "review-verdict.json").exists()
            or data.get("verdict")
            or data.get("runner") not in {None, "none"}
        ):
            return "review"
        return "review-pack"
    if (run_dir / "gate-result.json").exists() or data.get("risk"):
        return "gate"
    if (run_dir / "goal-state.json").exists() or (run_dir / "goal-contract.json").exists():
        return "goal"
    if data.get("verdict") or (run_dir / "review-verdict.json").exists():
        return "review"
    if "source_run" in data and (run_dir / "reflection.md").exists():
        return "reflect"
    if "input_source" in data and (run_dir / "agent-brief.md").exists():
        return "brief"
    if run_dir.name.endswith("-project-profile"):
        return "project-profile"
    if run_dir.name.endswith("-change-plan") or (run_dir / "change-plan.md").exists():
        return "change-plan"
    return "engineering-change"


def _latest_iteration(state: dict[str, Any]) -> dict[str, Any]:
    iterations = state.get("iterations") or []
    if not iterations:
        return {}
    return iterations[-1]


def _latest_iteration_artifacts(run_dir: Path, state: dict[str, Any]) -> list[str]:
    latest = _latest_iteration(state)
    iteration_number = latest.get("iteration") if latest else state.get("current_iteration")
    if isinstance(iteration_number, int) and iteration_number > 0:
        iteration_dir = run_dir / "iterations" / f"{iteration_number:02d}"
        candidates = [
            run_dir / "recovery-report.md",
            iteration_dir / "interruption-report.md",
            iteration_dir / "fix-prompt.md",
            iteration_dir / "timeout-report.md",
            iteration_dir / "stop-report.md",
            iteration_dir / "runner-error-report.md",
            iteration_dir / "worker-context-budget-report.md",
            iteration_dir / "review-context-budget-report.md",
            iteration_dir / "worker-prompt-metrics.md",
            iteration_dir / "review-prompt-metrics.md",
            iteration_dir / "workspace-check.md",
            iteration_dir / "workspace-check.json",
            iteration_dir / "verification-summary.md",
            iteration_dir / "review-context.json",
            iteration_dir / "review-checklist.md",
            iteration_dir / "review-findings.md",
            iteration_dir / "review-verdict.json",
        ]
        result = [str(path.resolve()) for path in candidates if path.exists()]
        for pattern in [
            "executions/*/execution.json",
            "executions/*/stop-request.json",
            "executions/*/process-output.txt",
        ]:
            result.extend(str(path.resolve()) for path in sorted(iteration_dir.glob(pattern)))
        return result

    # 兼容旧 run：没有 iteration 字段时退回到每类文件的最后一个。
    result: list[str] = []
    for filename in [
        "interruption-report.md",
        "fix-prompt.md",
        "timeout-report.md",
        "stop-report.md",
        "runner-error-report.md",
        "worker-context-budget-report.md",
        "review-context-budget-report.md",
        "worker-prompt-metrics.md",
        "review-prompt-metrics.md",
        "workspace-check.md",
        "workspace-check.json",
        "verification-summary.md",
        "review-context.json",
        "review-checklist.md",
        "review-findings.md",
        "review-verdict.json",
    ]:
        path = _latest_iteration_file(run_dir, filename)
        if path.exists():
            result.append(str(path.resolve()))
    return result


def _latest_goal_artifacts(run_dir: Path) -> list[str]:
    result: list[str] = []
    for pattern in [
        "checkpoints/*/checkpoint-plan.md",
        "checkpoints/*/checkpoint-evidence.json",
        "checkpoints/*/checkpoint-report.md",
    ]:
        matches = sorted(run_dir.glob(pattern))
        if matches:
            result.append(str(matches[-1].resolve()))
    return result


def _latest_execution_payload(run_dir: Path) -> dict[str, Any] | None:
    records = find_execution_records(run_dir)
    if not records:
        return None
    active_records = [
        record for record in records if record.lease.status in ACTIVE_EXECUTION_STATUSES
    ]
    record = max(active_records or records, key=_execution_heartbeat_utc)
    lease = record.lease
    return {
        "status": lease.status,
        "step": lease.step,
        "iteration": lease.iteration,
        "owner_pid": lease.owner_pid,
        "child_pid": lease.child_pid,
        "termination_unconfirmed": lease.termination_unconfirmed,
        "last_heartbeat": lease.last_heartbeat,
        "deadline": lease.deadline,
        "path": str(record.path.resolve()),
    }


def _execution_heartbeat_utc(record: ExecutionRecord) -> datetime:
    try:
        parsed = datetime.fromisoformat(record.lease.last_heartbeat)
    except ValueError as exc:
        raise ValueError(f"execution 记录 `{record.path}` 的 last_heartbeat 不是有效 ISO 时间。") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
