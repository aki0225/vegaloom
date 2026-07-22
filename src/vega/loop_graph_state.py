from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, TypedDict, cast
from uuid import uuid4

from .models import LoopAutomationState

GRAPH_STATE_SCHEMA_VERSION = 2
GRAPH_STATE_LEGACY_SCHEMA_VERSION = 1
GRAPH_ENGINE_VERSION = "gate5-review-v1"
GRAPH_ENGINE_LEGACY_VERSION = "gate3-checkpoint-v1"
GRAPH_SCHEMA_VERSION = "checkpoint-v1"
GRAPH_STATE_ARTIFACT = "graph/graph-state.json"
GRAPH_STATE_MAX_BYTES = 16 * 1024
GRAPH_TASK_CONTRACT_REF = "loop-plan.md"
GRAPH_BUSINESS_STATE_REF = "state.json"
GRAPH_POLICY_SNAPSHOT_REF = "project-policy-snapshot.json"
GRAPH_TERMINAL_REPORT_REF = "final-report.md"
GRAPH_TERMINAL_EVAL_REF = "eval.md"

GRAPH_STATE_KEYS = {
    "schema_version",
    "engine_version",
    "graph_schema_version",
    "run_id",
    "engine",
    "task_contract_ref",
    "state_ref",
    "policy_snapshot_ref",
    "policy_snapshot_sha256",
    "latest_step_result_id",
    "pending_human_decision_id",
    "review_results",
    "terminal_ref",
}
FORBIDDEN_SERIALIZED_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key",
    "api-key",
    "cookie:",
    ".env",
)


class ReviewResultRef(TypedDict):
    schema_version: int
    result_id: str
    review_plan_id: str
    reviewer_role: str
    evidence_snapshot_sha256: str
    attempt_id: str
    artifact_ref: str
    artifact_sha256: str


def merge_review_results_by_identity(
    current: Mapping[str, ReviewResultRef | Mapping[str, object]],
    update: Mapping[str, ReviewResultRef | Mapping[str, object]],
) -> dict[str, ReviewResultRef]:
    from .parallel_review import merge_parallel_review_result_refs

    merged = merge_parallel_review_result_refs(current, update)
    return cast(
        dict[str, ReviewResultRef],
        {
            identity: result_ref.model_dump(mode="json")
            for identity, result_ref in merged.items()
        },
    )


class VegaGraphState(TypedDict):
    schema_version: int
    engine_version: str
    graph_schema_version: str
    run_id: str
    engine: Literal["langgraph"]
    task_contract_ref: str
    state_ref: str
    policy_snapshot_ref: str
    policy_snapshot_sha256: str
    latest_step_result_id: str | None
    pending_human_decision_id: str | None
    review_results: Annotated[
        dict[str, ReviewResultRef],
        merge_review_results_by_identity,
    ]
    terminal_ref: str | None


class GraphStateValidationError(ValueError):
    pass


def create_graph_state(run_dir: Path) -> VegaGraphState:
    return {
        "schema_version": GRAPH_STATE_SCHEMA_VERSION,
        "engine_version": GRAPH_ENGINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "run_id": run_dir.name,
        "engine": "langgraph",
        "task_contract_ref": GRAPH_TASK_CONTRACT_REF,
        "state_ref": GRAPH_BUSINESS_STATE_REF,
        "policy_snapshot_ref": GRAPH_POLICY_SNAPSHOT_REF,
        "policy_snapshot_sha256": _sha256_file(
            run_dir / GRAPH_POLICY_SNAPSHOT_REF
        ),
        "latest_step_result_id": None,
        "pending_human_decision_id": None,
        "review_results": {},
        "terminal_ref": None,
    }


def refresh_graph_state(
    run_dir: Path,
    graph_state: VegaGraphState,
) -> VegaGraphState:
    business_state = _read_business_state(run_dir, graph_state["state_ref"])
    return {
        **graph_state,
        "terminal_ref": _expected_terminal_ref(run_dir, business_state),
    }


