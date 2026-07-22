from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from uuid import uuid4

from pydantic import ValidationError

from .execution_control import (
    ACTIVE_EXECUTION_STATUSES,
    ExecutionLease,
)
from .loop_step_result import hash_command
from .parallel_review import (
    PARALLEL_REVIEW_PROMPT_VERSION,
    ParallelReviewAggregate,
    ParallelReviewAttemptIdentity,
    ParallelReviewPlan,
    ParallelReviewResult,
    ParallelReviewResultRef,
    ReviewEvidenceSnapshot,
    ReviewerRole,
    build_parallel_review_attempt_identity,
    build_parallel_review_result_ref,
    build_review_evidence_snapshot,
)
from .redaction import redact_text


PARALLEL_REVIEW_ARTIFACT_MAX_BYTES = 2 * 1024 * 1024
PARALLEL_REVIEW_ROOT_DIR = "parallel-reviews"
_ROLE_DIR_NAMES = {
    "correctness_reviewer": "correctness",
    "verification_adequacy_reviewer": "verification",
    "security_design_reviewer": "security-design",
}
FORBIDDEN_SERIALIZED_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key",
    "api-key",
    "cookie:",
    ".env",
)


class ParallelReviewArtifactValidationError(ValueError):
    pass


def build_review_evidence_snapshot_from_artifacts(
    run_dir: Path,
    *,
    iteration: int,
    workspace_fingerprint: str,
    policy_snapshot_ref: str,
    verification_result_ref: str,
    risk_result_ref: str,
    acceptance_evidence_manifest_ref: str,
) -> ReviewEvidenceSnapshot:
    """从当前 run 的真实文件计算 evidence snapshot，拒绝只信任调用方声明的哈希。"""

    return build_review_evidence_snapshot(
        run_id=run_dir.name,
        iteration=iteration,
        workspace_fingerprint=workspace_fingerprint,
        policy_snapshot_sha256=_sha256_file(
            _require_regular_run_file(
                run_dir,
                policy_snapshot_ref,
                "policy_snapshot_ref",
            )
        ),
        verification_result_sha256=_sha256_file(
            _require_regular_run_file(
                run_dir,
                verification_result_ref,
                "verification_result_ref",
            )
        ),
        risk_result_sha256=_sha256_file(
            _require_regular_run_file(
                run_dir,
                risk_result_ref,
                "risk_result_ref",
            )
        ),
        acceptance_evidence_manifest_sha256=_sha256_file(
            _require_regular_run_file(
                run_dir,
                acceptance_evidence_manifest_ref,
                "acceptance_evidence_manifest_ref",
            )
        ),
    )


def parallel_review_plan_artifact_ref(plan: ParallelReviewPlan) -> str:
    return (
        f"iterations/{plan.iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(plan.plan_id)}/review-plan.json"
    )


def parallel_review_public_evidence_artifact_ref(
    plan: ParallelReviewPlan,
) -> str:
    return (
        f"iterations/{plan.iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(plan.plan_id)}/public-evidence.md"
    )


def parallel_review_role_prompt_artifact_ref(
    plan: ParallelReviewPlan,
    reviewer_role: ReviewerRole,
) -> str:
    return (
        f"iterations/{plan.iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(plan.plan_id)}/{_role_dir_name(reviewer_role)}/"
        "review-prompt.md"
    )


def parallel_review_result_artifact_ref(
    result: ParallelReviewResult,
) -> str:
    return (
        f"iterations/{result.iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(result.review_plan_id)}/"
        f"{_role_dir_name(result.reviewer_role)}/"
        f"{_result_file_name(result.result_id)}"
    )


def parallel_review_result_pointer_artifact_ref(
    plan: ParallelReviewPlan,
    reviewer_role: ReviewerRole,
) -> str:
    return (
        f"iterations/{plan.iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(plan.plan_id)}/{_role_dir_name(reviewer_role)}/"
        "result-ref.json"
    )


def parallel_review_execution_artifact_ref(
    plan: ParallelReviewPlan,
    *,
    reviewer_role: str,
    attempt_id: str,
) -> str:
    return (
        f"iterations/{plan.iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(plan.plan_id)}/{_role_dir_name(reviewer_role)}/"
        f"a-{hashlib.sha256(attempt_id.encode('utf-8')).hexdigest()[:16]}/"
        "execution.json"
    )


def parallel_review_aggregate_artifact_ref(
    aggregate: ParallelReviewAggregate,
) -> str:
    return (
        f"iterations/{aggregate.iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(aggregate.review_plan_id)}/aggregate.json"
    )


def write_parallel_review_plan(
    run_dir: Path,
    plan: ParallelReviewPlan,
) -> Path:
    validated = _validate_plan_for_run(run_dir, plan)
    ref = parallel_review_plan_artifact_ref(validated)
    path = _prepare_output_path(run_dir, ref, "review_plan_artifact")
    content = _serialize_model(validated)
    if not _write_text_create_once(path, content):
        _require_canonical_existing_content(
            path,
            content,
            "ReviewPlan artifact",
        )
        existing = read_parallel_review_plan(
            run_dir,
            iteration=validated.iteration,
            plan_id=validated.plan_id,
        )
        if existing != validated:
            raise ParallelReviewArtifactValidationError(
                "ReviewPlan artifact 已存在且内容不同，不得覆盖"
            )
    return path


