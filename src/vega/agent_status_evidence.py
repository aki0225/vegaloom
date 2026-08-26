from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .agent_candidate_evidence import candidate_scope_expectation
from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    SupervisorEvidenceItem,
    SupervisorEvidenceStatus,
    canonical_digest,
)
from .agent_contract_support import SHA256_PATTERN
from .agent_operation import child_summary_ref
from .agent_verification_retry_archive import retry_source_finish_archive_issue
from .execution_control import ExecutionLease
from .project_config import ScopeConfig, scope_policy_sha256
from .run_utils import resolve_run_dir
from .scope_gate import ScopeGateResult


_STAGES = ("post-worker", "post-core")
_STAGE_LABELS = {
    "post-worker": "Worker 后",
    "post-core": "Core 后",
}


def build_supervisor_evidence(
    run_dir: Path,
    state: AgentState,
    observation: AgentObservation | None,
    plan: AgentPlan,
) -> list[SupervisorEvidenceItem]:
    """只从可信 Observation 引用的摘要生成状态卡证据。

    状态卡是展示层，不能自行扫描 children 或 plan-scope 目录寻找“看起来像”
    成功的文件。引用缺失、身份不一致或摘要无法校验时，一律显示未验证。
    """

    if observation is None:
        return []
    if not observation.evidence_refs:
        if observation.authority == "external_claim":
            return []
        return _unverified_items("Observation 未提供 evidence_refs")
    if observation.authority == "external_claim":
        return _unverified_items("Observation 不是受信机器对账结果")

    child_payload, child_issue = _load_child_summary(
        run_dir,
        state,
        observation,
    )
    worker_item = _worker_evidence(
        run_dir,
        observation,
        child_payload,
        child_issue,
    )
    scope_items = [
        _scope_evidence(
            run_dir,
            state,
            plan,
            observation,
            child_payload,
            stage,
        )
        for stage in _STAGES
    ]
    core_item = _core_evidence(
        run_dir,
        observation,
        child_payload,
        child_issue,
    )
    return [worker_item, *scope_items, core_item]


def _load_child_summary(
    run_dir: Path,
    state: AgentState,
    observation: AgentObservation,
) -> tuple[dict[str, object] | None, str | None]:
    if observation.child_run is None or observation.operation_id is None:
        return None, "Observation 缺少 child 或 operation 绑定"
    expected_ref = child_summary_ref(
        observation.child_run,
        observation.operation_id,
    )
    refs = [ref for ref in observation.evidence_refs if ref.startswith("children/")]
    if refs.count(expected_ref) != 1 or len(refs) != 1:
        return None, "children 摘要缺失或 canonical 绑定不一致"
    try:
        path = _safe_ref_path(run_dir, expected_ref)
        payload_bytes = path.read_bytes()
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None, "children 摘要缺失或无法解析"
    expected_sha256 = observation.evidence_sha256.get(expected_ref)
    if (
        expected_sha256 is None
        or hashlib.sha256(payload_bytes).hexdigest() != expected_sha256
    ):
        return None, "children 摘要缺少绑定哈希或内容已变化"
    if not isinstance(payload, dict):
        return None, "children 摘要不是 JSON object"
    if (
        payload.get("authority") != "child_binding_summary"
        or payload.get("agent_run_id") != state.run_id
        or payload.get("work_item_id") != observation.work_item_id
        or payload.get("child_run") != observation.child_run
        or payload.get("operation_id") != observation.operation_id
    ):
        return None, "children 摘要的 run、Work Item、child 或 operation 不一致"
    return payload, None


def _worker_evidence(
    run_dir: Path,
    observation: AgentObservation,
    child_payload: dict[str, object] | None,
    issue: str | None,
) -> SupervisorEvidenceItem:
    if issue is not None or child_payload is None:
        return _item("Worker 执行", "unverified", issue or "没有可验证的 Worker 摘要")
    worker = child_payload.get("worker")
    if not isinstance(worker, dict):
        return _item("Worker 执行", "stale", "Worker 摘要字段损坏")
    if child_payload.get("operation_kind") == "verification_retry":
        return _retry_source_worker_evidence(run_dir, child_payload, worker)
    return _bound_worker_evidence(
        run_dir,
        observation.child_run,
        observation.operation_id,
        worker,
    )


