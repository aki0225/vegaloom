from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

import scripts.gate7_large_task_dogfood as gate7
import scripts.gate7_large_task_dogfood_r2 as gate7_r2
import scripts.gate7_large_task_dogfood_r3 as gate7_r3
import scripts.gate7_large_task_dogfood_r4 as gate7_r4
import scripts.gate7_large_task_dogfood_r5 as gate7_r5
import scripts.gate7_large_task_dogfood_r6 as gate7_r6
from vega.execution_control import RunnerExecutionContext, run_owned_process
from vega.project_config import CodexProviderDescriptor
from vega.runner import _provider_descriptor_argv, build_codex_exec_command


def test_load_and_validate_case_freezes_case_and_plan() -> None:
    case, case_sha256, plan_sha256 = gate7._load_and_validate_case()
    _, case_sha256_again, plan_sha256_again = gate7._load_and_validate_case()

    assert case_sha256 == gate7.FROZEN_CASE_SHA256
    assert case_sha256 == "9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9"
    assert [item["id"] for item in case["checkpoints"]] == ["CP01", "CP02", "CP03"]
    assert case_sha256_again == case_sha256
    assert plan_sha256_again == plan_sha256
    assert plan_sha256 == gate7.FROZEN_PLAN_SHA256


def test_r2_overlay_freezes_resolved_case_and_complete_plan() -> None:
    experiment = gate7_r2.R2_EXPERIMENT
    case, case_sha256, plan_sha256 = gate7._load_and_validate_case(
        experiment.case_path,
        frozen_case_sha256=experiment.frozen_case_sha256,
        frozen_plan_sha256=experiment.frozen_plan_sha256,
        case_hash_mode=experiment.case_hash_mode,
    )

    assert case["schema_version"] == 2
    assert case["case_identity"]["case_id"].endswith("-r2-v1")
    assert case["case_identity"]["real_session"].endswith("-r2-v1")
    assert case_sha256 == experiment.frozen_case_sha256
    assert plan_sha256 == experiment.frozen_plan_sha256
    assert gate7._baseline_tag_from_case(case, "linear") == (
        "gate-7a-pre-run-r2-v1"
    )
    assert gate7._consumed_tag_from_case(case, "langgraph") == (
        "gate-7c-langgraph-consumed-r2-v1"
    )


def test_r2_canonical_case_hash_ignores_checkout_line_endings(tmp_path: Path) -> None:
    experiment = gate7_r2.R2_EXPERIMENT
    case_dir = tmp_path / "gate-7"
    case_dir.mkdir()
    for source in (gate7.CASE_PATH, experiment.case_path):
        content = source.read_text(encoding="utf-8").replace("\r\n", "\n")
        case_dir.joinpath(source.name).write_bytes(
            content.replace("\n", "\r\n").encode("utf-8")
        )

    _, case_sha256, plan_sha256 = gate7._load_and_validate_case(
        case_dir / experiment.case_path.name,
        frozen_case_sha256=experiment.frozen_case_sha256,
        frozen_plan_sha256=experiment.frozen_plan_sha256,
        case_hash_mode=experiment.case_hash_mode,
    )

    assert case_sha256 == experiment.frozen_case_sha256
    assert plan_sha256 == experiment.frozen_plan_sha256


def test_r3_overlay_freezes_new_identity_and_repair_contract() -> None:
    experiment = gate7_r3.R3_EXPERIMENT
    case, case_sha256, plan_sha256 = gate7._load_and_validate_case(
        experiment.case_path,
        frozen_case_sha256=experiment.frozen_case_sha256,
        frozen_plan_sha256=experiment.frozen_plan_sha256,
        case_hash_mode=experiment.case_hash_mode,
    )

    assert case["case_identity"]["case_id"].endswith("-r3-v1")
    assert case["case_identity"]["real_session"].endswith("-r3-v1")
    assert case["r2_contract"]["repair_revision"] == (
        "r3-project-local-git-safe-directory"
    )
    assert case_sha256 == experiment.frozen_case_sha256
    assert plan_sha256 == experiment.frozen_plan_sha256
    assert gate7._baseline_tag_from_case(case, "linear") == (
        "gate-7a-pre-run-r3-v1"
    )