def write_parallel_review_public_evidence(
    run_dir: Path,
    plan: ParallelReviewPlan,
    content: str,
) -> tuple[Path, str]:
    validated = _validate_plan_for_run(run_dir, plan)
    ref = parallel_review_public_evidence_artifact_ref(validated)
    path = _prepare_output_path(
        run_dir,
        ref,
        "review_public_evidence_artifact",
    )
    serialized = _serialize_text(content, "公共 review evidence")
    if not _write_text_create_once(path, serialized):
        _require_canonical_existing_content(
            path,
            serialized,
            "公共 review evidence artifact",
        )
    return path, _sha256_file(path)


def write_parallel_review_role_prompt(
    run_dir: Path,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    content: str,
) -> tuple[Path, str]:
    validated = _validate_plan_for_run(run_dir, plan)
    if reviewer_role not in validated.required_roles:
        raise ParallelReviewArtifactValidationError(
            "不能为 ReviewPlan 之外的角色写入 prompt"
        )
    ref = parallel_review_role_prompt_artifact_ref(
        validated,
        reviewer_role,
    )
    path = _prepare_output_path(
        run_dir,
        ref,
        "review_role_prompt_artifact",
    )
    serialized = _serialize_text(content, "角色 review prompt")
    if not _write_text_create_once(path, serialized):
        _require_canonical_existing_content(
            path,
            serialized,
            "角色 review prompt artifact",
        )
    return path, _sha256_file(path)


def read_parallel_review_plan(
    run_dir: Path,
    *,
    iteration: int,
    plan_id: str,
) -> ParallelReviewPlan:
    ref = (
        f"iterations/{iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(plan_id)}/review-plan.json"
    )
    payload = _read_json_artifact(
        run_dir,
        ref,
        "ReviewPlan artifact",
    )
    try:
        plan = ParallelReviewPlan.model_validate(payload)
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "ReviewPlan artifact schema 或 identity 不合法"
        ) from exc
    validated = _validate_plan_for_run(run_dir, plan)
    if validated.iteration != iteration or validated.plan_id != plan_id:
        raise ParallelReviewArtifactValidationError(
            "ReviewPlan artifact 与请求 identity 不一致"
        )
    return validated


def prepare_parallel_review_execution_path(
    run_dir: Path,
    *,
    execution_ref: str,
) -> Path:
    if PurePosixPath(execution_ref).name != "execution.json":
        raise ParallelReviewArtifactValidationError(
            "review execution_ref 必须指向 execution.json"
        )
    return _prepare_output_path(
        run_dir,
        execution_ref,
        "review_execution_artifact",
    )


def claim_parallel_review_attempt(
    run_dir: Path,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    attempt: ParallelReviewAttemptIdentity,
) -> bool:
    """原子认领一次 Reviewer attempt；只有首次创建者可以调用 Runner。"""

    validated_plan = _validate_plan_for_run(run_dir, plan)
    if reviewer_role not in validated_plan.required_roles:
        raise ParallelReviewArtifactValidationError(
            "不能认领 ReviewPlan 之外的 reviewer attempt"
        )
    expected_attempt = build_parallel_review_attempt_identity(
        validated_plan,
        reviewer_role=reviewer_role,
        public_evidence_sha256=attempt.public_evidence_sha256,
        role_prompt_sha256=attempt.role_prompt_sha256,
    )
    if attempt != expected_attempt:
        raise ParallelReviewArtifactValidationError(
            "Reviewer attempt claim identity 与当前 ReviewPlan 不一致"
        )
    execution_ref = parallel_review_execution_artifact_ref(
        validated_plan,
        reviewer_role=reviewer_role,
        attempt_id=attempt.attempt_id,
    )
    claim_ref = PurePosixPath(execution_ref).with_name(
        "attempt-claim.json"
    ).as_posix()
    claim_path = _prepare_output_path(
        run_dir,
        claim_ref,
        "review_attempt_claim_artifact",
    )
    payload = {
        "schema_version": 1,
        "run_id": validated_plan.run_id,
        "iteration": validated_plan.iteration,
        "review_plan_id": validated_plan.plan_id,
        "reviewer_role": reviewer_role,
        "attempt_id": attempt.attempt_id,
        "step_id": attempt.step_id,
        "idempotency_key": attempt.idempotency_key,
        "input_fingerprint": attempt.input_fingerprint,
        "public_evidence_sha256": attempt.public_evidence_sha256,
        "role_prompt_sha256": attempt.role_prompt_sha256,
    }
    content = _serialize_json(payload)
    if _write_text_create_once(claim_path, content):
        return True
    existing = _read_json_path(
        claim_path,
        "Reviewer attempt claim artifact",
    )
    if existing != payload:
        raise ParallelReviewArtifactValidationError(
            "Reviewer attempt claim 已存在但 identity 不一致"
        )
    return False


