from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .decision import DecisionStore
from .goal_evidence import (
    EvidenceFreshness,
    LoopArtifactIntegrity,
    validate_loop_artifact_integrity,
    validate_loop_evidence_freshness,
)
from .loop_engine import (
    preflight_persisted_linear_engine,
    require_persisted_linear_engine,
)
from .memory import MemoryProposalStore
from .models import LoopAutomationState, ReviewVerdict
from .redaction import (
    redact_text,
    redact_value,
    write_redacted_json,
    write_redacted_json_atomic,
    write_redacted_text_atomic,
)
from .run_utils import resolve_run_dir


class FinishRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, run: str, engine: str | None = None) -> Path:
        run_dir = resolve_run_dir(self.workspace, run)
        preflight_persisted_linear_engine(run_dir / "state.json", engine)
        try:
            state = _load_finish_state(run_dir)
        except ValueError as exc:
            _write_finish_diagnostic(run_dir, str(exc))
            raise
        require_persisted_linear_engine(state.engine, engine)
        artifact_integrity = validate_loop_artifact_integrity(
            self.workspace,
            Path(state.repo_path),
            run_dir,
            state=state,
        )
        freshness = validate_loop_evidence_freshness(
            self.workspace,
            Path(state.repo_path),
            run_dir,
        )
        summary = build_finish_summary(
            run_dir,
            state,
            freshness,
            artifact_integrity,
        )
        report = render_finish_report(summary)
        summary["finish_report_ref"] = "finish-report.md"
        summary["finish_report_sha256"] = hashlib.sha256(
            redact_text(report).encode("utf-8")
        ).hexdigest()
        write_redacted_text_atomic(run_dir / "finish-report.md", report)
        write_redacted_json_atomic(run_dir / "finish-summary.json", summary)
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
    has_verification_failures = any((item.get("failed_count") or 0) > 0 for item in verification_results)
    final_report = _read_text(run_dir / "final-report.md")
    finish_status = _finish_status(
        state,
        latest_verdict,
        has_verification_failures,
        evidence_fresh=evidence_freshness.fresh,
        artifact_integrity_valid=artifact_integrity.valid,
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
        "iterations": [item.model_dump() for item in state.iterations],
        "latest_verdict": latest_verdict.model_dump() if latest_verdict else None,
        "review_verdicts": [item.model_dump() for item in verdicts],
        "test_summary_count": len(test_summaries),
        "diff_summary_count": len(diff_summaries),
        "verification_results": verification_results,
        "risk_gate_results": [item.model_dump() for item in risk_gate_results],
        "has_verification_failures": has_verification_failures,
        "artifact_integrity": artifact_integrity.as_dict(),
        "evidence_freshness": evidence_freshness.as_dict(),
        "memory_proposals": [proposal.model_dump() for proposal in proposals],
        "decisions": [decision.model_dump() for decision in decisions],
        "key_artifacts": _key_artifacts(run_dir),
        "commit_checklist": _commit_checklist(
            state,
            latest_verdict,
            test_summaries,
            has_verification_failures,
            evidence_fresh=evidence_freshness.fresh,
            artifact_integrity_valid=artifact_integrity.valid,
        ),
        "handoff_notes": _handoff_notes(
            state,
            latest_verdict,
            final_report,
            has_verification_failures,
            evidence_fresh=evidence_freshness.fresh,
            artifact_integrity_valid=artifact_integrity.valid,
        ),
    })