def test_r4_overlay_freezes_bounded_inspection_contract() -> None:
    experiment = gate7_r4.R4_EXPERIMENT
    case, case_sha256, plan_sha256 = gate7._load_and_validate_case(
        experiment.case_path,
        frozen_case_sha256=experiment.frozen_case_sha256,
        frozen_plan_sha256=experiment.frozen_plan_sha256,
        case_hash_mode=experiment.case_hash_mode,
    )

    inspection = case["r2_contract"]["inspection_contract"]
    assert case["case_identity"]["case_id"].endswith("-r4-v1")
    assert case["case_identity"]["real_session"].endswith("-r4-v1")
    assert case_sha256 == experiment.frozen_case_sha256
    assert plan_sha256 == experiment.frozen_plan_sha256
    assert inspection["max_tool_waves"] == 2
    assert inspection["max_exec_commands"] == 8
    assert inspection["max_read_lines"] == 60
    assert inspection["max_rg_matches_per_file"] == 20
    assert inspection["cli"] == {
        "tool_output_token_limit": 2048,
        "model_verbosity": "low",
        "model_reasoning_summary": "none",
    }
    assert inspection["checkpoint_read_paths"]["CP01"] == [
        "tests/test_appctx.py",
        "tests/test_basic.py",
        "tests/test_blueprints.py",
        "tests/test_helpers.py",
        "tests/test_testing.py",
        "src/flask/app.py",
        "src/flask/ctx.py",
    ]
    assert gate7._baseline_tag_from_case(case, "linear") == (
        "gate-7a-pre-run-r4-v1"
    )


def test_r5_overlay_changes_only_execution_identity_and_auth_mode() -> None:
    experiment = gate7_r5.R5_EXPERIMENT
    case, case_sha256, plan_sha256 = gate7._load_and_validate_case(
        experiment.case_path,
        frozen_case_sha256=experiment.frozen_case_sha256,
        frozen_plan_sha256=experiment.frozen_plan_sha256,
        case_hash_mode=experiment.case_hash_mode,
    )

    assert case["case_identity"]["case_id"].endswith("-r5-v1")
    assert case["case_identity"]["date"] == "2026-07-20"
    assert case["provider_budget"]["auth_mode"] == "api-key"
    assert case["r2_contract"]["execution_revision"] == "r5-api-key-auth"
    assert case["r2_contract"]["graph_schema_version"] == "gate7-r4-v1"
    assert case_sha256 == experiment.frozen_case_sha256
    assert plan_sha256 == experiment.frozen_plan_sha256
    assert gate7._auth_mode_from_case(case) == "api-key"
    assert gate7._baseline_tag_from_case(case, "linear") == (
        "gate-7a-pre-run-r5-v1"
    )
    assert gate7._consumed_tag_from_case(case, "langgraph") == (
        "gate-7c-langgraph-consumed-r5-v1"
    )


def test_codex_auth_mode_recognizes_api_key_without_exposing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate7.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="Logged in using an API key",
            stderr="",
        ),
    )

    assert gate7._codex_auth_mode("codex") == "api-key"


def test_r6_overlay_isolates_remote_tag_timeout_hardening() -> None:
    experiment = gate7_r6.R6_EXPERIMENT
    case, case_sha256, plan_sha256 = gate7._load_and_validate_case(
        experiment.case_path,
        frozen_case_sha256=experiment.frozen_case_sha256,
        frozen_plan_sha256=experiment.frozen_plan_sha256,
        case_hash_mode=experiment.case_hash_mode,
    )

    assert case["case_identity"]["case_id"].endswith("-r6-v1")
    assert case["case_identity"]["date"] == "2026-07-20"
    assert case["provider_budget"]["auth_mode"] == "api-key"
    assert case["r2_contract"]["execution_revision"] == (
        "r6-remote-tag-timeout-hardening"
    )
    assert case["r2_contract"]["graph_schema_version"] == "gate7-r4-v1"
    assert case_sha256 == experiment.frozen_case_sha256
    assert plan_sha256 == experiment.frozen_plan_sha256
    assert gate7._auth_mode_from_case(case) == "api-key"
    assert gate7._baseline_tag_from_case(case, "linear") == (
        "gate-7a-pre-run-r6-v1"
    )
    assert gate7._consumed_tag_from_case(case, "langgraph") == (
        "gate-7c-langgraph-consumed-r6-v1"
    )


def test_spawn_machine_decodes_child_output_as_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "machine-config.json"
    config_path.write_text(
        json.dumps({"engine": "linear"}, ensure_ascii=False),
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(gate7.subprocess, "run", fake_run)

    gate7._spawn_machine(config_path)

    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"


def test_process_output_tail_keeps_stderr_traceback() -> None:
    process = subprocess.CompletedProcess(
        args=["python"],
        returncode=1,
        stdout="",
        stderr="Traceback: machine-f failed",
    )

    assert "stderr=Traceback: machine-f failed" in gate7._process_output_tail(process)


def test_remote_tag_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["git", "ls-remote"],
            timeout=gate7.REMOTE_GIT_TIMEOUT_SECONDS,
        )

    monkeypatch.setattr(gate7.subprocess, "run", raise_timeout)

    with pytest.raises(gate7.Gate7Blocked, match="读取 remote tag.*超时"):
        gate7._remote_tag_commit_optional("gate-7a-consumed-r6-v1")


