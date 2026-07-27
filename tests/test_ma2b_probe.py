from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock

from vega.experimental.ma2b.probe import (
    ProbePlan,
    ProbeSlice,
    WorkerObservation,
    run_probe,
)


def test_sequential_and_parallel_produce_same_verified_workspace(tmp_path: Path) -> None:
    source = _source_workspace(tmp_path)
    plan = _two_slice_plan()
    verified: list[tuple[str, str]] = []
    calls: list[tuple[str, ...]] = []

    def worker(
        *,
        task_slices: tuple[ProbeSlice, ...],
        workspace: Path,
    ) -> WorkerObservation:
        calls.append(tuple(task_slice.slice_id for task_slice in task_slices))
        cache = workspace / "__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "generated.cpython-312.pyc").write_bytes(b"generated")
        for task_slice in task_slices:
            target = workspace / task_slice.allowed_write_paths[0]
            target.write_text(f"{task_slice.slice_id}\n", encoding="utf-8")
        return WorkerObservation(
            input_tokens=100,
            output_tokens=20,
            cost_usd=Decimal("0.001"),
        )

    def verifier(workspace: Path) -> bool:
        values = (
            (workspace / "backend.txt").read_text(encoding="utf-8"),
            (workspace / "frontend.txt").read_text(encoding="utf-8"),
        )
        verified.append(values)
        return values == ("backend\n", "frontend\n")

    sequential = run_probe(
        mode="sequential",
        plan=plan,
        source_workspace=source,
        run_root=tmp_path / "sequential",
        worker=worker,
        verifier=verifier,
    )
    parallel = run_probe(
        mode="parallel",
        plan=plan,
        source_workspace=source,
        run_root=tmp_path / "parallel",
        worker=worker,
        verifier=verifier,
    )

    assert sequential.status == parallel.status == "passed"
    assert sequential.changed_paths == parallel.changed_paths == (
        "backend.txt",
        "frontend.txt",
    )
    assert len(sequential.worker_results) == 1
    assert sequential.worker_results[0].slice_ids == ("backend", "frontend")
    assert calls[0] == ("backend", "frontend")
    assert sorted(calls[1:]) == [("backend",), ("frontend",)]
    assert verified == [("backend\n", "frontend\n")] * 2
    assert [item.slice_ids for item in parallel.worker_results] == [
        ("backend",),
        ("frontend",),
    ]
    assert all(item.observation.input_tokens == 100 for item in parallel.worker_results)


def test_parallel_workers_use_isolated_workspaces_and_run_concurrently(
    tmp_path: Path,
) -> None:
    barrier = Barrier(2)
    lock = Lock()
    workspaces: list[Path] = []

    def worker(
        *,
        task_slices: tuple[ProbeSlice, ...],
        workspace: Path,
    ) -> WorkerObservation:
        (task_slice,) = task_slices
        with lock:
            workspaces.append(workspace)
        barrier.wait(timeout=2)
        (workspace / task_slice.allowed_write_paths[0]).write_text(
            task_slice.slice_id,
            encoding="utf-8",
        )
        return WorkerObservation()

    result = run_probe(
        mode="parallel",
        plan=_two_slice_plan(),
        source_workspace=_source_workspace(tmp_path),
        run_root=tmp_path / "run",
        worker=worker,
        verifier=lambda _: True,
    )

    assert result.status == "passed"
    assert len(set(workspaces)) == 2
    assert all(path.parent.name == "workers" for path in workspaces)


def test_scope_violation_blocks_before_verifier(tmp_path: Path) -> None:
    verifier_calls = 0

    def worker(
        *,
        task_slices: tuple[ProbeSlice, ...],
        workspace: Path,
    ) -> WorkerObservation:
        (workspace / "outside.txt").write_text(
            task_slices[0].slice_id,
            encoding="utf-8",
        )
        return WorkerObservation()

    def verifier(_: Path) -> bool:
        nonlocal verifier_calls
        verifier_calls += 1
        return True

    result = run_probe(
        mode="sequential",
        plan=ProbePlan((ProbeSlice("backend", ("backend.txt",)),)),
        source_workspace=_source_workspace(tmp_path),
        run_root=tmp_path / "run",
        worker=worker,
        verifier=verifier,
    )

    assert result.status == "scope_violation"
    assert result.issue_code == "worker_write_scope_violation"
    assert result.changed_paths == ("outside.txt",)
    assert verifier_calls == 0


def test_parallel_plan_rejects_overlapping_write_scope_before_workers(
    tmp_path: Path,
) -> None:
    worker_calls = 0

    def worker(
        *,
        task_slices: tuple[ProbeSlice, ...],
        workspace: Path,
    ) -> WorkerObservation:
        nonlocal worker_calls
        worker_calls += 1
        return WorkerObservation()

    plan = ProbePlan(
        (
            ProbeSlice("first", ("backend.txt",)),
            ProbeSlice("second", ("backend.txt",)),
        )
    )
    result = run_probe(
        mode="parallel",
        plan=plan,
        source_workspace=_source_workspace(tmp_path),
        run_root=tmp_path / "run",
        worker=worker,
        verifier=lambda _: True,
    )

    assert result.status == "invalid_plan"
    assert result.issue_code == "parallel_write_scope_overlap"
    assert result.final_workspace is None
    assert worker_calls == 0


def test_worker_failure_blocks_run(tmp_path: Path) -> None:
    def worker(
        *,
        task_slices: tuple[ProbeSlice, ...],
        workspace: Path,
    ) -> WorkerObservation:
        slice_ids = ",".join(task_slice.slice_id for task_slice in task_slices)
        raise RuntimeError(f"{slice_ids}:{workspace}")

    result = run_probe(
        mode="sequential",
        plan=ProbePlan((ProbeSlice("backend", ("backend.txt",)),)),
        source_workspace=_source_workspace(tmp_path),
        run_root=tmp_path / "run",
        worker=worker,
        verifier=lambda _: True,
    )

    assert result.status == "worker_failed"
    assert result.issue_code == "worker_error"
    assert result.verifier_passed is None


def test_verifier_failure_is_reported(tmp_path: Path) -> None:
    def worker(
        *,
        task_slices: tuple[ProbeSlice, ...],
        workspace: Path,
    ) -> WorkerObservation:
        (task_slice,) = task_slices
        (workspace / task_slice.allowed_write_paths[0]).write_text(
            "changed",
            encoding="utf-8",
        )
        return WorkerObservation(output_tokens=5)

    result = run_probe(
        mode="sequential",
        plan=ProbePlan((ProbeSlice("backend", ("backend.txt",)),)),
        source_workspace=_source_workspace(tmp_path),
        run_root=tmp_path / "run",
        worker=worker,
        verifier=lambda _: False,
    )

    assert result.status == "verification_failed"
    assert result.issue_code == "verifier_failed"
    assert result.verifier_passed is False
    assert result.changed_paths == ("backend.txt",)


def _two_slice_plan() -> ProbePlan:
    return ProbePlan(
        (
            ProbeSlice("backend", ("backend.txt",)),
            ProbeSlice("frontend", ("frontend.txt",)),
        )
    )


def _source_workspace(root: Path) -> Path:
    workspace = root / "source"
    workspace.mkdir()
    (workspace / "backend.txt").write_text("before\n", encoding="utf-8")
    (workspace / "frontend.txt").write_text("before\n", encoding="utf-8")
    return workspace
