from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from .goal_evidence import validate_goal_evidence
from ..models import (
    GoalCheckpointEvidenceType,
    GoalCheckpointRecord,
    GoalCheckpointRef,
    GoalContract,
    GoalState,
)
from ..redaction import write_redacted_json, write_redacted_text
from ..run_utils import create_run_dir, resolve_run_dir
from ..trace import TraceWriter


class GoalRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def start(
        self,
        repo_path: Path,
        goal_text: str,
        input_source: str,
        scope_profile: str | None,
    ) -> Path:
        if not goal_text.strip():
            raise ValueError("goal 内容不能为空。")
        base_run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-goal"
        run_id, run_dir = create_run_dir(self.workspace, base_run_id)
        trace = TraceWriter(run_dir / "goal-trace.jsonl")

        contract = _make_contract(repo_path, goal_text, input_source, scope_profile)
        _write_contract(run_dir, contract)
        state = GoalState(
            run_id=run_id,
            repo_path=str(repo_path.resolve()),
            input_source=input_source,
            scope_profile=scope_profile,
            artifacts=[
                "state.json",
                "goal-state.json",
                "goal-trace.jsonl",
                "goal-contract.md",
                "goal-contract.json",
                "progress.md",
            ],
        )
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        trace.write(
            "goal_started",
            run_id=run_id,
            repo_path=str(repo_path.resolve()),
            scope_profile=scope_profile,
        )
        return run_dir

    def step(self, run: str) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "step")
        active = _active_checkpoint(state)
        if active is not None:
            raise ValueError(
                f"checkpoint {active.checkpoint} 尚未完成，不能生成下一 checkpoint plan。"
            )

        next_index = state.checkpoint_count + 1
        checkpoint_dir = run_dir / "checkpoints" / f"{next_index:02d}"
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        plan_path = checkpoint_dir / "checkpoint-plan.md"
        write_redacted_text(
            plan_path,
            _render_checkpoint_plan(state, contract, next_index),
        )

        rel_path = f"checkpoints/{next_index:02d}/checkpoint-plan.md"
        state.status = "running"
        state.current_step = "checkpoint_planned"
        state.checkpoint_count = next_index
        state.checkpoints = [*state.checkpoints, rel_path]
        state.checkpoint_records = [
            *state.checkpoint_records,
            GoalCheckpointRecord(checkpoint=f"{next_index:02d}", plan_path=rel_path),
        ]
        state.artifacts = _dedupe([*state.artifacts, rel_path])
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write(
            "goal_checkpoint_planned",
            checkpoint=next_index,
            path=rel_path,
        )
        return run_dir

    def attach(
        self,
        run: str,
        checkpoint: str,
        child_run: str,
        evidence_type: str,
        note: str | None = None,
    ) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "attach")
        checkpoint_id = _normalize_checkpoint(checkpoint)
        record = _find_checkpoint_record(state, checkpoint_id)
        if record is None:
            raise ValueError(f"checkpoint 不存在：{checkpoint_id}")
        if record.status != "planned":
            raise ValueError(f"checkpoint {checkpoint_id} 已完成，证据不可再修改。")
        active = _active_checkpoint(state)
        if active is None or active.checkpoint != checkpoint_id:
            raise ValueError(f"只能给当前 active checkpoint 挂载证据：{active.checkpoint if active else '无'}")
        if evidence_type not in _EVIDENCE_TYPES:
            raise ValueError(f"--type 只能是：{', '.join(sorted(_EVIDENCE_TYPES))}")
        if not child_run.strip():
            raise ValueError("--ref 不能为空。")
        checked_evidence_type = cast(GoalCheckpointEvidenceType, evidence_type)
        evidence = validate_goal_evidence(
            self.workspace,
            Path(state.repo_path),
            child_run.strip(),
            checked_evidence_type,
            note,
        )
        if any(item.type == evidence.type and item.run == evidence.run for item in record.refs):
            raise ValueError(f"checkpoint {checkpoint_id} 已存在相同证据：{evidence.type}:{evidence.run}")
        record.refs.append(evidence)
        evidence_path = run_dir / "checkpoints" / checkpoint_id / "checkpoint-evidence.json"
        write_redacted_json(
            evidence_path,
            record.model_dump(mode="json"),
        )
        rel_evidence = f"checkpoints/{checkpoint_id}/checkpoint-evidence.json"
        state.current_step = "checkpoint_evidence_attached"
        state.artifacts = _dedupe([*state.artifacts, rel_evidence])
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write(
            "goal_checkpoint_evidence_attached",
            checkpoint=checkpoint_id,
            child_run=evidence.run,
            evidence_type=evidence_type,
            completion_eligible=evidence.completion_eligible,
        )
        return run_dir

    def checkpoint_done(
        self,
        run: str,
        checkpoint: str,
        note: str | None = None,
        *,
        allow_manual_evidence: bool = False,
    ) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "checkpoint_done")
        checkpoint_id = _normalize_checkpoint(checkpoint)
        record = _find_checkpoint_record(state, checkpoint_id)
        if record is None:
            raise ValueError(f"checkpoint 不存在：{checkpoint_id}")
        if record.status != "planned":
            raise ValueError(f"checkpoint {checkpoint_id} 已经完成，不能重复 checkpoint-done。")
        active = _active_checkpoint(state)
        if active is None or active.checkpoint != checkpoint_id:
            raise ValueError(f"只能完成当前 active checkpoint：{active.checkpoint if active else '无'}")
        if not record.refs:
            raise ValueError("checkpoint 没有挂载任何证据，不能标记完成。")
        refreshed_refs, refresh_errors = _revalidate_checkpoint_refs(
            self.workspace,
            Path(state.repo_path),
            record,
        )
        if refresh_errors:
            raise ValueError(
                "checkpoint 证据重新校验失败：" + "；".join(refresh_errors)
            )
        record.refs = refreshed_refs
        eligible_refs = [item for item in record.refs if item.validated and item.completion_eligible]
        manual_refs = [item for item in record.refs if item.validated and item.type == "manual"]
        completion_mode = "validated"
        if not eligible_refs:
            if not allow_manual_evidence:
                raise ValueError(
                    "checkpoint 缺少可完成证据；请挂载成功的 loop/approved review/"
                    "ready_to_commit finish，或显式使用 --allow-manual-evidence。"
                )
            if not manual_refs:
                raise ValueError("--allow-manual-evidence 需要至少一个已校验的 manual 证据文件。")
            if not note or not note.strip():
                raise ValueError("manual evidence override 必须提供 --note，说明人工完成依据。")
            completion_mode = "manual_override"
        checkpoint_dir = run_dir / "checkpoints" / checkpoint_id
        report_rel = f"checkpoints/{checkpoint_id}/checkpoint-report.md"
        record.status = "done"
        record.report_path = report_rel
        record.completed_note = note.strip() if note and note.strip() else None
        record.completed_at = datetime.now(UTC).isoformat()
        record.completion_mode = completion_mode
        write_redacted_text(
            checkpoint_dir / "checkpoint-report.md",
            _render_checkpoint_report(state, contract, record),
        )
        write_redacted_json(
            checkpoint_dir / "checkpoint-evidence.json",
            record.model_dump(mode="json"),
        )
        state.status = "checkpoint_done"
        state.current_step = "checkpoint_done"
        state.artifacts = _dedupe(
            [
                *state.artifacts,
                f"checkpoints/{checkpoint_id}/checkpoint-evidence.json",
                report_rel,
            ]
        )
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write(
            "goal_checkpoint_done",
            checkpoint=checkpoint_id,
            ref_count=len(record.refs),
            completion_mode=completion_mode,
        )
        return run_dir

    def pause(self, run: str, reason: str) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "pause")
        if not reason.strip():
            raise ValueError("pause 必须提供原因。")
        previous_status = state.status
        state.status = "paused"
        state.current_step = "paused"
        state.pause_reason = reason
        state.paused_from_status = previous_status
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write("goal_paused", reason=reason)
        return run_dir

    def resume(self, run: str) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "resume")
        resumed_status = state.paused_from_status or "running"
        if state.status == "needs_human":
            resumed_status = "running"
        state.status = resumed_status
        state.current_step = "resumed"
        state.paused_from_status = None
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write("goal_resumed")
        return run_dir

    def stop(self, run: str, reason: str) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "stop")
        if not reason.strip():
            raise ValueError("stop 必须提供原因。")
        state.status = "stopped"
        state.current_step = "stopped"
        state.stop_reason = reason
        write_redacted_text(
            run_dir / "stop-report.md",
            _render_stop_report(state, contract, reason),
        )
        state.artifacts = _dedupe([*state.artifacts, "stop-report.md"])
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write("goal_stopped", reason=reason)
        return run_dir

    def complete(self, run: str, note: str) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "complete")
        if not note.strip():
            raise ValueError("complete 必须提供 --note，说明如何确认 success conditions。")
        if not contract.success_conditions:
            raise ValueError("goal contract 未声明 success conditions，不能标记完成。")
        if not state.checkpoint_records:
            raise ValueError("goal 尚未产生 checkpoint，不能标记完成。")
        incomplete = [item.checkpoint for item in state.checkpoint_records if item.status != "done"]
        if incomplete:
            raise ValueError(f"仍有未完成 checkpoint：{', '.join(incomplete)}")
        invalid = [
            item.checkpoint
            for item in state.checkpoint_records
            if not _checkpoint_completion_is_current(
                self.workspace,
                Path(state.repo_path),
                item,
            )
        ]
        if invalid:
            raise ValueError(
                f"checkpoint 完成证据已失效，不能 complete：{', '.join(invalid)}"
            )

        state.status = "success"
        state.current_step = "completed"
        state.completion_note = note.strip()
        state.completed_at = datetime.now(UTC).isoformat()
        report_path = run_dir / "goal-final-report.md"
        eval_path = run_dir / "goal-eval.md"
        write_redacted_text(report_path, _render_goal_final_report(state, contract))
        state.artifacts = _dedupe([*state.artifacts, "goal-final-report.md", "goal-eval.md"])
        state.eval_results = _run_goal_eval(run_dir, state, contract)
        write_redacted_text(eval_path, _render_goal_eval(state.eval_results))
        has_failures = any(item.startswith("FAIL:") for item in state.eval_results)
        if has_failures:
            state.status = "needs_human"
            state.current_step = "completion_eval_failed"
            write_redacted_text(report_path, _render_goal_final_report(state, contract))
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        event = "goal_completion_failed" if has_failures else "goal_completed"
        TraceWriter(run_dir / "goal-trace.jsonl").write(
            event,
            checkpoint_count=state.checkpoint_count,
            completion_note=state.completion_note,
            eval_results=state.eval_results,
        )
        return run_dir

    def recover(self, run: str, reason: str) -> Path:
        run_dir, state, contract = self._load(run)
        _ensure_action_allowed(state, "recover")
        if not reason.strip():
            raise ValueError("recover 必须提供原因。")
        previous_step = state.current_step
        state.status = "needs_human"
        state.current_step = "recovered"
        state.recover_reason = reason
        write_redacted_text(
            run_dir / "recovery-report.md",
            _render_recovery_report(state, previous_step, reason),
        )
        state.artifacts = _dedupe([*state.artifacts, "recovery-report.md"])
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write(
            "goal_recovered",
            previous_step=previous_step,
            reason=reason,
        )
        return run_dir

    def _load(self, run: str) -> tuple[Path, GoalState, GoalContract]:
        run_dir = resolve_run_dir(self.workspace, run)
        state_path = run_dir / "goal-state.json"
        mirror_state_path = run_dir / "state.json"
        contract_path = run_dir / "goal-contract.json"
        if not state_path.exists() or not mirror_state_path.exists() or not contract_path.exists():
            raise ValueError(f"不是 goal run：{run}")
        state_payload = _load_goal_json(state_path, "goal-state.json")
        mirror_state_payload = _load_goal_json(mirror_state_path, "state.json")
        contract_payload = _load_goal_json(contract_path, "goal-contract.json")
        if state_payload != mirror_state_payload:
            raise ValueError("goal-state.json 与 state.json 不一致，不能继续执行。")
        if str(state_payload.get("run_id") or "") != run_dir.name:
            raise ValueError("goal state.run_id 与 run 目录身份不一致。")
        contract_run_id = contract_payload.get("run_id")
        if contract_run_id is not None and str(contract_run_id) != run_dir.name:
            raise ValueError("goal contract.run_id 与 run 目录身份不一致。")
        try:
            state = GoalState.model_validate(state_payload)
            contract = GoalContract.model_validate(contract_payload)
        except ValidationError as exc:
            raise ValueError(f"goal JSON schema 不合法：{exc.errors()[0]['type']}") from exc
        if Path(state.repo_path).resolve() != Path(contract.repo_path).resolve():
            raise ValueError("goal state.repo_path 与 contract.repo_path 不一致。")
        _validate_checkpoint_artifacts(run_dir, state)
        return run_dir, state, contract