def test_claim_real_execution_remote_precheck_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = json.loads(gate7_r6.R6_EXPERIMENT.case_path.read_text(encoding="utf-8"))

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["git", "ls-remote"],
            timeout=gate7.REMOTE_GIT_TIMEOUT_SECONDS,
        )

    monkeypatch.setattr(gate7.subprocess, "run", raise_timeout)

    with pytest.raises(
        gate7.Gate7Blocked,
        match="检查 remote consumed tag.*超时",
    ):
        gate7._claim_real_execution("linear", "a" * 40, case)


def test_claim_real_execution_push_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = json.loads(gate7_r6.R6_EXPERIMENT.case_path.read_text(encoding="utf-8"))
    calls = 0

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise subprocess.TimeoutExpired(
                cmd=command,
                timeout=gate7.REMOTE_GIT_TIMEOUT_SECONDS,
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(gate7.subprocess, "run", fake_run)

    with pytest.raises(gate7.Gate7Blocked, match="推送 consumed tag.*超时"):
        gate7._claim_real_execution("linear", "a" * 40, case)


def test_r4_inspection_contract_drift_fails_closed() -> None:
    case, _, _ = gate7._load_and_validate_case(
        gate7_r4.R4_EXPERIMENT.case_path,
        frozen_case_sha256=gate7_r4.R4_EXPERIMENT.frozen_case_sha256,
        frozen_plan_sha256=gate7_r4.R4_EXPERIMENT.frozen_plan_sha256,
        case_hash_mode=gate7_r4.R4_EXPERIMENT.case_hash_mode,
    )
    inspection = json.loads(
        json.dumps(case["r2_contract"]["inspection_contract"], ensure_ascii=False)
    )
    inspection["max_exec_commands"] = 0

    with pytest.raises(gate7.Gate7Blocked, match="max_exec_commands"):
        gate7._validate_inspection_contract(inspection)


def test_provider_retry_overrides_are_explicitly_zero() -> None:
    arguments = _provider_descriptor_argv(
        CodexProviderDescriptor(
            name=gate7.PROVIDER,
            base_url=gate7.PROVIDER_BASE_URL,
            request_max_retries=0,
            stream_max_retries=0,
        )
    )

    assert "model_providers.sandboxproxy.request_max_retries=0" in arguments
    assert "model_providers.sandboxproxy.stream_max_retries=0" in arguments


def test_real_gate7_runner_disables_multi_agent_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate7, "_resolve_codex_executable", lambda _: "codex")
    runner = gate7._build_real_runner()
    command = build_codex_exec_command(
        "codex",
        runner.options,
        tmp_path,
        "workspace-write",
    )

    gate7._assert_multi_agent_disabled(command)
    assert command.count("multi_agent") == 1
    assert runner.execution_identity("workspace-write")["multi_agent"] == "disabled"

    with pytest.raises(gate7.Gate7Blocked, match="禁用一次"):
        gate7._assert_multi_agent_disabled(
            [item for item in command if item != "multi_agent"]
        )


def test_r4_real_runner_adds_only_frozen_output_reduction_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate7, "_resolve_codex_executable", lambda _: "codex")
    monkeypatch.setattr("vega.runner.shutil.which", lambda executable: executable)
    policy = _r4_inspection_policy("CP01")
    runner = gate7._build_real_runner(policy)
    command = build_codex_exec_command(
        "codex",
        runner.options,
        tmp_path,
        "workspace-write",
    )
    bounded_command = runner.build_command(tmp_path, "workspace-write")

    assert isinstance(runner, gate7.BoundedCodexExecRunner)
    assert command[-1] == "-"
    assert "tool_output_token_limit=2048" in bounded_command
    assert 'model_verbosity="low"' in bounded_command
    assert 'model_reasoning_summary="none"' in bounded_command
    assert bounded_command[-1] == "-"
    assert runner.execution_identity("workspace-write")[
        "tool_output_token_limit"
    ] == "2048"


def test_event_ledger_passes_and_payload_tamper_fails_closed(tmp_path: Path) -> None:
    ledger = gate7.EventLedger(
        tmp_path / "events.jsonl",
        run_id="gate7-test-run",
        engine="linear",
    )
    ledger.append("run_started", operation_kind="control", payload={"step": 1})
    ledger.append(
        "checkpoint_started",
        operation_kind="external_effect",
        step_id="CP01",
        attempt_id="attempt-1",
        logical_operation_id="CP01",
        idempotency_key="CP01-once",
        payload={"checkpoint": "CP01"},
    )

    entries = ledger.read()
    gate7.validate_event_chain(entries)

    tampered = json.loads(json.dumps(entries, ensure_ascii=False))
    tampered[1]["payload"]["checkpoint"] = "CP01-tampered"
    with pytest.raises(gate7.Gate7Blocked, match="event hash"):
        gate7.validate_event_chain(tampered)


