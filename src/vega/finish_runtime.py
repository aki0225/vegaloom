from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .decision import DecisionStore
from .finish_presentation import build_finish_first_screen, render_finish_report
from .finish_policy import decide_finish_status
from .loop_evidence import (
    EvidenceFreshness,
    validate_loop_evidence_snapshot,
)
from .loop_integrity import (
    LoopArtifactIntegrity,
    latest_verification_failed,
    trusted_verification_passed,
)
from .memory_artifacts import MemoryProposalStore
from .models import LoopAutomationState, ReviewVerdict
from .redaction import redact_text, redact_value, write_redacted_json, write_redacted_text
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir


class FinishRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, run: str) -> Path:
        run_dir = resolve_run_dir(self.workspace, run)
        with RunMutationLock.acquire(run_dir, "loop.finish"):
            return self._run_locked(run_dir)

    def _run_locked(self, run_dir: Path) -> Path:
        try:
            state = _load_finish_state(run_dir)
        except ValueError as exc:
            _write_finish_diagnostic(run_dir, str(exc))
            raise
        validation_snapshot = validate_loop_evidence_snapshot(
            self.workspace,
            Path(state.repo_path),
            run_dir,
            state=state,
        )
        summary = build_finish_summary(
            run_dir,
            state,
            validation_snapshot.evidence_freshness,
            validation_snapshot.artifact_integrity,
        )
        write_redacted_json(run_dir / "finish-summary.json", summary)
        write_redacted_text(run_dir / "finish-report.md", render_finish_report(summary))
        return run_dir


def build_finish_summary(
    run_dir: Path,
    state: LoopAutomationState,
    evidence_freshness: EvidenceFreshness,
    artifact_integrity: LoopArtifactIntegrity,
) -> dict[str, Any]:
    verdicts = list(artifact_integrity.review_verdicts)
    latest_verdict = verdicts[-1] if verdicts else None
    proposals = MemoryProposalStore(run_dir).list()
    decisions = DecisionStore(run_dir).list()
    test_summaries = _collect_iteration_text(run_dir, "test-summary.md")
    diff_summaries = _collect_iteration_text(run_dir, "diff-summary.md")
    verification_results = list(artifact_integrity.verification_results)
    risk_gate_results = list(artifact_integrity.risk_gate_results)
    has_verification_failures = any(
        (item.get("failed_count") or 0) > 0 for item in verification_results
    )
    latest_verification_has_failed = latest_verification_failed(
        state,
        artifact_integrity,
    )
    verification_passed = trusted_verification_passed(state, artifact_integrity)
    final_report = _read_text(run_dir / "final-report.md")
    finish_status = decide_finish_status(
        state.status,
        latest_verdict.verdict if latest_verdict else None,
        latest_verification_has_failed,
        verification_passed=verification_passed,
        evidence_fresh=evidence_freshness.fresh,
        artifact_integrity_valid=artifact_integrity.valid,
    )
    commit_checklist = _commit_checklist(
        state,
        latest_verdict,
        test_summaries,
        latest_verification_has_failed,
        verification_passed=verification_passed,
        evidence_fresh=evidence_freshness.fresh,
        artifact_integrity_valid=artifact_integrity.valid,
    )
    handoff_notes = _handoff_notes(
        state,
        latest_verdict,
        final_report,
        latest_verification_has_failed,
        has_historical_verification_failures=has_verification_failures,
        verification_passed=verification_passed,
        evidence_fresh=evidence_freshness.fresh,
        artifact_integrity_valid=artifact_integrity.valid,
    )
    first_screen = build_finish_first_screen(
        state,
        finish_status,
        latest_verdict,
        verification_results,
        risk_gate_results,
        artifact_integrity,
        evidence_freshness,
        handoff_notes,
        latest_verification_failed=latest_verification_has_failed,
        verification_passed=verification_passed,
        has_verification_failures=has_verification_failures,
    )
    return redact_value({
        "run_id": state.run_id,
        "run_dir": str(run_dir.resolve()),
        "repo_path": state.repo_path,
        "task_mode": state.task_mode,
        "automation_mode": state.automation_mode,
        "loop_status": state.status,
        "finish_status": finish_status,
        "created_at": datetime.now(UTC).isoformat(),
        "first_screen": first_screen,
        "iterations": [item.model_dump() for item in state.iterations],
        "latest_verdict": latest_verdict.model_dump() if latest_verdict else None,
        "review_verdicts": [item.model_dump() for item in verdicts],
        "test_summary_count": len(test_summaries),
        "diff_summary_count": len(diff_summaries),
        "verification_results": verification_results,
        "risk_gate_results": [item.model_dump() for item in risk_gate_results],
        "has_verification_failures": has_verification_failures,
        "latest_verification_failed": latest_verification_has_failed,
        "verification_passed": verification_passed,
        "artifact_integrity": artifact_integrity.as_dict(),
        "evidence_freshness": evidence_freshness.as_dict(),
        "memory_proposals": [proposal.model_dump() for proposal in proposals],
        "decisions": [decision.model_dump() for decision in decisions],
        "key_artifacts": _key_artifacts(run_dir),
        "commit_checklist": commit_checklist,
        "handoff_notes": handoff_notes,
    })


