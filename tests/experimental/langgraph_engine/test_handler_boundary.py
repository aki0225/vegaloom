from __future__ import annotations

import ast
import inspect
import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import vega.loop_engine as loop_engine
import vega.loop_runtime as loop_runtime
import vega.loop_steps as loop_steps
from vega.loop_runtime import LoopAutomationRuntime
from vega.loop_steps import (
    CaptureWorkspaceStepRequest,
    FinalizeRunStepRequest,
    HumanDecisionStepRequest,
    LoopStepServices,
    PrepareRunStepRequest,
    ReflectStepRequest,
    ReviewStepRequest,
    RiskStepRequest,
    VerificationStepRequest,
    WorkerEpochStepRequest,
    WorkspaceReconcileStepRequest,
)
from vega.models import BriefInput
from vega.runner import RunnerResult


class TrackedChangeRunner:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        repo_path.joinpath("README.md").write_text(
            "# Demo\nchanged\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="changed README",
            command=["tracked-change"],
        )


class ApprovingRunner:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "通过",
                    "findings": [],
                },
                ensure_ascii=False,
            ),
            command=["approve"],
        )


def _init_git_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    path.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Tests",
            "-c",
            "user.email=vega@example.invalid",
            "commit",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _brief(repo: Path) -> BriefInput:
    return BriefInput(
        mode="bug",
        text="修复 README",
        source="test",
        repo_path=str(repo),
    )


def _imported_roots(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    return imported_roots


def _recording_services(
    defaults: LoopStepServices,
    events: list[str],
    requests: dict[str, list[object]],
) -> LoopStepServices:
    def wrap(
        name: str,
        handler: Callable[[Any], Any],
    ) -> Callable[[Any], Any]:
        def recorded(request: Any) -> Any:
            events.append(name)
            requests.setdefault(name, []).append(request)
            return handler(request)

        return recorded

    return replace(
        defaults,
        prepare_run=wrap("prepare_run", defaults.prepare_run),
        capture_workspace=wrap(
            "capture_workspace",
            defaults.capture_workspace,
        ),
        execute_worker_epoch=wrap(
            "execute_worker_epoch",
            defaults.execute_worker_epoch,
        ),
        reconcile_workspace=wrap(
            "reconcile_workspace",
            defaults.reconcile_workspace,
        ),
        run_verification=wrap("run_verification", defaults.run_verification),
        run_reflect=wrap("run_reflect", defaults.run_reflect),
        evaluate_risk=wrap("evaluate_risk", defaults.evaluate_risk),
        request_human_decision=wrap(
            "request_human_decision",
            defaults.request_human_decision,
        ),
        dispatch_review=wrap("dispatch_review", defaults.dispatch_review),
        finalize_run=wrap("finalize_run", defaults.finalize_run),
    )


def test_gate1_engine_step_and_runtime_modules_have_no_langgraph_import() -> None:
    assert "langgraph" not in _imported_roots(loop_engine)
    assert "langgraph" not in _imported_roots(loop_steps)
    assert "langgraph" not in _imported_roots(loop_runtime)


def test_structured_step_service_contract_is_complete() -> None:
    assert set(LoopStepServices.__dataclass_fields__) == {
        "prepare_run",
        "capture_workspace",
        "execute_worker_epoch",
        "reconcile_workspace",
        "run_verification",
        "run_reflect",
        "evaluate_risk",
        "request_human_decision",
        "dispatch_review",
        "finalize_run",
    }


def test_linear_operational_methods_do_not_bypass_step_services() -> None:
    source = "\n".join(
        inspect.getsource(method)
        for method in (
            LoopAutomationRuntime.start,
            LoopAutomationRuntime.continue_assist,
            LoopAutomationRuntime._run_auto_iterations,  # noqa: SLF001
        )
    )

    for direct_dependency in (
        "BriefRuntime(",
        "snapshot_workspace(",
        "run_workspace_check(",
        "run_project_verification(",
        "ReflectRuntime(",
        "ReviewRuntime(",
        "evaluate_risk(",
    ):
        assert direct_dependency not in source


def test_constructor_injected_services_receive_all_auto_step_requests(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    worker = TrackedChangeRunner()
    reviewer = ApprovingRunner()
    default_runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )
    events: list[str] = []
    requests: dict[str, list[object]] = {}
    services = _recording_services(
        default_runtime.step_services,
        events,
        requests,
    )
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
        step_services=services,
    )
    brief = _brief(repo)

    run_dir = runtime.start(
        brief,
        "auto",
        max_iterations=1,
        verify=True,
    )

    assert runtime.step_services is services
    assert events == [
        "prepare_run",
        "capture_workspace",
        "execute_worker_epoch",
        "reconcile_workspace",
        "run_verification",
        "run_reflect",
        "evaluate_risk",
        "dispatch_review",
        "finalize_run",
    ]
    assert isinstance(requests["prepare_run"][0], PrepareRunStepRequest)
    assert requests["prepare_run"][0].brief_input is brief  # type: ignore[union-attr]

    capture_request = requests["capture_workspace"][0]
    assert isinstance(capture_request, CaptureWorkspaceStepRequest)
    assert capture_request.repo_path == repo

    worker_request = requests["execute_worker_epoch"][0]
    assert isinstance(worker_request, WorkerEpochStepRequest)
    assert worker_request.repo_path == repo
    assert worker_request.sandbox == "workspace-write"
    assert worker_request.execution_context.step == "worker"
    assert worker_request.execution_context.iteration == 1

    workspace_request = requests["reconcile_workspace"][0]
    assert isinstance(workspace_request, WorkspaceReconcileStepRequest)
    assert workspace_request.repo_path == repo
    assert workspace_request.baseline is not None
    assert workspace_request.output_dir.name == "01"

    verification_request = requests["run_verification"][0]
    assert isinstance(verification_request, VerificationStepRequest)
    assert verification_request.workspace == tmp_path
    assert verification_request.repo_path == repo

    reflect_request = requests["run_reflect"][0]
    assert isinstance(reflect_request, ReflectStepRequest)
    assert reflect_request.workspace == tmp_path
    assert reflect_request.repo_path == repo
    assert reflect_request.source_run
    assert reflect_request.test_log is not None

    risk_request = requests["evaluate_risk"][0]
    assert isinstance(risk_request, RiskStepRequest)
    assert risk_request.workspace == tmp_path
    assert risk_request.repo_path == repo
    assert "request_human_decision" not in requests

    review_request = requests["dispatch_review"][0]
    assert isinstance(review_request, ReviewStepRequest)
    assert review_request.reflect_run.name == risk_request.source_run
    assert review_request.loop_run_dir == run_dir
    assert review_request.iteration == 1

    finalize_request = requests["finalize_run"][0]
    assert isinstance(finalize_request, FinalizeRunStepRequest)
    assert finalize_request.run_dir == run_dir
    assert finalize_request.state.run_id == run_dir.name
    assert finalize_request.status == "success"


