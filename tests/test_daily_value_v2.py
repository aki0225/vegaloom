from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.daily_value_v2 as daily_value_v2
import scripts.daily_value_v2_worker as daily_value_v2_worker


FINGERPRINT = "a" * 64
BASELINE = "b" * 40


def test_environment_preflight_binds_packages_collection_and_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python_executable, workspace = _preflight_paths(tmp_path)
    monkeypatch.setattr(daily_value_v2, "_run_command", _passing_command)
    monkeypatch.setattr(
        daily_value_v2,
        "_process_snapshot",
        lambda: {"status": "observed", "total_process_count": 3},
    )

    payload = daily_value_v2.build_environment_preflight(
        python_executable,
        workspace,
        ["tests/test_case.py"],
        expected_environment_fingerprint=None,
        max_control_latency_seconds=2,
        active_formal_treatments=0,
        competing_workload_observed=False,
    )

    assert payload["status"] == "ready"
    assert payload["provider_request_performed"] is False
    assert payload["collected_node_count"] == 2
    assert payload["environment"]["packages"] == [
        {"name": "pytest", "version": "9.0.2"}
    ]
    assert len(payload["environment_fingerprint"]) == 64
    assert set(payload["gates"].values()) == {"passed"}


@pytest.mark.parametrize(
    ("failure", "expected_gate"),
    [
        ("collection", "target_collection"),
        ("latency", "control_latency"),
        ("overlap", "formal_treatment_overlap"),
        ("workload", "competing_workload"),
        ("fingerprint", "environment_match"),
    ],
)
def test_environment_preflight_fails_closed_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_gate: str,
) -> None:
    python_executable, workspace = _preflight_paths(tmp_path)

    def fake_run(command: list[str], *, cwd: Path, timeout_seconds: int) -> dict:
        result = _passing_command(command, cwd=cwd, timeout_seconds=timeout_seconds)
        if failure == "collection" and "pytest" in command:
            result["exit_code"] = 1
            result["stdout"] = ""
            result["stderr"] = "ModuleNotFoundError: hypothesis"
        if failure == "latency" and command[0] == "git":
            result["duration_seconds"] = 3.0
        return result

    monkeypatch.setattr(daily_value_v2, "_run_command", fake_run)
    monkeypatch.setattr(
        daily_value_v2,
        "_process_snapshot",
        lambda: {"status": "observed", "total_process_count": 1},
    )
    payload = daily_value_v2.build_environment_preflight(
        python_executable,
        workspace,
        ["tests/test_case.py"],
        expected_environment_fingerprint=("0" * 64 if failure == "fingerprint" else None),
        max_control_latency_seconds=2,
        active_formal_treatments=1 if failure == "overlap" else 0,
        competing_workload_observed=failure == "workload",
    )

    assert payload["status"] == "blocked"
    assert payload["gates"][expected_gate] == "failed"
    assert payload["provider_request_performed"] is False


def test_timestamped_worker_records_receive_time_and_keeps_stderr(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("do task", encoding="utf-8")
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "ready",
                "environment_fingerprint": FINGERPRINT,
            }
        ),
        encoding="utf-8",
    )
    event_path = tmp_path / "events.jsonl"
    stderr_path = tmp_path / "worker.stderr.txt"
    result_path = tmp_path / "worker-result.json"
    child = (
        "import json, sys; "
        "assert sys.stdin.read() == 'do task'; "
        "print(json.dumps({'type': 'thread.started'}), flush=True); "
        "print(json.dumps({'type': 'turn.completed'}), flush=True); "
        "print('diagnostic', file=sys.stderr, flush=True)"
    )

    payload = daily_value_v2_worker.run_timestamped_json_command(
        [sys.executable, "-c", child, "--json"],
        workspace=workspace,
        prompt_path=prompt_path,
        preflight_path=preflight_path,
        event_path=event_path,
        stderr_path=stderr_path,
        result_path=result_path,
        timeout_seconds=30,
        expected_environment_fingerprint=FINGERPRINT,
    )
    events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
    ]

    assert payload["exit_code"] == 0
    assert payload["timed_out"] is False
    assert payload["termination_confirmed"] is True
    assert payload["event_timing"]["event_count"] == 2
    assert payload["event_timing"]["invalid_event_count"] == 0
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["received_at"].endswith("+00:00") for event in events)
    assert events[0]["event"]["type"] == "thread.started"
    assert stderr_path.read_text(encoding="utf-8").strip() == "diagnostic"


def test_timestamped_worker_rejects_hidden_rerun_and_unsafe_config(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "events.jsonl"
    existing.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="禁止参数"):
        daily_value_v2_worker.validate_worker_command(
            ["codex", "exec", "--json", "--ignore-user-config"]
        )
    with pytest.raises(ValueError, match="必须显式启用 --json"):
        daily_value_v2_worker.validate_worker_command(["codex", "exec"])


