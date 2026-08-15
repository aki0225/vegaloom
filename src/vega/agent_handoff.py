from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .agent_contract import (
    AgentCheckpoint,
    AgentObservation,
    AgentPlan,
    AgentState,
    utc_now,
)
from .agent_handoff_safety import (
    collect_handoff_issues,
    prepare_task_card_root,
    require_plain_task_card_tree,
)
from .agent_persistence import (
    append_agent_trace,
    load_agent_checkpoint,
    read_agent_trace,
    save_agent_state,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    current_branch,
    load_agent_bundle,
    write_checkpoint,
    write_run_metadata,
    write_status_card,
)
from .agent_task_card import (
    AgentTaskCard,
    HistoricalGateEvidence,
    ResumeCapsule,
    TaskCardError,
    compute_handoff_workspace_digest,
    load_task_card,
    render_task_card,
    save_task_card,
    task_card_content_digest,
)
from .redaction import sensitive_path_reason, write_redacted_json, write_redacted_text


_HANDOFF_PHASES = frozenset({"ready", "needs_human", "stopped"})
_TASK_CARD_STATUS = frozenset({"ready", "needs_human"})
_OBSERVATION_GATES = ("verification", "risk", "review")


@dataclass(frozen=True)
class HandoffResult:
    run: AgentRun
    checkpoint: AgentCheckpoint
    task_card_path: Path
    task_card_digest: str
    handoff_status: str