def _commit_checklist(
    state: LoopAutomationState,
    latest_verdict: ReviewVerdict | None,
    test_summaries: list[str],
    latest_verification_failed: bool = False,
    *,
    verification_passed: bool = False,
    evidence_fresh: bool = True,
    artifact_integrity_valid: bool = True,
) -> list[str]:
    checklist = [
        "人工检查 `git diff`，确认没有无关文件或调试残留。",
        "确认当前 Git 提交状态与运行模式一致，且没有自动 push、merge、release 或写入长期 memory。",
    ]
    if latest_verdict and latest_verdict.verdict == "approve":
        checklist.append("隔离 reviewer 已 approve。")
    else:
        checklist.append("隔离 reviewer 未 approve；提交前需要人工确认。")
    if latest_verification_failed:
        checklist.append("自动验证存在失败；修复并重新验证前不能进入提交。")
    elif verification_passed:
        checklist.append("最新 iteration 存在受信、非空且全部通过的结构化验证。")
    else:
        checklist.append("最新 iteration 缺少受信的结构化验证通过证据；不能自动进入提交。")
    if artifact_integrity_valid:
        if latest_verdict is None:
            checklist.append("现有迭代 artifact 已与 state 完成一致性校验；尚无可信 review 产物。")
        else:
            checklist.append("迭代 artifact 已与 state 和可信 child review run 完成一致性校验。")
    else:
        checklist.append("迭代 artifact 缺失、损坏或未绑定；修复证据链前不能进入提交。")
    if latest_verdict is None:
        checklist.append("尚未获得可信 reviewer 结论；完成独立审查前不能进入提交。")
    elif evidence_fresh:
        checklist.append("当前仓库快照与通过 reviewer 时的可信指纹一致。")
    else:
        checklist.append("review 后仓库快照已变化；必须重新 reflect/review。")
    effective_test_summaries = [
        text
        for text in test_summaries
        if "未提供测试日志" not in text and "未识别自动验证命令" not in text
    ]
    if effective_test_summaries:
        checklist.append("已发现测试摘要；提交前确认测试命令和结果可信。")
    else:
        checklist.append("未发现有效测试摘要；提交前建议补充最小验证。")
    if state.memory_proposals:
        checklist.append("如需沉淀经验，人工 review memory-proposals.jsonl 后再 accept。")
    return checklist