def test_build_worker_prompt_is_engine_neutral_and_hides_oracle_identity() -> None:
    case, _, _ = gate7._load_and_validate_case()
    checkpoint = case["checkpoints"][1]

    linear_prompt = gate7._build_worker_prompt(
        checkpoint,
        engine="linear",
        machine="machine-e",
        handoff_path=None,
    )
    langgraph_prompt = gate7._build_worker_prompt(
        checkpoint,
        engine="langgraph",
        machine="machine-e",
        handoff_path=None,
    )

    assert linear_prompt == langgraph_prompt
    assert gate7.MERGE_SHA not in linear_prompt
    assert gate7.ORACLE_DIFF_SHA256 not in linear_prompt
    assert "linear" not in linear_prompt.casefold()
    assert "langgraph" not in linear_prompt.casefold()
    assert "有界检查合同" not in linear_prompt


def test_r4_worker_prompt_is_engine_neutral_and_freezes_bounded_inspection() -> None:
    case, _, _ = gate7._load_and_validate_case(
        gate7_r4.R4_EXPERIMENT.case_path,
        frozen_case_sha256=gate7_r4.R4_EXPERIMENT.frozen_case_sha256,
        frozen_plan_sha256=gate7_r4.R4_EXPERIMENT.frozen_plan_sha256,
        case_hash_mode=gate7_r4.R4_EXPERIMENT.case_hash_mode,
    )
    checkpoint = case["checkpoints"][0]
    policy = _r4_inspection_policy("CP01")

    linear_prompt = gate7._build_worker_prompt(
        checkpoint,
        engine="linear",
        machine="machine-e",
        handoff_path=None,
        inspection_policy=policy,
    )
    langgraph_prompt = gate7._build_worker_prompt(
        checkpoint,
        engine="langgraph",
        machine="machine-e",
        handoff_path=None,
        inspection_policy=policy,
    )

    assert linear_prompt == langgraph_prompt
    assert "最多 2 个工具波次、8 次 exec" in linear_prompt
    assert "每个 Get-Content 片段最多 60 行" in linear_prompt
    assert "rg exit 1 且无匹配是正常空结果" in linear_prompt
    assert "rg -n --max-count <N> -e '<pattern>'" in linear_prompt
    assert "第二个工具波次结束后必须立即使用 apply_patch" in linear_prompt
    assert gate7.MERGE_SHA not in linear_prompt
    assert gate7.ORACLE_DIFF_SHA256 not in linear_prompt


def test_transcript_audit_accepts_two_bounded_tool_waves() -> None:
    transcript = "\n".join(
        [
            "OpenAI Codex v0.144.5",
            "codex",
            "exec",
            (
                '"C:\\\\fixtures\\\\bin\\\\pwsh.exe" '
                "-Command 'git status --short' in C:\\repo"
            ),
            "exec",
            (
                '"pwsh" -Command "rg -n --max-count 20 '
                "-e 'teardown(request|appcontext)' -- "
                'tests/test_appctx.py" in C:\\repo'
            ),
            " succeeded in 1ms:",
            "",
            " succeeded in 2ms:",
            "tests/test_appctx.py:10:def teardown():",
            "codex",
            "exec",
            (
                '"pwsh" -Command \'Get-Content tests/test_appctx.py '
                "-First 60' in C:\\repo"
            ),
            " succeeded in 3ms:",
            "line one",
            "line two",
            "tokens used",
            "1,234",
            "",
        ]
    )

    audit = gate7._audit_codex_transcript(
        transcript,
        _r4_inspection_policy("CP01"),
    )

    assert audit["status"] == "passed"
    assert audit["parse_complete"] is True
    assert audit["command_count"] == 3
    assert audit["result_count"] == 3
    assert audit["tool_wave_count"] == 2
    assert audit["duplicate_command_count"] == 0
    assert audit["unbounded_read_count"] == 0
    assert audit["tokens_used"] == 1234
    assert audit["expected_workdir"] == "c:/repo"
    assert audit["observed_workdirs"] == ["c:/repo", "c:/repo", "c:/repo"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            '"pwsh" -Command \'Get-Content tests/test_appctx.py\' in C:\\repo',
            "Get-Content 缺少",
        ),
        (
            (
                '"pwsh" -Command \'Get-Content tests/test_appctx.py '
                "-First 61' in C:\\repo"
            ),
            "读取行数超过",
        ),
        (
            (
                '"pwsh" -Command "rg -n -e \'teardown\' -- '
                'tests/test_appctx.py" in C:\\repo'
            ),
            "rg 缺少",
        ),
        (
            '"pwsh" -Command \'python -c "print(1)"\' in C:\\repo',
            "不匹配",
        ),
        (
            (
                '"pwsh" -Command \'git status --short; '
                "Remove-Item secret.txt' in C:\\repo"
            ),
            "不匹配",
        ),
        (
            (
                '"pwsh" -Command "rg -n --max-count 20 -e \'x\' -- '
                "tests/test_appctx.py; Invoke-WebRequest http://127.0.0.1:9\" "
                "in C:\\repo"
            ),
            "未注册路径",
        ),
        (
            (
                '"pwsh" -Command \'Get-Content tests/test_appctx.py | '
                "Select-Object -Skip 100000 -First 60; Remove-Item secret.txt' "
                "in C:\\repo"
            ),
            "不匹配",
        ),
        (
            (
                '"pwsh" -Command \'rg -n --max-count 20 '
                '-e "$(Set-Content escaped.txt x)" -- '
                "tests/test_appctx.py' in C:\\repo"
            ),
            "可展开",
        ),
        (
            (
                '"pwsh" -Command \'rg -n --max-count 20 '
                '-e "$env:SECRET" -- tests/test_appctx.py\' in C:\\repo'
            ),
            "可展开",
        ),
        (
            (
                '"pwsh" -Command \'rg -n --max-count 20 '
                '-e "teardown`n" -- tests/test_appctx.py\' in C:\\repo'
            ),
            "反引号",
        ),
        (
            (
                '"pwsh" -Command "rg -n --max-count 20 -e \'teardown\' -- '
                'tests/test_appctx.py" in C:\\other'
            ),
            "工作目录漂移",
        ),
        (
            '"cmd" -Command \'git status --short\' in C:\\repo',
            "wrapper",
        ),
    ],
)
def test_transcript_audit_rejects_unbounded_or_unknown_exec(
    command: str,
    expected: str,
) -> None:
    transcript = "\n".join(
        [
            "codex",
            "exec",
            command,
            " succeeded in 1ms:",
            "output",
            "tokens used",
            "1,000",
            "",
        ]
    )

    audit = gate7._audit_codex_transcript(
        transcript,
        _r4_inspection_policy("CP01"),
    )

    assert audit["status"] == "failed"
    assert any(expected in item for item in audit["violations"])


