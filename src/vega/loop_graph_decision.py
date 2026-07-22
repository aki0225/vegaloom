from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .decision import DecisionStore
from .loop_graph_recovery import current_workspace_fingerprint
from .loop_step_result import find_step_result_by_id
from .loop_steps import HumanDecisionStepRequest
from .models import DecisionEntry, LoopAutomationState
from .project_config import project_policy_snapshot

PENDING_DECISION_SCHEMA_VERSION = 1
DECISION_CONSUMPTION_SCHEMA_VERSION = 1
DECISION_ARTIFACT_MAX_BYTES = 256 * 1024
PENDING_ID_PATTERN = re.compile(r"^pending-[0-9a-f]{24}$")
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class GraphDecisionValidationError(ValueError):
    pass


class DecisionEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "risk_result",
        "risk_report",
        "verification_result",
        "verification_summary",
    ]
    path: str
    sha256: str


class PendingHumanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = PENDING_DECISION_SCHEMA_VERSION
    pending_id: str
    run_id: str
    iteration: int = Field(ge=1)
    decision_type: Literal["gate"] = "gate"
    allowed_decisions: list[Literal["approved", "rejected"]]
    workspace_fingerprint: str
    policy_snapshot_sha256: str
    policy_fingerprint: str
    latest_step_result_id: str
    verification_status: Literal["skipped", "passed", "failed"]
    verification_failed_count: int = Field(ge=0)
    evidence_refs: list[DecisionEvidenceRef]
    reflect_run_id: str
    reflect_state_sha256: str
    reflect_diff_sha256: str
    binding_sha256: str
    created_at: str

    @property
    def artifact_ref(self) -> str:
        return f"graph/pending-decisions/{self.pending_id}.json"


class DecisionConsumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = DECISION_CONSUMPTION_SCHEMA_VERSION
    pending_id: str
    decision_id: str
    decision: Literal["approved", "rejected"]
    decision_entry_sha256: str
    binding_sha256: str
    consumed_at: str


def prepare_pending_decision(
    run_dir: Path,
    request: HumanDecisionStepRequest,
    *,
    latest_step_result_id: str | None,
) -> PendingHumanDecision:
    if latest_step_result_id is None:
        raise GraphDecisionValidationError(
            "HITL pending decision 缺少当前 worker Step Result"
        )
    iteration_dir = run_dir / "iterations" / f"{request.iteration:02d}"
    risk_result = iteration_dir / "risk-gate-result.json"
    risk_report = iteration_dir / "risk-gate-report.md"
    evidence_refs = [
        _evidence_ref(
            run_dir,
            risk_result,
            "risk_result",
            expected_sha256=request.risk_result_sha256,
        ),
        _evidence_ref(
            run_dir,
            risk_report,
            "risk_report",
            expected_sha256=request.risk_report_sha256,
        ),
    ]
    if request.verification_result_path is not None:
        evidence_refs.append(
            _evidence_ref(
                run_dir,
                request.verification_result_path,
                "verification_result",
            )
        )
    if request.verification_summary_path is not None:
        evidence_refs.append(
            _evidence_ref(
                run_dir,
                request.verification_summary_path,
                "verification_summary",
            )
        )
    reflect_run = _validate_reflect_run(run_dir, request.reflect_run)
    policy_path = run_dir / "project-policy-snapshot.json"
    binding = {
        "run_id": run_dir.name,
        "iteration": request.iteration,
        "decision_type": "gate",
        "allowed_decisions": ["approved", "rejected"],
        "workspace_fingerprint": current_workspace_fingerprint(
            request.repo_path
        ),
        "policy_snapshot_sha256": _sha256_file(policy_path),
        "policy_fingerprint": _policy_fingerprint(request.repo_path),
        "latest_step_result_id": latest_step_result_id,
        "verification_status": request.verification_status,
        "verification_failed_count": request.verification_failed_count,
        "evidence_refs": [
            item.model_dump(mode="json")
            for item in evidence_refs
        ],
        "reflect_run_id": reflect_run.name,
        "reflect_state_sha256": _sha256_file(reflect_run / "state.json"),
        "reflect_diff_sha256": _sha256_file(reflect_run / "full-diff.patch"),
    }
    binding_sha256 = _sha256_json(binding)
    pending_id = f"pending-{binding_sha256[:24]}"
    candidate = PendingHumanDecision(
        pending_id=pending_id,
        binding_sha256=binding_sha256,
        created_at=datetime.now(UTC).isoformat(),
        **binding,
    )
    path = _pending_path(run_dir, pending_id)
    if path.exists():
        existing = read_pending_decision(run_dir, pending_id)
        if _pending_binding(existing) != _pending_binding(candidate):
            raise GraphDecisionValidationError(
                "pending decision identity 已存在但 binding 不一致"
            )
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    _write_json_atomic(path, candidate.model_dump(mode="json"))
    return candidate


