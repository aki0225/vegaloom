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

from .execution_control import (
    TERMINAL_EXECUTION_STATUSES,
    ExecutionLease,
)

STEP_RESULT_SCHEMA_VERSION = 1
STEP_RESULT_DIR = "step-results"
STEP_RESULT_MAX_BYTES = 128 * 1024
STEP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PREFIXED_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
FORBIDDEN_SERIALIZED_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key",
    "api-key",
    "cookie:",
    ".env",
)

ReplayClass = Literal[
    "pure_replayable",
    "read_only_replayable",
    "external_non_replayable",
]
StepResultStatus = Literal["success", "error", "timed_out", "stopped", "skipped"]


class StepResultValidationError(ValueError):
    pass


class AttemptIdentity(BaseModel):
    """外部副作用 attempt 的稳定输入身份，不复制进程终态。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = STEP_RESULT_SCHEMA_VERSION
    run_id: str
    engine: Literal["langgraph"] = "langgraph"
    graph_schema_version: str
    step_id: str
    step_name: str
    iteration: int = Field(ge=1)
    attempt_id: str
    idempotency_key: str
    replay_class: ReplayClass
    runner_identity: dict[str, str]
    base_head: str
    before_workspace_fingerprint: str
    policy_snapshot_sha256: str
    command_sha256: str
    input_fingerprint: str
    started_at: str


class StepResultOutputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str


class StepResultOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StepResultStatus
    summary: str
    error: str | None = None


class StepResultManifest(BaseModel):
    """Graph 节点对 execution、workspace 和输出证据的内容寻址解释。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = STEP_RESULT_SCHEMA_VERSION
    step_result_id: str
    run_id: str
    engine: Literal["langgraph"] = "langgraph"
    graph_schema_version: str
    step_id: str
    step_name: str
    iteration: int = Field(ge=1)
    attempt_id: str
    idempotency_key: str
    replay_class: ReplayClass
    input_fingerprint: str
    base_head: str
    before_workspace_fingerprint: str
    after_workspace_fingerprint: str
    policy_snapshot_sha256: str
    command_sha256: str
    runner_identity: dict[str, str]
    execution_ref: str
    execution_sha256: str
    output_refs: list[StepResultOutputRef]
    result: StepResultOutcome
    created_at: str