def write_parallel_review_execution(
    run_dir: Path,
    *,
    execution_ref: str,
    execution: ExecutionLease,
) -> Path:
    """只供确定性 fake reviewer 写入 execution 事实，不替代真实 Runner 控制器。"""

    path = prepare_parallel_review_execution_path(
        run_dir,
        execution_ref=execution_ref,
    )
    content = _serialize_json(execution.model_dump(mode="json"))
    if not _write_text_create_once(path, content):
        _require_canonical_existing_content(
            path,
            content,
            "review execution artifact",
        )
        existing = _read_execution(path)
        if existing != execution:
            raise ParallelReviewArtifactValidationError(
                "review execution artifact 已存在且内容不同，不得覆盖"
            )
    return path


def reconcile_stale_parallel_review_execution(
    run_dir: Path,
    *,
    execution_ref: str,
    expected_execution: ExecutionLease,
    recovered_execution: ExecutionLease,
) -> Path:
    """把失去执行主体的 active lease 原子收敛为人工处理终态。"""

    path = prepare_parallel_review_execution_path(
        run_dir,
        execution_ref=execution_ref,
    )
    if not path.is_file():
        raise ParallelReviewArtifactValidationError(
            "待恢复的 review execution artifact 不存在"
        )
    current = _read_execution(path)
    if current != expected_execution:
        raise ParallelReviewArtifactValidationError(
            "review execution 在恢复前已发生变化"
        )
    if current.status not in ACTIVE_EXECUTION_STATUSES:
        raise ParallelReviewArtifactValidationError(
            "只能收敛 active review execution"
        )
    if (
        recovered_execution.status != "failed"
        or not recovered_execution.termination_unconfirmed
        or recovered_execution.finished_at is None
    ):
        raise ParallelReviewArtifactValidationError(
            "stale review execution 只能收敛为 termination_unconfirmed 失败终态"
        )
    mutable_fields = {
        "status",
        "termination_unconfirmed",
        "reason",
        "returncode",
        "process_output_sha256",
        "process_output_bytes",
        "last_heartbeat",
        "lease_expires_at",
        "deadline",
        "finished_at",
    }
    if current.model_dump(exclude=mutable_fields) != (
        recovered_execution.model_dump(exclude=mutable_fields)
    ):
        raise ParallelReviewArtifactValidationError(
            "stale review execution 恢复时改变了不可变身份"
        )

    expected_sha256 = _sha256_file(path)
    content = _serialize_json(
        recovered_execution.model_dump(mode="json")
    )
    temp_path = path.with_name(f".recover-{uuid4().hex[:10]}")
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
            raise ParallelReviewArtifactValidationError(
                "无法 staged 写入 stale review execution"
            ) from exc
        if (
            _sha256_file(path) != expected_sha256
            or _read_execution(path) != current
        ):
            raise ParallelReviewArtifactValidationError(
                "review execution 在恢复提交前已发生变化"
            )
        try:
            os.replace(temp_path, path)
        except OSError as exc:
            raise ParallelReviewArtifactValidationError(
                "无法原子提交 stale review execution"
            ) from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ParallelReviewArtifactValidationError(
                "无法清理 stale review execution staged 文件"
            ) from exc
    if _read_execution(path) != recovered_execution:
        raise ParallelReviewArtifactValidationError(
            "stale review execution 提交后复核失败"
        )
    return path


def write_parallel_review_process_output(
    run_dir: Path,
    *,
    execution_ref: str,
    content: str,
) -> tuple[Path, str, int]:
    pure_execution_ref = PurePosixPath(execution_ref)
    if pure_execution_ref.name != "execution.json":
        raise ParallelReviewArtifactValidationError(
            "process output 必须绑定规范 execution.json"
        )
    output_ref = pure_execution_ref.with_name(
        "process-output.txt"
    ).as_posix()
    path = _prepare_output_path(
        run_dir,
        output_ref,
        "review_process_output_artifact",
    )
    serialized = _serialize_process_output(content)
    if not _write_text_create_once(path, serialized):
        _require_canonical_existing_content(
            path,
            serialized,
            "review process output artifact",
        )
    return path, _sha256_file(path), path.stat().st_size