def read_pending_decision(
    run_dir: Path,
    pending_id: str,
) -> PendingHumanDecision:
    _require_pending_id(pending_id)
    payload = _read_json(_pending_path(run_dir, pending_id), "pending decision")
    try:
        pending = PendingHumanDecision.model_validate(payload)
    except ValidationError as exc:
        raise GraphDecisionValidationError(
            "pending decision schema 不合法"
        ) from exc
    if pending.schema_version != PENDING_DECISION_SCHEMA_VERSION:
        raise GraphDecisionValidationError(
            "pending decision schema 不受支持"
        )
    if pending.pending_id != pending_id or pending.run_id != run_dir.name:
        raise GraphDecisionValidationError(
            "pending decision identity 不一致"
        )
    if pending.allowed_decisions != ["approved", "rejected"]:
        raise GraphDecisionValidationError(
            "pending decision allowed decisions 不合法"
        )
    _require_sha256(pending.binding_sha256, "binding_sha256")
    _require_prefixed_sha256(
        pending.workspace_fingerprint,
        "workspace_fingerprint",
    )
    for field in (
        "policy_snapshot_sha256",
        "policy_fingerprint",
        "reflect_state_sha256",
        "reflect_diff_sha256",
    ):
        _require_sha256(getattr(pending, field), field)
    if _sha256_json(_pending_identity_payload(pending)) != pending.binding_sha256:
        raise GraphDecisionValidationError(
            "pending decision binding hash 不一致"
        )
    for evidence in pending.evidence_refs:
        _require_sha256(evidence.sha256, f"{evidence.kind}.sha256")
        _resolve_run_ref(run_dir, evidence.path, evidence.kind)
    return pending


def validate_pending_decision_bindings(
    run_dir: Path,
    pending: PendingHumanDecision,
    *,
    state: LoopAutomationState,
    decision: DecisionEntry,
) -> None:
    if state.run_id != run_dir.name or pending.run_id != state.run_id:
        raise GraphDecisionValidationError(
            "pending decision 与业务 run identity 不一致"
        )
    if state.status != "running" or state.current_step != "human_decision":
        raise GraphDecisionValidationError(
            "当前业务状态不允许消费 HITL decision"
        )
    if state.current_iteration != pending.iteration:
        raise GraphDecisionValidationError(
            "pending decision iteration 与业务状态不一致"
        )
    if decision.run_id != run_dir.name:
        raise GraphDecisionValidationError(
            "decision ledger run identity 不一致"
        )
    if decision.type != pending.decision_type:
        raise GraphDecisionValidationError(
            "decision type 与 pending decision 不一致"
        )
    if decision.decision not in pending.allowed_decisions:
        raise GraphDecisionValidationError(
            "decision value 不在 pending allowed decisions 中"
        )
    if pending.artifact_ref not in decision.references:
        raise GraphDecisionValidationError(
            "decision ledger 未引用当前 pending decision artifact"
        )
    if (
        decision.decision == "approved"
        and pending.verification_failed_count > 0
    ):
        raise GraphDecisionValidationError(
            "verification failed 不能被人工批准覆盖"
        )
    repo_path = Path(state.repo_path).resolve()
    _validate_current_binding_evidence(
        run_dir,
        pending,
        repo_path=repo_path,
    )