def test_v2_result_keeps_runtime_and_post_seal_verification_separate() -> None:
    native = _result("native")
    vega = _result("vega")

    validated = daily_value_v2.validate_v2_results([native, vega])
    summary = daily_value_v2.build_v2_summary(validated)

    assert native["runtime_verification_status"] == "not_applicable"
    assert vega["runtime_verification_status"] == "passed"
    assert summary["evidence_status"] == "clean_pair_observed"
    assert summary["complete_pair_count"] == 1
    assert summary["clean_pair_count"] == 1
    assert summary["owner_manual_actions_total"] == 2
    assert summary["automation_actions_total"] == 6


def test_v2_result_rejects_environment_drift_between_treatments() -> None:
    native = _result("native")
    vega = _result("vega")
    vega["environment_fingerprint"] = "c" * 64

    with pytest.raises(ValueError, match="环境 fingerprint 不一致"):
        daily_value_v2.validate_v2_results([native, vega])


def test_v2_result_rejects_execution_contract_drift_between_treatments() -> None:
    native = _result("native")
    vega = _result("vega")
    vega["timeout_seconds"] = 1200

    with pytest.raises(ValueError, match="执行合同不一致"):
        daily_value_v2.validate_v2_results([native, vega])


def test_v2_success_requires_post_seal_verifier_and_reviewer() -> None:
    result = _result("vega")
    result["post_seal_verification_status"] = "failed"

    with pytest.raises(ValueError, match="post-seal passed"):
        daily_value_v2.validate_v2_results([result])


def test_v2_result_rejects_ambiguous_manual_action_and_absolute_ref() -> None:
    result = _result("native")
    result["owner_manual_actions"] = True
    with pytest.raises(ValueError, match="owner manual actions"):
        daily_value_v2.validate_v2_results([result])

    result = _result("native")
    result["preflight_ref"] = "C:/private/preflight.json"  # repo-path-policy: allow-test-fixture
    with pytest.raises(ValueError, match="绝对路径"):
        daily_value_v2.validate_v2_results([result])


def test_v2_result_requires_received_timestamps_when_events_exist() -> None:
    result = _result("vega")
    result["event_timing"]["first_received_at"] = None

    with pytest.raises(ValueError, match="首尾接收时间"):
        daily_value_v2.validate_v2_results([result])


def test_v2_experiment_script_stays_bounded() -> None:
    core_script = Path(daily_value_v2.__file__)
    worker_script = Path(daily_value_v2_worker.__file__)

    assert len(core_script.read_text(encoding="utf-8").splitlines()) <= 650
    assert len(worker_script.read_text(encoding="utf-8").splitlines()) <= 350


def _preflight_paths(tmp_path: Path) -> tuple[Path, Path]:
    python_executable = tmp_path / "python.exe"
    python_executable.write_bytes(b"test")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath(".git").mkdir()
    return python_executable, workspace


def _passing_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> dict:
    del cwd, timeout_seconds
    stdout = ""
    if ENVIRONMENT_MARKER in command:
        stdout = json.dumps(
            {
                "implementation": "CPython",
                "version": "3.12.10",
                "cache_tag": "cpython-312",
                "abi_flags": "",
                "platform": "win-amd64",
                "packages": [{"name": "pytest", "version": "9.0.2"}],
            }
        )
    elif "pytest" in command:
        stdout = (
            "tests/test_case.py::test_one\n"
            "tests/test_case.py::test_two\n"
            "\n2 tests collected\n"
        )
    return {
        "exit_code": 0,
        "timed_out": False,
        "termination_confirmed": True,
        "duration_seconds": 0.1,
        "stdout": stdout,
        "stderr": "",
    }


ENVIRONMENT_MARKER = daily_value_v2.ENVIRONMENT_PROBE


def _result(treatment: str) -> dict:
    runtime_status = "not_applicable" if treatment == "native" else "passed"
    return {
        "schema_version": 2,
        "experiment_version": "V2",
        "run_id": f"dv-b02-{treatment}-v2",
        "case_id": "DV-B02",
        "treatment": treatment,
        "baseline_commit": BASELINE,
        "model": "test-model",
        "reasoning_effort": "medium",
        "timeout_seconds": 600,
        "run_status": "completed",
        "final_disposition": "success",
        "runtime_verification_status": runtime_status,
        "post_seal_verification_status": "passed",
        "reviewer_verdict": "approve",
        "reviewer_independent_findings": 0,
        "wall_clock_seconds": 30.0,
        "tokens": {"input": 10, "output": 5, "cached_input": 0},
        "owner_manual_actions": 1,
        "automation_actions": 3,
        "recovery_used": False,
        "artifact_read": True,
        "environment_fingerprint": FINGERPRINT,
        "preflight_ref": f"runs/{treatment}/preflight.json",
        "event_log_ref": f"runs/{treatment}/events.jsonl",
        "event_timing": {
            "event_count": 2,
            "invalid_event_count": 0,
            "first_received_at": "2026-07-29T08:00:00+00:00",
            "last_received_at": "2026-07-29T08:01:00+00:00",
        },
        "evidence_refs": [f"runs/{treatment}/result.json"],
        "notes": "",
    }