def read_parallel_review_process_output(
    run_dir: Path,
    *,
    execution_ref: str,
    process_output_sha256: str | None,
    process_output_bytes: int,
) -> str:
    pure_execution_ref = PurePosixPath(execution_ref)
    if pure_execution_ref.name != "execution.json":
        raise ParallelReviewArtifactValidationError(
            "process output 必须绑定规范 execution.json"
        )
    output_ref = pure_execution_ref.with_name(
        "process-output.txt"
    ).as_posix()
    path = _require_regular_run_file(
        run_dir,
        output_ref,
        "review_process_output_artifact",
    )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ParallelReviewArtifactValidationError(
            "review process output 无法读取"
        ) from exc
    if (
        process_output_sha256 is None
        or hashlib.sha256(payload).hexdigest()
        != process_output_sha256
        or len(payload) != process_output_bytes
    ):
        raise ParallelReviewArtifactValidationError(
            "review execution 未绑定实际 process output"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParallelReviewArtifactValidationError(
            "review process output 不是 UTF-8"
        ) from exc


def write_parallel_review_result(
    run_dir: Path,
    result: ParallelReviewResult,
) -> ParallelReviewResultRef:
    validated = _validate_result_for_run(run_dir, result)
    ref = parallel_review_result_artifact_ref(validated)
    path = _prepare_output_path(run_dir, ref, "review_result_artifact")
    content = _serialize_model(validated)
    if not _write_text_create_once(path, content):
        _require_canonical_existing_content(
            path,
            content,
            "Reviewer result artifact",
        )
        existing_payload = _read_json_artifact(
            run_dir,
            ref,
            "Reviewer result artifact",
        )
        try:
            existing = ParallelReviewResult.model_validate(existing_payload)
        except ValidationError as exc:
            raise ParallelReviewArtifactValidationError(
                "已有 Reviewer result artifact 不可信"
            ) from exc
        if existing != validated:
            raise ParallelReviewArtifactValidationError(
                "Reviewer result artifact 已存在且内容不同，不得覆盖"
            )

    artifact_sha256 = _sha256_file(path)
    result_ref = build_parallel_review_result_ref(
        validated,
        artifact_ref=ref,
        artifact_sha256=artifact_sha256,
    )
    # 从磁盘重新读取一次，避免把尚未完成实际 hash 复核的引用交给 Graph State。
    read_parallel_review_result(run_dir, result_ref)
    plan = read_parallel_review_plan(
        run_dir,
        iteration=validated.iteration,
        plan_id=validated.review_plan_id,
    )
    return _claim_parallel_review_result_ref(
        run_dir,
        plan,
        result_ref,
    )


def read_parallel_review_result(
    run_dir: Path,
    result_ref: ParallelReviewResultRef | Mapping[str, object],
) -> ParallelReviewResult:
    try:
        validated_ref = ParallelReviewResultRef.model_validate(
            (
                result_ref.model_dump(mode="json")
                if isinstance(result_ref, ParallelReviewResultRef)
                else dict(result_ref)
            )
        )
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result ref schema 不合法"
        ) from exc

    path = _require_regular_run_file(
        run_dir,
        validated_ref.artifact_ref,
        "artifact_ref",
    )
    actual_artifact_sha256 = _sha256_file(path)
    if actual_artifact_sha256 != validated_ref.artifact_sha256:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result artifact hash 不匹配"
        )
    payload = _read_json_path(path, "Reviewer result artifact")
    try:
        result = ParallelReviewResult.model_validate(payload)
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result artifact schema 或 identity 不合法"
        ) from exc
    validated = _validate_result_for_run(run_dir, result)
    expected_ref = build_parallel_review_result_ref(
        validated,
        artifact_ref=validated_ref.artifact_ref,
        artifact_sha256=actual_artifact_sha256,
    )
    if expected_ref != validated_ref:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result ref 与实际 artifact identity 不一致"
        )
    expected_artifact_ref = parallel_review_result_artifact_ref(validated)
    if validated_ref.artifact_ref != expected_artifact_ref:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result artifact 未位于规范化内容寻址路径"
        )
    return validated


def list_parallel_review_result_refs(
    run_dir: Path,
    plan: ParallelReviewPlan,
) -> tuple[ParallelReviewResultRef, ...]:
    """发现同一 ReviewPlan 已发布的可信 result，供 checkpoint 恢复复用。"""

    validated_plan = _validate_plan_for_run(run_dir, plan)
    plan_ref = parallel_review_plan_artifact_ref(validated_plan)
    plan_dir = _resolve_run_ref(
        run_dir,
        str(PurePosixPath(plan_ref).parent),
        "review_plan_dir",
    )
    if not plan_dir.exists():
        return ()
    if not plan_dir.is_dir():
        raise ParallelReviewArtifactValidationError(
            "ReviewPlan artifact 目录不是普通目录"
        )

    refs: list[ParallelReviewResultRef] = []
    for role in _ROLE_DIR_NAMES:
        role_dir = plan_dir / _role_dir_name(role)
        if not role_dir.exists():
            continue
        if not role_dir.is_dir():
            raise ParallelReviewArtifactValidationError(
                "Reviewer role artifact 路径不是普通目录"
            )
        result_paths = sorted(role_dir.glob("r-*.json"))
        pointer_path = role_dir / "result-ref.json"
        if (
            pointer_path.exists() or result_paths
        ) and role not in validated_plan.required_roles:
            raise ParallelReviewArtifactValidationError(
                "发现 ReviewPlan 之外角色的 result artifact"
            )
        if pointer_path.exists():
            result_ref = _read_parallel_review_result_pointer(
                run_dir,
                validated_plan,
                role,
            )
        elif not result_paths:
            continue
        elif len(result_paths) > 1:
            raise ParallelReviewArtifactValidationError(
                "同一 ReviewPlan 角色存在多个未认领 result artifact"
            )
        else:
            result_ref = _build_parallel_review_result_ref_from_path(
                run_dir,
                result_paths[0],
            )
            result_ref = _claim_parallel_review_result_ref(
                run_dir,
                validated_plan,
                result_ref,
            )
        refs.append(result_ref)

    role_order = {
        role: index
        for index, role in enumerate(validated_plan.required_roles)
    }
    return tuple(
        sorted(
            refs,
            key=lambda item: (
                role_order[item.reviewer_role],
                item.result_id,
            ),
        )
    )