def validate_graph_state(
    run_dir: Path,
    payload: VegaGraphState | dict[str, object],
    *,
    require_task_contract: bool = True,
) -> VegaGraphState:
    if not isinstance(payload, dict):
        raise GraphStateValidationError("Graph State 顶层必须是 JSON object")
    if set(payload) != GRAPH_STATE_KEYS:
        missing = sorted(GRAPH_STATE_KEYS - set(payload))
        extra = sorted(set(payload) - GRAPH_STATE_KEYS)
        raise GraphStateValidationError(
            f"Graph State 字段不匹配：missing={missing} extra={extra}"
        )
    if type(payload["schema_version"]) is not int:
        raise GraphStateValidationError("Graph State schema_version 必须是整数")
    for field in (
        "engine_version",
        "graph_schema_version",
        "run_id",
        "engine",
        "task_contract_ref",
        "state_ref",
        "policy_snapshot_ref",
        "policy_snapshot_sha256",
    ):
        if not isinstance(payload[field], str):
            raise GraphStateValidationError(
                f"Graph State {field} 必须是字符串"
            )
    if not isinstance(payload["review_results"], dict):
        raise GraphStateValidationError(
            "Graph State review_results 必须是 JSON object"
        )
    terminal_ref = payload["terminal_ref"]
    if terminal_ref is not None and not isinstance(terminal_ref, str):
        raise GraphStateValidationError(
            "Graph State terminal_ref 必须是字符串或 null"
        )
    graph_state = cast(VegaGraphState, dict(payload))
    if graph_state["schema_version"] not in {
        GRAPH_STATE_LEGACY_SCHEMA_VERSION,
        GRAPH_STATE_SCHEMA_VERSION,
    }:
        raise GraphStateValidationError("Graph State schema_version 不受支持")
    expected_engine_version = (
        GRAPH_ENGINE_LEGACY_VERSION
        if graph_state["schema_version"] == GRAPH_STATE_LEGACY_SCHEMA_VERSION
        else GRAPH_ENGINE_VERSION
    )
    if graph_state["engine_version"] != expected_engine_version:
        raise GraphStateValidationError("Graph State engine_version 不匹配")
    if graph_state["graph_schema_version"] != GRAPH_SCHEMA_VERSION:
        raise GraphStateValidationError("Graph State graph_schema_version 不匹配")
    if graph_state["run_id"] != run_dir.name:
        raise GraphStateValidationError("Graph State run_id 与 run 目录不一致")
    if graph_state["engine"] != "langgraph":
        raise GraphStateValidationError("Graph State engine 必须固定为 langgraph")
    if graph_state["task_contract_ref"] != GRAPH_TASK_CONTRACT_REF:
        raise GraphStateValidationError(
            f"Graph State task_contract_ref 必须固定为 {GRAPH_TASK_CONTRACT_REF}"
        )
    if graph_state["state_ref"] != GRAPH_BUSINESS_STATE_REF:
        raise GraphStateValidationError(
            f"Graph State state_ref 必须固定为 {GRAPH_BUSINESS_STATE_REF}"
        )
    if graph_state["policy_snapshot_ref"] != GRAPH_POLICY_SNAPSHOT_REF:
        raise GraphStateValidationError(
            "Graph State policy_snapshot_ref 必须固定为 "
            f"{GRAPH_POLICY_SNAPSHOT_REF}"
        )
    latest_step_result_id = graph_state["latest_step_result_id"]
    if latest_step_result_id is not None and not isinstance(
        latest_step_result_id,
        str,
    ):
        raise GraphStateValidationError(
            "Graph State latest_step_result_id 必须是字符串或 null"
        )
    pending_human_decision_id = graph_state["pending_human_decision_id"]
    if pending_human_decision_id is not None:
        if not isinstance(pending_human_decision_id, str):
            raise GraphStateValidationError(
                "Graph State pending_human_decision_id 必须是字符串或 null"
            )
        try:
            from .loop_graph_decision import read_pending_decision

            pending = read_pending_decision(
                run_dir,
                pending_human_decision_id,
            )
        except Exception as exc:
            raise GraphStateValidationError(
                "Graph State 引用的 pending decision 不可信"
            ) from exc
        if pending.run_id != graph_state["run_id"]:
            raise GraphStateValidationError(
                "Graph State pending decision run identity 不一致"
            )
    if (
        graph_state["schema_version"] == GRAPH_STATE_LEGACY_SCHEMA_VERSION
        and graph_state["review_results"]
    ):
        raise GraphStateValidationError(
            "Graph State v1 不允许写入并行 reviewer 结果"
        )
    if graph_state["schema_version"] == GRAPH_STATE_SCHEMA_VERSION:
        graph_state["review_results"] = _validate_review_results(
            run_dir,
            graph_state["review_results"],
        )

    state_path = _resolve_run_ref(run_dir, graph_state["state_ref"], "state_ref")
    policy_path = _resolve_run_ref(
        run_dir,
        graph_state["policy_snapshot_ref"],
        "policy_snapshot_ref",
    )
    task_path = _resolve_run_ref(
        run_dir,
        graph_state["task_contract_ref"],
        "task_contract_ref",
    )
    terminal_ref = graph_state["terminal_ref"]
    terminal_path = (
        _resolve_run_ref(run_dir, terminal_ref, "terminal_ref")
        if terminal_ref is not None
        else None
    )
    if not state_path.is_file():
        raise GraphStateValidationError("Graph State 引用的 state.json 不存在")
    if not policy_path.is_file():
        raise GraphStateValidationError("Graph State 引用的 policy snapshot 不存在")
    if require_task_contract and not task_path.is_file():
        raise GraphStateValidationError("Graph State 引用的 task contract 不存在")
    if terminal_path is not None and not terminal_path.is_file():
        raise GraphStateValidationError("Graph State 引用的 terminal artifact 不存在")
    if _sha256_file(policy_path) != graph_state["policy_snapshot_sha256"]:
        raise GraphStateValidationError("Graph State policy snapshot hash 不匹配")
    if latest_step_result_id is not None:
        try:
            from .loop_step_result import find_step_result_by_id

            step_result = find_step_result_by_id(run_dir, latest_step_result_id)
        except Exception as exc:
            raise GraphStateValidationError(
                "Graph State 引用的 step result 不可信"
            ) from exc
        if step_result.run_id != graph_state["run_id"]:
            raise GraphStateValidationError(
                "Graph State step result run identity 不一致"
            )

    business_state = _read_business_state(run_dir, graph_state["state_ref"])
    if business_state.run_id != graph_state["run_id"]:
        raise GraphStateValidationError("Graph State 与 state.json run_id 不一致")
    if business_state.engine != "langgraph":
        raise GraphStateValidationError("Graph State 与 state.json engine 不一致")
    expected_terminal_ref = _expected_terminal_ref(run_dir, business_state)
    if terminal_ref != expected_terminal_ref:
        raise GraphStateValidationError(
            "Graph State terminal_ref 与权威业务终态不一致"
        )

    serialized = serialize_graph_state(graph_state)
    if len(serialized.encode("utf-8")) > GRAPH_STATE_MAX_BYTES:
        raise GraphStateValidationError("Graph State 序列化结果超过 16 KiB")
    lowered = serialized.lower()
    marker = next(
        (item for item in FORBIDDEN_SERIALIZED_MARKERS if item in lowered),
        None,
    )
    if marker is not None:
        raise GraphStateValidationError(f"Graph State 包含禁入敏感标记：{marker}")
    return graph_state