def _retry_source_worker_evidence(
    run_dir: Path,
    child_payload: dict[str, object],
    worker: dict[str, object],
) -> SupervisorEvidenceItem:
    child_run = worker.get("source_child_run")
    operation_id = worker.get("source_operation_id")
    summary_ref = worker.get("source_summary_ref")
    summary_sha256 = worker.get("source_summary_sha256")
    if (
        not isinstance(child_run, str)
        or child_run != child_payload.get("child_run")
        or not isinstance(operation_id, str)
        or not isinstance(summary_ref, str)
        or not isinstance(summary_sha256, str)
        or SHA256_PATTERN.fullmatch(summary_sha256) is None
    ):
        return _item("Worker 执行", "stale", "验证恢复缺少原始 Worker 绑定")
    expected_ref = child_summary_ref(child_run, operation_id)
    if summary_ref != expected_ref:
        return _item("Worker 执行", "stale", "原始 Worker 摘要引用不是 canonical 路径")
    archive_issue = retry_source_finish_archive_issue(run_dir, child_payload, worker)
    if archive_issue is not None:
        return _item("Worker 执行", "stale", archive_issue)
    try:
        source_bytes = _safe_ref_path(run_dir, summary_ref).read_bytes()
        source = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return _item("Worker 执行", "stale", "原始 Worker 摘要缺失或无法解析")
    if hashlib.sha256(source_bytes).hexdigest() != summary_sha256:
        return _item("Worker 执行", "stale", "原始 Worker 摘要哈希不匹配")
    if (
        not isinstance(source, dict)
        or source.get("authority") != "child_binding_summary"
        or source.get("agent_run_id") != child_payload.get("agent_run_id")
        or source.get("work_item_id") != child_payload.get("work_item_id")
        or source.get("child_run") != child_run
        or source.get("operation_id") != operation_id
        or source.get("operation_kind") == "verification_retry"
    ):
        return _item("Worker 执行", "stale", "原始 Worker 摘要身份不一致")
    source_worker = source.get("worker")
    if not isinstance(source_worker, dict):
        return _item("Worker 执行", "stale", "原始 Worker 字段损坏")
    result = _bound_worker_evidence(
        run_dir,
        child_run,
        operation_id,
        source_worker,
    )
    if result.status != "passed":
        return result
    return _item(
        "Worker 执行",
        "passed",
        "复用原始 Worker execution；本轮只重跑验证、风险门禁与 Reviewer",
    )


def _bound_worker_evidence(
    run_dir: Path,
    child_run: str | None,
    operation_id: str | None,
    worker: dict[str, object],
) -> SupervisorEvidenceItem:
    runner_status = worker.get("runner_status")
    lease_status = worker.get("lease_status")
    if runner_status != "success" or lease_status != "completed":
        return _item(
            "Worker 执行",
            "failed",
            f"runner_status={runner_status!r}，lease_status={lease_status!r}",
        )
    if child_run is None or operation_id is None:
        return _item("Worker 执行", "unverified", "Observation 缺少执行绑定")
    expected_artifact = (
        f"executions/worker/{operation_id}/execution.json"
    )
    execution_artifact = worker.get("execution_artifact")
    execution_sha256 = worker.get("execution_sha256")
    if (
        execution_artifact != expected_artifact
        or not isinstance(execution_sha256, str)
        or SHA256_PATTERN.fullmatch(execution_sha256) is None
    ):
        return _item(
            "Worker 执行",
            "stale",
            "Worker execution 引用或摘要格式与 operation 不一致",
        )
    try:
        child_dir = _resolve_child_dir(run_dir, child_run)
        execution_bytes = _safe_ref_path(
            child_dir,
            expected_artifact,
        ).read_bytes()
        lease = ExecutionLease.model_validate_json(execution_bytes)
    except (OSError, UnicodeError, ValidationError, ValueError):
        return _item(
            "Worker 执行",
            "stale",
            "Worker execution 缺失、损坏或无法验证",
        )
    if hashlib.sha256(execution_bytes).hexdigest() != execution_sha256:
        return _item("Worker 执行", "stale", "Worker execution 摘要不匹配")
    if (
        lease.run_id != child_run
        or lease.execution_id != operation_id
        or lease.step != "worker"
        or lease.status != "completed"
        or lease.returncode != 0
        or lease.termination_unconfirmed
    ):
        return _item(
            "Worker 执行",
            "stale",
            "Worker execution 的 child、operation 或终态与摘要不一致",
        )
    return _item(
        "Worker 执行",
        "passed",
        "runner_status=success，lease_status=completed，execution 摘要匹配",
    )