def _claim_parallel_review_result_ref(
    run_dir: Path,
    plan: ParallelReviewPlan,
    result_ref: ParallelReviewResultRef,
) -> ParallelReviewResultRef:
    validated_ref = ParallelReviewResultRef.model_validate(
        result_ref.model_dump(mode="json")
    )
    result = read_parallel_review_result(run_dir, validated_ref)
    if (
        result.review_plan_id != plan.plan_id
        or result.reviewer_role not in plan.required_roles
        or result.evidence_snapshot_sha256
        != plan.evidence_snapshot_sha256
    ):
        raise ParallelReviewArtifactValidationError(
            "Reviewer result pointer 不能认领错误 plan、role 或 snapshot"
        )
    pointer_ref = parallel_review_result_pointer_artifact_ref(
        plan,
        result.reviewer_role,
    )
    pointer_path = _prepare_output_path(
        run_dir,
        pointer_ref,
        "review_result_pointer_artifact",
    )
    content = _serialize_model(validated_ref)
    if _write_text_create_once(pointer_path, content):
        return validated_ref
    return _read_parallel_review_result_pointer(
        run_dir,
        plan,
        result.reviewer_role,
    )


def _read_parallel_review_result_pointer(
    run_dir: Path,
    plan: ParallelReviewPlan,
    reviewer_role: ReviewerRole,
) -> ParallelReviewResultRef:
    pointer_ref = parallel_review_result_pointer_artifact_ref(
        plan,
        reviewer_role,
    )
    payload = _read_json_artifact(
        run_dir,
        pointer_ref,
        "Reviewer result pointer artifact",
    )
    try:
        result_ref = ParallelReviewResultRef.model_validate(payload)
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result pointer schema 不合法"
        ) from exc
    result = read_parallel_review_result(run_dir, result_ref)
    if (
        result.review_plan_id != plan.plan_id
        or result.reviewer_role != reviewer_role
        or result.evidence_snapshot_sha256
        != plan.evidence_snapshot_sha256
    ):
        raise ParallelReviewArtifactValidationError(
            "Reviewer result pointer 与当前 plan、role 或 snapshot 不一致"
        )
    return result_ref


def _build_parallel_review_result_ref_from_path(
    run_dir: Path,
    path: Path,
) -> ParallelReviewResultRef:
    try:
        relative_ref = path.relative_to(run_dir).as_posix()
    except ValueError as exc:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result artifact 不在 run 目录内"
        ) from exc
    payload = _read_json_artifact(
        run_dir,
        relative_ref,
        "Reviewer result artifact",
    )
    try:
        result = ParallelReviewResult.model_validate(payload)
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "已有 Reviewer result artifact 不可信"
        ) from exc
    result_ref = build_parallel_review_result_ref(
        result,
        artifact_ref=relative_ref,
        artifact_sha256=_sha256_file(path),
    )
    read_parallel_review_result(run_dir, result_ref)
    return result_ref


def write_parallel_review_aggregate(
    run_dir: Path,
    aggregate: ParallelReviewAggregate,
) -> tuple[Path, str]:
    validated = _validate_aggregate_for_run(run_dir, aggregate)
    ref = parallel_review_aggregate_artifact_ref(validated)
    path = _prepare_output_path(run_dir, ref, "review_aggregate_artifact")
    content = _serialize_model(validated)
    if not _write_text_create_once(path, content):
        _require_canonical_existing_content(
            path,
            content,
            "Reviewer aggregate artifact",
        )
        existing = read_parallel_review_aggregate(
            run_dir,
            iteration=validated.iteration,
            plan_id=validated.review_plan_id,
        )
        if existing != validated:
            raise ParallelReviewArtifactValidationError(
                "Reviewer aggregate artifact 已存在且内容不同，不得覆盖"
            )
    return path, _sha256_file(path)


def read_parallel_review_aggregate(
    run_dir: Path,
    *,
    iteration: int,
    plan_id: str,
    artifact_sha256: str | None = None,
) -> ParallelReviewAggregate:
    ref = (
        f"iterations/{iteration:02d}/{PARALLEL_REVIEW_ROOT_DIR}/"
        f"{_plan_dir_name(plan_id)}/aggregate.json"
    )
    path = _require_regular_run_file(
        run_dir,
        ref,
        "review_aggregate_artifact",
    )
    if artifact_sha256 is not None and _sha256_file(path) != artifact_sha256:
        raise ParallelReviewArtifactValidationError(
            "Reviewer aggregate artifact hash 不匹配"
        )
    payload = _read_json_path(path, "Reviewer aggregate artifact")
    try:
        aggregate = ParallelReviewAggregate.model_validate(payload)
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "Reviewer aggregate artifact schema 或 identity 不合法"
        ) from exc
    validated = _validate_aggregate_for_run(run_dir, aggregate)
    if (
        validated.iteration != iteration
        or validated.review_plan_id != plan_id
    ):
        raise ParallelReviewArtifactValidationError(
            "Reviewer aggregate artifact 与请求 identity 不一致"
        )
    return validated