def render_finish_report(summary: dict[str, Any]) -> str:
    latest = summary.get("latest_verdict") or {}
    lines = [
        "# Finish Report",
        "",
        f"- run：`{summary['run_id']}`",
        f"- 仓库：`{summary['repo_path']}`",
        f"- 任务类型：`{summary['task_mode']}`",
        f"- 自动化模式：`{summary['automation_mode']}`",
        f"- loop 状态：`{summary['loop_status']}`",
        f"- finish 状态：`{summary['finish_status']}`",
        f"- 最新 reviewer verdict：`{latest.get('verdict', '无')}`",
        "",
        "## 结论",
        "",
    ]
    lines.extend(f"- {item}" for item in summary["handoff_notes"])
    freshness = summary.get("evidence_freshness") or {}
    lines.extend(
        [
            "",
            "## 证据新鲜度",
            "",
            f"- 状态：`{'fresh' if freshness.get('fresh') else 'stale'}`",
            f"- trusted fingerprint：`{freshness.get('trusted_workspace_fingerprint') or 'missing'}`",
            f"- current fingerprint：`{freshness.get('current_workspace_fingerprint') or 'missing'}`",
        ]
    )
    if freshness.get("issues"):
        lines.append(f"- issues：`{', '.join(freshness['issues'])}`")
    integrity = summary.get("artifact_integrity") or {}
    lines.extend(
        [
            "",
            "## Artifact 完整性",
            "",
            f"- 状态：`{'valid' if integrity.get('valid') else 'invalid'}`",
            f"- 可信 reviewer verdict 数：`{integrity.get('review_verdict_count', 0)}`",
            f"- 可信 verification result 数：`{integrity.get('verification_result_count', 0)}`",
            f"- 可信 risk gate result 数：`{integrity.get('risk_gate_result_count', 0)}`",
        ]
    )
    if integrity.get("issues"):
        lines.append(f"- issues：`{', '.join(integrity['issues'])}`")
    lines.extend(["", "## Commit 前 Checklist", ""])
    lines.extend(f"- {item}" for item in summary["commit_checklist"])
    lines.extend(["", "## 迭代记录", ""])
    if summary["iterations"]:
        for item in summary["iterations"]:
            lines.append(
                f"- 第 {item['iteration']} 轮：worker=`{item['worker_status']}`，"
                f"verification=`{item.get('verification_status', 'skipped')}`，"
                f"verdict=`{item.get('verdict') or '无'}`，findings={item.get('findings_count', 0)}"
            )
    else:
        lines.append("- 尚未产生迭代记录。")
    lines.extend(["", "## 验证门禁", ""])
    if summary.get("verification_results"):
        lines.append(
            "- 结果："
            + ("存在失败，不能进入 ready_to_commit。" if summary.get("has_verification_failures") else "未发现失败。")
        )
        for item in summary["verification_results"]:
            lines.append(
                f"- `{item.get('path')}`：commands={item.get('command_count', 0)}，"
                f"failed={item.get('failed_count', 0)}"
            )
    else:
        lines.append("- 未发现自动验证结果；提交前建议补充最小验证。")
    if summary["memory_proposals"]:
        lines.extend(["", "## 可选 Memory Proposal", ""])
        for proposal in summary["memory_proposals"]:
            lines.append(f"- `{proposal['id']}`：{proposal['title']}")
    lines.extend(["", "## 人工决策", ""])
    if summary["decisions"]:
        for decision in summary["decisions"]:
            lines.append(
                f"- `{decision['type']}` / `{decision['decision']}`：{decision['reason']} "
                f"（actor: {decision['actor']}）"
            )
    else:
        lines.append("- 暂无人工 decision 记录。")
    lines.extend(["", "## 关键产物", ""])
    lines.extend(f"- `{item}`" for item in summary["key_artifacts"])
    lines.extend(
        [
            "",
            "## 明确边界",
            "",
            "- Finish 不会自动 commit、push、release。",
            "- Finish 不会自动接受 memory proposal。",
            "- 是否提交、是否显式生成并接受经验候选，仍由用户人工决定。",
        ]
    )
    return redact_text("\n".join(lines).rstrip() + "\n")


def _finish_status(
    state: LoopAutomationState,
    latest_verdict: ReviewVerdict | None,
    has_verification_failures: bool = False,
    *,
    evidence_fresh: bool = True,
    artifact_integrity_valid: bool = True,
) -> str:
    if not artifact_integrity_valid:
        return "needs_human"
    if not evidence_fresh:
        return "needs_human"
    if has_verification_failures:
        return "needs_fix"
    if state.status == "success" and latest_verdict and latest_verdict.verdict == "approve":
        return "ready_to_commit"
    if latest_verdict and latest_verdict.verdict == "request_changes":
        return "needs_fix"
    if state.status in {"failed", "needs_human"}:
        return "needs_human"
    return "incomplete"


def _commit_checklist(
    state: LoopAutomationState,
    latest_verdict: ReviewVerdict | None,
    test_summaries: list[str],
    has_verification_failures: bool = False,
    *,
    evidence_fresh: bool = True,
    artifact_integrity_valid: bool = True,
) -> list[str]:
    checklist = [
        "人工检查 `git diff`，确认没有无关文件或调试残留。",
        "确认没有自动 commit、push、release 或写入长期 memory。",
    ]
    if latest_verdict and latest_verdict.verdict == "approve":
        checklist.append("隔离 reviewer 已 approve。")
    else:
        checklist.append("隔离 reviewer 未 approve；提交前需要人工确认。")
    if has_verification_failures:
        checklist.append("自动验证存在失败；修复并重新验证前不能进入提交。")
    if artifact_integrity_valid:
        checklist.append("迭代 artifact 已与 state 和可信 child review run 完成一致性校验。")
    else:
        checklist.append("迭代 artifact 缺失、损坏或未绑定；修复证据链前不能进入提交。")
    if evidence_fresh:
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
    has_verification_failures: bool = False,
    *,
    evidence_fresh: bool = True,
    artifact_integrity_valid: bool = True,
) -> list[str]:
    notes = []
    if not artifact_integrity_valid:
        notes.append("迭代 artifact 完整性校验失败，未采用未绑定或损坏的 verdict/verification。")
    if not evidence_fresh:
        notes.append("review 后工作区发生变化或可信快照缺失，现有 approve 已失效。")
    if has_verification_failures:
        notes.append("自动验证存在失败，不能仅凭 reviewer approve 进入 ready_to_commit。")
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
