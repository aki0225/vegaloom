from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Literal, Protocol

from .task_pack_models import validate_contract_path


ProbeMode = Literal["sequential", "parallel"]
ProbeStatus = Literal[
    "passed",
    "invalid_plan",
    "worker_failed",
    "scope_violation",
    "verification_failed",
]

_SLICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_IGNORED_ARTIFACT_DIRECTORIES = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache"}
)


@dataclass(frozen=True)
class ProbeSlice:
    slice_id: str
    allowed_write_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _SLICE_ID_PATTERN.fullmatch(self.slice_id):
            raise ValueError("slice_id 格式无效")
        normalized = tuple(validate_contract_path(path) for path in self.allowed_write_paths)
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("allowed_write_paths 必须非空且不能重复")
        object.__setattr__(self, "allowed_write_paths", normalized)


@dataclass(frozen=True)
class ProbePlan:
    slices: tuple[ProbeSlice, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.slices) <= 2:
            raise ValueError("能力探针只允许一到两个 slice")
        slice_ids = [item.slice_id for item in self.slices]
        if len(slice_ids) != len(set(slice_ids)):
            raise ValueError("slice_id 不能重复")


@dataclass(frozen=True)
class WorkerObservation:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if value is not None and value < 0:
                raise ValueError("Token 观测值不能为负数")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("成本观测值不能为负数")


class WorkerAdapter(Protocol):
    def __call__(
        self,
        *,
        task_slices: tuple[ProbeSlice, ...],
        workspace: Path,
    ) -> WorkerObservation: ...


class ProbeVerifier(Protocol):
    def __call__(self, workspace: Path) -> bool: ...


@dataclass(frozen=True)
class ProbeWorkerResult:
    slice_ids: tuple[str, ...]
    workspace: Path
    changed_paths: tuple[str, ...]
    wall_seconds: float
    observation: WorkerObservation


@dataclass(frozen=True)
class ProbeRunResult:
    mode: ProbeMode
    status: ProbeStatus
    issue_code: str | None
    final_workspace: Path | None
    changed_paths: tuple[str, ...]
    worker_results: tuple[ProbeWorkerResult, ...]
    verifier_passed: bool | None
    wall_seconds: float


@dataclass(frozen=True)
class _WorkerFailure:
    issue_code: str


def run_probe(
    *,
    mode: ProbeMode,
    plan: ProbePlan,
    source_workspace: Path,
    run_root: Path,
    worker: WorkerAdapter,
    verifier: ProbeVerifier,
) -> ProbeRunResult:
    started = perf_counter()
    if mode == "parallel":
        issue = _parallel_plan_issue(plan)
        if issue is not None:
            return _blocked_result(mode, "invalid_plan", issue, started)

    baseline = _snapshot_workspace(source_workspace)
    _prepare_empty_directory(run_root)
    final_workspace = run_root / "final"
    shutil.copytree(source_workspace, final_workspace)

    if mode == "sequential":
        worker_results, failure = _run_sequential(
            plan,
            final_workspace,
            worker,
        )
    else:
        worker_results, failure = _run_parallel(
            plan,
            source_workspace,
            final_workspace,
            run_root,
            baseline,
            worker,
        )

    if failure is not None:
        changed_paths = tuple(
            sorted(_changed_paths(baseline, _snapshot_workspace(final_workspace)))
        )
        return ProbeRunResult(
            mode=mode,
            status=_failure_status(failure.issue_code),
            issue_code=failure.issue_code,
            final_workspace=final_workspace,
            changed_paths=changed_paths,
            worker_results=worker_results,
            verifier_passed=None,
            wall_seconds=perf_counter() - started,
        )

    changed_paths = tuple(sorted(_changed_paths(baseline, _snapshot_workspace(final_workspace))))
    try:
        verifier_passed = verifier(final_workspace)
    except Exception:
        return ProbeRunResult(
            mode=mode,
            status="verification_failed",
            issue_code="verifier_error",
            final_workspace=final_workspace,
            changed_paths=changed_paths,
            worker_results=worker_results,
            verifier_passed=False,
            wall_seconds=perf_counter() - started,
        )

    return ProbeRunResult(
        mode=mode,
        status="passed" if verifier_passed else "verification_failed",
        issue_code=None if verifier_passed else "verifier_failed",
        final_workspace=final_workspace,
        changed_paths=changed_paths,
        worker_results=worker_results,
        verifier_passed=verifier_passed,
        wall_seconds=perf_counter() - started,
    )


def _run_sequential(
    plan: ProbePlan,
    workspace: Path,
    worker: WorkerAdapter,
) -> tuple[tuple[ProbeWorkerResult, ...], _WorkerFailure | None]:
    result, failure = _run_worker(plan.slices, workspace, worker)
    if failure is not None:
        return (), failure
    assert result is not None
    return (result,), None