def sha256_parallel_review_artifact(
    run_dir: Path,
    artifact_ref: str,
) -> str:
    return _sha256_file(
        _require_regular_run_file(
            run_dir,
            artifact_ref,
            "parallel_review_artifact",
        )
    )


def _validate_plan_for_run(
    run_dir: Path,
    plan: ParallelReviewPlan,
) -> ParallelReviewPlan:
    try:
        validated = ParallelReviewPlan.model_validate(
            plan.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "ReviewPlan schema 或 identity 不合法"
        ) from exc
    if validated.run_id != run_dir.name:
        raise ParallelReviewArtifactValidationError(
            "ReviewPlan run_id 与 run 目录不一致"
        )
    return validated


def _validate_result_for_run(
    run_dir: Path,
    result: ParallelReviewResult,
) -> ParallelReviewResult:
    try:
        validated = ParallelReviewResult.model_validate(
            result.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result schema 或 identity 不合法"
        ) from exc
    if validated.run_id != run_dir.name:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result run_id 与 run 目录不一致"
        )

    plan = read_parallel_review_plan(
        run_dir,
        iteration=validated.iteration,
        plan_id=validated.review_plan_id,
    )
    if (
        validated.evidence_snapshot_sha256
        != plan.evidence_snapshot_sha256
        or validated.reviewer_role not in plan.required_roles
    ):
        raise ParallelReviewArtifactValidationError(
            "Reviewer result 未绑定当前 ReviewPlan 的必需角色和 evidence snapshot"
        )

    if validated.status == "active":
        raise ParallelReviewArtifactValidationError(
            "active Reviewer execution 不能发布终态 result artifact"
        )
    expected_execution_ref = parallel_review_execution_artifact_ref(
        plan,
        reviewer_role=validated.reviewer_role,
        attempt_id=validated.attempt_id,
    )
    if validated.execution_ref != expected_execution_ref:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result execution_ref 未位于规范化 attempt 路径"
        )
    execution_path = _require_regular_run_file(
        run_dir,
        validated.execution_ref,
        "execution_ref",
    )
    if _sha256_file(execution_path) != validated.execution_sha256:
        raise ParallelReviewArtifactValidationError(
            "Reviewer result execution hash 不匹配"
        )
    execution = _read_execution(execution_path)
    if execution.status in ACTIVE_EXECUTION_STATUSES:
        raise ParallelReviewArtifactValidationError(
            "active Reviewer execution 不能发布任何终态 result artifact"
        )
    _validate_execution_binding(
        run_dir,
        plan,
        validated,
        execution,
    )
    return validated