def hash_command(command: list[str]) -> str:
    payload = json.dumps(
        command,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_step_result(
    run_dir: Path,
    *,
    attempt: AttemptIdentity,
    after_workspace_fingerprint: str,
    execution_ref: str,
    output_refs: list[StepResultOutputRef],
    outcome: StepResultOutcome,
) -> StepResultManifest:
    execution_path = _resolve_run_ref(run_dir, execution_ref, "execution_ref")
    manifest = StepResultManifest(
        step_result_id="sha256:" + "0" * 64,
        run_id=attempt.run_id,
        graph_schema_version=attempt.graph_schema_version,
        step_id=attempt.step_id,
        step_name=attempt.step_name,
        iteration=attempt.iteration,
        attempt_id=attempt.attempt_id,
        idempotency_key=attempt.idempotency_key,
        replay_class=attempt.replay_class,
        input_fingerprint=attempt.input_fingerprint,
        base_head=attempt.base_head,
        before_workspace_fingerprint=attempt.before_workspace_fingerprint,
        after_workspace_fingerprint=after_workspace_fingerprint,
        policy_snapshot_sha256=attempt.policy_snapshot_sha256,
        command_sha256=attempt.command_sha256,
        runner_identity=attempt.runner_identity,
        execution_ref=execution_ref,
        execution_sha256=_sha256_file(execution_path),
        output_refs=output_refs,
        result=outcome,
        created_at=datetime.now(UTC).isoformat(),
    )
    manifest = _with_content_id(manifest)
    return validate_step_result(run_dir, manifest)


def write_step_result(
    run_dir: Path,
    manifest: StepResultManifest,
) -> Path:
    normalized = _with_content_id(manifest)
    path = _step_result_path(run_dir, normalized.step_id)
    if path.exists():
        existing = read_step_result(run_dir, normalized.step_id)
        if existing == normalized:
            return path
        raise StepResultValidationError(
            f"step result 已存在且内容不同，不得覆盖：{normalized.step_id}"
        )
    validated = validate_step_result(run_dir, normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    _write_text_atomic(path, serialize_step_result(validated))
    return path


def read_step_result(
    run_dir: Path,
    step_id: str,
) -> StepResultManifest:
    path = _step_result_path(run_dir, step_id)
    _assert_no_link_or_reparse(run_dir, path)
    if not path.is_file():
        raise StepResultValidationError(f"step result 不存在：{step_id}")
    try:
        if path.stat().st_size > STEP_RESULT_MAX_BYTES:
            raise StepResultValidationError("step result 文件超过大小限制")
        raw = path.read_text(encoding="utf-8")
    except StepResultValidationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise StepResultValidationError("step result 文件无法读取") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_json_object)
        manifest = StepResultManifest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise StepResultValidationError("step result 文件格式不合法") from exc
    validated = validate_step_result(run_dir, manifest)
    if validated.step_id != step_id:
        raise StepResultValidationError(
            "step result 文件名与 manifest.step_id 不一致"
        )
    return validated


def find_step_result_by_id(
    run_dir: Path,
    step_result_id: str,
) -> StepResultManifest:
    _require_prefixed_sha256(step_result_id, "step_result_id")
    step_results_dir = _resolve_run_ref(run_dir, STEP_RESULT_DIR, "step_result_dir")
    if not step_results_dir.exists():
        raise StepResultValidationError("step result 目录不存在")
    _assert_no_link_or_reparse(run_dir, step_results_dir)
    matches: list[StepResultManifest] = []
    for path in sorted(step_results_dir.glob("*.json")):
        manifest = read_step_result(run_dir, path.stem)
        if manifest.step_result_id == step_result_id:
            matches.append(manifest)
    if len(matches) != 1:
        raise StepResultValidationError(
            f"step_result_id 必须唯一命中一个 manifest，实际为 {len(matches)}"
        )
    return matches[0]


def validate_step_result(
    run_dir: Path,
    manifest: StepResultManifest,
) -> StepResultManifest:
    if manifest.schema_version != STEP_RESULT_SCHEMA_VERSION:
        raise StepResultValidationError("step result schema_version 不受支持")
    if manifest.run_id != run_dir.name:
        raise StepResultValidationError("step result run_id 与 run 目录不一致")
    if manifest.engine != "langgraph":
        raise StepResultValidationError("step result engine 必须为 langgraph")
    _require_step_id(manifest.step_id)
    if manifest.step_name != "worker":
        raise StepResultValidationError("Gate 3 step result 只允许绑定 worker")
    if manifest.replay_class != "external_non_replayable":
        raise StepResultValidationError("worker step result 必须是 external_non_replayable")
    if not manifest.attempt_id.strip():
        raise StepResultValidationError("attempt_id 不能为空")
    _require_prefixed_sha256(manifest.step_result_id, "step_result_id")
    _require_prefixed_sha256(manifest.idempotency_key, "idempotency_key")
    _require_prefixed_sha256(manifest.input_fingerprint, "input_fingerprint")
    _require_prefixed_sha256(
        manifest.before_workspace_fingerprint,
        "before_workspace_fingerprint",
    )
    _require_prefixed_sha256(
        manifest.after_workspace_fingerprint,
        "after_workspace_fingerprint",
    )
    _require_hex_sha256(
        manifest.policy_snapshot_sha256,
        "policy_snapshot_sha256",
    )
    _require_hex_sha256(manifest.command_sha256, "command_sha256")
    _require_hex_sha256(manifest.execution_sha256, "execution_sha256")
    if not GIT_SHA_PATTERN.fullmatch(manifest.base_head):
        raise StepResultValidationError("base_head 不是合法 Git SHA")
    if not manifest.runner_identity or any(
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(value, str)
        for key, value in manifest.runner_identity.items()
    ):
        raise StepResultValidationError("runner_identity 必须是非空字符串映射")
    try:
        datetime.fromisoformat(manifest.created_at)
    except ValueError as exc:
        raise StepResultValidationError("created_at 不是合法 ISO 时间") from exc

    expected_id = _content_id(manifest)
    if manifest.step_result_id != expected_id:
        raise StepResultValidationError("step_result_id 与规范化内容不一致")

    execution_path = _resolve_run_ref(
        run_dir,
        manifest.execution_ref,
        "execution_ref",
    )
    if not execution_path.is_file():
        raise StepResultValidationError("step result 引用的 execution 不存在")
    if _sha256_file(execution_path) != manifest.execution_sha256:
        raise StepResultValidationError("step result execution hash 不匹配")
    execution = _read_execution(execution_path)
    if execution.run_id != manifest.run_id:
        raise StepResultValidationError("execution run_id 与 step result 不一致")
    if execution.engine != manifest.engine:
        raise StepResultValidationError("execution engine 与 step result 不一致")
    if execution.graph_schema_version != manifest.graph_schema_version:
        raise StepResultValidationError(
            "execution graph_schema_version 与 step result 不一致"
        )
    if execution.step != manifest.step_name:
        raise StepResultValidationError("execution step 与 step result 不一致")
    if execution.step_id != manifest.step_id:
        raise StepResultValidationError("execution step_id 与 step result 不一致")
    if execution.iteration != manifest.iteration:
        raise StepResultValidationError("execution iteration 与 step result 不一致")
    if execution.status not in TERMINAL_EXECUTION_STATUSES:
        raise StepResultValidationError("execution 尚未进入终态")
    if execution.termination_unconfirmed:
        raise StepResultValidationError("execution 终止未确认，不能生成 step result")
    execution_command_sha256 = (
        execution.command_sha256
        if execution.command_sha256 is not None
        else hash_command(execution.command)
    )
    if execution_command_sha256 != manifest.command_sha256:
        raise StepResultValidationError("execution command hash 与 step result 不一致")
    if execution.attempt_id != manifest.attempt_id:
        raise StepResultValidationError("execution attempt_id 与 step result 不一致")
    if execution.idempotency_key != manifest.idempotency_key:
        raise StepResultValidationError(
            "execution idempotency_key 与 step result 不一致"
        )
    if execution.replay_class != manifest.replay_class:
        raise StepResultValidationError(
            "execution replay_class 与 step result 不一致"
        )
    if execution.runner_identity != manifest.runner_identity:
        raise StepResultValidationError(
            "execution runner_identity 与 step result 不一致"
        )
    if execution.base_head != manifest.base_head:
        raise StepResultValidationError("execution base_head 与 step result 不一致")
    if (
        execution.before_workspace_fingerprint
        != manifest.before_workspace_fingerprint
    ):
        raise StepResultValidationError(
            "execution workspace fingerprint 与 step result 不一致"
        )
    if execution.policy_snapshot_sha256 != manifest.policy_snapshot_sha256:
        raise StepResultValidationError(
            "execution policy snapshot 与 step result 不一致"
        )
    if execution.input_fingerprint != manifest.input_fingerprint:
        raise StepResultValidationError(
            "execution input fingerprint 与 step result 不一致"
        )

    attempt = _read_attempt_identity(run_dir, manifest.iteration)
    for field in (
        "run_id",
        "engine",
        "graph_schema_version",
        "step_id",
        "step_name",
        "iteration",
        "attempt_id",
        "idempotency_key",
        "replay_class",
        "runner_identity",
        "base_head",
        "before_workspace_fingerprint",
        "policy_snapshot_sha256",
        "command_sha256",
        "input_fingerprint",
    ):
        if getattr(attempt, field) != getattr(manifest, field):
            raise StepResultValidationError(
                f"attempt {field} 与 step result 不一致"
            )

    if not manifest.output_refs:
        raise StepResultValidationError("step result 至少需要一个输出引用")
    seen_refs: set[str] = set()
    for output_ref in manifest.output_refs:
        if output_ref.path in seen_refs:
            raise StepResultValidationError("step result 包含重复输出引用")
        seen_refs.add(output_ref.path)
        _require_hex_sha256(output_ref.sha256, "output sha256")
        output_path = _resolve_run_ref(run_dir, output_ref.path, "output_ref")
        if not output_path.is_file():
            raise StepResultValidationError(
                f"step result 输出不存在：{output_ref.path}"
            )
        if _sha256_file(output_path) != output_ref.sha256:
            raise StepResultValidationError(
                f"step result 输出 hash 不匹配：{output_ref.path}"
            )

    serialized = serialize_step_result(manifest)
    if len(serialized.encode("utf-8")) > STEP_RESULT_MAX_BYTES:
        raise StepResultValidationError("step result 序列化结果超过大小限制")
    lowered = serialized.lower()
    marker = next(
        (item for item in FORBIDDEN_SERIALIZED_MARKERS if item in lowered),
        None,
    )
    if marker is not None:
        raise StepResultValidationError(f"step result 包含敏感标记：{marker}")
    return manifest


def serialize_step_result(manifest: StepResultManifest) -> str:
    return json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _with_content_id(manifest: StepResultManifest) -> StepResultManifest:
    return manifest.model_copy(
        update={
            "step_result_id": _content_id(manifest),
        }
    )


def _content_id(manifest: StepResultManifest) -> str:
    payload = manifest.model_dump(mode="json")
    payload["step_result_id"] = None
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _step_result_path(run_dir: Path, step_id: str) -> Path:
    _require_step_id(step_id)
    return _resolve_run_ref(
        run_dir,
        f"{STEP_RESULT_DIR}/{step_id}.json",
        "step_result_path",
    )


def _read_attempt_identity(
    run_dir: Path,
    iteration: int,
) -> AttemptIdentity:
    path = _resolve_run_ref(
        run_dir,
        f"iterations/{iteration:02d}/executions/worker/attempt.json",
        "attempt_ref",
    )
    _assert_no_link_or_reparse(run_dir, path)
    if not path.is_file():
        raise StepResultValidationError("step result 引用的 attempt 不存在")
    try:
        if path.stat().st_size > STEP_RESULT_MAX_BYTES:
            raise StepResultValidationError("attempt manifest 文件超过大小限制")
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_object,
        )
        attempt = AttemptIdentity.model_validate(payload)
    except StepResultValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise StepResultValidationError("attempt manifest 无法解析") from exc
    if attempt.schema_version != STEP_RESULT_SCHEMA_VERSION:
        raise StepResultValidationError("attempt manifest schema 不受支持")
    return attempt