def _run_parallel(
    plan: ProbePlan,
    source_workspace: Path,
    final_workspace: Path,
    run_root: Path,
    baseline: dict[str, bytes],
    worker: WorkerAdapter,
) -> tuple[tuple[ProbeWorkerResult, ...], _WorkerFailure | None]:
    worker_root = run_root / "workers"
    worker_root.mkdir()
    workspaces: dict[str, Path] = {}
    for index, task_slice in enumerate(plan.slices, start=1):
        # slice_id 仍用于证据绑定，但不再进入物理路径，避免真实仓库深层文件在 Windows
        # 上叠加长实验名后超过传统路径预算。
        workspace = worker_root / f"w{index}"
        shutil.copytree(source_workspace, workspace)
        workspaces[task_slice.slice_id] = workspace

    completed: dict[str, ProbeWorkerResult] = {}
    failures: dict[str, _WorkerFailure] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            task_slice.slice_id: executor.submit(
                _run_worker,
                (task_slice,),
                workspaces[task_slice.slice_id],
                worker,
            )
            for task_slice in plan.slices
        }
        for slice_id, future in futures.items():
            result, failure = future.result()
            if failure is not None:
                failures[slice_id] = failure
            else:
                assert result is not None
                completed[slice_id] = result

    ordered_results = tuple(
        completed[item.slice_id] for item in plan.slices if item.slice_id in completed
    )
    if failures:
        first_failure = next(
            failures[item.slice_id] for item in plan.slices if item.slice_id in failures
        )
        return ordered_results, first_failure

    for task_slice in plan.slices:
        result = completed[task_slice.slice_id]
        _integrate_changes(
            baseline,
            result.workspace,
            final_workspace,
            result.changed_paths,
        )
    return ordered_results, None


def _run_worker(
    task_slices: tuple[ProbeSlice, ...],
    workspace: Path,
    worker: WorkerAdapter,
) -> tuple[ProbeWorkerResult | None, _WorkerFailure | None]:
    if not task_slices:
        return None, _WorkerFailure("worker_assignment_invalid")
    before = _snapshot_workspace(workspace)
    started = perf_counter()
    try:
        observation = worker(task_slices=task_slices, workspace=workspace)
    except Exception:
        return None, _WorkerFailure("worker_error")
    if not isinstance(observation, WorkerObservation):
        return None, _WorkerFailure("worker_result_invalid")

    changed_paths = tuple(sorted(_changed_paths(before, _snapshot_workspace(workspace))))
    allowed_write_paths = {
        path
        for task_slice in task_slices
        for path in task_slice.allowed_write_paths
    }
    if not set(changed_paths).issubset(allowed_write_paths):
        return None, _WorkerFailure("worker_write_scope_violation")
    return (
        ProbeWorkerResult(
            slice_ids=tuple(task_slice.slice_id for task_slice in task_slices),
            workspace=workspace,
            changed_paths=changed_paths,
            wall_seconds=perf_counter() - started,
            observation=observation,
        ),
        None,
    )


def _parallel_plan_issue(plan: ProbePlan) -> str | None:
    if len(plan.slices) != 2:
        return "parallel_requires_two_slices"
    first, second = plan.slices
    if set(first.allowed_write_paths).intersection(second.allowed_write_paths):
        return "parallel_write_scope_overlap"
    return None


def _integrate_changes(
    baseline: dict[str, bytes],
    worker_workspace: Path,
    final_workspace: Path,
    changed_paths: tuple[str, ...],
) -> None:
    for relative_path in changed_paths:
        source = worker_workspace / relative_path
        target = final_workspace / relative_path
        if relative_path not in baseline and not source.exists():
            continue
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.exists():
            target.unlink()


def _snapshot_workspace(workspace: Path) -> dict[str, bytes]:
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("source_workspace 必须是普通目录")
    snapshot: dict[str, bytes] = {}
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if path.is_symlink():
            raise ValueError("workspace 不能包含链接")
        if any(part in _IGNORED_ARTIFACT_DIRECTORIES for part in relative.parts):
            continue
        if path.is_file():
            relative_path = relative.as_posix()
            snapshot[relative_path] = path.read_bytes()
        elif not path.is_dir():
            raise ValueError("workspace 不能包含特殊文件")
    return snapshot


def _changed_paths(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {
        path
        for path in set(before).union(after)
        if before.get(path) != after.get(path)
    }


def _prepare_empty_directory(path: Path) -> None:
    if path.exists():
        raise ValueError("run_root 必须不存在")
    path.mkdir(parents=True)


def _failure_status(issue_code: str) -> ProbeStatus:
    if issue_code == "worker_write_scope_violation":
        return "scope_violation"
    return "worker_failed"


def _blocked_result(
    mode: ProbeMode,
    status: ProbeStatus,
    issue_code: str,
    started: float,
) -> ProbeRunResult:
    return ProbeRunResult(
        mode=mode,
        status=status,
        issue_code=issue_code,
        final_workspace=None,
        changed_paths=(),
        worker_results=(),
        verifier_passed=None,
        wall_seconds=perf_counter() - started,
    )


__all__ = [
    "ProbeMode",
    "ProbePlan",
    "ProbeRunResult",
    "ProbeSlice",
    "ProbeStatus",
    "ProbeVerifier",
    "ProbeWorkerResult",
    "WorkerAdapter",
    "WorkerObservation",
    "run_probe",
]