def test_transcript_audit_rejects_duplicate_and_incomplete_results() -> None:
    command = (
        '"pwsh" -Command "rg -n --max-count 20 -e \'teardown\' -- '
        'tests/test_appctx.py" in C:\\repo'
    )
    transcript = "\n".join(
        [
            "codex",
            "exec",
            command,
            "exec",
            command,
            " succeeded in 1ms:",
            "one result only",
            "tokens used",
            "1,000",
            "",
        ]
    )

    audit = gate7._audit_codex_transcript(
        transcript,
        _r4_inspection_policy("CP01"),
    )

    assert audit["status"] == "failed"
    assert audit["parse_complete"] is False
    assert audit["duplicate_command_count"] == 1
    assert any("解析不完整" in item for item in audit["violations"])
    assert any("重复命令" in item for item in audit["violations"])


def test_checkpoint_payload_round_trips_transcript_audit() -> None:
    evidence = _fake_checkpoint_evidence("CP01", 1)
    evidence.transcript_audit = {
        **gate7._not_applicable_transcript_audit(""),
        "status": "passed",
        "command_count": 3,
        "result_count": 3,
    }

    restored = gate7._checkpoint_evidence_from_payload(
        gate7._checkpoint_payload(evidence)
    )

    assert restored.transcript_audit == evidence.transcript_audit


