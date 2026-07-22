from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime as real_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "langgraph_reviewer_topology_eval.py"
)


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "langgraph_reviewer_topology_eval",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_readiness_matches_frozen_provider_session_counts() -> None:
    harness = _load_harness()
    dataset = harness.load_public_dataset(harness.DEFAULT_DATASET)

    assert harness._validate_readiness(dataset) == {
        "single": 12,
        "fixed_three": 36,
        "adaptive": 24,
    }
    assert harness._sha256_file(
        harness.DEFAULT_GROUND_TRUTH
    ) == harness.FROZEN_GROUND_TRUTH_SHA256
    ground_truth = harness.load_ground_truth(
        harness.DEFAULT_GROUND_TRUTH,
        expected_sha256=harness.FROZEN_GROUND_TRUTH_SHA256,
    )
    assert {
        finding.rule_id
        for case in ground_truth.cases
        for finding in case.expected_findings
    } == set(harness._FROZEN_RULE_IDS)


def test_topology_order_is_balanced_over_twelve_cases() -> None:
    harness = _load_harness()
    orders = [harness._topology_order(index) for index in range(12)]

    assert orders[0] == ("single", "adaptive", "fixed_three")
    assert orders[1] == ("adaptive", "fixed_three", "single")
    assert orders[2] == ("fixed_three", "single", "adaptive")
    for position in range(3):
        counts = Counter(order[position] for order in orders)
        assert counts == Counter(
            {"single": 4, "fixed_three": 4, "adaptive": 4}
        )


def test_token_parser_uses_last_codex_total() -> None:
    harness = _load_harness()
    output = (
        "tokens used\n1,234\n"
        "diagnostic\n"
        "tokens used\n8,024\n"
    )

    assert harness._parse_tokens_used(output) == 8024
    assert harness._parse_tokens_used("no usage") is None


def test_summary_date_uses_current_local_date(monkeypatch) -> None:
    harness = _load_harness()

    class FrozenDateTime:
        @classmethod
        def now(cls):
            return real_datetime(2026, 7, 19, 10, 11, 40)

    monkeypatch.setattr(harness, "datetime", FrozenDateTime)

    assert harness._current_local_date() == "2026-07-19"


def test_public_evidence_uses_neutral_case_identity(tmp_path: Path) -> None:
    harness = _load_harness()
    dataset = harness.load_public_dataset(harness.DEFAULT_DATASET)
    case = dataset.cases[0]
    fixture = harness._prepare_fixture(
        tmp_path / "fixtures",
        case,
        neutral_case_id="case-01",
    )

    evidence = harness._render_public_evidence(
        case,
        fixture=fixture,
        dataset_id=dataset.dataset_id,
        dataset_sha256=harness.FROZEN_DATASET_SHA256,
        ground_truth_sha256=harness.FROZEN_GROUND_TRUTH_SHA256,
        neutral_case_id="case-01",
    )

    assert "case-01" in evidence
    assert case.case_id not in evidence
    assert case.category not in evidence
    assert "correctness.off_by_one" not in evidence
    assert harness.GROUND_TRUTH_FORBIDDEN_MARKER not in evidence
    git_log = harness._git_output(
        fixture.repo_path,
        "log",
        "-1",
        "--format=%B",
    )
    assert git_log == "fixture baseline case-01"
    assert case.case_id not in git_log