def _scope_evidence(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    observation: AgentObservation,
    child_payload: dict[str, object] | None,
    stage: str,
) -> SupervisorEvidenceItem:
    labels = (
        {
            "post-worker": "验证恢复前",
            "post-core": "验证恢复后",
        }
        if child_payload is not None
        and child_payload.get("operation_kind") == "verification_retry"
        else _STAGE_LABELS
    )
    label = f"计划范围（{labels[stage]}）"
    if (
        observation.operation_id is None
        or observation.work_item_id is None
    ):
        return _item(label, "unverified", "Observation 缺少 Work Item 的 operation 绑定")
    work_item = next(
        (
            item
            for item in plan.work_items
            if item.work_item_id == observation.work_item_id
        ),
        None,
    )
    if work_item is None:
        return _item(label, "unverified", "Plan 缺少 Observation 对应 Work Item")
    expected_ref = (
        f"plan-scope/{stage}-"
        f"{canonical_digest({'operation_id': observation.operation_id, 'stage': stage})}.json"
    )
    refs = [ref for ref in observation.evidence_refs if ref.startswith("plan-scope/")]
    if refs.count(expected_ref) != 1:
        return _item(label, "unverified", "未找到与 operation 和 stage 匹配的 scope 证据")
    try:
        payload_bytes = _safe_ref_path(run_dir, expected_ref).read_bytes()
        payload = json.loads(payload_bytes.decode("utf-8"))
        result = ScopeGateResult.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
        return _item(label, "stale", "scope 证据缺失、损坏或 schema 不可验证")
    expected_sha256 = observation.evidence_sha256.get(expected_ref)
    if (
        expected_sha256 is None
        or hashlib.sha256(payload_bytes).hexdigest() != expected_sha256
    ):
        return _item(label, "stale", "scope 证据缺少绑定哈希或内容已变化")
    forbidden_paths = list(
        dict.fromkeys(
            path
            for item in plan.work_items
            for path in item.forbidden_paths
        )
    )
    expected_scope = ScopeConfig(
        allowed_paths=list(work_item.allowed_paths),
        forbidden_paths=forbidden_paths,
    )
    candidate_scope, candidate_issue = candidate_scope_expectation(
        run_dir,
        state,
        observation,
    )
    if candidate_issue is not None:
        return _item(label, "stale", candidate_issue)
    expected_head = (
        candidate_scope.post_worker_head
        if stage == "post-worker"
        else candidate_scope.post_core_head
    )
    if (
        result.allowed_paths != expected_scope.allowed_paths
        or result.forbidden_paths != expected_scope.forbidden_paths
        or result.scope_policy_sha256 != scope_policy_sha256(expected_scope)
        or sorted(result.changed_files) != sorted(candidate_scope.changed_files)
        or not _matching_git_heads(result.expected_head_sha, result.current_head_sha)
        or (
            expected_head is not None
            and result.current_head_sha != expected_head
        )
    ):
        return _item(
            label,
            "stale",
            "scope 证据与批准 Plan、Observation 或 Git HEAD 不一致",
        )
    if (
        result.status == "success"
        and not result.violations
        and not result.failure_code
        and not result.diagnostic
    ):
        return _item(label, "passed", "ScopeGateResult status=success，violations=[]")
    if result.status == "failed":
        return _item(
            label,
            "failed",
            f"ScopeGateResult status=failed，violations={len(result.violations)}",
        )
    return _item(
        label,
        "unverified",
        f"ScopeGateResult status={result.status}，violations={len(result.violations)}",
    )
