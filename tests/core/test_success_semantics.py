from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.experimental.inspection.loop_spec import default_engineering_change_spec
from vega.models import BriefInput
from vega.reflect_runtime import ReflectRuntime
from vega.experimental.inspection.reviewer import run_review
from vega.runner import RunnerResult
from vega.experimental.inspection.runtime import EngineeringChangeRuntime


class QueueRunner:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[str] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        self.calls.append(prompt)
        return RunnerResult(
            status="success",
            output=self.outputs.pop(0),
            command=["queue-runner"],
        )


class NoopRunner:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        return RunnerResult(status="success", output="no changes", command=["noop"])


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
            "# Demo\nimplemented\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(status="success", output="changed README", command=["tracked"])


class UntrackedChangeRunner:
    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.content = content

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        target = repo_path / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.content, encoding="utf-8")
        return RunnerResult(
            status="success",
            output="created untracked implementation",
            command=["untracked"],
        )


class ExistingUntrackedMutationRunner:
    def __init__(self, path: str, action: str, content: str = "") -> None:
        self.path = path
        self.action = action
        self.content = content

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        target = repo_path / self.path
        if self.action == "modify":
            target.write_text(self.content, encoding="utf-8")
        else:
            target.unlink()
        return RunnerResult(
            status="success",
            output=f"{self.action} existing untracked file",
            command=["existing-untracked"],
        )


class UnavailableLLM:
    model = "test-model"
    provider_alias = "test-provider"

    def available(self) -> bool:
        return False

    def generate_plan(self, task_text: str) -> str:
        raise AssertionError("unavailable LLM must not be called")

    def generate_report(
        self,
        task_text: str,
        tool_results: list[object],
        required_sections: list[str] | None = None,
    ) -> str:
        raise AssertionError("unavailable LLM must not be called")


def test_reviewer_does_not_treat_repeated_question_as_answer(tmp_path: Path) -> None:
    runtime = EngineeringChangeRuntime(tmp_path, llm_client=UnavailableLLM())
    task_text = "# Task\n\n问题：\n- README 是否说明安装命令？\n"
    state = _minimal_run_state(tmp_path)
    report = runtime._fallback_report(task_text, state)

    results = run_review(task_text, state, runtime.loop_spec, report)

    assert "FAIL: 报告没有覆盖 1 个任务问题" in results


def test_deterministic_fallback_with_unanswered_question_fails_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    task = tmp_path / "task.md"
    task.write_text(
        "# Task\n\n目标文件：\n- `README.md`\n\n问题：\n- README 是否说明安装命令？\n",
        encoding="utf-8",
    )

    run_dir = EngineeringChangeRuntime(
        workspace,
        llm_client=UnavailableLLM(),
    ).run(task, repo)

    state = _read_json(run_dir / "state.json")
    assert state["status"] == "failed"
    assert "FAIL: 报告没有覆盖 1 个任务问题" in state["review_results"]