def write_graph_state(run_dir: Path, graph_state: VegaGraphState) -> Path:
    validated = validate_graph_state(run_dir, graph_state)
    _resolve_run_ref(
        run_dir,
        GRAPH_STATE_ARTIFACT,
        "graph_state_artifact",
    )
    path = run_dir.joinpath(*PurePosixPath(GRAPH_STATE_ARTIFACT).parts)
    _assert_no_link_or_reparse(run_dir, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    _write_text_atomic(path, serialize_graph_state(validated))
    return path


def read_graph_state(run_dir: Path) -> VegaGraphState:
    path = _resolve_run_ref(
        run_dir,
        GRAPH_STATE_ARTIFACT,
        "graph_state_artifact",
    )
    _assert_no_link_or_reparse(run_dir, path)
    if not path.is_file():
        raise GraphStateValidationError("Graph State 文件不存在")
    try:
        if path.stat().st_size > GRAPH_STATE_MAX_BYTES:
            raise GraphStateValidationError("Graph State 文件超过 16 KiB")
        raw = path.read_text(encoding="utf-8")
    except GraphStateValidationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise GraphStateValidationError("Graph State 文件无法读取") from exc
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_object,
        )
    except json.JSONDecodeError as exc:
        raise GraphStateValidationError("Graph State 文件不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise GraphStateValidationError("Graph State 顶层必须是 JSON object")
    return validate_graph_state(run_dir, payload)


def serialize_graph_state(graph_state: VegaGraphState) -> str:
    return json.dumps(
        graph_state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _validate_review_results(
    run_dir: Path,
    raw_results: Mapping[str, object],
) -> dict[str, ReviewResultRef]:
    from .parallel_review import ParallelReviewResultRef
    from .parallel_review_artifacts import read_parallel_review_result

    validated: dict[str, ReviewResultRef] = {}
    plan_ids: set[str] = set()
    evidence_snapshot_ids: set[str] = set()
    iterations: set[int] = set()
    reviewer_roles: set[str] = set()
    for identity, raw_ref in raw_results.items():
        if not isinstance(identity, str):
            raise GraphStateValidationError(
                "Graph State review result map key 必须是字符串"
            )
        try:
            if not isinstance(raw_ref, Mapping):
                raise TypeError("review result ref 不是 object")
            result_ref = ParallelReviewResultRef.model_validate(dict(raw_ref))
            result = read_parallel_review_result(run_dir, result_ref)
        except Exception as exc:
            raise GraphStateValidationError(
                "Graph State 引用的 Reviewer result 不可信"
            ) from exc
        if identity != result_ref.result_id:
            raise GraphStateValidationError(
                "Graph State review result map key 与 result_id 不一致"
            )
        if result.reviewer_role in reviewer_roles:
            raise GraphStateValidationError(
                "Graph State 同一 ReviewPlan 不允许重复 reviewer role"
            )
        reviewer_roles.add(result.reviewer_role)
        plan_ids.add(result.review_plan_id)
        evidence_snapshot_ids.add(result.evidence_snapshot_sha256)
        iterations.add(result.iteration)
        validated[identity] = cast(
            ReviewResultRef,
            result_ref.model_dump(mode="json"),
        )

    if len(plan_ids) > 1:
        raise GraphStateValidationError(
            "Graph State review results 不能混合多个 ReviewPlan"
        )
    if len(evidence_snapshot_ids) > 1:
        raise GraphStateValidationError(
            "Graph State review results 不能混合多个 evidence snapshot"
        )
    if len(iterations) > 1:
        raise GraphStateValidationError(
            "Graph State review results 不能混合多个 iteration"
        )
    return {identity: validated[identity] for identity in sorted(validated)}


def _read_business_state(run_dir: Path, state_ref: str) -> LoopAutomationState:
    state_path = _resolve_run_ref(run_dir, state_ref, "state_ref")
    try:
        return LoopAutomationState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - Graph 交叉校验必须 fail-closed
        raise GraphStateValidationError("Graph State 引用的业务状态不合法") from exc


def _expected_terminal_ref(
    run_dir: Path,
    business_state: LoopAutomationState,
) -> str | None:
    if business_state.status not in {"success", "failed", "needs_human"}:
        return None
    if run_dir.joinpath(GRAPH_TERMINAL_REPORT_REF).is_file():
        return GRAPH_TERMINAL_REPORT_REF
    return GRAPH_TERMINAL_EVAL_REF


def _resolve_run_ref(run_dir: Path, ref: str, field: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise GraphStateValidationError(f"{field} 必须是非空 POSIX 相对路径")
    pure = PurePosixPath(ref)
    if (
        pure.is_absolute()
        or ":" in pure.parts[0]
        or "." in pure.parts
        or ".." in pure.parts
    ):
        raise GraphStateValidationError(f"{field} 不能越过 run 目录")
    candidate = run_dir.joinpath(*pure.parts)
    _assert_no_link_or_reparse(run_dir, candidate)
    resolved = candidate.resolve()
    root = run_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise GraphStateValidationError(f"{field} 不能越过 run 目录")
    return resolved


def _sha256_file(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise GraphStateValidationError(f"无法读取 Graph State 引用：{path.name}") from exc
    return hashlib.sha256(content).hexdigest()


def _assert_no_link_or_reparse(run_dir: Path, target: Path) -> None:
    try:
        relative = target.relative_to(run_dir)
    except ValueError as exc:
        raise GraphStateValidationError(
            "Graph State 路径不能越过 run 目录"
        ) from exc

    current = run_dir
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            continue
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise GraphStateValidationError(
                f"无法检查 Graph State 路径：{current.name}"
            ) from exc
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(
            file_attributes & reparse_flag
        ):
            raise GraphStateValidationError(
                "Graph State 路径不能包含链接或 reparse point"
            )


def _write_text_atomic(path: Path, content: str) -> None:
    # 临时名不应长于最终文件名，避免 Windows 长路径下原文件可写而临时文件失败。
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
            raise GraphStateValidationError(
                f"Graph State 包含重复字段：{key}"
            )
        payload[key] = value
    return payload