def _validate_execution_binding(
    run_dir: Path,
    plan: ParallelReviewPlan,
    result: ParallelReviewResult,
    execution: ExecutionLease,
) -> None:
    if (
        execution.run_id != result.run_id
        or execution.step != "reviewer"
        or execution.iteration != result.iteration
        or execution.attempt_id != result.attempt_id
    ):
        raise ParallelReviewArtifactValidationError(
            "review execution 与 Reviewer result identity 不一致"
        )
    if execution.engine != "langgraph":
        raise ParallelReviewArtifactValidationError(
            "Gate 5 review execution engine 必须为 langgraph"
        )
    if execution.replay_class != "read_only_replayable":
        raise ParallelReviewArtifactValidationError(
            "review execution 必须标记为 read_only_replayable"
        )

    public_evidence_path = _require_regular_run_file(
        run_dir,
        parallel_review_public_evidence_artifact_ref(plan),
        "review_public_evidence_artifact",
    )
    role_prompt_path = _require_regular_run_file(
        run_dir,
        parallel_review_role_prompt_artifact_ref(
            plan,
            result.reviewer_role,
        ),
        "review_role_prompt_artifact",
    )
    expected_attempt = build_parallel_review_attempt_identity(
        plan,
        reviewer_role=result.reviewer_role,
        public_evidence_sha256=_sha256_file(public_evidence_path),
        role_prompt_sha256=_sha256_file(role_prompt_path),
    )
    if (
        execution.runner_identity.get("role") != result.reviewer_role
        or execution.runner_identity.get("prompt_version")
        != PARALLEL_REVIEW_PROMPT_VERSION
        or execution.runner_identity.get("public_evidence_sha256")
        != expected_attempt.public_evidence_sha256
        or execution.runner_identity.get("role_prompt_sha256")
        != expected_attempt.role_prompt_sha256
    ):
        raise ParallelReviewArtifactValidationError(
            "review execution runner identity 未绑定实际 evidence 与角色 prompt"
        )
    if (
        result.attempt_id != expected_attempt.attempt_id
        or execution.attempt_id != expected_attempt.attempt_id
        or execution.step_id != expected_attempt.step_id
        or execution.idempotency_key != expected_attempt.idempotency_key
        or execution.input_fingerprint
        != expected_attempt.input_fingerprint
    ):
        raise ParallelReviewArtifactValidationError(
            "review execution attempt identity 未绑定实际输入"
        )
    if execution.command_sha256 != hash_command(execution.command):
        raise ParallelReviewArtifactValidationError(
            "review execution command hash 与实际命令不一致"
        )
    read_parallel_review_process_output(
        run_dir,
        execution_ref=result.execution_ref,
        process_output_sha256=execution.process_output_sha256,
        process_output_bytes=execution.process_output_bytes,
    )

    if result.status == "completed":
        if execution.status != "completed" or execution.termination_unconfirmed:
            raise ParallelReviewArtifactValidationError(
                "completed Reviewer result 缺少可信 completed execution"
            )
        return
    if result.status == "timed_out" and execution.status != "timed_out":
        raise ParallelReviewArtifactValidationError(
            "timed_out Reviewer result 与 execution 终态不一致"
        )
    if result.status == "stopped" and execution.status != "stopped":
        raise ParallelReviewArtifactValidationError(
            "stopped Reviewer result 与 execution 终态不一致"
        )
    if (
        result.status in {"provider_error", "parse_error"}
        and execution.status not in {"completed", "failed"}
    ):
        raise ParallelReviewArtifactValidationError(
            "Reviewer provider/parse error 缺少可信 execution 终态"
        )
    if (
        result.status == "active"
        and execution.status not in ACTIVE_EXECUTION_STATUSES
    ):
        raise ParallelReviewArtifactValidationError(
            "active Reviewer result 与 execution 状态不一致"
        )
    if (
        result.status == "termination_unconfirmed"
        and not execution.termination_unconfirmed
    ):
        raise ParallelReviewArtifactValidationError(
            "termination_unconfirmed Reviewer result 缺少对应执行事实"
        )


def _validate_aggregate_for_run(
    run_dir: Path,
    aggregate: ParallelReviewAggregate,
) -> ParallelReviewAggregate:
    try:
        validated = ParallelReviewAggregate.model_validate(
            aggregate.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "Reviewer aggregate schema 或 identity 不合法"
        ) from exc
    if validated.run_id != run_dir.name:
        raise ParallelReviewArtifactValidationError(
            "Reviewer aggregate run_id 与 run 目录不一致"
        )
    plan = read_parallel_review_plan(
        run_dir,
        iteration=validated.iteration,
        plan_id=validated.review_plan_id,
    )
    if (
        validated.evidence_snapshot_sha256
        != plan.evidence_snapshot_sha256
    ):
        raise ParallelReviewArtifactValidationError(
            "Reviewer aggregate 与 ReviewPlan evidence snapshot 不一致"
        )
    return validated


def _read_execution(path: Path) -> ExecutionLease:
    payload = _read_json_path(path, "review execution artifact")
    try:
        return ExecutionLease.model_validate(payload)
    except ValidationError as exc:
        raise ParallelReviewArtifactValidationError(
            "review execution artifact schema 不合法"
        ) from exc


def _read_json_artifact(
    run_dir: Path,
    ref: str,
    label: str,
) -> object:
    path = _require_regular_run_file(run_dir, ref, label)
    return _read_json_path(path, label)


def _read_json_path(path: Path, label: str) -> object:
    try:
        if path.stat().st_size > PARALLEL_REVIEW_ARTIFACT_MAX_BYTES:
            raise ParallelReviewArtifactValidationError(
                f"{label} 超过大小限制"
            )
        raw = path.read_text(encoding="utf-8")
    except ParallelReviewArtifactValidationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ParallelReviewArtifactValidationError(
            f"{label} 无法读取"
        ) from exc
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_object,
        )
    except json.JSONDecodeError as exc:
        raise ParallelReviewArtifactValidationError(
            f"{label} 不是合法 JSON"
        ) from exc


def _require_regular_run_file(
    run_dir: Path,
    ref: str,
    field: str,
) -> Path:
    path = _resolve_run_ref(run_dir, ref, field)
    if not path.is_file():
        raise ParallelReviewArtifactValidationError(
            f"{field} 引用的文件不存在"
        )
    return path