def validate_consumed_approval(
    run_dir: Path,
    *,
    iteration: int,
    repo_path: Path,
    consumption_ref: str | None = None,
) -> bool:
    pending_candidates = [
        read_pending_decision(run_dir, path.stem)
        for path in sorted(
            (run_dir / "graph" / "pending-decisions").glob("pending-*.json")
        )
    ]
    matching = [
        pending
        for pending in pending_candidates
        if pending.iteration == iteration
    ]
    if len(matching) != 1:
        raise GraphDecisionValidationError(
            "当前 iteration 缺少唯一 pending decision identity"
        )
    pending = matching[0]
    expected_consumption_ref = (
        "graph/decision-consumptions/"
        f"{pending.pending_id}.json"
    )
    if (
        consumption_ref is not None
        and consumption_ref != expected_consumption_ref
    ):
        raise GraphDecisionValidationError(
            "consumption_ref 与当前 iteration pending identity 不一致"
        )
    consumption = read_decision_consumption(run_dir, pending.pending_id)
    decision = DecisionStore(run_dir).get(consumption.decision_id)
    if decision.decision != "approved" or consumption.decision != "approved":
        raise GraphDecisionValidationError(
            "当前 pending decision 未被 approved consumption 消费"
        )
    if pending.verification_failed_count > 0:
        raise GraphDecisionValidationError(
            "verification failed 不能被 approval 覆盖"
        )
    if pending.artifact_ref not in decision.references:
        raise GraphDecisionValidationError(
            "consumed approval 未引用当前 pending artifact"
        )
    if _sha256_json(decision.model_dump(mode="json")) != (
        consumption.decision_entry_sha256
    ):
        raise GraphDecisionValidationError(
            "consumed approval 与 decision ledger 内容不一致"
        )
    if consumption.binding_sha256 != pending.binding_sha256:
        raise GraphDecisionValidationError(
            "consumed approval 与 pending binding 不一致"
        )
    _validate_current_binding_evidence(
        run_dir,
        pending,
        repo_path=repo_path.resolve(),
    )
    return True


def _validate_current_binding_evidence(
    run_dir: Path,
    pending: PendingHumanDecision,
    *,
    repo_path: Path,
) -> None:
    if current_workspace_fingerprint(repo_path) != pending.workspace_fingerprint:
        raise GraphDecisionValidationError(
            "workspace 已偏离 pending decision binding"
        )
    policy_path = run_dir / "project-policy-snapshot.json"
    if _sha256_file(policy_path) != pending.policy_snapshot_sha256:
        raise GraphDecisionValidationError(
            "project policy snapshot artifact 已漂移"
        )
    if _policy_fingerprint(repo_path) != pending.policy_fingerprint:
        raise GraphDecisionValidationError(
            "当前项目 policy 已偏离 pending decision binding"
        )
    for evidence in pending.evidence_refs:
        path = _resolve_run_ref(run_dir, evidence.path, evidence.kind)
        if _sha256_text_file(path) != evidence.sha256:
            raise GraphDecisionValidationError(
                f"pending decision evidence 已漂移：{evidence.kind}"
            )
    reflect_run = _reflect_run_path(run_dir, pending.reflect_run_id)
    if _sha256_file(reflect_run / "state.json") != pending.reflect_state_sha256:
        raise GraphDecisionValidationError(
            "reflect state 已偏离 pending decision binding"
        )
    if _sha256_file(reflect_run / "full-diff.patch") != pending.reflect_diff_sha256:
        raise GraphDecisionValidationError(
            "reflect diff 已偏离 pending decision binding"
        )
    step_result = find_step_result_by_id(
        run_dir,
        pending.latest_step_result_id,
    )
    if step_result.run_id != run_dir.name:
        raise GraphDecisionValidationError(
            "pending decision Step Result run identity 不一致"
        )


def consume_pending_decision(
    run_dir: Path,
    pending: PendingHumanDecision,
    decision: DecisionEntry,
) -> DecisionConsumption:
    decision_hash = _sha256_json(decision.model_dump(mode="json"))
    candidate = DecisionConsumption(
        pending_id=pending.pending_id,
        decision_id=decision.id,
        decision=decision.decision,
        decision_entry_sha256=decision_hash,
        binding_sha256=pending.binding_sha256,
        consumed_at=datetime.now(UTC).isoformat(),
    )
    path = _consumption_path(run_dir, pending.pending_id)
    if path.exists():
        return _read_matching_consumption(run_dir, candidate)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GraphDecisionValidationError(
            "无法准备 decision consumption 目录"
        ) from exc
    _assert_no_link_or_reparse(run_dir, path)
    if _write_json_create_once(path, candidate.model_dump(mode="json")):
        return candidate
    return _read_matching_consumption(run_dir, candidate)