def test_single_case_fake_artifact_chain_never_calls_provider(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    dataset = harness.load_public_dataset(harness.DEFAULT_DATASET)
    case = dataset.cases[0]
    fixture = harness._prepare_fixture(
        tmp_path / "fixtures",
        case,
        neutral_case_id="case-01",
    )
    run_dir = tmp_path / "runs" / "gate55-test-case-01"
    run_dir.mkdir(parents=True)
    evidence, routing = harness._prepare_case_evidence(
        run_dir,
        public_case=case,
        fixture=fixture,
        dataset_id=dataset.dataset_id,
        dataset_sha256=harness.FROZEN_DATASET_SHA256,
        ground_truth_sha256=harness.FROZEN_GROUND_TRUTH_SHA256,
        neutral_case_id="case-01",
    )
    runner = harness.DeterministicFakeReviewer()
    bundles = [
        harness._execute_topology(
            run_dir=run_dir,
            repo_path=fixture.repo_path,
            public_case=case,
            topology=topology,
            routing=routing,
            evidence=evidence,
            runner=runner,
            timeout_seconds=30,
            ground_truth_sha256=harness.FROZEN_GROUND_TRUTH_SHA256,
            neutral_case_id="case-01",
        )
        for topology in harness.TOPOLOGIES
    ]

    assert [item.record.topology for item in bundles] == list(
        harness.TOPOLOGIES
    )
    assert [len(item.results) for item in bundles] == [1, 3, 2]
    assert len({item.record.evidence_snapshot_sha256 for item in bundles}) == 1
    for evidence_path in run_dir.rglob("public-evidence.md"):
        text = evidence_path.read_text(encoding="utf-8")
        assert case.case_id not in text
        assert case.category not in text


def test_prompt_guard_rejects_private_markers_before_runner(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    dataset = harness.load_public_dataset(harness.DEFAULT_DATASET)
    case = dataset.cases[0]
    fixture = harness._prepare_fixture(
        tmp_path / "fixtures",
        case,
        neutral_case_id="case-01",
    )

    class Inner:
        calls = 0

        def execution_identity(self, sandbox: str):
            return {"sandbox": sandbox}

        def run(self, prompt, repo_path, **kwargs):
            self.calls += 1
            return harness.RunnerResult(
                status="success",
                output="{}",
                command=["inner"],
            )

    inner = Inner()
    guard = harness.PromptGuardRunner(
        inner,
        neutral_case_id="case-01",
        forbidden_markers=(
            case.case_id,
            "security.path_traversal",
            "GATE55-case-01-r1-adaptive-correctness",
        ),
    )
    guard.run(
        "中性公共 evidence",
        fixture.repo_path,
        sandbox="read-only",
        timeout_seconds=30,
    )
    assert inner.calls == 1

    for marker in (
        case.case_id,
        "security.path_traversal",
        "GATE55-case-01-r1-adaptive-correctness",
    ):
        with pytest.raises(RuntimeError, match="私有标记"):
            guard.run(
                f"prompt {marker}",
                fixture.repo_path,
                sandbox="read-only",
                timeout_seconds=30,
            )
    assert inner.calls == 1


def test_real_runtime_contract_rejects_identity_and_budget_overrides() -> None:
    harness = _load_harness()
    provider = harness.CodexProviderDescriptor(
        name=harness.FROZEN_PROVIDER,
        base_url=harness.FROZEN_PROVIDER_BASE_URL,
        wire_api=harness.FROZEN_PROVIDER_WIRE_API,
        requires_openai_auth=True,
        supports_websockets=False,
    )
    kwargs = {
        "model": harness.FROZEN_MODEL,
        "reviewer_reasoning": harness.FROZEN_REASONING,
        "timeout_seconds": harness.FROZEN_REVIEW_TIMEOUT_SECONDS,
        "preflight_timeout_seconds": (
            harness.FROZEN_PREFLIGHT_TIMEOUT_SECONDS
        ),
        "max_provider_sessions": harness.FROZEN_PROVIDER_SESSION_LIMIT,
        "expected_provider": harness.FROZEN_PROVIDER,
        "expected_auth_mode": harness.FROZEN_AUTH_MODE,
        "expected_codex_version": harness.FROZEN_CODEX_VERSION,
        "provider": provider,
        "windows_sandbox_session_override": (
            harness.FROZEN_WINDOWS_SANDBOX_OVERRIDE
        ),
    }
    harness._require_frozen_runtime_contract(**kwargs)

    with pytest.raises(RuntimeError, match="不得由 CLI 改写"):
        harness._require_frozen_runtime_contract(
            **{**kwargs, "max_provider_sessions": 91}
        )
    with pytest.raises(RuntimeError, match="不得由 CLI 改写"):
        harness._require_frozen_runtime_contract(
            **{**kwargs, "model": "gpt-other"}
        )


def test_execution_baseline_requires_frozen_branch_and_tag(
    monkeypatch,
) -> None:
    harness = _load_harness()

    def valid_git_output(repo, *args):
        if args == ("branch", "--show-current"):
            return harness.FROZEN_BRANCH
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == (
            "rev-parse",
            f"refs/tags/{harness.FROZEN_EXECUTION_TAG}^{{commit}}",
        ):
            return "a" * 40
        raise AssertionError(args)

    monkeypatch.setattr(harness, "_git_output", valid_git_output)
    harness._require_execution_baseline()

    def moved_tag(repo, *args):
        value = valid_git_output(repo, *args)
        if args[0] == "rev-parse" and args[1].startswith("refs/tags/"):
            return "b" * 40
        return value

    monkeypatch.setattr(harness, "_git_output", moved_tag)
    with pytest.raises(RuntimeError, match="baseline tag 不一致"):
        harness._require_execution_baseline()


def test_provider_phase_error_persists_blocked_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _load_harness()
    monkeypatch.setattr(
        harness,
        "_execute_case_matrix",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")
        ),
    )
    output_root = tmp_path / "output"

    exit_code = harness.run_evaluation(
        runner_mode="fake",
        session="blocked-contract",
        model="gpt-test",
        reviewer_reasoning="high",
        timeout_seconds=30,
        preflight_timeout_seconds=30,
        max_provider_sessions=90,
        dataset_path=harness.DEFAULT_DATASET,
        ground_truth_path=harness.DEFAULT_GROUND_TRUTH,
        ground_truth_sha256=harness.FROZEN_GROUND_TRUTH_SHA256,
        fixture_root=tmp_path / "fixtures",
        output_root=output_root,
        run_root=tmp_path / "runs",
        expected_provider="sandboxproxy",
        expected_auth_mode="chatgpt",
        expected_codex_version="0.144.5",
        provider=harness.CodexProviderDescriptor(
            name="sandboxproxy",
            base_url="http://127.0.0.1:18080/v1",
            wire_api="responses",
            requires_openai_auth=True,
            supports_websockets=False,
        ),
        windows_sandbox_session_override="elevated",
    )

    summary = json.loads(
        output_root.joinpath(
            "blocked-contract",
            "summary.json",
        ).read_text(encoding="utf-8")
    )
    assert exit_code == 1
    assert summary["decision"] == "blocked"
    assert summary["default_topology"] == "single"
    assert "provider unavailable" in summary["failures"][0]


def test_preflight_exception_persists_blocked_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _load_harness()
    monkeypatch.setattr(harness, "_require_clean_project", lambda: None)
    monkeypatch.setattr(
        harness,
        "_require_execution_baseline",
        lambda: None,
    )
    monkeypatch.setattr(
        harness,
        "_require_frozen_runtime_contract",
        lambda **kwargs: None,
    )
    def fail_preflight(evaluation, **kwargs):
        evaluation.budget.reserve_session()
        raise OSError("cannot start codex")

    monkeypatch.setattr(
        harness,
        "_run_real_preflight",
        fail_preflight,
    )
    output_root = tmp_path / "output"

    exit_code = harness.run_evaluation(
        runner_mode="real",
        session="preflight-blocked",
        model=harness.FROZEN_MODEL,
        reviewer_reasoning=harness.FROZEN_REASONING,
        timeout_seconds=harness.FROZEN_REVIEW_TIMEOUT_SECONDS,
        preflight_timeout_seconds=(
            harness.FROZEN_PREFLIGHT_TIMEOUT_SECONDS
        ),
        max_provider_sessions=harness.FROZEN_PROVIDER_SESSION_LIMIT,
        dataset_path=harness.DEFAULT_DATASET,
        ground_truth_path=harness.DEFAULT_GROUND_TRUTH,
        ground_truth_sha256=harness.FROZEN_GROUND_TRUTH_SHA256,
        fixture_root=tmp_path / "fixtures",
        output_root=output_root,
        run_root=tmp_path / "runs",
        expected_provider=harness.FROZEN_PROVIDER,
        expected_auth_mode=harness.FROZEN_AUTH_MODE,
        expected_codex_version=harness.FROZEN_CODEX_VERSION,
        provider=harness.CodexProviderDescriptor(
            name=harness.FROZEN_PROVIDER,
            base_url=harness.FROZEN_PROVIDER_BASE_URL,
            wire_api=harness.FROZEN_PROVIDER_WIRE_API,
            requires_openai_auth=True,
            supports_websockets=False,
        ),
        windows_sandbox_session_override=(
            harness.FROZEN_WINDOWS_SANDBOX_OVERRIDE
        ),
    )

    summary = json.loads(
        output_root.joinpath(
            "preflight-blocked",
            "summary.json",
        ).read_text(encoding="utf-8")
    )
    assert exit_code == 1
    assert summary["decision"] == "blocked"
    assert summary["default_topology"] == "single"
    assert summary["preflight"]["conclusion"] == "blocked"
    assert "cannot start codex" in summary["failures"][0]


def test_clean_false_major_prevents_adaptive_winner() -> None:
    harness = _load_harness()

    def summary(**updates):
        values = {
            "clean_false_blocker_case_count": 0,
            "clean_false_major_case_count": 0,
            "unique_true_positive_blocker_major_count": 0,
            "blocker_major_recall": 0.5,
            "finding_recall": 0.5,
            "finding_precision": 1.0,
            "verdict_accuracy": 1.0,
            "severity_range_accuracy": 1.0,
        }
        values.update(updates)
        return SimpleNamespace(**values)

    summaries = {
        "single": summary(),
        "adaptive": summary(
            unique_true_positive_blocker_major_count=1,
            clean_false_major_case_count=1,
            blocker_major_recall=0.6,
            finding_recall=0.6,
        ),
        "fixed_three": summary(),
    }
    trigger = harness.RepeatTrigger(
        case_id="case",
        case_index=0,
        comparison_kind="adaptive-vs-single",
        topologies=("adaptive", "single"),
        finding_ids=("finding",),
        reason="test",
    )
    outcome = harness.RepeatOutcome(
        trigger=trigger,
        completed=True,
        unresolved_reason=None,
        candidate_reproduced_finding_ids=["finding"],
        comparison_missed_finding_ids=["finding"],
    )
    costs = {
        "single": {
            "complete": True,
            "tokens_complete": True,
            "provider_sessions": 12,
            "total_tokens": 100,
        },
        "adaptive": {
            "complete": True,
            "tokens_complete": True,
            "provider_sessions": 24,
            "total_tokens": 180,
        },
        "fixed_three": {
            "complete": True,
            "tokens_complete": True,
            "provider_sessions": 36,
            "total_tokens": 300,
        },
    }

    assert harness._decide_topology(
        summaries,
        repeat_outcomes=[outcome],
        topology_costs=costs,
    ) != "adaptive-wins"


def test_one_reproduced_unique_finding_is_sufficient_for_adaptive() -> None:
    harness = _load_harness()

    def summary(**updates):
        values = {
            "clean_false_blocker_case_count": 0,
            "clean_false_major_case_count": 0,
            "unique_true_positive_blocker_major_count": 0,
            "blocker_major_recall": 0.5,
            "finding_recall": 0.5,
            "finding_precision": 1.0,
            "verdict_accuracy": 1.0,
            "severity_range_accuracy": 1.0,
        }
        values.update(updates)
        return SimpleNamespace(**values)

    trigger = harness.RepeatTrigger(
        case_id="case",
        case_index=0,
        comparison_kind="adaptive-vs-single",
        topologies=("adaptive", "single"),
        finding_ids=("finding-a", "finding-b"),
        reason="test",
    )
    outcome = harness.RepeatOutcome(
        trigger=trigger,
        completed=True,
        unresolved_reason=None,
        candidate_reproduced_finding_ids=["finding-a"],
        comparison_missed_finding_ids=["finding-a"],
    )
    costs = {
        "single": {
            "complete": True,
            "provider_sessions": 12,
            "total_tokens": 100,
        },
        "adaptive": {
            "complete": True,
            "provider_sessions": 24,
            "total_tokens": 180,
        },
        "fixed_three": {
            "complete": True,
            "provider_sessions": 36,
            "total_tokens": 300,
        },
    }

    assert harness._decide_topology(
        {
            "single": summary(),
            "adaptive": summary(
                unique_true_positive_blocker_major_count=2,
                blocker_major_recall=0.7,
                finding_recall=0.7,
            ),
            "fixed_three": summary(
                clean_false_blocker_case_count=1,
            ),
        },
        repeat_outcomes=[outcome],
        topology_costs=costs,
    ) == "adaptive-wins"


def test_cost_incompleteness_blocks_winner() -> None:
    harness = _load_harness()
    summary = SimpleNamespace(
        clean_false_blocker_case_count=0,
        clean_false_major_case_count=0,
        unique_true_positive_blocker_major_count=0,
        blocker_major_recall=1.0,
        finding_recall=1.0,
        finding_precision=1.0,
        verdict_accuracy=1.0,
    )
    costs = {
        topology: {
            "complete": topology != "adaptive",
            "provider_sessions": 0,
            "total_tokens": 0,
        }
        for topology in harness.TOPOLOGIES
    }

    assert harness._decide_topology(
        {
            "single": summary,
            "adaptive": summary,
            "fixed_three": summary,
        },
        repeat_outcomes=[],
        topology_costs=costs,
    ) == "blocked"


def test_fixed_clean_false_major_repeats_against_adaptive() -> None:
    harness = _load_harness()
    dataset = harness.load_public_dataset(harness.DEFAULT_DATASET)
    bundles = {}
    for case in dataset.cases:
        for topology in harness.TOPOLOGIES:
            fixed_false_major = (
                case.case_id == dataset.cases[0].case_id
                and topology == "fixed_three"
            )
            score = SimpleNamespace(
                true_positive_blocker_major_finding_ids=[],
                clean_has_false_blocker=False,
                clean_has_false_major=fixed_false_major,
                clean_false_major_count=1 if fixed_false_major else 0,
            )
            bundles[(case.case_id, topology)] = SimpleNamespace(
                record=SimpleNamespace(score=score)
            )

    triggers = harness._build_repeat_triggers(
        dataset,
        bundles=bundles,
    )

    assert [
        trigger.comparison_kind
        for trigger in triggers
        if trigger.case_id == dataset.cases[0].case_id
    ] == ["fixed-three-vs-adaptive"]
    assert triggers[0].topologies == ("fixed_three", "adaptive")


def test_process_output_audit_rejects_ground_truth_access(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    run_dir = tmp_path / "run"
    output = run_dir / "review" / "process-output.txt"
    output.parent.mkdir(parents=True)
    output.write_text(
        "Get-Content eval\\gate-5.5\\ground-truth.json\n",
        encoding="utf-8",
    )
    result = SimpleNamespace(
        execution_ref="review/execution.json",
    )

    with pytest.raises(
        harness.EvaluationContractViolation,
        match="私有路径",
    ):
        harness._audit_process_output_isolation(run_dir, [result])