def create_handoff(
    workspace: Path,
    run: str,
    *,
    reason: str,
) -> HandoffResult:
    """把已经停止调度的 Agent run 生成可人工提交的 Task Card。"""

    if not reason.strip():
        raise ValueError("handoff 必须提供 reason")

    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    if state.handoff_status != "none":
        raise ValueError("当前 Agent run 已经生成 Handoff，拒绝重复发布 Task Card")
    if state.active_child_run or state.active_operation_id:
        raise ValueError("Writer 仍处于 active binding；先 stop 并完成 recover 对账")
    if state.phase not in _HANDOFF_PHASES:
        raise ValueError(
            f"当前阶段 {state.phase} 不能生成 Handoff；必须先停止调度并完成现场对账"
        )
    if not plan.approval_is_current():
        raise ValueError("只有当前已批准 Plan 才能生成 Handoff")
    if not state.current_work_item:
        raise ValueError("当前 Agent run 没有可交接的 Work Item")
    if state.latest_checkpoint_id is None:
        raise ValueError("当前 Agent run 缺少最近 Checkpoint，拒绝生成 Handoff")

    work_item = _current_work_item(plan, state)
    repo = bound_repo(run_dir)
    branch = current_branch(repo)
    _ensure_no_existing_handoff(repo, state.task_id, branch)
    card_path = _task_card_path(repo, state.task_id)
    snapshot = capture_bound_workspace(run_dir)
    latest = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    issues = collect_handoff_issues(state, latest, snapshot)
    sensitive_paths = [
        path
        for path in snapshot.changed_files
        if sensitive_path_reason(path) is not None
    ]
    if sensitive_paths:
        raise ValueError(
            "交接现场包含敏感路径，不能写入 Git Task Card："
            + ", ".join(sensitive_paths)
        )

    handoff_status = "handoff_ready" if not issues else "handoff_blocked"
    checkpoint_state = update_state(
        state,
        state_version=state.state_version + 1,
        workspace_fingerprint=snapshot.fingerprint,
        handoff_status=handoff_status,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        checkpoint_state,
        snapshot,
        reason=f"准备 Handoff：{reason.strip()}",
        status="safe" if handoff_status == "handoff_ready" else "blocked",
        pending_actions=(
            []
            if handoff_status == "handoff_ready" and checkpoint_state.phase == "stopped"
            else ["human"]
        ),
        operation_started=False,
        external_side_effects=latest.external_side_effects,
    )

    observation, observation_time = _latest_observation(run_dir)
    workspace_digest = compute_handoff_workspace_digest(
        repo,
        list(snapshot.changed_files),
    )
    gate_evidence = _historical_gate_evidence(
        observation,
        source_revision=snapshot.head_sha,
        recorded_at=observation_time,
    )
    capsule = ResumeCapsule(
        current_work_item=state.current_work_item,
        stopped_at=f"{reason.strip()}；当前阶段：{state.phase}",
        confirmed_facts=_compact(
            [
                *plan.observed_facts,
                f"交接基线 HEAD：{snapshot.head_sha}",
            ]
        ),
        unresolved_hypotheses=_compact(
            [
                *plan.hypotheses,
                *(f"未决：{value}" for value in plan.unresolved_decisions),
            ]
        ),
        failed_attempts=_compact(list(latest.failed_attempts)),
        restrictions=_compact(
            [
                *plan.non_goals,
                *(f"禁止路径：{value}" for value in work_item.forbidden_paths),
            ]
        ),
        risk_notes=_compact(
            [
                *work_item.risk_notes,
                f"外部副作用状态：{latest.external_side_effects}",
                "ignored 文件不会进入 Task Card 或 Git 提交；新机器必须按项目说明重建",
                *issues,
            ]
        ),
        human_checks=_compact(
            [
                "把 WIP 文件与 Task Card 一起提交前，执行 git diff --cached --check",
                "旧 Verification、Risk、Reviewer 结果在新机器上只能作为 historical 证据",
                "确认当前任务不依赖未提交的 ignored 文件或本机私密配置",
                *work_item.verification,
            ]
        ),
        changed_files=list(snapshot.changed_files),
        workspace_digest=workspace_digest,
        gate_evidence=gate_evidence,
        external_side_effects=latest.external_side_effects,
        writer_stopped=state.active_child_run is None,
        workspace_explained=not issues,
        allowed_actions=(
            ["human"]
            if handoff_status == "handoff_blocked"
            else ["repair", "human"]
            if snapshot.changed_files
            else ["next", "human"]
        ),
        next_step=(
            "先人工核对 Task Card、WIP、进程和外部副作用；当前 Handoff blocked，不能自动启动 Worker"
            if handoff_status == "handoff_blocked"
            else (
                "新机器拉取包含 Task Card 的任务分支后，运行 vega agent resume --repo .，"
                "重新对账后再人工确认当前 Work Item"
            )
        ),
        recommended_command="vega agent resume --repo .",
    )
    card = AgentTaskCard(
        task_id=state.task_id,
        status=_task_card_status(state, handoff_status),
        branch=branch,
        base_revision=_metadata_revision(metadata, snapshot.head_sha),
        plan=plan,
        current_work_item=state.current_work_item,
        handoff_sequence=_next_handoff_sequence(repo, state.task_id, branch),
        handoff_status=handoff_status,
        handoff_base_revision=snapshot.head_sha,
        handoff_workspace_digest=workspace_digest,
        last_handoff_checkpoint=checkpoint.checkpoint_id,
        progress_notes=_compact(
            [
                latest.reason,
                *( [observation.machine_summary] if observation else []),
            ]
        ),
        failed_attempts=list(latest.failed_attempts),
        risk_notes=list(capsule.risk_notes),
        verification_notes=[
            f"{evidence.gate}: {evidence.status}（historical）"
            for evidence in gate_evidence
        ],
        resume_capsule=capsule,
    )

    card_content = render_task_card(card)
    card_digest = task_card_content_digest(card_content)
    relative_card = card_path.relative_to(repo).as_posix()

    write_run_metadata(
        run_dir,
        repo,
        _metadata_revision(metadata, snapshot.head_sha),
        task_card=relative_card,
    )
    write_redacted_json(
        run_dir / "handoff-manifest.json",
        {
            "schema_version": 1,
            "run_id": state.run_id,
            "task_id": state.task_id,
            "task_card": relative_card,
            "task_card_sha256": card_digest,
            "handoff_status": handoff_status,
            "checkpoint_id": checkpoint.checkpoint_id,
            "handoff_base_revision": snapshot.head_sha,
            "handoff_workspace_digest": workspace_digest,
            "changed_files": list(snapshot.changed_files),
            "pending_git_actions": _git_checklist(relative_card, snapshot.changed_files, branch),
        },
    )
    write_redacted_text(
        run_dir / "handoff-summary.md",
        _render_handoff_summary(
            card=card,
            card_path=relative_card,
            card_digest=card_digest,
            branch=branch,
            changed_files=snapshot.changed_files,
            issues=issues,
        ),
    )
    published_state = update_state(
        checkpoint_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=checkpoint_state.state_version + 1,
    )
    require_plain_task_card_tree(repo, card_path.parent)
    save_task_card(card_path, card)
    try:
        save_agent_state(run_dir / "agent-state.json", published_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="agent_handoff_created",
            state=published_state,
            observation_summary=f"已生成 {relative_card}",
            route_reason=reason.strip(),
            artifact_refs=[
                relative_card,
                "handoff-manifest.json",
                "handoff-summary.md",
                f"checkpoints/{checkpoint.checkpoint_id}.json",
            ],
        )
        write_status_card(
            run_dir,
            published_state,
            plan,
            checkpoint=checkpoint,
            next_step=capsule.next_step,
        )
    except Exception:
        card_path.unlink(missing_ok=True)
        save_agent_state(run_dir / "agent-state.json", state)
        write_run_metadata(
            run_dir,
            repo,
            _metadata_revision(metadata, snapshot.head_sha),
        )
        raise
    return HandoffResult(
        run=AgentRun(run_dir=run_dir, state=published_state, plan=plan),
        checkpoint=checkpoint,
        task_card_path=card_path,
        task_card_digest=card_digest,
        handoff_status=handoff_status,
    )