def test_r4_gate7c_revalidates_transcript_files_payloads_and_events(
    tmp_path: Path,
) -> None:
    case, _, _ = gate7._load_and_validate_case(
        gate7_r4.R4_EXPERIMENT.case_path,
        frozen_case_sha256=gate7_r4.R4_EXPERIMENT.frozen_case_sha256,
        frozen_plan_sha256=gate7_r4.R4_EXPERIMENT.frozen_plan_sha256,
        case_hash_mode=gate7_r4.R4_EXPERIMENT.case_hash_mode,
    )
    summary_dir = tmp_path / "run"
    fixture_dir = tmp_path / "fixture"
    ledgers: dict[str, gate7.EventLedger] = {}
    for machine in ("machine-e", "machine-f"):
        machine_dir = summary_dir / machine
        machine_dir.mkdir(parents=True)
        fixture_dir.joinpath(machine, "repo").mkdir(parents=True)
        ledgers[machine] = gate7.EventLedger(
            machine_dir / "events.jsonl",
            run_id="gate7-r4-revalidation",
            engine="linear",
        )

    checkpoints: list[dict[str, object]] = []
    for index, checkpoint_id in enumerate(("CP01", "CP02", "CP03"), start=1):
        machine = "machine-f" if checkpoint_id == "CP03" else "machine-e"
        expected_workdir = fixture_dir / machine / "repo"
        policy = _r4_inspection_policy(
            checkpoint_id,
            expected_workdir=expected_workdir,
        )
        transcript = _valid_r4_transcript(expected_workdir)
        output_sha256 = gate7._sha256_text(transcript)
        audit = gate7._audit_codex_transcript(transcript, policy)
        assert audit["status"] == "passed"
        execution_dir = (
            summary_dir
            / machine
            / "executions"
            / checkpoint_id.casefold()
        )
        execution_dir.mkdir(parents=True)
        execution_dir.joinpath("process-output.txt").write_text(
            transcript,
            encoding="utf-8",
        )
        execution_dir.joinpath("transcript-audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        evidence = _fake_checkpoint_evidence(checkpoint_id, index)
        evidence.output_sha256 = output_sha256
        evidence.transcript_audit = audit
        payload = gate7._checkpoint_payload(evidence)
        checkpoints.append(payload)
        ledgers[machine].append(
            "checkpoint_completed",
            operation_kind="terminal",
            step_id=checkpoint_id,
            attempt_id=evidence.attempt_id,
            payload={
                "output_sha256": output_sha256,
                "transcript_audit_sha256": gate7._sha256_text(
                    gate7._canonical_json(audit)
                ),
                "transcript_audit_status": "passed",
            },
        )

    gate7._revalidate_r4_transcript_evidence(
        case=case,
        summary_dir=summary_dir,
        fixture_dir=fixture_dir,
        checkpoints=checkpoints,
    )

    audit_path = (
        summary_dir
        / "machine-f"
        / "executions"
        / "cp03"
        / "transcript-audit.json"
    )
    tampered = json.loads(audit_path.read_text(encoding="utf-8"))
    tampered["command_count"] = 999
    audit_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(gate7.Gate7Blocked, match="无法复验"):
        gate7._revalidate_r4_transcript_evidence(
            case=case,
            summary_dir=summary_dir,
            fixture_dir=fixture_dir,
            checkpoints=checkpoints,
        )


@pytest.mark.parametrize("unsafe_ref", ["/absolute/path", "../escape", "safe/../escape"])
def test_safe_relative_ref_rejects_absolute_and_parent_refs(unsafe_ref: str) -> None:
    with pytest.raises(gate7.Gate7Blocked, match="非法相对 artifact ref"):
        gate7._safe_relative_ref(unsafe_ref)


def test_safe_relative_ref_accepts_nested_artifact_ref() -> None:
    assert gate7._safe_relative_ref("engine-state/linear-cursor.json") == Path(
        "engine-state",
        "linear-cursor.json",
    )


def test_duplicate_effect_count_detects_reused_logical_operation_and_key() -> None:
    events = [
        {
            "event": "checkpoint_started",
            "logical_operation_id": "CP01",
            "idempotency_key": "CP01-once",
        },
        {
            "event": "checkpoint_started",
            "logical_operation_id": "CP01",
            "idempotency_key": "CP01-once",
        },
        {
            "event": "checkpoint_started",
            "logical_operation_id": "CP01",
            "idempotency_key": "CP01-retry-is-not-duplicate",
        },
        {
            "event": "checkpoint_completed",
            "logical_operation_id": "CP01",
            "idempotency_key": "CP01-once",
        },
    ]

    assert gate7._duplicate_effect_count(events) == 1


def test_run_command_turns_timeout_into_structured_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["example", "--flag"]

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            command,
            7,
            output=b"partial stdout\n",
            stderr="partial stderr\n",
        )

    monkeypatch.setattr(gate7.subprocess, "run", raise_timeout)

    evidence = gate7._run_command(command, cwd=tmp_path, timeout=7)

    assert evidence.returncode == 124
    assert evidence.command == command
    assert "partial stdout" in evidence.output
    assert "partial stderr" in evidence.output
    assert "timed out after 7 seconds" in evidence.output


def test_worker_git_config_is_project_local_and_injected_to_owned_process(
    tmp_path: Path,
) -> None:
    repo = _make_git_repo(tmp_path)
    config_path = tmp_path / "worker-gitconfig"

    gate7._configure_worker_git_config(repo, config_path)

    command = [
        sys.executable,
        "-c",
        (
            "import os, subprocess; "
            "subprocess.run(['git', 'status', '--porcelain=v1'], check=True); "
            "assert os.environ['GIT_CONFIG_GLOBAL']; "
            "print('GIT_CONFIG_GLOBAL_INJECTED')"
        ),
    ]
    result = run_owned_process(
        command,
        "",
        repo,
        10,
        RunnerExecutionContext(
            execution_dir=tmp_path / "run" / "executions" / "worker",
            run_id="gate7-safe-directory-test",
            step="worker",
            git_config_global=config_path,
        ),
    )

    assert result.status == "success"
    assert "GIT_CONFIG_GLOBAL_INJECTED" in result.output
    assert config_path.read_text(encoding="utf-8") == (
        f"[safe]\n\tdirectory = {repo.resolve().as_posix()}\n"
    )


