from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .agent_contract import utc_now
from .agent_mutation import agent_mutation
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_recovery_request import AgentRecoveryRequest
from .agent_recovery_support import latest_checkpoint
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace,
    load_agent_bundle,
    write_checkpoint,
    write_status_card,
)
from .execution_paths import ExecutionPathGuard
from .redaction import write_redacted_json_once


class SupervisorAgentSideEffectAdjudicator:
    """把人工副作用确认追加为新证据，不改写旧 Checkpoint。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    @agent_mutation("agent.adjudicate_side_effects")
    def adjudicate(self, run: str, request: AgentRecoveryRequest) -> AgentRun:
        run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
        actor = request.actor.strip()
        reason = request.reason.strip()
        if not actor or not reason:
            raise ValueError("副作用裁决必须提供 actor 和 reason")
        if request.external_side_effects == "unknown":
            raise ValueError("人工裁决必须把外部副作用明确为 none 或 known")
        if state.phase != "needs_human":
            raise ValueError("只有 needs_human 状态可以执行外部副作用裁决")
        if state.active_child_run or state.active_operation_id:
            raise ValueError("Writer 仍处于 active binding，不能裁决外部副作用")
        if state.handoff_status != "none":
            raise ValueError("Handoff 已发布，不能回写外部副作用裁决")

        previous = latest_checkpoint(run_dir, state)
        if previous.external_side_effects != "unknown":
            raise ValueError("最近 Checkpoint 不存在待裁决的未知外部副作用")
        if previous.status != "blocked" or previous.phase != "needs_human":
            raise ValueError("最近 Checkpoint 不是等待人工裁决的 blocked 现场")
        if (
            previous.state_version + 1 != state.state_version
            or previous.current_work_item != state.current_work_item
            or previous.active_child_run is not None
            or previous.operation_started
        ):
            raise ValueError("最近 Checkpoint 与当前 Agent State 绑定不一致")

        actual = capture_bound_workspace(run_dir)
        if (
            not request.workspace_explained
            or actual.unsafe_index_paths
            or not actual.git_control_complete
            or state.workspace_fingerprint != actual.fingerprint
            or previous.workspace_fingerprint != actual.fingerprint
        ):
            raise ValueError("Workspace 已漂移或控制信息不完整，拒绝使用过期副作用证据")

        previous_ref = f"checkpoints/{previous.checkpoint_id}.json"
        evidence = _bind_evidence(run_dir, [previous_ref, *request.evidence_refs])
        adjudication_ref = f"adjudications/side-effects-{uuid4().hex[:12]}.json"
        adjudication_guard = ExecutionPathGuard(
            trusted_root=run_dir,
            execution_dir=run_dir / "adjudications",
        )
        adjudication_dir = adjudication_guard.prepare()
        adjudication_path = adjudication_dir / Path(adjudication_ref).name
        adjudication_guard.validate_artifact(adjudication_path)
        write_redacted_json_once(
            adjudication_path,
            {
                "schema_version": 1,
                "run_id": state.run_id,
                "actor": actor,
                "reason": reason,
                "previous_checkpoint_id": previous.checkpoint_id,
                "previous_external_side_effects": "unknown",
                "resolved_external_side_effects": request.external_side_effects,
                "workspace_fingerprint": actual.fingerprint,
                "evidence": evidence,
                "created_at": utc_now(),
            },
        )

        cleared = request.external_side_effects == "none"
        next_state = update_state(
            state,
            phase="stopped" if cleared else "needs_human",
            state_version=state.state_version + 1,
            workspace_fingerprint=actual.fingerprint,
            allowed_actions=[] if cleared else ["human"],
        )
        checkpoint = write_checkpoint(
            run_dir,
            next_state,
            actual,
            reason=(
                f"人工 {actor} 裁决外部副作用为 "
                f"{request.external_side_effects}：{reason}"
            ),
            status="safe" if cleared else "blocked",
            pending_actions=[] if cleared else ["human"],
            evidence_refs=[previous_ref, adjudication_ref, *request.evidence_refs],
            completed_attempts=list(previous.completed_attempts),
            failed_attempts=list(previous.failed_attempts),
            operation_started=False,
            external_side_effects=request.external_side_effects,
        )
        next_state = update_state(
            next_state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=next_state.state_version + 1,
        )
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="agent_side_effects_adjudicated",
            state=next_state,
            observation_summary=(
                f"人工 {actor} 已确认外部副作用为 "
                f"{request.external_side_effects}"
            ),
            route_reason=reason,
            artifact_refs=[
                previous_ref,
                adjudication_ref,
                f"checkpoints/{checkpoint.checkpoint_id}.json",
            ],
        )
        write_status_card(
            run_dir,
            next_state,
            plan,
            checkpoint=checkpoint,
            next_step=(
                "外部副作用已确认不存在；可生成 Handoff，但仍需人工检查 WIP 与 Task Card"
                if cleared
                else "已确认存在外部副作用；任务继续等待人工处理，禁止自动重试或发布 ready Handoff"
            ),
        )
        save_agent_state(run_dir / "agent-state.json", next_state)
        return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def _bind_evidence(
    run_dir: Path,
    refs: list[str],
) -> list[dict[str, str]]:
    if len(refs) < 2:
        raise ValueError("副作用裁决至少需要一个人工提供的 run-local evidence_ref")
    root = run_dir.resolve(strict=True)
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in refs:
        normalized = _normalize_evidence_ref(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        candidate = run_dir.joinpath(*PurePosixPath(normalized).parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"副作用证据不存在：{normalized}") from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError(f"副作用证据必须是 run 内普通文件：{normalized}")
        evidence.append(
            {
                "path": normalized,
                "sha256": _sha256_file(resolved),
            }
        )
    if len(evidence) < 2:
        raise ValueError("副作用裁决至少需要一个人工提供的 run-local evidence_ref")
    return evidence


def _normalize_evidence_ref(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or normalized.startswith("//")
        or any(part in {"", ".", ".."} for part in path.parts)
        or (len(normalized) >= 2 and normalized[1] == ":")
    ):
        raise ValueError(f"副作用证据必须是 run 相对路径：{value}")
    return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