def _make_contract(
    repo_path: Path,
    goal_text: str,
    input_source: str,
    scope_profile: str | None,
) -> GoalContract:
    return GoalContract(
        objective=_extract_objective(goal_text),
        repo_path=str(repo_path.resolve()),
        input_source=input_source,
        raw_text=goal_text,
        scope_profile=scope_profile,
        non_goals=_extract_bullets_after(goal_text, {"non-goals", "non goals", "非目标"}),
        success_conditions=_extract_bullets_after(
            goal_text,
            {"success conditions", "success criteria", "验收标准", "成功条件"},
        ),
    )


def _write_contract(run_dir: Path, contract: GoalContract) -> None:
    write_redacted_json(
        run_dir / "goal-contract.json",
        contract.model_dump(mode="json"),
    )
    write_redacted_text(
        run_dir / "goal-contract.md",
        _render_contract_markdown(contract),
    )


def _save_goal_state(run_dir: Path, state: GoalState) -> None:
    state.save(run_dir / "goal-state.json")
    state.save(run_dir / "state.json")


def _load_goal_json(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{label} 无法读取。") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 已损坏，无法解析 JSON。") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} schema 不合法：顶层必须是 JSON object。")
    return payload


def _validate_checkpoint_artifacts(run_dir: Path, state: GoalState) -> None:
    if state.checkpoint_count != len(state.checkpoint_records):
        raise ValueError("goal checkpoint_count 与 checkpoint_records 数量不一致。")

    expected_plan_paths: list[str] = []
    for index, record in enumerate(state.checkpoint_records, start=1):
        checkpoint_id = f"{index:02d}"
        expected_plan_path = f"checkpoints/{checkpoint_id}/checkpoint-plan.md"
        expected_plan_paths.append(expected_plan_path)
        if record.checkpoint != checkpoint_id or record.plan_path != expected_plan_path:
            raise ValueError(f"checkpoint {checkpoint_id} 身份或 plan_path 与 goal state 不一致。")
        if not (run_dir / expected_plan_path).is_file():
            raise ValueError(f"checkpoint {checkpoint_id} plan artifact 缺失。")
        if expected_plan_path not in state.artifacts:
            raise ValueError(f"checkpoint {checkpoint_id} plan 未登记到 goal state artifacts。")

        evidence_path = run_dir / "checkpoints" / checkpoint_id / "checkpoint-evidence.json"
        evidence_required = bool(record.refs) or record.status == "done"
        if evidence_required:
            if not evidence_path.is_file():
                raise ValueError(f"checkpoint {checkpoint_id} evidence JSON 缺失。")
            evidence_payload = _load_goal_json(
                evidence_path,
                f"checkpoint {checkpoint_id} evidence JSON",
            )
            try:
                artifact_record = GoalCheckpointRecord.model_validate(evidence_payload)
            except ValidationError as exc:
                raise ValueError(
                    f"checkpoint {checkpoint_id} evidence JSON schema 不合法。"
                ) from exc
            if artifact_record.model_dump(mode="json") != record.model_dump(mode="json"):
                raise ValueError(f"checkpoint {checkpoint_id} evidence JSON 与 goal state 不一致。")
            evidence_rel = f"checkpoints/{checkpoint_id}/checkpoint-evidence.json"
            if evidence_rel not in state.artifacts:
                raise ValueError(f"checkpoint {checkpoint_id} evidence 未登记到 goal state artifacts。")
        elif evidence_path.exists():
            raise ValueError(f"checkpoint {checkpoint_id} 存在未绑定的 evidence JSON。")

        if record.status == "done":
            expected_report_path = f"checkpoints/{checkpoint_id}/checkpoint-report.md"
            if record.report_path != expected_report_path:
                raise ValueError(f"checkpoint {checkpoint_id} report_path 与 goal state 不一致。")
            if not (run_dir / expected_report_path).is_file():
                raise ValueError(f"checkpoint {checkpoint_id} report artifact 缺失。")
            if expected_report_path not in state.artifacts:
                raise ValueError(f"checkpoint {checkpoint_id} report 未登记到 goal state artifacts。")

    if state.checkpoints != expected_plan_paths:
        raise ValueError("goal checkpoints 列表与 checkpoint_records 不一致。")