def test_scope_guard_detects_edits_to_previously_changed_files(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    (repo / "cp01.txt").write_text("base cp01\n", encoding="utf-8", newline="\n")
    (repo / "cp02.txt").write_text("base cp02\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "--", "cp01.txt", "cp02.txt")
    _git(repo, "commit", "-m", "add checkpoint files")
    base = _git_output(repo, "rev-parse", "HEAD")
    (repo / "cp01.txt").write_text("cp01 complete\n", encoding="utf-8", newline="\n")
    before = gate7._scope_outside_diff_digest(
        repo,
        ["cp02.txt"],
        base=base,
    )

    (repo / "cp01.txt").write_text(
        "cp01 mutated by cp02\n",
        encoding="utf-8",
        newline="\n",
    )
    (repo / "cp02.txt").write_text("cp02 complete\n", encoding="utf-8", newline="\n")
    after = gate7._scope_outside_diff_digest(
        repo,
        ["cp02.txt"],
        base=base,
    )

    assert after != before


def test_artifact_scan_reports_counts_and_rejects_sensitive_material(
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "evidence.txt").write_text("safe\n", encoding="utf-8")

    result = gate7._scan_artifacts(["private-canary"], [clean])

    assert result == {
        "files_scanned": 1,
        "files_skipped": 0,
        "canary_hit_count": 0,
        "sensitive_material_hit_count": 0,
    }

    (clean / "secret.txt").write_text(
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n",
        encoding="utf-8",
    )
    with pytest.raises(gate7.Gate7Failure, match="敏感材料泄漏"):
        gate7._scan_artifacts(["private-canary"], [clean])


def test_linear_engine_cursor_round_trips_without_external_attempts(tmp_path: Path) -> None:
    checkpoints = [
        _fake_checkpoint_evidence("CP01", 1),
        _fake_checkpoint_evidence("CP02", 2),
    ]
    bundle = _build_cursor_bundle(
        engine_state=gate7._seal_linear_cursor(
            exchange=tmp_path / "exchange",
            session="gate7-linear-test",
            **_cursor_identity(),
            checkpoints=checkpoints,
        ),
    )

    result = gate7._resume_linear_cursor(
        exchange=tmp_path / "exchange",
        bundle=bundle,
    )

    assert result["before_phase"] == "cp02_completed"
    assert result["after_phase"] == "cp03_authorized"
    assert result["resume_external_attempts"] == 0
    assert result["checkpoint_count_before"] == 0
    assert result["checkpoint_count_after"] == 0


def test_langgraph_sqlite_graph_runs_cp01_cp02_then_resumes_only_cp03(
    tmp_path: Path,
) -> None:
    pytest.importorskip("langgraph")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from vega.loop_graph_checkpoint import (
        checkpoint_config,
        open_sqlite_checkpointer,
        validate_checkpoint_manifest,
    )

    case, _, _ = gate7._load_and_validate_case()
    run_dir = tmp_path / "gate7-langgraph-test"
    graph_config = checkpoint_config(run_dir.name)
    source_calls: list[str] = []

    def execute_source(checkpoint: dict[str, object]) -> gate7.CheckpointEvidence:
        checkpoint_id = str(checkpoint["id"])
        source_calls.append(checkpoint_id)
        return _fake_checkpoint_evidence(checkpoint_id, len(source_calls))

    with open_sqlite_checkpointer(run_dir) as checkpointer:
        graph = gate7._build_langgraph_orchestration_graph(
            case["checkpoints"],
            execute_source,
        ).compile(checkpointer=checkpointer, interrupt_after=["cp02"])
        source_state = graph.invoke(
            {
                "phase": "ready",
                "completed_checkpoints": [],
                "checkpoint_evidence": [],
                **_cursor_identity(),
            },
            graph_config,
        )
        source_snapshot = graph.get_state(graph_config)

    assert source_calls == ["CP01", "CP02"]
    assert source_state["completed_checkpoints"] == ["CP01", "CP02"]
    assert source_snapshot.next == ("cp03",)
    manifest_before = validate_checkpoint_manifest(run_dir)
    target_calls: list[str] = []

    def execute_target(checkpoint: dict[str, object]) -> gate7.CheckpointEvidence:
        checkpoint_id = str(checkpoint["id"])
        target_calls.append(checkpoint_id)
        return _fake_checkpoint_evidence(checkpoint_id, 3)

    with open_sqlite_checkpointer(run_dir, require_existing=True) as checkpointer:
        graph = gate7._build_langgraph_orchestration_graph(
            case["checkpoints"],
            execute_target,
        ).compile(checkpointer=checkpointer)
        resumed = graph.invoke(None, graph_config)
        target_snapshot = graph.get_state(graph_config)

    assert target_calls == ["CP03"]
    assert resumed["completed_checkpoints"] == ["CP01", "CP02", "CP03"]
    assert target_snapshot.next == ()
    manifest_after = validate_checkpoint_manifest(run_dir)
    assert manifest_after.checkpoint_count > manifest_before.checkpoint_count


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("commit", "head"),
        ("stage", "index_tree"),
        ("remote", "config"),
    ],
)
def test_worker_git_guard_detects_commit_stage_and_remote_config_changes(
    tmp_path: Path,
    mutation: str,
    expected_fragment: str,
) -> None:
    repo = _make_git_repo(tmp_path)
    before = gate7._git_guard_snapshot(repo)

    _GIT_GUARD_MUTATIONS[mutation](repo)

    with pytest.raises(gate7.Gate7Failure, match=expected_fragment):
        gate7._assert_worker_git_guard(repo, before)