def test_worker_interruption_short_circuits_downstream_step_services(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    worker = TrackedChangeRunner()
    reviewer = ApprovingRunner()
    default_runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )
    events: list[str] = []
    requests: dict[str, list[object]] = {}
    services = _recording_services(
        default_runtime.step_services,
        events,
        requests,
    )

    def stop_worker(request: WorkerEpochStepRequest) -> RunnerResult:
        events.append("execute_worker_epoch")
        requests.setdefault("execute_worker_epoch", []).append(request)
        return RunnerResult(
            status="stopped",
            output="",
            error="测试停止",
            command=["stop"],
        )

    services = replace(services, execute_worker_epoch=stop_worker)
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
        step_services=services,
    )

    run_dir = runtime.start(
        _brief(repo),
        "auto",
        max_iterations=1,
        verify=True,
    )
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))

    assert events == [
        "prepare_run",
        "capture_workspace",
        "execute_worker_epoch",
        "finalize_run",
    ]
    assert state["status"] == "needs_human"
    assert state["current_step"] == "stopped"


def test_linear_high_risk_flow_routes_human_decision_through_step_service(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nrisk:\n  high_paths:\n    - README.md\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", ".vega.yaml"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Tests",
            "-c",
            "user.email=vega@example.invalid",
            "commit",
            "-m",
            "add policy",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    worker = TrackedChangeRunner()
    reviewer = ApprovingRunner()
    default_runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )
    events: list[str] = []
    requests: dict[str, list[object]] = {}
    services = _recording_services(
        default_runtime.step_services,
        events,
        requests,
    )

    run_dir = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
        step_services=services,
    ).start(
        _brief(repo),
        "auto",
        max_iterations=1,
        verify=False,
    )

    assert events[-3:] == [
        "evaluate_risk",
        "request_human_decision",
        "finalize_run",
    ]
    request = requests["request_human_decision"][0]
    assert isinstance(request, HumanDecisionStepRequest)
    assert request.iteration == 1
    assert request.repo_path == repo
    state = json.loads(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_needs_human"
