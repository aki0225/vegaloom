from __future__ import annotations

import importlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

import vega.loop_graph_decision as loop_graph_decision
from vega.decision import DecisionStore
from vega.goal_evidence import validate_review_evidence_freshness
from vega.loop_graph_decision import (
    GraphDecisionValidationError,
    consume_pending_decision,
    read_decision_consumption,
    read_pending_decision,
    validate_pending_decision_bindings,
)
from vega.loop_graph_state import read_graph_state
from vega.models import LoopAutomationState
from vega.review_runtime import ReviewRuntime

_LANGGRAPH_MODULES = ("langgraph", "langgraph.checkpoint.sqlite")


def _module_spec_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError as exc:
        missing_name = exc.name
        if missing_name and (
            module_name == missing_name
            or module_name.startswith(f"{missing_name}.")
        ):
            return False
        raise


_missing_langgraph_modules = [
    module_name
    for module_name in _LANGGRAPH_MODULES
    if not _module_spec_exists(module_name)
]
if _missing_langgraph_modules:
    pytest.skip(
        "需要安装 `vegaloom[langgraph]` 可选依赖",
        allow_module_level=True,
    )
for _module_name in _LANGGRAPH_MODULES:
    importlib.import_module(_module_name)

_loop_graph_runtime = importlib.import_module("vega.loop_graph_runtime")
GraphExecutionInterrupted = _loop_graph_runtime.GraphExecutionInterrupted
_interrupt_resume = importlib.import_module(
    "tests.experimental.langgraph_engine.test_interrupt_resume"
)
HitlReviewer = _interrupt_resume.HitlReviewer
_build_runtime = _interrupt_resume._build_runtime
_start_pending_run = _interrupt_resume._start_pending_run
_state = _interrupt_resume._state


class CrashOnce:
    def __init__(self, target: str) -> None:
        self.target = target
        self.triggered = False

    def __call__(self, point: str) -> None:
        if point == self.target and not self.triggered:
            self.triggered = True
            raise GraphExecutionInterrupted(
                f"fault injected at {point}"
            )


def _pending(run_dir: Path):
    pending_id = read_graph_state(run_dir)["pending_human_decision_id"]
    assert pending_id is not None
    return read_pending_decision(run_dir, pending_id)


def _append_approval(
    run_dir: Path,
    *,
    references: list[str],
):
    return DecisionStore(run_dir).append(
        decision_type="gate",
        decision="approved",
        reason="人工确认当前高风险变更可以进入隔离审查。",
        references=references,
    )


def test_workspace_drift_invalidates_old_approval(tmp_path: Path) -> None:
    workspace, repo, run_dir, worker, reviewer = _start_pending_run(
        tmp_path
    )
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    repo.joinpath("README.md").write_text(
        "# Demo\nGATE4_HITL_EFFECT\nDRIFT_AFTER_APPROVAL\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        GraphDecisionValidationError,
        match="workspace 已偏离",
    ):
        _build_runtime(
            workspace,
            worker,
            reviewer,
        ).resume_langgraph_decision(
            run_dir.name,
            decision.id,
            engine="langgraph",
        )

    assert worker.calls == 1
    assert reviewer.calls == 0
    assert not (
        run_dir
        / "graph"
        / "decision-consumptions"
        / f"{pending.pending_id}.json"
    ).exists()


def test_evidence_drift_invalidates_old_approval(tmp_path: Path) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    risk_report = run_dir / "iterations" / "01" / "risk-gate-report.md"
    risk_report.write_text(
        risk_report.read_text(encoding="utf-8")
        + "\n人工篡改\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        GraphDecisionValidationError,
        match="evidence 已漂移",
    ):
        _build_runtime(
            workspace,
            worker,
            reviewer,
        ).resume_langgraph_decision(
            run_dir.name,
            decision.id,
            engine="langgraph",
        )

    assert worker.calls == 1
    assert reviewer.calls == 0


def test_project_policy_drift_invalidates_old_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo, run_dir, worker, reviewer = _start_pending_run(
        tmp_path
    )
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "risk:",
                "  high_paths:",
                "    - README.md",
                "    - src/**",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    # 独立固定 workspace 身份，证明 policy fingerprint 本身也是恢复握手的一部分，
    # 而不是只依赖 workspace fingerprint 的偶然覆盖。
    monkeypatch.setattr(
        loop_graph_decision,
        "current_workspace_fingerprint",
        lambda _repo_path: pending.workspace_fingerprint,
    )

    with pytest.raises(
        GraphDecisionValidationError,
        match="当前项目 policy 已偏离",
    ):
        _build_runtime(
            workspace,
            worker,
            reviewer,
        ).resume_langgraph_decision(
            run_dir.name,
            decision.id,
            engine="langgraph",
        )

    assert worker.calls == 1
    assert reviewer.calls == 0