def _r4_inspection_policy(
    checkpoint_id: str,
    *,
    expected_workdir: Path | str = r"C:\repo",
) -> dict[str, object]:
    case, _, _ = gate7._load_and_validate_case(
        gate7_r4.R4_EXPERIMENT.case_path,
        frozen_case_sha256=gate7_r4.R4_EXPERIMENT.frozen_case_sha256,
        frozen_plan_sha256=gate7_r4.R4_EXPERIMENT.frozen_plan_sha256,
        case_hash_mode=gate7_r4.R4_EXPERIMENT.case_hash_mode,
    )
    contract = gate7._inspection_contract_from_case(case)
    assert contract is not None
    policy = gate7._inspection_policy_from_config(
        {"inspection_contract": contract},
        checkpoint_id,
        expected_workdir=expected_workdir,
    )
    assert policy is not None
    return policy


def _valid_r4_transcript(expected_workdir: Path | str = r"C:\repo") -> str:
    return "\n".join(
        [
            "OpenAI Codex v0.144.5",
            "codex",
            "exec",
            f'"pwsh" -Command \'git status --short\' in {expected_workdir}',
            " succeeded in 1ms:",
            "",
            "tokens used",
            "1,000",
            "",
        ]
    )


def _cursor_identity() -> dict[str, str]:
    return {
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "migration_id": "migration-gate7-test",
        "case_sha256": gate7.FROZEN_CASE_SHA256,
        "plan_sha256": gate7.FROZEN_PLAN_SHA256,
    }


def _build_cursor_bundle(*, engine_state: dict[str, object]) -> dict[str, object]:
    return {
        **_cursor_identity(),
        "source_machine": "machine-e",
        "target_machine": "machine-f",
        "completed_checkpoints": ["CP01", "CP02"],
        "checkpoint_evidence_sha256": engine_state["checkpoint_evidence_sha256"],
        "engine_state": engine_state,
    }


def _fake_checkpoint_evidence(
    checkpoint: str,
    index: int,
) -> gate7.CheckpointEvidence:
    machine = "machine-f" if checkpoint == "CP03" else "machine-e"
    return gate7.CheckpointEvidence(
        checkpoint=checkpoint,
        machine=machine,
        attempt_id=f"attempt-{index}",
        logical_operation_id=f"logical-{checkpoint}",
        prompt_sha256=str(index) * 64,
        output_sha256=str(index + 3) * 64,
        tokens_used=index,
        changed_files=[f"file-{index}.txt"],
        diff_lines=index,
        commit_sha=f"{index:x}" * 40,
        tree_sha=f"{index + 3:x}" * 40,
        parent_sha=f"{max(index - 1, 0):x}" * 40,
        ref_name=f"refs/heads/gate7/test/{checkpoint.casefold()}",
        verification=[],
        elapsed_seconds=float(index),
    )


def _make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "user.email", "gate7@example.invalid")
    _git(repo, "config", "user.name", "Gate 7 Test")
    _git(repo, "remote", "add", "origin", str((tmp_path / "origin.git").resolve()))
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "--", "tracked.txt")
    _git(repo, "commit", "-m", "create fixture")
    return repo


def _mutate_commit(repo: Path) -> None:
    (repo / "tracked.txt").write_text("committed change\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "--", "tracked.txt")
    _git(repo, "commit", "-m", "worker commit")


def _mutate_stage(repo: Path) -> None:
    (repo / "tracked.txt").write_text("staged change\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "--", "tracked.txt")


def _mutate_remote(repo: Path) -> None:
    _git(repo, "remote", "set-url", "origin", "file:///tmp/gate7-mutated-origin.git")


_GIT_GUARD_MUTATIONS: dict[str, Callable[[Path], None]] = {
    "commit": _mutate_commit,
    "stage": _mutate_stage,
    "remote": _mutate_remote,
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