def _read_matching_consumption(
    run_dir: Path,
    candidate: DecisionConsumption,
) -> DecisionConsumption:
    existing = read_decision_consumption(run_dir, candidate.pending_id)
    if (
        existing.pending_id == candidate.pending_id
        and existing.decision_id == candidate.decision_id
        and existing.decision == candidate.decision
        and existing.decision_entry_sha256
        == candidate.decision_entry_sha256
        and existing.binding_sha256 == candidate.binding_sha256
    ):
        return existing
    raise GraphDecisionValidationError(
        "pending decision 已被不同 decision identity 消费"
    )


def read_decision_consumption(
    run_dir: Path,
    pending_id: str,
) -> DecisionConsumption:
    _require_pending_id(pending_id)
    payload = _read_json(
        _consumption_path(run_dir, pending_id),
        "decision consumption",
    )
    try:
        consumption = DecisionConsumption.model_validate(payload)
    except ValidationError as exc:
        raise GraphDecisionValidationError(
            "decision consumption schema 不合法"
        ) from exc
    if consumption.schema_version != DECISION_CONSUMPTION_SCHEMA_VERSION:
        raise GraphDecisionValidationError(
            "decision consumption schema 不受支持"
        )
    if consumption.pending_id != pending_id:
        raise GraphDecisionValidationError(
            "decision consumption pending identity 不一致"
        )
    _require_sha256(
        consumption.decision_entry_sha256,
        "decision_entry_sha256",
    )
    _require_sha256(consumption.binding_sha256, "binding_sha256")
    return consumption


def _pending_binding(pending: PendingHumanDecision) -> dict[str, object]:
    return {
        **_pending_identity_payload(pending),
        "pending_id": pending.pending_id,
        "binding_sha256": pending.binding_sha256,
    }


def _pending_identity_payload(
    pending: PendingHumanDecision,
) -> dict[str, object]:
    payload = pending.model_dump(mode="json")
    payload.pop("schema_version", None)
    payload.pop("pending_id", None)
    payload.pop("binding_sha256", None)
    payload.pop("created_at", None)
    return payload


def _evidence_ref(
    run_dir: Path,
    path: Path,
    kind: Literal[
        "risk_result",
        "risk_report",
        "verification_result",
        "verification_summary",
    ],
    *,
    expected_sha256: str | None = None,
) -> DecisionEvidenceRef:
    resolved = path.resolve()
    root = run_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise GraphDecisionValidationError(
            f"{kind} evidence 必须位于当前 run 目录"
        )
    ref = resolved.relative_to(root).as_posix()
    _resolve_run_ref(run_dir, ref, kind)
    actual_sha256 = _sha256_text_file(resolved)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise GraphDecisionValidationError(
            f"{kind} evidence hash 与业务记录不一致"
        )
    return DecisionEvidenceRef(
        kind=kind,
        path=ref,
        sha256=actual_sha256,
    )


def _validate_reflect_run(run_dir: Path, reflect_run: Path) -> Path:
    resolved = reflect_run.resolve()
    runs_root = run_dir.parent.resolve()
    if resolved.parent != runs_root or not resolved.is_dir():
        raise GraphDecisionValidationError(
            "reflect run 必须是当前 workspace/runs 的直接子目录"
        )
    return _reflect_run_path(run_dir, resolved.name)


def _reflect_run_path(run_dir: Path, run_id: str) -> Path:
    if (
        not run_id
        or run_id in {".", ".."}
        or "/" in run_id
        or "\\" in run_id
        or ":" in run_id
    ):
        raise GraphDecisionValidationError("reflect run id 不合法")
    candidate = run_dir.parent / run_id
    _assert_no_link_or_reparse(run_dir.parent, candidate)
    resolved = candidate.resolve()
    if resolved.parent != run_dir.parent.resolve() or not resolved.is_dir():
        raise GraphDecisionValidationError("reflect run 不存在或越过 runs root")
    return resolved


def _policy_fingerprint(repo_path: Path) -> str:
    return _sha256_json(project_policy_snapshot(repo_path))