def test_failed_verification_cannot_be_overridden_by_approval(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(
        tmp_path,
        verification_failed_count=1,
    )
    pending = _pending(run_dir)
    assert pending.verification_failed_count == 1
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )

    with pytest.raises(
        GraphDecisionValidationError,
        match="verification failed",
    ):
        _build_runtime(
            workspace,
            worker,
            reviewer,
            verification_failed_count=1,
        ).resume_langgraph_decision(
            run_dir.name,
            decision.id,
            engine="langgraph",
        )

    assert worker.calls == 1
    assert reviewer.calls == 0


def test_decision_must_reference_current_pending_artifact(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(tmp_path)
    decision = _append_approval(
        run_dir,
        references=["iterations/01/risk-gate-report.md"],
    )

    with pytest.raises(
        GraphDecisionValidationError,
        match="未引用当前 pending",
    ):
        _build_runtime(
            workspace,
            worker,
            reviewer,
        ).resume_langgraph_decision(
            run_dir.name,
            decision.id,
            engine="langgraph",
        )

    assert worker.calls == 1
    assert reviewer.calls == 0


def test_consumption_is_idempotent_for_same_decision_and_conflicts_for_other(
    tmp_path: Path,
) -> None:
    _, _, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    state = LoopAutomationState.model_validate(_state(run_dir))
    first = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    validate_pending_decision_bindings(
        run_dir,
        pending,
        state=state,
        decision=first,
    )
    consumed = consume_pending_decision(run_dir, pending, first)

    assert consume_pending_decision(
        run_dir,
        pending,
        first,
    ) == consumed

    second = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    with pytest.raises(
        GraphDecisionValidationError,
        match="不同 decision identity",
    ):
        consume_pending_decision(run_dir, pending, second)


def test_existing_consumption_reuses_record_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    consumed = consume_pending_decision(run_dir, pending, decision)

    def fail_if_called(_path, _payload) -> bool:
        raise AssertionError("已有 consumption 不应再次进入写入路径")

    monkeypatch.setattr(
        loop_graph_decision,
        "_write_json_create_once",
        fail_if_called,
    )

    assert consume_pending_decision(
        run_dir,
        pending,
        decision,
    ) == consumed


def test_concurrent_same_decision_consumption_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    barrier = Barrier(2)
    real_create_once = loop_graph_decision._write_json_create_once

    def synchronized_create_once(path, payload) -> bool:
        barrier.wait(timeout=5)
        return real_create_once(path, payload)

    monkeypatch.setattr(
        loop_graph_decision,
        "_write_json_create_once",
        synchronized_create_once,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: consume_pending_decision(
                    run_dir,
                    pending,
                    decision,
                ),
                range(2),
            )
        )

    assert results[0] == results[1]
    assert results[0] == read_decision_consumption(
        run_dir,
        pending.pending_id,
    )


def test_concurrent_consumption_publishes_only_one_decision_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    first = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    second = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    barrier = Barrier(2)
    real_create_once = loop_graph_decision._write_json_create_once

    def synchronized_create_once(path, payload) -> bool:
        barrier.wait(timeout=5)
        return real_create_once(path, payload)

    monkeypatch.setattr(
        loop_graph_decision,
        "_write_json_create_once",
        synchronized_create_once,
    )

    def consume(decision):
        try:
            return (
                "consumed",
                consume_pending_decision(run_dir, pending, decision),
            )
        except GraphDecisionValidationError as exc:
            return ("conflict", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume, (first, second)))

    consumed = [result for status, result in results if status == "consumed"]
    conflicts = [result for status, result in results if status == "conflict"]
    assert len(consumed) == 1
    assert conflicts == ["pending decision 已被不同 decision identity 消费"]
    assert read_decision_consumption(
        run_dir,
        pending.pending_id,
    ).decision_id == consumed[0].decision_id


def test_consumption_write_failure_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    real_open = Path.open

    def fail_temp_open(path: Path, *args, **kwargs):
        if path.name.startswith(".tmp-"):
            raise OSError("simulated disk failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_temp_open)

    with pytest.raises(
        GraphDecisionValidationError,
        match="无法写入 decision consumption 临时文件",
    ):
        consume_pending_decision(run_dir, pending, decision)


def test_consumption_rejects_filesystem_without_atomic_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )

    def fail_link(_source, _target) -> None:
        raise OSError("simulated unsupported hard link")

    monkeypatch.setattr(loop_graph_decision.os, "link", fail_link)

    with pytest.raises(
        GraphDecisionValidationError,
        match="文件系统不支持 decision consumption 独占发布",
    ):
        consume_pending_decision(run_dir, pending, decision)


