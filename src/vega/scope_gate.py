from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import LoopIterationState, ScopeGateViolation
from .project_config import ScopeConfig, scope_policy_sha256
from .redaction import redact_text
from .trace import TraceWriter
from .workspace_check import capture_tracked_scope_snapshot

SCOPE_GATE_RESULT_ARTIFACT = "scope-gate-result.json"
SCOPE_GATE_REPORT_ARTIFACT = "scope-gate-report.md"
SCOPE_GATE_POST_VERIFICATION_RESULT_ARTIFACT = "scope-gate-post-verification-result.json"
SCOPE_GATE_POST_VERIFICATION_REPORT_ARTIFACT = "scope-gate-post-verification-report.md"
SCOPE_GATE_PRE_REVIEW_RESULT_ARTIFACT = "scope-gate-pre-review-result.json"
SCOPE_GATE_PRE_REVIEW_REPORT_ARTIFACT = "scope-gate-pre-review-report.md"
REPORT_BINDING_START = "<!-- vega-scope-gate-binding\n"
REPORT_BINDING_END = "\n-->"
ScopeGatePhase = Literal["pre_verification", "post_verification", "pre_review"]


class ScopeGateResult(BaseModel):
    """一次精确路径范围检查的完整、可落盘结果。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["skipped", "success", "failed"]
    iteration: int = Field(ge=1)
    phase: ScopeGatePhase
    scope_policy_sha256: str | None = None
    expected_head_sha: str | None = None
    current_head_sha: str | None = None
    workspace_status_sha256: str | None = None
    index_flags_sha256: str | None = None
    changed_paths_sha256: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    staged_changed_files: list[str] = Field(default_factory=list)
    unstaged_changed_files: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    unsafe_index_paths: list[str] = Field(default_factory=list)
    violations: list[ScopeGateViolation] = Field(default_factory=list)
    failure_code: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class LoopScopeGateEvidence:
    """iteration-local scope gate 的落盘证据摘要。"""

    result: ScopeGateResult
    result_sha256: str
    report_sha256: str

    @property
    def passed(self) -> bool:
        return self.result.status in {"skipped", "success"}


@dataclass(frozen=True)
class LoopScopeGateArtifactIntegrity:
    """scope gate artifact 与 iteration state 的绑定校验结果。"""

    valid: bool
    issues: tuple[str, ...]
    evaluated: bool
    result: ScopeGateResult | None = None


def evaluate_scope_gate(
    repo_path: Path,
    scope: ScopeConfig,
    *,
    iteration: int,
    phase: ScopeGatePhase,
    expected_head_sha: str | None = None,
    expected_policy_sha256: str | None = None,
) -> ScopeGateResult:
    """读取 staged/unstaged tracked diff，并按精确仓库相对 glob 判定范围。

    禁止规则优先于允许规则；只要有一条越界路径，整体结果即为 failed。此函数不清理、
    不回滚、也不尝试阻止操作系统写入，只提供后续流程必须遵守的 fail-closed 决策。
    """
    policy_sha256 = scope_policy_sha256(scope)
    try:
        snapshot = capture_tracked_scope_snapshot(repo_path.resolve())
    except Exception as exc:  # noqa: BLE001 - Git 读取失败必须停止后续自动流程
        return ScopeGateResult(
            status="failed",
            iteration=iteration,
            phase=phase,
            scope_policy_sha256=policy_sha256,
            expected_head_sha=expected_head_sha,
            allowed_paths=list(scope.allowed_paths),
            forbidden_paths=list(scope.forbidden_paths),
            failure_code="scope_evaluation_failed",
            diagnostic=redact_text(f"{type(exc).__name__}: {exc}")[:1000],
        )

    raw_staged_files = list(snapshot.staged_files)
    raw_unstaged_files = list(snapshot.unstaged_files)
    changed_paths_sha256 = _changed_paths_sha256(raw_staged_files, raw_unstaged_files)
    staged_files, staged_redacted = _safe_machine_paths(raw_staged_files)
    unstaged_files, unstaged_redacted = _safe_machine_paths(raw_unstaged_files)
    unsafe_index_paths, _ = _safe_machine_paths(
        list(snapshot.unsafe_index_paths)
    )
    changed_files = list(dict.fromkeys([*staged_files, *unstaged_files]))
    result_context = {
        "iteration": iteration,
        "phase": phase,
        "scope_policy_sha256": policy_sha256,
        "expected_head_sha": expected_head_sha,
        "current_head_sha": snapshot.head_sha,
        "workspace_status_sha256": snapshot.status_sha256,
        "index_flags_sha256": snapshot.index_flags_sha256,
        "changed_paths_sha256": changed_paths_sha256,
        "allowed_paths": list(scope.allowed_paths),
        "forbidden_paths": list(scope.forbidden_paths),
        "staged_changed_files": staged_files,
        "unstaged_changed_files": unstaged_files,
        "changed_files": changed_files,
        "unsafe_index_paths": unsafe_index_paths,
    }
    if expected_policy_sha256 and policy_sha256 != expected_policy_sha256:
        return ScopeGateResult(
            status="failed",
            **result_context,
            failure_code="scope_policy_mismatch",
            diagnostic="当前 scope 规则与 loop 启动时策略摘要不一致。",
        )
    if expected_head_sha and snapshot.head_sha != expected_head_sha:
        return ScopeGateResult(
            status="failed",
            **result_context,
            failure_code="scope_head_changed",
            diagnostic="Git HEAD 在 loop 启动后发生变化；已拒绝继续自动验证或审查。",
        )
    if unsafe_index_paths:
        return ScopeGateResult(
            status="failed",
            **result_context,
            failure_code="scope_index_flags_unsafe",
            diagnostic=(
                "仓库存在 assume-unchanged 或 skip-worktree 标记；"
                "Git 状态无法作为完整范围证据。"
            ),
        )
    if staged_redacted or unstaged_redacted:
        return ScopeGateResult(
            status="failed",
            **result_context,
            failure_code="scope_path_identity_unsafe",
            diagnostic="tracked diff 包含会触发脱敏的路径；无法安全保存稳定机器身份。",
        )
    if not scope.allowed_paths and not scope.forbidden_paths:
        return ScopeGateResult(
            status="skipped",
            **result_context,
        )

    violations: list[ScopeGateViolation] = []
    for path in changed_files:
        if not _is_safe_repo_relative_path(path):
            violations.append(
                ScopeGateViolation(
                    code="unsafe_changed_path",
                    path=path,
                )
            )
            continue
        forbidden_matches = _matching_patterns(path, scope.forbidden_paths)
        if forbidden_matches:
            violations.append(
                ScopeGateViolation(
                    code="forbidden_path",
                    path=path,
                    matched_patterns=forbidden_matches,
                )
            )
            continue
        if scope.allowed_paths:
            allowed_matches = _matching_patterns(path, scope.allowed_paths)
            if not allowed_matches:
                violations.append(
                    ScopeGateViolation(
                        code="outside_allowed_paths",
                        path=path,
                    )
                )

    return ScopeGateResult(
        status="failed" if violations else "success",
        **result_context,
        violations=violations,
    )


def write_loop_scope_gate_evidence(
    repo_path: Path,
    scope: ScopeConfig,
    iteration_dir: Path,
    trace: TraceWriter,
    *,
    iteration: int,
    phase: ScopeGatePhase,
    expected_head_sha: str | None = None,
    expected_policy_sha256: str | None = None,
) -> LoopScopeGateEvidence:
    """执行 scope gate，并把结果、报告和 trace 与当前 iteration 绑定。"""
    result = evaluate_scope_gate(
        repo_path,
        scope,
        iteration=iteration,
        phase=phase,
        expected_head_sha=expected_head_sha,
        expected_policy_sha256=expected_policy_sha256,
    )
    result_text = json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    result_artifact, report_artifact = _artifact_names(phase)
    result_path = iteration_dir / result_artifact
    result_path.write_text(result_text, encoding="utf-8", newline="\n")
    result_sha256 = _sha256_text(result_text)

    report_text = _render_scope_gate_report(result, result_sha256)
    report_path = iteration_dir / report_artifact
    report_path.write_text(report_text, encoding="utf-8", newline="\n")
    report_sha256 = _sha256_text(report_text)

    event = "scope_gate_failed" if result.status == "failed" else "scope_gate_finished"
    trace.write(
        event,
        iteration=iteration,
        phase=phase,
        status=result.status,
        changed_files=result.changed_files,
        violation_codes=[violation.code for violation in result.violations],
        failure_code=result.failure_code,
        result_sha256=result_sha256,
        report_sha256=report_sha256,
    )
    return LoopScopeGateEvidence(
        result=result,
        result_sha256=result_sha256,
        report_sha256=report_sha256,
    )


def scope_gate_state_fields(
    evidence: LoopScopeGateEvidence,
    *,
    phase: ScopeGatePhase,
) -> dict[str, object]:
    """将 artifact 摘要写入 iteration state，供 eval 与 Finish 交叉校验。"""
    if phase == "post_verification":
        return {
            "scope_gate_post_verification_status": evidence.result.status,
            "scope_gate_post_verification_changed_files": evidence.result.changed_files,
            "scope_gate_post_verification_violations": evidence.result.violations,
            "scope_gate_post_verification_result_sha256": evidence.result_sha256,
            "scope_gate_post_verification_report_sha256": evidence.report_sha256,
        }
    if phase == "pre_review":
        return {
            "scope_gate_pre_review_status": evidence.result.status,
            "scope_gate_pre_review_changed_files": evidence.result.changed_files,
            "scope_gate_pre_review_violations": evidence.result.violations,
            "scope_gate_pre_review_result_sha256": evidence.result_sha256,
            "scope_gate_pre_review_report_sha256": evidence.report_sha256,
        }
    return {
        "scope_gate_status": evidence.result.status,
        "scope_gate_changed_files": evidence.result.changed_files,
        "scope_gate_violations": evidence.result.violations,
        "scope_gate_result_sha256": evidence.result_sha256,
        "scope_gate_report_sha256": evidence.report_sha256,
    }


def validate_iteration_scope_gate_artifacts(
    iteration_dir: Path,
    iteration: LoopIterationState,
    *,
    phase: ScopeGatePhase,
    repo_path: Path | None = None,
    trace_path: Path | None = None,
    required: bool = False,
    expected_head_sha: str | None = None,
    expected_policy_sha256: str | None = None,
) -> LoopScopeGateArtifactIntegrity:
    """校验 scope gate 的 result/report/state/trace，并对最新 iteration 重算。

    老 run 或在 workspace gate 前停止的 iteration 不会包含该证据；一旦已经进入
    verification、Reflect 或 reviewer，则 scope gate 必须有可校验的 binding。
    """
    result_artifact, report_artifact = _artifact_names(phase)
    result_path = iteration_dir / result_artifact
    report_path = iteration_dir / report_artifact
    state_fields = _state_fields(iteration, phase)
    has_artifact = result_path.exists() or report_path.exists()
    state_has_binding = any(
        [
            bool(state_fields["changed_files"]),
            bool(state_fields["violations"]),
            state_fields["result_sha256"] is not None,
            state_fields["report_sha256"] is not None,
        ]
    )
    downstream_started = _downstream_started(iteration, phase)
    if not has_artifact and not state_has_binding:
        issues = (
            ["scope_gate_required_before_downstream_steps"]
            if required and downstream_started
            else []
        )
        return _integrity(issues, evaluated=False)

    issues: list[str] = []
    result_text = _read_text(result_path, "scope_gate_result", issues)
    report_text = _read_text(report_path, "scope_gate_report", issues)
    _validate_hash(
        result_text,
        state_fields["result_sha256"],
        "scope_gate_result",
        issues,
    )
    _validate_hash(
        report_text,
        state_fields["report_sha256"],
        "scope_gate_report",
        issues,
    )
    payload = _load_json_object(result_text, issues)
    report_binding = _load_report_binding(report_text, report_path, issues)
    if payload is None:
        return _integrity(issues, evaluated=True)

    try:
        result = ScopeGateResult.model_validate(payload)
    except ValidationError:
        issues.append("scope_gate_result_schema_invalid")
        return _integrity(issues, evaluated=True)

    if result.status != state_fields["status"]:
        issues.append("scope_gate_status_mismatch")
    if result.iteration != iteration.iteration:
        issues.append("scope_gate_iteration_mismatch")
    if result.phase != phase:
        issues.append("scope_gate_phase_mismatch")
    if (
        expected_policy_sha256 is not None
        and result.scope_policy_sha256 != expected_policy_sha256
    ):
        issues.append("scope_gate_policy_hash_mismatch")
    if expected_head_sha is not None and result.expected_head_sha != expected_head_sha:
        issues.append("scope_gate_expected_head_mismatch")
    if result.changed_files != state_fields["changed_files"]:
        issues.append("scope_gate_changed_files_mismatch")
    if _violations_payload(result.violations) != _violations_payload(state_fields["violations"]):
        issues.append("scope_gate_violations_mismatch")
    if result.status == "skipped" and (result.allowed_paths or result.forbidden_paths):
        issues.append("scope_gate_skipped_with_rules")
    if result.status == "success" and (result.violations or result.failure_code):
        issues.append("scope_gate_success_result_inconsistent")
    if result.status == "failed" and not (result.violations or result.failure_code):
        issues.append("scope_gate_failure_reason_missing")
    if result.status == "failed" and downstream_started:
        issues.append("scope_gate_failure_bypassed")

    _validate_report_binding(report_binding, iteration, phase, state_fields, issues)
    _validate_trace_binding(
        trace_path,
        iteration,
        phase,
        state_fields,
        result,
        issues,
        required=required,
    )
    _validate_recomputed_result(
        repo_path,
        result,
        issues,
        expected_head_sha=expected_head_sha,
        expected_policy_sha256=expected_policy_sha256,
    )
    return _integrity(issues, evaluated=True, result=result)


def _safe_machine_paths(paths: list[str]) -> tuple[list[str], bool]:
    safe_paths: list[str] = []
    redacted = False
    for path in paths:
        safe_path = redact_text(path)
        if safe_path != path:
            redacted = True
            safe_path = f"<redacted-path-{hashlib.sha256(path.encode('utf-8')).hexdigest()[:16]}>"
        safe_paths.append(safe_path)
    return safe_paths, redacted


def _changed_paths_sha256(staged_files: list[str], unstaged_files: list[str]) -> str:
    payload = json.dumps(
        {
            "staged": staged_files,
            "unstaged": unstaged_files,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _matching_patterns(path: str, patterns: list[str]) -> list[str]:
    return [pattern for pattern in patterns if _path_matches_pattern(path, pattern)]


def _path_matches_pattern(path: str, pattern: str) -> bool:
    """以 segment 为边界匹配 POSIX glob，`**` 仅表示零到多个完整目录段。"""
    path_segments = tuple(path.split("/"))
    pattern_segments = tuple(pattern.split("/"))

    @lru_cache(maxsize=None)
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_segments):
            return path_index == len(path_segments)
        current_pattern = pattern_segments[pattern_index]
        if current_pattern == "**":
            if pattern_index == len(pattern_segments) - 1:
                return True
            return any(
                matches(next_path_index, pattern_index + 1)
                for next_path_index in range(path_index, len(path_segments) + 1)
            )
        if path_index == len(path_segments):
            return False
        if not fnmatchcase(path_segments[path_index], current_pattern):
            return False
        return matches(path_index + 1, pattern_index + 1)

    return matches(0, 0)


def _is_safe_repo_relative_path(path: str) -> bool:
    if not path or path != path.strip():
        return False
    if any(character in path for character in ("\r", "\n", "\0")):
        return False
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in path):
        return False
    if path.startswith(("/", "\\")) or "\\" in path:
        return False
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        return False
    return all(segment not in {"", ".", ".."} for segment in path.split("/"))


def _render_scope_gate_report(result: ScopeGateResult, result_sha256: str) -> str:
    lines = [
        "# 精确路径范围门禁报告",
        "",
        f"- iteration：`{result.iteration:02d}`",
        f"- 阶段：`{result.phase}`",
        f"- 状态：`{result.status}`",
        f"- 启动 HEAD：`{result.expected_head_sha or '未绑定'}`",
        f"- 当前 HEAD：`{result.current_head_sha or '无法读取'}`",
        f"- scope policy SHA-256：`{result.scope_policy_sha256 or '未绑定'}`",
        f"- index flags SHA-256：`{result.index_flags_sha256 or '无法读取'}`",
        f"- staged tracked 文件数：`{len(result.staged_changed_files)}`",
        f"- unstaged tracked 文件数：`{len(result.unstaged_changed_files)}`",
        "",
        "## 规则",
        "",
        "- allowed_paths："
        + (
            "、".join(f"`{item}`" for item in result.allowed_paths)
            if result.allowed_paths
            else "未配置"
        ),
        "- forbidden_paths："
        + (
            "、".join(f"`{item}`" for item in result.forbidden_paths)
            if result.forbidden_paths
            else "未配置"
        ),
        "",
        "## 当前 tracked diff",
        "",
    ]
    if result.changed_files:
        lines.extend(f"- `{path}`" for path in result.changed_files)
    else:
        lines.append("- 无。")
    if result.unsafe_index_paths:
        lines.extend(["", "## 不安全 index 标记", ""])
        lines.extend(f"- `{path}`" for path in result.unsafe_index_paths)
    lines.extend(["", "## 结论", ""])
    if result.status == "skipped":
        lines.append("- 未配置精确路径范围；为兼容既有项目，本轮未限制 tracked diff。")
    elif result.status == "success":
        lines.append("- 当前 tracked diff 全部符合精确路径范围，可进入当前阶段的后续流程。")
    elif result.violations:
        lines.append("- 检测到越界 tracked diff；Vega 已停止当前阶段的后续流程。")
        for violation in result.violations:
            patterns = (
                "、".join(f"`{item}`" for item in violation.matched_patterns)
                if violation.matched_patterns
                else "无匹配 allowlist"
            )
            lines.append(f"- `{violation.code}`：`{violation.path}`；规则：{patterns}")
    else:
        lines.append("- scope gate 无法给出可放行结论；Vega 已 fail-closed。")
        if result.failure_code:
            lines.append(f"- failure code：`{result.failure_code}`")
        lines.append(f"- 诊断：{result.diagnostic or '未提供'}")

    binding = {
        "schema_version": 1,
        "status": result.status,
        "iteration": result.iteration,
        "phase": result.phase,
        "result_sha256": result_sha256,
    }
    lines.extend(
        [
            "",
            "## 证据绑定",
            "",
            REPORT_BINDING_START.rstrip(),
            json.dumps(binding, ensure_ascii=False, sort_keys=True),
            REPORT_BINDING_END,
        ]
    )
    return redact_text("\n".join(lines).rstrip() + "\n")


def _artifact_names(phase: ScopeGatePhase) -> tuple[str, str]:
    if phase == "post_verification":
        return (
            SCOPE_GATE_POST_VERIFICATION_RESULT_ARTIFACT,
            SCOPE_GATE_POST_VERIFICATION_REPORT_ARTIFACT,
        )
    if phase == "pre_review":
        return SCOPE_GATE_PRE_REVIEW_RESULT_ARTIFACT, SCOPE_GATE_PRE_REVIEW_REPORT_ARTIFACT
    return SCOPE_GATE_RESULT_ARTIFACT, SCOPE_GATE_REPORT_ARTIFACT


def _state_fields(
    iteration: LoopIterationState,
    phase: ScopeGatePhase,
) -> dict[str, object]:
    if phase == "post_verification":
        return {
            "status": iteration.scope_gate_post_verification_status,
            "changed_files": iteration.scope_gate_post_verification_changed_files,
            "violations": iteration.scope_gate_post_verification_violations,
            "result_sha256": iteration.scope_gate_post_verification_result_sha256,
            "report_sha256": iteration.scope_gate_post_verification_report_sha256,
        }
    if phase == "pre_review":
        return {
            "status": iteration.scope_gate_pre_review_status,
            "changed_files": iteration.scope_gate_pre_review_changed_files,
            "violations": iteration.scope_gate_pre_review_violations,
            "result_sha256": iteration.scope_gate_pre_review_result_sha256,
            "report_sha256": iteration.scope_gate_pre_review_report_sha256,
        }
    return {
        "status": iteration.scope_gate_status,
        "changed_files": iteration.scope_gate_changed_files,
        "violations": iteration.scope_gate_violations,
        "result_sha256": iteration.scope_gate_result_sha256,
        "report_sha256": iteration.scope_gate_report_sha256,
    }


def _downstream_started(iteration: LoopIterationState, phase: ScopeGatePhase) -> bool:
    if phase == "post_verification":
        return any(
            [
                iteration.reflect_run is not None,
                iteration.reviewer_status != "skipped",
                iteration.review_run is not None,
                iteration.verdict is not None,
            ]
        )
    if phase == "pre_review":
        return any(
            [
                iteration.risk_gate_status != "skipped",
                iteration.reviewer_status != "skipped",
                iteration.review_run is not None,
                iteration.verdict is not None,
            ]
        )
    return any(
        [
            iteration.verification_status != "skipped",
            iteration.reflect_run is not None,
            iteration.reviewer_status != "skipped",
            iteration.review_run is not None,
            iteration.verdict is not None,
        ]
    )


def _read_text(path: Path, prefix: str, issues: list[str]) -> str:
    if not path.is_file():
        issues.append(f"{prefix}_missing")
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        issues.append(f"{prefix}_unreadable")
        return ""


def _validate_hash(
    text: str,
    expected_hash: str | None,
    prefix: str,
    issues: list[str],
) -> None:
    if not expected_hash:
        issues.append(f"{prefix}_hash_missing")
    elif expected_hash != _sha256_text(text):
        issues.append(f"{prefix}_hash_mismatch")


def _load_json_object(text: str, issues: list[str]) -> dict[str, object] | None:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        issues.append("scope_gate_result_invalid_json")
        return None
    if not isinstance(payload, dict):
        issues.append("scope_gate_result_schema_invalid")
        return None
    return payload


def _load_report_binding(
    report_text: str,
    report_path: Path,
    issues: list[str],
) -> dict[str, object] | None:
    if not report_text:
        if report_path.is_file():
            issues.append("scope_gate_report_binding_missing")
        return None
    if report_text.count(REPORT_BINDING_START) != 1:
        issues.append("scope_gate_report_binding_missing")
        return None
    start = report_text.find(REPORT_BINDING_START) + len(REPORT_BINDING_START)
    end = report_text.find(REPORT_BINDING_END, start)
    if end < 0:
        issues.append("scope_gate_report_binding_invalid")
        return None
    try:
        payload = json.loads(report_text[start:end])
    except json.JSONDecodeError:
        issues.append("scope_gate_report_binding_invalid_json")
        return None
    if not isinstance(payload, dict):
        issues.append("scope_gate_report_binding_schema_invalid")
        return None
    return payload


def _validate_report_binding(
    binding: dict[str, object] | None,
    iteration: LoopIterationState,
    phase: ScopeGatePhase,
    state_fields: dict[str, object],
    issues: list[str],
) -> None:
    if binding is None:
        return
    if binding.get("schema_version") != 1:
        issues.append("scope_gate_report_binding_schema_unsupported")
    if binding.get("status") != state_fields["status"]:
        issues.append("scope_gate_report_status_mismatch")
    if binding.get("iteration") != iteration.iteration:
        issues.append("scope_gate_report_iteration_mismatch")
    if binding.get("phase") != phase:
        issues.append("scope_gate_report_phase_mismatch")
    if binding.get("result_sha256") != state_fields["result_sha256"]:
        issues.append("scope_gate_report_result_hash_mismatch")


def _validate_trace_binding(
    trace_path: Path | None,
    iteration: LoopIterationState,
    phase: ScopeGatePhase,
    state_fields: dict[str, object],
    result: ScopeGateResult,
    issues: list[str],
    *,
    required: bool,
) -> None:
    if trace_path is None:
        return
    if not trace_path.is_file():
        issues.append("scope_gate_trace_missing")
        return
    try:
        events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        issues.append("scope_gate_trace_invalid")
        return
    expected_event = "scope_gate_failed" if result.status == "failed" else "scope_gate_finished"
    matching_events = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("event") == expected_event
        and event.get("iteration") == iteration.iteration
        and event.get("phase") == phase
    ]
    if not matching_events:
        issues.append("scope_gate_trace_event_missing")
        return
    if len(matching_events) != 1:
        issues.append("scope_gate_trace_event_duplicate")
        return
    event = matching_events[0]
    if event.get("status") != state_fields["status"]:
        issues.append("scope_gate_trace_status_mismatch")
    if event.get("result_sha256") != state_fields["result_sha256"]:
        issues.append("scope_gate_trace_result_hash_mismatch")
    if event.get("report_sha256") != state_fields["report_sha256"]:
        issues.append("scope_gate_trace_report_hash_mismatch")
    if event.get("changed_files") != result.changed_files:
        issues.append("scope_gate_trace_changed_files_mismatch")
    if event.get("violation_codes") != [
        violation.code for violation in result.violations
    ]:
        issues.append("scope_gate_trace_violation_codes_mismatch")
    if event.get("failure_code") != result.failure_code:
        issues.append("scope_gate_trace_failure_code_mismatch")
    if required:
        _validate_trace_phase_order(events, iteration, phase, issues)


def _validate_trace_phase_order(
    events: list[object],
    iteration: LoopIterationState,
    phase: ScopeGatePhase,
    issues: list[str],
) -> None:
    phase_indices: dict[str, list[int]] = {
        "pre_verification": [],
        "post_verification": [],
        "pre_review": [],
    }
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("iteration") != iteration.iteration:
            continue
        event_phase = event.get("phase")
        if (
            isinstance(event_phase, str)
            and event_phase in phase_indices
            and event.get("event") in {"scope_gate_finished", "scope_gate_failed"}
        ):
            phase_indices[event_phase].append(index)
    required_phases = {
        "pre_verification": ["pre_verification"],
        "post_verification": ["pre_verification", "post_verification"],
        "pre_review": ["pre_verification", "post_verification", "pre_review"],
    }[phase]
    if any(len(phase_indices[item]) != 1 for item in required_phases):
        issues.append("scope_gate_trace_phase_order_invalid")
        return
    indices = [phase_indices[item][0] for item in required_phases]
    if indices != sorted(indices):
        issues.append("scope_gate_trace_phase_order_invalid")
        return

    pre_index = phase_indices["pre_verification"][0]
    current_index = phase_indices[phase][0]
    if iteration.worker_status == "success":
        worker_indices = _trace_event_indices(
            events,
            iteration.iteration,
            "worker_finished",
        )
        if len(worker_indices) != 1 or worker_indices[0] >= pre_index:
            issues.append("scope_gate_trace_parent_order_invalid")
            return
    if phase in {"post_verification", "pre_review"} and iteration.verification_status in {
        "passed",
        "failed",
    }:
        verification_indices = _trace_event_indices(
            events,
            iteration.iteration,
            "verification_finished",
        )
        post_index = phase_indices["post_verification"][0]
        if (
            len(verification_indices) != 1
            or not pre_index < verification_indices[0] < post_index
        ):
            issues.append("scope_gate_trace_parent_order_invalid")
            return
    if phase == "pre_review":
        post_index = phase_indices["post_verification"][0]
        reflect_indices = _trace_event_indices(
            events,
            iteration.iteration,
            "reflect_finished",
        )
        if (
            iteration.reflect_run is not None
            and (
                len(reflect_indices) != 1
                or not post_index < reflect_indices[0] < current_index
            )
        ):
            issues.append("scope_gate_trace_parent_order_invalid")
            return
        if (
            iteration.reviewer_status != "skipped"
            or iteration.review_run is not None
            or iteration.verdict is not None
        ):
            review_indices = _trace_event_indices(
                events,
                iteration.iteration,
                "review_started",
            )
            if len(review_indices) != 1 or review_indices[0] <= current_index:
                issues.append("scope_gate_trace_parent_order_invalid")


def _trace_event_indices(
    events: list[object],
    iteration: int,
    event_name: str,
) -> list[int]:
    return [
        index
        for index, event in enumerate(events)
        if isinstance(event, dict)
        and event.get("iteration") == iteration
        and event.get("event") == event_name
    ]


def _validate_recomputed_result(
    repo_path: Path | None,
    result: ScopeGateResult,
    issues: list[str],
    *,
    expected_head_sha: str | None,
    expected_policy_sha256: str | None,
) -> None:
    if repo_path is None:
        return
    try:
        expected = evaluate_scope_gate(
            repo_path,
            ScopeConfig(
                allowed_paths=result.allowed_paths,
                forbidden_paths=result.forbidden_paths,
            ),
            iteration=result.iteration,
            phase=result.phase,
            expected_head_sha=expected_head_sha,
            expected_policy_sha256=expected_policy_sha256,
        )
    except Exception:  # noqa: BLE001 - 无法重算时不信任旧的 scope 结论
        issues.append("scope_gate_recomputation_failed")
        return
    if _result_semantics(expected) != _result_semantics(result):
        issues.append("scope_gate_recomputed_result_mismatch")


def _result_semantics(result: ScopeGateResult) -> tuple[object, ...]:
    return (
        result.status,
        result.phase,
        result.scope_policy_sha256,
        result.expected_head_sha,
        result.current_head_sha,
        result.workspace_status_sha256,
        result.index_flags_sha256,
        result.changed_paths_sha256,
        tuple(result.allowed_paths),
        tuple(result.forbidden_paths),
        tuple(result.staged_changed_files),
        tuple(result.unstaged_changed_files),
        tuple(result.changed_files),
        tuple(result.unsafe_index_paths),
        tuple(
            (
                violation.code,
                violation.path,
                tuple(violation.matched_patterns),
            )
            for violation in result.violations
        ),
        result.failure_code,
    )


def _violations_payload(violations: list[ScopeGateViolation]) -> list[dict[str, object]]:
    return [violation.model_dump(mode="json") for violation in violations]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _integrity(
    issues: list[str],
    *,
    evaluated: bool,
    result: ScopeGateResult | None = None,
) -> LoopScopeGateArtifactIntegrity:
    unique_issues = tuple(dict.fromkeys(issues))
    return LoopScopeGateArtifactIntegrity(
        valid=not unique_issues,
        issues=unique_issues,
        evaluated=evaluated,
        result=result if not unique_issues else None,
    )