def _handoff_notes(
    state: LoopAutomationState,
    latest_verdict: ReviewVerdict | None,
    final_report: str,
    latest_verification_failed: bool = False,
    *,
    has_historical_verification_failures: bool = False,
    verification_passed: bool = False,
    evidence_fresh: bool = True,
    artifact_integrity_valid: bool = True,
) -> list[str]:
    notes = []
    if not artifact_integrity_valid:
        notes.append("迭代 artifact 完整性校验失败，未采用未绑定或损坏的 verdict/verification。")
    if not evidence_fresh:
        freshness_note = (
            "尚未产生可采用的 reviewer 快照，不能确认独立审查已经完成。"
            if latest_verdict is None
            else "reviewer 结论对应的工作区快照已变化，现有结论已失效。"
        )
        notes.append(freshness_note)
    if latest_verification_failed:
        notes.append("自动验证存在失败，不能进入 ready_to_commit。")
    elif not verification_passed:
        notes.append("自动验证结论未知，不能进入 ready_to_commit。")
    elif has_historical_verification_failures:
        notes.append("历史 iteration 曾验证失败，最新 iteration 已取得受信通过。")
    if latest_verdict:
        notes.append(f"最新 reviewer 结论：{latest_verdict.verdict}，{latest_verdict.summary}")
    else:
        notes.append("尚未发现 reviewer verdict，不能视为完成。")
    if final_report:
        notes.append("已存在 final-report.md，可作为交付总结基础。")
    else:
        notes.append("尚未生成 final-report.md，需要先完成 loop continue 或人工总结。")
    if state.status == "success":
        notes.append("loop 状态为 success，可以进入人工提交前检查。")
    elif state.status == "needs_human":
        notes.append("loop 状态为 needs_human，需要人工判断后续修复或审查。")
    else:
        notes.append(f"loop 状态为 {state.status}，不建议直接提交。")
    return notes


def _collect_iteration_text(run_dir: Path, filename: str) -> list[str]:
    return [_read_text(path) for path in sorted(run_dir.glob(f"iterations/*/{filename}"))]


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return redact_text(path.read_text(encoding="utf-8", errors="replace"))


def _key_artifacts(run_dir: Path) -> list[str]:
    candidates = [
        "agent-brief.md",
        "project-context.md",
        "worker-prompt.md",
        "final-report.md",
        "finish-report.md",
        "finish-summary.json",
        "memory-proposals.jsonl",
        "decisions.jsonl",
        "eval.md",
    ]
    result = [str((run_dir / item).resolve()) for item in candidates if (run_dir / item).exists()]
    result.extend(str(path.resolve()) for path in sorted(run_dir.glob("iterations/*/fix-prompt.md")))
    result.extend(str(path.resolve()) for path in sorted(run_dir.glob("iterations/*/verification-summary.md")))
    result.extend(str(path.resolve()) for path in sorted(run_dir.glob("iterations/*/review-findings.md")))
    result.extend(str(path.resolve()) for path in sorted(run_dir.glob("iterations/*/review-verdict.json")))
    return result


def _load_finish_state(run_dir: Path) -> LoopAutomationState:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise ValueError(f"loop state.json 缺失，无法执行 Finish：{state_path}")
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"loop state.json 无法读取：{type(exc).__name__}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"loop state.json 已损坏，无法解析 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("loop state.json schema 不合法：顶层必须是 JSON object")
    if "automation_mode" not in payload:
        raise ValueError("指定 run 不是 loop automation run")
    try:
        state = LoopAutomationState.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"loop state.json schema 不合法：{exc.errors()[0]['type']}") from exc
    if state.run_id != run_dir.name:
        raise ValueError(
            "loop state.run_id 与 run 目录身份不一致，疑似移植了其他证据链。"
        )
    return state


def _write_finish_diagnostic(run_dir: Path, message: str) -> None:
    write_redacted_json(
        run_dir / "finish-diagnostic.json",
        {
            "status": "failed",
            "message": message,
            "created_at": datetime.now(UTC).isoformat(),
            "run_dir": str(run_dir.resolve()),
        },
    )