def test_crash_after_consumption_reuses_same_decision_without_second_approval(
    tmp_path: Path,
) -> None:
    workspace, repo, run_dir, worker, reviewer = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    crash = CrashOnce("after_decision_consumption_before_state")

    with pytest.raises(GraphExecutionInterrupted, match="fault injected"):
        _build_runtime(
            workspace,
            worker,
            reviewer,
            graph_fault_injector=crash,
        ).resume_langgraph_decision(
            run_dir.name,
            decision.id,
            engine="langgraph",
        )

    assert _state(run_dir)["current_step"] == "human_decision"
    assert reviewer.calls == 0
    assert len(DecisionStore(run_dir).list()) == 1
    assert read_decision_consumption(
        run_dir,
        pending.pending_id,
    ).decision_id == decision.id

    _build_runtime(
        workspace,
        worker,
        reviewer,
    ).resume_langgraph_decision(
        run_dir.name,
        decision.id,
        engine="langgraph",
    )

    assert _state(run_dir)["status"] == "success"
    assert worker.calls == 1
    assert reviewer.calls == 1
    assert len(DecisionStore(run_dir).list()) == 1

    loop_state = _state(run_dir)
    review_run_id = loop_state["iterations"][0]["review_run"]
    assert isinstance(review_run_id, str)
    review_run = run_dir.parent / review_run_id
    review_state = _state(review_run)
    review_context = json.loads(
        review_run.joinpath("review-context.json").read_text(
            encoding="utf-8"
        )
    )
    assert review_state["status"] == "success"
    assert review_context["human_approval"]["status"] == "valid"
    freshness = validate_review_evidence_freshness(
        workspace,
        repo,
        review_run.name,
    )
    assert freshness.fresh is True, freshness.issues


def test_standalone_high_risk_review_remains_blocked_without_consumption(
    tmp_path: Path,
) -> None:
    workspace, repo, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    reviewer = HitlReviewer()

    review_run = ReviewRuntime(
        workspace,
        runner=reviewer,
    ).run(
        repo,
        pending.reflect_run_id,
    )

    review_state = _state(review_run)
    review_context = json.loads(
        review_run.joinpath("review-context.json").read_text(
            encoding="utf-8"
        )
    )
    assert reviewer.calls == 1
    assert review_state["status"] == "needs_human"
    assert review_state["current_step"] == "risk_gate_needs_human"
    assert review_context["human_approval"]["status"] == "missing"


def test_drifted_consumption_cannot_authorize_high_risk_reviewer(
    tmp_path: Path,
) -> None:
    workspace, repo, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    state = LoopAutomationState.model_validate(_state(run_dir))
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    validate_pending_decision_bindings(
        run_dir,
        pending,
        state=state,
        decision=decision,
    )
    consumption = consume_pending_decision(
        run_dir,
        pending,
        decision,
    )
    risk_report = run_dir / "iterations" / "01" / "risk-gate-report.md"
    risk_report.write_text(
        risk_report.read_text(encoding="utf-8") + "\n消费后漂移\n",
        encoding="utf-8",
        newline="\n",
    )
    reviewer = HitlReviewer()

    review_run = ReviewRuntime(
        workspace,
        runner=reviewer,
    ).run(
        repo,
        pending.reflect_run_id,
        human_approval_run_dir=run_dir,
        human_approval_iteration=pending.iteration,
        human_approval_ref=(
            "graph/decision-consumptions/"
            f"{consumption.pending_id}.json"
        ),
    )

    review_state = _state(review_run)
    review_context = json.loads(
        review_run.joinpath("review-context.json").read_text(
            encoding="utf-8"
        )
    )
    approval = review_context["human_approval"]
    assert reviewer.calls == 1
    assert review_state["status"] == "needs_human"
    assert review_state["current_step"] == "risk_gate_needs_human"
    assert approval["status"] == "invalid"
    assert "evidence 已漂移" in approval["diagnostic"]


def test_review_rejects_consumption_ref_alias_for_another_pending_identity(
    tmp_path: Path,
) -> None:
    workspace, repo, run_dir, _, _ = _start_pending_run(tmp_path)
    pending = _pending(run_dir)
    state = LoopAutomationState.model_validate(_state(run_dir))
    decision = _append_approval(
        run_dir,
        references=[pending.artifact_ref],
    )
    validate_pending_decision_bindings(
        run_dir,
        pending,
        state=state,
        decision=decision,
    )
    consumption = consume_pending_decision(
        run_dir,
        pending,
        decision,
    )
    real_path = (
        run_dir
        / "graph"
        / "decision-consumptions"
        / f"{consumption.pending_id}.json"
    )
    alias_pending_id = "pending-" + "f" * 24
    alias_ref = (
        "graph/decision-consumptions/"
        f"{alias_pending_id}.json"
    )
    run_dir.joinpath(*alias_ref.split("/")).write_bytes(
        real_path.read_bytes()
    )
    reviewer = HitlReviewer()

    review_run = ReviewRuntime(
        workspace,
        runner=reviewer,
    ).run(
        repo,
        pending.reflect_run_id,
        human_approval_run_dir=run_dir,
        human_approval_iteration=pending.iteration,
        human_approval_ref=alias_ref,
    )

    review_state = _state(review_run)
    review_context = json.loads(
        review_run.joinpath("review-context.json").read_text(
            encoding="utf-8"
        )
    )
    approval = review_context["human_approval"]
    assert reviewer.calls == 1
    assert review_state["status"] == "needs_human"
    assert review_state["current_step"] == "risk_gate_needs_human"
    assert approval["status"] == "invalid"
    assert "pending identity 不一致" in approval["diagnostic"]