def _require_step_id(step_id: str) -> None:
    if not STEP_ID_PATTERN.fullmatch(step_id):
        raise StepResultValidationError("step_id 格式不合法")


def _require_hex_sha256(value: str, field: str) -> None:
    if not HEX_SHA256_PATTERN.fullmatch(value):
        raise StepResultValidationError(f"{field} 不是合法 SHA-256")


def _require_prefixed_sha256(value: str, field: str) -> None:
    if not PREFIXED_SHA256_PATTERN.fullmatch(value):
        raise StepResultValidationError(f"{field} 不是合法 sha256 identity")


def _resolve_run_ref(run_dir: Path, ref: str, field: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise StepResultValidationError(f"{field} 必须是非空 POSIX 相对路径")
    pure = PurePosixPath(ref)
    if (
        pure.is_absolute()
        or ":" in pure.parts[0]
        or "." in pure.parts
        or ".." in pure.parts
    ):
        raise StepResultValidationError(f"{field} 不能越过 run 目录")
    candidate = run_dir.joinpath(*pure.parts)
    _assert_no_link_or_reparse(run_dir, candidate)
    resolved = candidate.resolve()
    root = run_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise StepResultValidationError(f"{field} 不能越过 run 目录")
    return resolved


def _assert_no_link_or_reparse(run_dir: Path, target: Path) -> None:
    try:
        relative = target.relative_to(run_dir)
    except ValueError as exc:
        raise StepResultValidationError("step result 路径不能越过 run 目录") from exc
    current = run_dir
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            continue
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise StepResultValidationError(
                f"无法检查 step result 路径：{current.name}"
            ) from exc
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag):
            raise StepResultValidationError(
                "step result 路径不能包含链接或 reparse point"
            )


def _read_execution(path: Path) -> ExecutionLease:
    try:
        return ExecutionLease.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise StepResultValidationError("execution.json 无法解析") from exc


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise StepResultValidationError(f"无法读取引用文件：{path.name}") from exc


def _write_text_atomic(path: Path, content: str) -> None:
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


def _reject_duplicate_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise StepResultValidationError(
                f"step result 包含重复字段：{key}"
            )
        payload[key] = value
    return payload