def _latest_observation(
    run_dir: Path,
) -> tuple[AgentObservation | None, str]:
    try:
        trace = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError):
        return None, utc_now()
    for item in reversed(trace):
        refs = item.get("artifact_refs")
        if not isinstance(refs, list):
            continue
        for ref in reversed(refs):
            if not isinstance(ref, str) or not ref.startswith("observations/"):
                continue
            path = run_dir / ref
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                observation = AgentObservation.model_validate(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            timestamp = item.get("ts")
            return observation, timestamp if isinstance(timestamp, str) else utc_now()
    return None, utc_now()


def _historical_gate_evidence(
    observation: AgentObservation | None,
    *,
    source_revision: str,
    recorded_at: str,
) -> list[HistoricalGateEvidence]:
    return [
        HistoricalGateEvidence(
            gate=gate,
            status=getattr(observation, gate) if observation else "not_run",
            source_revision=source_revision,
            recorded_at=recorded_at,
            artifact_refs=list(observation.evidence_refs) if observation else [],
        )
        for gate in _OBSERVATION_GATES
    ]


def _current_work_item(plan: AgentPlan, state: AgentState):
    try:
        return next(
            item for item in plan.work_items if item.work_item_id == state.current_work_item
        )
    except StopIteration as exc:
        raise ValueError("当前 Work Item 不属于已批准 Plan") from exc


def _task_card_status(state: AgentState, handoff_status: str) -> str:
    if handoff_status == "handoff_blocked":
        return "needs_human"
    if state.phase == "stopped":
        return "ready"
    if state.phase not in _TASK_CARD_STATUS:
        raise ValueError(f"当前阶段不能写入 Task Card：{state.phase}")
    return state.phase


def _metadata_revision(metadata: dict[str, str], fallback: str) -> str:
    revision = metadata.get("base_revision")
    return revision if isinstance(revision, str) and revision else fallback


def _task_card_path(repo: Path, task_id: str) -> Path:
    date = datetime.now(UTC)
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:48] or "task"
    root = prepare_task_card_root(repo, date.strftime("%Y-%m"))
    base = root / f"{date.strftime('%Y-%m-%d')}-{slug}-handoff.md"
    candidate = base
    suffix = 2
    while os.path.lexists(candidate):
        candidate = root / f"{date.strftime('%Y-%m-%d')}-{slug}-handoff-{suffix:02d}.md"
        suffix += 1
    return candidate


def _ensure_no_existing_handoff(repo: Path, task_id: str, branch: str) -> None:
    task_root = repo / ".vega" / "tasks"
    if not os.path.lexists(task_root):
        return
    require_plain_task_card_tree(repo, task_root)
    for path in task_root.rglob("*.md"):
        if path.is_symlink():
            raise TaskCardError("Task Card 目录中不能包含链接文件")
        try:
            card = load_task_card(path)
        except (OSError, ValueError, TaskCardError):
            continue
        if (
            card.task_id == task_id
            and card.branch == branch
            and card.status not in {"completed", "stopped"}
            and card.handoff_status != "none"
        ):
            raise TaskCardError(
                "当前任务和分支已存在未终止 Handoff Task Card；"
                "请先人工处理旧卡，拒绝生成重复交接"
            )


def _next_handoff_sequence(repo: Path, task_id: str, branch: str) -> int:
    root = repo / ".vega" / "tasks"
    if not root.exists():
        return 1
    highest = 0
    for path in root.rglob("*.md"):
        try:
            card = load_task_card(path)
        except (OSError, ValueError, TaskCardError):
            continue
        if card.task_id == task_id and card.branch == branch:
            highest = max(highest, card.handoff_sequence)
    return highest + 1


def _compact(values: list[str], *, limit: int = 12) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in unique:
            unique.append(text)
    return unique[:limit]


def _git_checklist(
    card_path: str,
    changed_files: tuple[str, ...],
    branch: str,
) -> list[str]:
    paths = [*changed_files, card_path]
    add_paths = " ".join(f"`{path}`" for path in paths) or "`<Task Card>`"
    return [
        "人工确认旧 Writer、进程和外部副作用已经停止或已明确标记 blocked",
        "执行 `git status --short` 和 `git diff --check`",
        f"只暂存 WIP 与 Task Card：{add_paths}",
        "执行 `git diff --cached --check`，人工检查完整 staged diff",
        f"人工决定是否在任务分支 `{branch}` commit 和 push",
        "新机器使用 `git pull --ff-only` 后运行 `vega agent resume --repo .`",
    ]


def _render_handoff_summary(
    *,
    card: AgentTaskCard,
    card_path: str,
    card_digest: str,
    branch: str,
    changed_files: tuple[str, ...],
    issues: list[str],
) -> str:
    lines = [
        "# Vega Handoff Summary",
        "",
        f"- Task Card：`{card_path}`",
        f"- Task Card SHA-256：`{card_digest}`",
        f"- 分支：`{branch}`",
        f"- Handoff 状态：`{card.handoff_status}`",
        f"- 当前 Work Item：`{card.current_work_item}`",
        f"- WIP 文件：{', '.join(f'`{path}`' for path in changed_files) or '无'}",
        "",
        "## 现场说明",
        "",
        *(
            [f"- 阻断原因：{issue}" for issue in issues]
            if issues
            else ["- 现场可解释，旧 Writer 未保持 active binding。"]
        ),
        "",
        "## 人工 Git 清单",
        "",
        *_git_checklist(card_path, changed_files, branch),
        "",
        "Vega 不会自动执行 commit、push、release 或删除文件。",
        "",
    ]
    return "\n".join(lines)