def _core_evidence(
    run_dir: Path,
    observation: AgentObservation,
    child_payload: dict[str, object] | None,
    issue: str | None,
) -> SupervisorEvidenceItem:
    if issue is not None or child_payload is None:
        return _item("核心完成", "unverified", issue or "没有可验证的 Core 摘要")
    core = child_payload.get("core")
    if not isinstance(core, dict):
        return _item("核心完成", "stale", "Core 摘要字段损坏")
    core_status = core.get("status")
    finish_status = core.get("finish_status")
    finish_sha256 = core.get("finish_sha256")
    if finish_status != "ready_to_commit":
        return _item(
            "核心完成",
            "failed",
            f"finish_status={finish_status!r}",
        )
    if (
        core_status != "success"
        or not isinstance(finish_sha256, str)
        or SHA256_PATTERN.fullmatch(finish_sha256) is None
        or observation.child_run is None
    ):
        return _item(
            "核心完成",
            "stale",
            "Core 摘要终态或 finish 摘要格式不可信",
        )
    try:
        child_dir = _resolve_child_dir(run_dir, observation.child_run)
        finish_bytes = (child_dir / "finish-summary.json").read_bytes()
        finish = json.loads(finish_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return _item(
            "核心完成",
            "stale",
            "child finish-summary.json 缺失、损坏或无法验证",
        )
    if hashlib.sha256(finish_bytes).hexdigest() != finish_sha256:
        return _item("核心完成", "stale", "child Finish 摘要不匹配")
    if (
        not isinstance(finish, dict)
        or finish.get("run_id") != observation.child_run
        or finish.get("finish_status") != "ready_to_commit"
        or finish.get("verification_passed") is not True
        or finish.get("latest_verification_failed") is not False
        or not _nested_flag(finish, "artifact_integrity", "valid")
        or not _nested_flag(finish, "evidence_freshness", "fresh")
    ):
        return _item(
            "核心完成",
            "stale",
            "child Finish 身份、验证或完整性证据不满足采用条件",
        )
    return _item(
        "核心完成",
        "passed",
        "finish_status=ready_to_commit；Core 报告中的 worker/scope=skipped 仅表示 assist Core 未自行执行这些步骤",
    )


def _unverified_items(reason: str) -> list[SupervisorEvidenceItem]:
    return [
        _item(label, "unverified", reason)
        for label in (
            "Worker 执行",
            "计划范围（Worker 后）",
            "计划范围（Core 后）",
            "核心完成",
        )
    ]


def _item(
    label: str,
    status: SupervisorEvidenceStatus,
    detail: str,
) -> SupervisorEvidenceItem:
    return SupervisorEvidenceItem(label=label, status=status, detail=detail)


def _safe_ref_path(run_dir: Path, relative: str) -> Path:
    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("证据引用必须是仓库相对路径")
    path = (run_dir / candidate).resolve(strict=False)
    root = run_dir.resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("证据引用越过 Agent run 目录")
    return path


def _resolve_child_dir(run_dir: Path, child_run: str) -> Path:
    try:
        return resolve_run_dir(run_dir.parent.parent, child_run)
    except FileNotFoundError as exc:
        raise ValueError("无法定位 Observation 绑定的 child run") from exc


def _nested_flag(payload: dict[str, object], key: str, nested: str) -> bool:
    value = payload.get(key)
    return isinstance(value, dict) and value.get(nested) is True


def _matching_git_heads(expected: object, current: object) -> bool:
    if not isinstance(expected, str) or expected != current:
        return False
    return len(expected) in {40, 64} and all(
        character in "0123456789abcdef"
        for character in expected
    )