def _pending_path(run_dir: Path, pending_id: str) -> Path:
    _require_pending_id(pending_id)
    return _resolve_run_ref(
        run_dir,
        f"graph/pending-decisions/{pending_id}.json",
        "pending_decision",
    )


def _consumption_path(run_dir: Path, pending_id: str) -> Path:
    _require_pending_id(pending_id)
    return _resolve_run_ref(
        run_dir,
        f"graph/decision-consumptions/{pending_id}.json",
        "decision_consumption",
    )


def _read_json(path: Path, label: str) -> object:
    if not path.is_file():
        raise GraphDecisionValidationError(f"{label} 不存在")
    try:
        if path.stat().st_size > DECISION_ARTIFACT_MAX_BYTES:
            raise GraphDecisionValidationError(f"{label} 超过大小限制")
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_object,
        )
    except GraphDecisionValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GraphDecisionValidationError(f"{label} 无法解析") from exc


def _resolve_run_ref(run_dir: Path, ref: str, field: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise GraphDecisionValidationError(
            f"{field} 必须是非空 POSIX 相对路径"
        )
    pure = PurePosixPath(ref)
    if (
        pure.is_absolute()
        or ":" in pure.parts[0]
        or "." in pure.parts
        or ".." in pure.parts
    ):
        raise GraphDecisionValidationError(f"{field} 不能越过 run 目录")
    candidate = run_dir.joinpath(*pure.parts)
    _assert_no_link_or_reparse(run_dir, candidate)
    resolved = candidate.resolve()
    root = run_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise GraphDecisionValidationError(f"{field} 不能越过 run 目录")
    return resolved


def _assert_no_link_or_reparse(root: Path, target: Path) -> None:
    root = root.resolve()
    candidate = target.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise GraphDecisionValidationError(
            "decision artifact 路径不能越过控制根目录"
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            continue
        metadata = current.lstat()
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag):
            raise GraphDecisionValidationError(
                "decision artifact 路径不能包含链接或 reparse point"
            )


def _serialize_json(payload: object) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if len(content.encode("utf-8")) > DECISION_ARTIFACT_MAX_BYTES:
        raise GraphDecisionValidationError(
            "decision artifact 超过大小限制"
        )
    return content


def _write_json_create_once(path: Path, payload: object) -> bool:
    content = _serialize_json(payload)
    temp_path = path.with_name(f".tmp-{uuid4().hex}")
    try:
        try:
            with temp_path.open(
                "x",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise GraphDecisionValidationError(
                "无法写入 decision consumption 临时文件"
            ) from exc
        try:
            # 硬链接发布在同一文件系统内是原子的，且目标已存在时不会覆盖。
            os.link(temp_path, path)
        except FileExistsError:
            return False
        except OSError as exc:
            raise GraphDecisionValidationError(
                "文件系统不支持 decision consumption 独占发布"
            ) from exc
        return True
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            raise GraphDecisionValidationError(
                "无法清理 decision consumption 临时文件"
            ) from exc


def _write_json_atomic(path: Path, payload: object) -> None:
    content = _serialize_json(payload)
    temp_path = path.with_name(f".tmp-{uuid4().hex[:10]}")
    last_error: OSError | None = None
    try:
        temp_path.write_text(
            content,
            encoding="utf-8",
            newline="\n",
        )
        for _ in range(10):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.02)
        assert last_error is not None
        raise last_error
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise GraphDecisionValidationError(
            f"无法读取 decision binding 文件：{path.name}"
        ) from exc


def _sha256_text_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GraphDecisionValidationError(
            f"无法读取 decision evidence：{path.name}"
        ) from exc
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_pending_id(value: str) -> None:
    if not PENDING_ID_PATTERN.fullmatch(value):
        raise GraphDecisionValidationError("pending decision id 不合法")


def _require_sha256(value: str, field: str) -> None:
    if not HEX_SHA256_PATTERN.fullmatch(value):
        raise GraphDecisionValidationError(f"{field} 不是合法 SHA-256")


def _require_prefixed_sha256(value: str, field: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise GraphDecisionValidationError(
            f"{field} 不是合法 sha256 identity"
        )


def _reject_duplicate_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise GraphDecisionValidationError(
                f"decision JSON 包含重复字段：{key}"
            )
        payload[key] = value
    return payload