def test_deterministic_fallback_without_explicit_questions_fails_with_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    task = tmp_path / "task.md"
    task.write_text(
        "# Task\n\n目标文件：\n- `README.md`\n\n需求：\n- 检查 README 的安装命令并给出结论\n",
        encoding="utf-8",
    )

    run_dir = EngineeringChangeRuntime(
        workspace,
        llm_client=UnavailableLLM(),
    ).run(task, repo)

    state = _read_json(run_dir / "state.json")
    trace = _read_jsonl(run_dir / "trace.jsonl")
    for artifact in [
        "plan.md",
        "report.md",
        "review.md",
        "eval.md",
        "state.json",
        "trace.jsonl",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact
    assert state["status"] == "failed"
    assert state["current_step"] == "done"
    assert "FAIL: 报告明确声明未生成任务答案" in state["review_results"]
    assert "FAIL: reviewer pass 存在失败项" in state["eval_results"]
    assert [item["event"] for item in trace[-3:]] == [
        "review_written",
        "eval_written",
        "run_finished",
    ]
    assert trace[-1]["status"] == "failed"


@pytest.mark.parametrize(
    ("budget_name", "limit", "expected_operation"),
    [
        ("max_steps", 1, "plan"),
        ("max_tool_calls", 1, "file.search:TODO"),
        ("max_minutes", 0, "task_loaded"),
    ],
)
def test_engineering_change_enforces_runtime_budgets_before_excess_work(
    tmp_path: Path,
    budget_name: str,
    limit: int,
    expected_operation: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    task = tmp_path / "task.md"
    task.write_text(
        "# Task\n\n目标文件：\n- `README.md`\n\n问题：\n- README 是否说明安装命令？\n",
        encoding="utf-8",
    )
    spec = default_engineering_change_spec()
    setattr(spec.budget, budget_name, limit)

    run_dir = EngineeringChangeRuntime(
        workspace,
        loop_spec=spec,
        llm_client=UnavailableLLM(),
    ).run(task, repo)

    state = _read_json(run_dir / "state.json")
    trace = _read_jsonl(run_dir / "trace.jsonl")
    budget_event = next(item for item in trace if item["event"] == "budget_exceeded")
    assert state["status"] == "failed"
    assert state["current_step"] == "budget_exceeded"
    assert any(f"runtime budget 超限：{budget_name}" in item for item in state["eval_results"])
    assert budget_event["kind"] == budget_name
    assert budget_event["operation"] == expected_operation

    if budget_name == "max_steps":
        assert not (run_dir / "plan.md").exists()
    elif budget_name == "max_tool_calls":
        assert len(state["tool_results"]) == 1
        assert state["tool_results"][0]["tool"] == "file.read"
    else:
        assert not any(item["event"] == "task_loaded" for item in trace)
        assert state["tool_results"] == []


def test_auto_loop_zero_diff_never_succeeds(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    reviewer = QueueRunner([_review_json("approve")])

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=NoopRunner(),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "no_diff"
    assert state["iterations"][0]["verdict"] is None
    assert reviewer.calls == []
    assert "没有可审查的 tracked diff" in (run_dir / "final-report.md").read_text(
        encoding="utf-8"
    )


def test_auto_loop_untracked_implementation_requires_human_without_reading_content(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    reviewer = QueueRunner([_review_json("approve")])
    marker = "UNTRACKED_CONTENT_MUST_NOT_REACH_REVIEWER"

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=UntrackedChangeRunner("src/new_feature.py", marker),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    iteration_dir = run_dir / "iterations" / "01"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "untracked_files"
    assert state["current_iteration"] == 1
    assert state["iterations"][0]["workspace_new_files_count"] == 1
    assert reviewer.calls == []
    assert "src/new_feature.py" in (iteration_dir / "fix-prompt.md").read_text(
        encoding="utf-8"
    )
    assert marker not in _read_tree(run_dir)


@pytest.mark.parametrize("action", ["modify", "delete"])
def test_auto_loop_detects_existing_untracked_file_mutation(
    tmp_path: Path,
    action: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    marker = "EXISTING_UNTRACKED_SECRET_CONTENT"
    repo.joinpath("local.txt").write_text("before\n", encoding="utf-8")
    reviewer = QueueRunner([_review_json("approve")])

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=ExistingUntrackedMutationRunner(
            "local.txt",
            action,
            marker,
        ),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    workspace_check = _read_json(
        run_dir / "iterations" / "01" / "workspace-check.json"
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_check_failed"
    assert workspace_check["baseline_untracked_changed"] is True
    assert any(
        "修改或删除了启动前已存在的未跟踪文件" in reason
        for reason in workspace_check["reasons"]
    )
    assert reviewer.calls == []
    assert marker not in _read_tree(run_dir)


def test_assist_continue_rejects_any_untracked_file_before_review(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    reviewer = QueueRunner([_review_json("approve")])
    runtime = LoopAutomationRuntime(workspace, reviewer_runner=reviewer)
    run_dir = runtime.start(_brief(repo), "assist", verify=False)
    marker = "ASSIST_UNTRACKED_CONTENT_MUST_STAY_LOCAL"
    repo.joinpath("local.py").write_text(marker, encoding="utf-8")

    runtime.continue_assist(run_dir.name, repo, verify=False)

    state = _read_json(run_dir / "state.json")
    iteration_dir = run_dir / "iterations" / "01"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "untracked_files"
    assert state["current_iteration"] == 1
    assert state["iterations"][0]["workspace_new_files_count"] == 1
    assert reviewer.calls == []
    assert (iteration_dir / "workspace-check.json").exists()
    assert "local.py" in (iteration_dir / "fix-prompt.md").read_text(
        encoding="utf-8"
    )
    assert marker not in _read_tree(run_dir)


def test_reflect_lists_untracked_paths_without_copying_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    marker = "PRIVATE_UNTRACKED_IMPLEMENTATION_CONTENT"
    repo.joinpath("src").mkdir()
    repo.joinpath("src", "new_feature.py").write_text(marker, encoding="utf-8")

    run_dir = ReflectRuntime(workspace).run(repo)

    summary = (run_dir / "diff-summary.md").read_text(encoding="utf-8")
    assert "src/new_feature.py" in summary
    assert "内容不进入 reviewer" in summary
    assert "按本地预算指纹" in summary
    assert "未读取未跟踪文件内容" not in summary
    assert marker not in _read_tree(run_dir)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("empty_trace", "FAIL: trace.jsonl 为空"),
        (
            "missing_terminal",
            "FAIL: trace.jsonl 缺少未被 supersede 的 run_finished 终态事件",
        ),
        (
            "duplicate_terminal",
            "FAIL: trace.jsonl 必须且只能包含一个未被 supersede 的 run_finished 终态事件",
        ),
        ("trailing_event", "FAIL: run_finished 必须是 trace.jsonl 最后一条事件"),
        (
            "invalid_supersede",
            "FAIL: run_terminal_superseded 证据无效："
            "run_terminal_superseded_target_invalid",
        ),
        ("zero_iterations", "FAIL: success loop 至少需要一轮 iteration"),
        ("tampered_verdict", "FAIL: review-verdict.json 与 iteration.verdict 不一致"),
        (
            "missing_verification",
            "FAIL: verification 状态已记录但缺少 verification-summary.md",
        ),
    ],
)
def test_loop_eval_rejects_broken_success_evidence(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    run_dir = _create_successful_loop(tmp_path)
    state_path = run_dir / "state.json"
    state = _read_json(state_path)

    if mutation == "empty_trace":
        (run_dir / "trace.jsonl").write_text("", encoding="utf-8")
    elif mutation == "missing_terminal":
        items = [
            item
            for item in _read_jsonl(run_dir / "trace.jsonl")
            if item.get("event") != "run_finished"
        ]
        _write_jsonl(run_dir / "trace.jsonl", items)
    elif mutation == "duplicate_terminal":
        items = _read_jsonl(run_dir / "trace.jsonl")
        items.append(dict(items[-1]))
        _write_jsonl(run_dir / "trace.jsonl", items)
    elif mutation == "trailing_event":
        items = _read_jsonl(run_dir / "trace.jsonl")
        items.append({"event": "unexpected_after_finish"})
        _write_jsonl(run_dir / "trace.jsonl", items)
    elif mutation == "invalid_supersede":
        items = _read_jsonl(run_dir / "trace.jsonl")
        items.append(
            {
                "event": "run_terminal_superseded",
                "terminal_event_index": len(items) + 10,
                "terminal_status": "success",
            }
        )
        _write_jsonl(run_dir / "trace.jsonl", items)
    elif mutation == "zero_iterations":
        state["iterations"] = []
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    elif mutation == "tampered_verdict":
        verdict_path = run_dir / "iterations" / "01" / "review-verdict.json"
        verdict = _read_json(verdict_path)
        verdict["verdict"] = "request_changes"
        verdict_path.write_text(json.dumps(verdict, ensure_ascii=False), encoding="utf-8")
    elif mutation == "missing_verification":
        (run_dir / "iterations" / "01" / "verification-summary.md").unlink()

    results = run_loop_eval(run_dir, state["artifacts"])

    assert expected in results


def test_loop_eval_accepts_consistent_success_evidence(tmp_path: Path) -> None:
    run_dir = _create_successful_loop(tmp_path)
    state = _read_json(run_dir / "state.json")

    results = run_loop_eval(run_dir, state["artifacts"])

    assert not [result for result in results if result.startswith("FAIL:")]


def test_loop_eval_rejects_superseded_terminal_without_state_binding(
    tmp_path: Path,
) -> None:
    run_dir = _create_successful_loop(tmp_path)
    state = _read_json(run_dir / "state.json")
    items = _read_jsonl(run_dir / "trace.jsonl")
    old_terminal_index = len(items) - 1
    old_terminal = dict(items[-1])
    recovery_id = "synthetic-unbound-recovery"
    items.append(
        {
            "event": "run_terminal_superseded",
            "ts": "2026-07-17T00:00:00+00:00",
            "terminal_event_index": old_terminal_index,
            "terminal_status": old_terminal["status"],
            "recovery_id": recovery_id,
        }
    )
    items.append(
        {
            "event": "loop_recovered",
            "ts": "2026-07-17T00:00:01+00:00",
            "recovery_id": recovery_id,
            "superseded_terminal_event": old_terminal_index,
        }
    )
    items.append(old_terminal)
    _write_jsonl(run_dir / "trace.jsonl", items)

    results = run_loop_eval(run_dir, state["artifacts"])

    assert (
        "FAIL: run_terminal_superseded 证据无效："
        "run_terminal_superseded_state_binding_missing"
    ) in results


def test_loop_eval_rejects_missing_risk_gate_artifact(tmp_path: Path) -> None:
    run_dir = _create_successful_loop(tmp_path)
    state = _read_json(run_dir / "state.json")
    run_dir.joinpath("iterations", "01", "risk-gate-report.md").unlink()

    results = run_loop_eval(run_dir, state["artifacts"])

    assert "FAIL: risk_gate_report_missing" in results


def test_loop_eval_checks_every_iteration_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(),
        reviewer_runner=QueueRunner(
            [_review_json("request_changes"), _review_json("approve")]
        ),
    ).start(_brief(repo), "auto", max_iterations=2, verify=False)
    state = _read_json(run_dir / "state.json")
    first_verdict_path = run_dir / "iterations" / "01" / "review-verdict.json"
    first_verdict = _read_json(first_verdict_path)
    first_verdict["verdict"] = "approve"
    first_verdict_path.write_text(
        json.dumps(first_verdict, ensure_ascii=False),
        encoding="utf-8",
    )

    results = run_loop_eval(run_dir, state["artifacts"])

    assert "FAIL: review-verdict.json 与 iteration.verdict 不一致" in results


def test_loop_runtime_writes_one_terminal_event_for_consistent_run(tmp_path: Path) -> None:
    run_dir = _create_successful_loop(tmp_path)
    state = _read_json(run_dir / "state.json")
    trace = _read_jsonl(run_dir / "trace.jsonl")
    terminal_events = [item for item in trace if item.get("event") == "run_finished"]

    assert len(terminal_events) == 1
    assert terminal_events[0]["status"] == "success"
    assert trace[-1] == terminal_events[0]
    assert "PASS: trace 终态与 state.status 一致" in state["eval_results"]


def test_needs_human_run_uses_pause_event_instead_of_terminal_event(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=NoopRunner(),
        reviewer_runner=QueueRunner([_review_json("approve")]),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    trace = _read_jsonl(run_dir / "trace.jsonl")
    assert trace[-1]["event"] == "run_paused"
    assert trace[-1]["status"] == "needs_human"
    assert not [item for item in trace if item.get("event") == "run_finished"]


def test_loop_eval_exception_fails_closed_without_temporary_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)

    def raise_eval(*args, **kwargs):
        raise RuntimeError("forced eval failure")

    monkeypatch.setattr("vega.loop_runtime.run_loop_eval", raise_eval)
    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(),
        reviewer_runner=QueueRunner([_review_json("approve")]),
    ).start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    terminal_events = [
        item
        for item in _read_jsonl(run_dir / "trace.jsonl")
        if item.get("event") == "run_finished"
    ]
    assert state["status"] == "failed"
    assert "FAIL: loop eval 执行异常：RuntimeError" in state["eval_results"]
    assert [item["status"] for item in terminal_events] == ["failed"]


def test_success_finalization_checks_success_evidence_before_terminal_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    iteration_checks: list[dict[str, object]] = []
    snapshot_calls = 0

    def fail_iteration_check(iteration_dir, iteration, **kwargs):
        del iteration_dir, iteration
        iteration_checks.append(kwargs)
        return ["FAIL: forced success evidence failure"]

    def record_snapshot(*args, **kwargs):
        nonlocal snapshot_calls
        del args, kwargs
        snapshot_calls += 1
        return SimpleNamespace(
            artifact_integrity=SimpleNamespace(
                valid=False,
                issues=(),
                verification_results=(),
            ),
            evidence_freshness=SimpleNamespace(fresh=True, issues=()),
        )

    monkeypatch.setattr(
        "vega.loop_runtime._loop_iteration_evidence_checks",
        fail_iteration_check,
    )
    monkeypatch.setattr(
        "vega.loop_runtime.validate_loop_evidence_snapshot",
        record_snapshot,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(),
        reviewer_runner=QueueRunner([_review_json("approve")]),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    terminal_events = [
        item
        for item in _read_jsonl(run_dir / "trace.jsonl")
        if item.get("event") == "run_finished"
    ]
    assert state["status"] == "failed"
    assert "FAIL: forced success evidence failure" in state["eval_results"]
    assert [item["status"] for item in terminal_events] == ["failed"]
    assert snapshot_calls == 1
    assert iteration_checks[0]["workspace"] is None
    assert iteration_checks[0]["repo_path"] is None


def test_failed_review_run_cannot_promote_parent_loop_to_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    review_run = workspace / "runs" / "failed-review"
    review_run.mkdir(parents=True)
    (review_run / "state.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "runner_status": "success",
            }
        ),
        encoding="utf-8",
    )
    (review_run / "review-verdict.json").write_text(
        _review_json("approve"),
        encoding="utf-8",
    )

    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(),
        reviewer_runner=QueueRunner([_review_json("approve")]),
    )
    monkeypatch.setattr(runtime, "_run_review", lambda *args, **kwargs: review_run)

    run_dir = runtime.start(_brief(repo), "auto", max_iterations=1, verify=False)

    state = _read_json(run_dir / "state.json")
    assert state["status"] == "failed"
    assert state["current_step"] == "review_run_failed"
    assert "不能采用其 verdict" in (run_dir / "final-report.md").read_text(
        encoding="utf-8"
    )


def test_trusted_needs_human_review_is_preserved_for_finish(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    review_summary = "README 变更缺少兼容性说明，需要人工确认。"
    finding = {
        "severity": "major",
        "file": "README.md",
        "line": 1,
        "title": "兼容性说明不足",
        "evidence": "当前 diff 改变了公开说明，但没有交代旧用法是否仍受支持。",
        "recommendation": "补充兼容性边界，或由人工确认该变更可以接受。",
    }
    reviewer_output = json.dumps(
        {
            "verdict": "needs_human",
            "summary": review_summary,
            "findings": [finding],
            "reviewed_files": ["README.md"],
            "checked_items": ["需求覆盖", "兼容性风险", "验证证据"],
        },
        ensure_ascii=False,
    )

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(),
        reviewer_runner=QueueRunner([reviewer_output]),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)

    state = _read_json(run_dir / "state.json")
    iteration = state["iterations"][0]
    review_run = workspace / "runs" / iteration["review_run"]
    child_state = _read_json(review_run / "state.json")

    assert child_state["status"] == "needs_human"
    assert child_state["current_step"] == "done"
    assert child_state["verdict"] == "needs_human"
    assert not [
        result
        for result in child_state["eval_results"]
        if result.startswith("FAIL:")
    ]
    assert state["status"] == "needs_human"
    assert state["current_step"] == "done"
    assert iteration["verdict"] == "needs_human"
    assert iteration["findings_count"] == 1

    FinishRuntime(workspace).run(run_dir.name)
    finish = _read_json(run_dir / "finish-summary.json")
    latest_verdict = finish["latest_verdict"]
    review = finish["first_screen"]["review"]

    assert finish["finish_status"] == "needs_human"
    assert finish["loop_status"] == "needs_human"
    assert latest_verdict["verdict"] == "needs_human"
    assert latest_verdict["summary"] == review_summary
    assert latest_verdict["findings"] == [finding]
    assert latest_verdict["reviewed_files"] == ["README.md"]
    assert review["findings"] == [finding]
    assert review["coverage"]["complete"] is True
    assert review["coverage"]["reviewed_files"] == ["README.md"]


def _create_successful_loop(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    return LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(),
        reviewer_runner=QueueRunner([_review_json("approve")]),
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)


def _minimal_run_state(tmp_path: Path):
    from vega.models import RunState

    return RunState(
        run_id="review-test",
        loop_name="engineering-change",
        status="running",
        repo_path=str(tmp_path),
        task_file=str(tmp_path / "task.md"),
    )


def _brief(repo: Path) -> BriefInput:
    return BriefInput(
        mode="feature",
        text="实现 README 需求",
        source="test",
        repo_path=str(repo),
    )


def _review_json(verdict: str) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "summary": "review complete",
            "findings": [],
            "reviewed_files": ["README.md"],
            "checked_items": ["scope", "tests"],
        }
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8")
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('verification passed')\"",
                "  max_commands: 1",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", "README.md", ".vega.yaml"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )


def _read_tree(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file()
    )