def _write_progress(run_dir: Path, state: GoalState, contract: GoalContract) -> None:
    write_redacted_text(
        run_dir / "progress.md",
        _render_progress(state, contract),
    )


def _render_contract_markdown(contract: GoalContract) -> str:
    lines = [
        "# Goal Contract",
        "",
        f"- 仓库：`{contract.repo_path}`",
        f"- 输入：`{contract.input_source}`",
        f"- scope：`{contract.scope_profile or 'default'}`",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Non-goals",
        "",
    ]
    lines.extend(f"- {item}" for item in contract.non_goals or ["未显式声明。"])
    lines.extend(["", "## Success Conditions", ""])
    lines.extend(f"- {item}" for item in contract.success_conditions or ["未显式声明。"])
    lines.extend(
        [
            "",
            "## Raw Goal",
            "",
            contract.raw_text.strip(),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_progress(state: GoalState, contract: GoalContract) -> str:
    lines = [
        "# Goal Progress",
        "",
        f"- run：`{state.run_id}`",
        f"- 状态：`{state.status}`",
        f"- 当前步骤：`{state.current_step}`",
        f"- 仓库：`{state.repo_path}`",
        f"- scope：`{state.scope_profile or 'default'}`",
        f"- checkpoint 数：`{state.checkpoint_count}`",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Checkpoints",
        "",
    ]
    if state.checkpoint_records:
        for record in state.checkpoint_records:
            refs = (
                ", ".join(
                    f"{item.type}:{item.run}"
                    + ("(eligible)" if item.completion_eligible else "(evidence)")
                    for item in record.refs
                )
                or "无"
            )
            mode = f"，mode=`{record.completion_mode}`" if record.completion_mode else ""
            lines.append(f"- `{record.checkpoint}`：`{record.status}`{mode}，refs：{refs}")
    elif state.checkpoints:
        lines.extend(f"- `{item}`" for item in state.checkpoints)
    else:
        lines.append("- 尚未生成 checkpoint plan。")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Goal P0 只维护状态、checkpoint plan 和经过校验的证据引用。",
            "- 不调用 worker，不自动修改目标仓库，不自动 commit，不写长期 memory。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_checkpoint_plan(state: GoalState, contract: GoalContract, index: int) -> str:
    return "\n".join(
        [
            f"# Checkpoint {index:02d} Plan",
            "",
            f"- goal：`{state.run_id}`",
            f"- 仓库：`{state.repo_path}`",
            f"- scope：`{state.scope_profile or 'default'}`",
            "",
            "## Objective",
            "",
            contract.objective,
            "",
            "## Suggested Manual Loop",
            "",
            "1. 人工确认本 checkpoint 的目标和非目标。",
            "2. 如需执行代码修改，另行启动普通 `vega loop` 或主会话人工实现。",
            "3. 修改后使用 reflect/gate/review/finish 形成证据。",
            "4. 再决定是否进入下一个 checkpoint。",
            "",
            "## P0 Boundary",
            "",
            "- 本文件只是计划产物。",
            "- 未调用 worker/reviewer。",
            "- 未修改目标仓库。",
            "- 未写长期 memory。",
        ]
    ).rstrip() + "\n"


def _render_checkpoint_report(
    state: GoalState,
    contract: GoalContract,
    record: GoalCheckpointRecord,
) -> str:
    lines = [
        f"# Checkpoint {record.checkpoint} Report",
        "",
        f"- goal：`{state.run_id}`",
        f"- 仓库：`{state.repo_path}`",
        f"- 状态：`{record.status}`",
        f"- 完成时间：`{record.completed_at or 'unknown'}`",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Evidence Refs",
        "",
    ]
    if record.refs:
        for item in record.refs:
            lines.extend(
                [
                    f"- `{item.type}`：`{item.run}`",
                    f"  - validated：`{item.validated}`",
                    f"  - completion eligible：`{item.completion_eligible}`",
                    f"  - validation：{item.validation_summary or '未提供'}",
                    f"  - note：{item.note or '无'}",
                ]
            )
    else:
        lines.append("- 未挂载证据引用。")
    lines.extend(
        [
            "",
            "## Completion Decision",
            "",
            f"- mode：`{record.completion_mode or 'unknown'}`",
            f"- note：{record.completed_note or '未填写。'}",
        ]
    )
    lines.extend(
        [
            "",
            "## P0 Boundary",
            "",
            "- 本报告只汇总人工挂载的证据引用。",
            "- 未自动调用 worker/reviewer，未修改目标仓库，未写长期 memory。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_stop_report(state: GoalState, contract: GoalContract, reason: str) -> str:
    return "\n".join(
        [
            "# Goal Stop Report",
            "",
            f"- run：`{state.run_id}`",
            f"- 仓库：`{state.repo_path}`",
            f"- 原因：{reason}",
            f"- checkpoint 数：`{state.checkpoint_count}`",
            "",
            "## Objective",
            "",
            contract.objective,
            "",
            "## 结论",
            "",
            "- 已停止继续调度新的 goal step。",
            "- 未自动回滚，未删除文件，未提交代码。",
        ]
    ).rstrip() + "\n"


def _render_recovery_report(state: GoalState, previous_step: str, reason: str) -> str:
    return "\n".join(
        [
            "# Goal Recovery Report",
            "",
            f"- run：`{state.run_id}`",
            f"- 仓库：`{state.repo_path}`",
            f"- 原步骤：`{previous_step}`",
            f"- 原因：{reason}",
            "",
            "## 结论",
            "",
            "- 已将 goal 从 `running` 标记为 `needs_human`。",
            "- 未恢复外部 worker 上下文，未清理工作区，未继续执行。",
            "- 请人工检查目标仓库和 checkpoint 产物后再决定继续、停止或重开。",
        ]
    ).rstrip() + "\n"


def _render_goal_final_report(state: GoalState, contract: GoalContract) -> str:
    lines = [
        "# Goal Final Report",
        "",
        f"- run：`{state.run_id}`",
        f"- 仓库：`{state.repo_path}`",
        f"- 状态：`{state.status}`",
        f"- 完成时间：`{state.completed_at or 'unknown'}`",
        f"- 完成说明：{state.completion_note or '未提供'}",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Success Conditions",
        "",
    ]
    lines.extend(f"- {item}" for item in contract.success_conditions)
    lines.extend(["", "## Checkpoints", ""])
    for record in state.checkpoint_records:
        lines.append(
            f"- `{record.checkpoint}`：`{record.status}`，"
            f"mode=`{record.completion_mode or 'unknown'}`，refs={len(record.refs)}"
        )
    lines.extend(
        [
            "",
            "## Completion Boundary",
            "",
            "- Goal complete 是人工确认后的状态收口，不会自动 commit、push、release。",
            "- 完成结论来自 checkpoint 证据和人工说明，不代表 Vega 自动理解了全部业务正确性。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _run_goal_eval(run_dir: Path, state: GoalState, contract: GoalContract) -> list[str]:
    results = [
        f"{'PASS' if (run_dir / name).exists() else 'FAIL'}: artifact 存在：{name}"
        for name in [
            "state.json",
            "goal-state.json",
            "goal-trace.jsonl",
            "goal-contract.json",
            "progress.md",
            "goal-final-report.md",
        ]
    ]
    results.append(
        "PASS: goal 声明 success conditions"
        if contract.success_conditions
        else "FAIL: goal 未声明 success conditions"
    )
    results.append(
        "PASS: goal 至少包含一个 checkpoint"
        if state.checkpoint_records
        else "FAIL: goal 没有 checkpoint"
    )
    incomplete = [item.checkpoint for item in state.checkpoint_records if item.status != "done"]
    results.append(
        "PASS: 所有 checkpoint 已完成"
        if not incomplete
        else f"FAIL: 未完成 checkpoint：{', '.join(incomplete)}"
    )
    invalid = [
        item.checkpoint
        for item in state.checkpoint_records
        if not _checkpoint_completion_is_valid(item)
    ]
    results.append(
        "PASS: checkpoint 完成记录包含证据和完成模式"
        if not invalid
        else f"FAIL: checkpoint 证据不完整：{', '.join(invalid)}"
    )
    return results


def _render_goal_eval(results: list[str]) -> str:
    return "# Goal Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n"


def _extract_objective(goal_text: str) -> str:
    for line in goal_text.splitlines():
        text = line.strip().lstrip("#").strip()
        if text.lower().startswith("objective:"):
            value = text.split(":", 1)[1].strip()
            if value:
                return value
    for line in goal_text.splitlines():
        text = line.strip().lstrip("#").strip()
        if text and text.lower() != "goal":
            return text
    return goal_text.strip().splitlines()[0].strip()


def _extract_bullets_after(goal_text: str, headings: set[str]) -> list[str]:
    lines = goal_text.splitlines()
    collecting = False
    result: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        normalized = line.strip("#").rstrip(":").strip().lower()
        if collecting and (line.startswith("#") or (line.endswith(":") and not line.startswith("-"))):
            break
        if normalized in headings:
            collecting = True
            continue
        if collecting and line.startswith("-"):
            result.append(line.lstrip("-").strip())
    return result


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


_EVIDENCE_TYPES: set[GoalCheckpointEvidenceType] = {"loop", "reflect", "gate", "review", "finish", "manual"}


def _normalize_checkpoint(checkpoint: str) -> str:
    value = checkpoint.strip()
    if not value:
        raise ValueError("--checkpoint 不能为空。")
    if value.isdigit():
        return f"{int(value):02d}"
    return value


def _find_checkpoint_record(state: GoalState, checkpoint: str) -> GoalCheckpointRecord | None:
    for record in state.checkpoint_records:
        if record.checkpoint == checkpoint:
            return record
    return None


def _active_checkpoint(state: GoalState) -> GoalCheckpointRecord | None:
    active = [record for record in state.checkpoint_records if record.status == "planned"]
    if len(active) > 1:
        raise ValueError(
            "goal state 存在多个 active checkpoint，状态已不一致；请停止并人工检查。"
        )
    return active[0] if active else None


def _checkpoint_completion_is_valid(record: GoalCheckpointRecord) -> bool:
    if record.status != "done" or not record.refs:
        return False
    if record.completion_mode == "validated":
        return any(item.validated and item.completion_eligible for item in record.refs)
    if record.completion_mode == "manual_override":
        return any(item.validated and item.type == "manual" for item in record.refs)
    return False


def _checkpoint_completion_is_current(
    workspace: Path,
    repo_path: Path,
    record: GoalCheckpointRecord,
) -> bool:
    refs, errors = _revalidate_checkpoint_refs(workspace, repo_path, record)
    if errors:
        return False
    refreshed = record.model_copy(update={"refs": refs})
    return _checkpoint_completion_is_valid(refreshed)


def _revalidate_checkpoint_refs(
    workspace: Path,
    repo_path: Path,
    record: GoalCheckpointRecord,
) -> tuple[list[GoalCheckpointRef], list[str]]:
    refreshed: list[GoalCheckpointRef] = []
    errors: list[str] = []
    for reference in record.refs:
        try:
            current = validate_goal_evidence(
                workspace,
                repo_path,
                reference.run,
                reference.type,
                reference.note,
            )
        except (FileNotFoundError, ValueError) as exc:
            errors.append(f"{reference.type}:{reference.run} -> {exc}")
            continue
        current.attached_at = reference.attached_at
        refreshed.append(current)
    return refreshed, errors


def _ensure_action_allowed(state: GoalState, action: str) -> None:
    if action == "resume":
        if state.status == "paused":
            return
        if state.status == "needs_human" and state.current_step == "recovered":
            return
        raise ValueError(f"当前 goal 状态不允许 resume：{state.status}/{state.current_step}")
    if action == "complete" and state.status == "needs_human":
        if state.current_step == "completion_eval_failed":
            return
        raise ValueError(f"当前 goal 状态不允许 complete：{state.status}/{state.current_step}")

    allowed: dict[str, set[str]] = {
        "step": {"created", "running", "checkpoint_done"},
        "attach": {"running"},
        "checkpoint_done": {"running"},
        "pause": {"created", "running", "checkpoint_done"},
        "stop": {
            "created",
            "running",
            "checkpoint_done",
            "paused",
            "needs_human",
            "blocked",
            "timeout",
            "stale",
        },
        "complete": {"checkpoint_done"},
        "recover": {"running"},
    }
    if action not in allowed:
        raise ValueError(f"未知 goal action：{action}")
    if state.status not in allowed[action]:
        raise ValueError(f"当前 goal 状态不允许 {action}：{state.status}")