def _prepare_output_path(
    run_dir: Path,
    ref: str,
    field: str,
) -> Path:
    path = _resolve_run_ref(run_dir, ref, field)
    _assert_no_link_or_reparse(run_dir, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    return path


def _resolve_run_ref(run_dir: Path, ref: str, field: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise ParallelReviewArtifactValidationError(
            f"{field} 必须是非空 POSIX 相对路径"
        )
    pure = PurePosixPath(ref)
    if (
        pure.is_absolute()
        or ":" in pure.parts[0]
        or "." in pure.parts
        or ".." in pure.parts
    ):
        raise ParallelReviewArtifactValidationError(
            f"{field} 不能越过 run 目录"
        )
    candidate = run_dir.joinpath(*pure.parts)
    _assert_no_link_or_reparse(run_dir, candidate)
    resolved = candidate.resolve()
    root = run_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise ParallelReviewArtifactValidationError(
            f"{field} 不能越过 run 目录"
        )
    return resolved


def _assert_no_link_or_reparse(root: Path, target: Path) -> None:
    resolved_root = root.resolve()
    candidate = target.absolute()
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ParallelReviewArtifactValidationError(
            "parallel review artifact 路径不能越过 run 目录"
        ) from exc
    current = resolved_root
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            continue
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ParallelReviewArtifactValidationError(
                f"无法检查 parallel review artifact 路径：{current.name}"
            ) from exc
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(
            file_attributes & reparse_flag
        ):
            raise ParallelReviewArtifactValidationError(
                "parallel review artifact 路径不能包含链接或 reparse point"
            )


def _serialize_model(
    model: ParallelReviewPlan
    | ParallelReviewResult
    | ParallelReviewAggregate,
) -> str:
    return _serialize_json(model.model_dump(mode="json"))


def _plan_dir_name(plan_id: str) -> str:
    prefix = "review-plan-"
    normalized = plan_id.strip().lower()
    digest = normalized.removeprefix(prefix)
    if (
        not normalized.startswith(prefix)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ParallelReviewArtifactValidationError("review_plan_id 格式不合法")
    return f"p-{digest[:24]}"


def _role_dir_name(reviewer_role: str) -> str:
    try:
        return _ROLE_DIR_NAMES[reviewer_role]
    except KeyError as exc:
        raise ParallelReviewArtifactValidationError(
            "reviewer_role 不受支持"
        ) from exc


def _result_file_name(result_id: str) -> str:
    prefix = "review-result-"
    normalized = result_id.strip().lower()
    digest = normalized.removeprefix(prefix)
    if (
        not normalized.startswith(prefix)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ParallelReviewArtifactValidationError("result_id 格式不合法")
    return f"r-{digest[:24]}.json"


def _serialize_json(payload: object) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if len(content.encode("utf-8")) > PARALLEL_REVIEW_ARTIFACT_MAX_BYTES:
        raise ParallelReviewArtifactValidationError(
            "parallel review artifact 超过大小限制"
        )
    lowered = content.lower()
    marker = next(
        (item for item in FORBIDDEN_SERIALIZED_MARKERS if item in lowered),
        None,
    )
    if marker is not None:
        raise ParallelReviewArtifactValidationError(
            f"parallel review artifact 包含敏感标记：{marker}"
        )
    return content


def _serialize_text(content: str, label: str) -> str:
    serialized = redact_text(content).replace("\r\n", "\n").replace("\r", "\n")
    if not serialized.strip():
        raise ParallelReviewArtifactValidationError(f"{label} 不能为空")
    serialized = serialized.rstrip() + "\n"
    if len(serialized.encode("utf-8")) > PARALLEL_REVIEW_ARTIFACT_MAX_BYTES:
        raise ParallelReviewArtifactValidationError(
            f"{label} 超过大小限制"
        )
    return serialized


def _serialize_process_output(content: str) -> str:
    serialized = redact_text(content).replace("\r\n", "\n").replace(
        "\r",
        "\n",
    )
    serialized = serialized.rstrip() + "\n"
    if len(serialized.encode("utf-8")) > PARALLEL_REVIEW_ARTIFACT_MAX_BYTES:
        raise ParallelReviewArtifactValidationError(
            "review process output 超过大小限制"
        )
    return serialized


def _write_text_create_once(path: Path, content: str) -> bool:
    temp_path = path.with_name(f".tmp-{uuid4().hex[:10]}")
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
            raise ParallelReviewArtifactValidationError(
                "无法写入 parallel review artifact 临时文件"
            ) from exc
        try:
            # 同一文件系统内硬链接发布是原子的，且目标存在时不会覆盖历史证据。
            os.link(temp_path, path)
        except FileExistsError:
            return False
        except OSError as exc:
            raise ParallelReviewArtifactValidationError(
                "文件系统不支持 parallel review artifact 独占发布"
            ) from exc
        return True
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ParallelReviewArtifactValidationError(
                "无法清理 parallel review artifact 临时文件"
            ) from exc


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ParallelReviewArtifactValidationError(
            f"无法读取 parallel review artifact：{path.name}"
        ) from exc


def _require_canonical_existing_content(
    path: Path,
    expected_content: str,
    label: str,
) -> None:
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise ParallelReviewArtifactValidationError(
            f"{label} 无法读取"
        ) from exc
    if actual != expected_content.encode("utf-8"):
        raise ParallelReviewArtifactValidationError(
            f"{label} 已存在且内容不同，不得覆盖"
        )


def _reject_duplicate_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ParallelReviewArtifactValidationError(
                f"parallel review artifact 包含重复字段：{key}"
            )
        payload[key] = value
    return payload
