from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
CASE_ID_PATTERN = re.compile(r"DV-[BF]\d{2}\Z")
RUN_STATUSES = ("completed", "stopped", "timed_out", "infrastructure_failure")
FINAL_DISPOSITIONS = ("success", "needs_human", "failed", "not_completed")
VERIFICATION_STATUSES = ("passed", "failed", "not_run", "invalid", "not_applicable")
REVIEWER_VERDICTS = ("approve", "request_changes", "needs_human", "not_run", "invalid")
RESULT_REQUIRED = {
    "schema_version",
    "experiment_version",
    "run_id",
    "case_id",
    "treatment",
    "baseline_commit",
    "model",
    "reasoning_effort",
    "timeout_seconds",
    "run_status",
    "final_disposition",
    "runtime_verification_status",
    "post_seal_verification_status",
    "reviewer_verdict",
    "reviewer_independent_findings",
    "wall_clock_seconds",
    "tokens",
    "owner_manual_actions",
    "automation_actions",
    "recovery_used",
    "artifact_read",
    "environment_fingerprint",
    "preflight_ref",
    "event_log_ref",
    "event_timing",
    "evidence_refs",
    "notes",
}
ENVIRONMENT_PROBE = r"""
import json
import platform
import re
import sys
import sysconfig
from importlib.metadata import distributions
packages = {}
for distribution in distributions():
    raw_name = distribution.metadata.get("Name")
    if raw_name:
        name = re.sub(r"[-_.]+", "-", raw_name).lower()
        packages[name] = str(distribution.version)

print(json.dumps({
    "implementation": platform.python_implementation(),
    "version": platform.python_version(),
    "cache_tag": sys.implementation.cache_tag,
    "abi_flags": getattr(sys, "abiflags", ""),
    "platform": sysconfig.get_platform(),
    "packages": [
        {"name": name, "version": packages[name]}
        for name in sorted(packages)
    ],
}, sort_keys=True))
"""


def build_environment_preflight(
    python_executable: Path,
    workspace: Path,
    collect_targets: list[str],
    *,
    expected_environment_fingerprint: str | None,
    max_control_latency_seconds: float,
    active_formal_treatments: int,
    competing_workload_observed: bool,
    command_timeout_seconds: int = 60,
) -> dict[str, Any]:
    """在调用 Provider 前冻结环境，并证明目标测试至少可以收集。"""
    python_executable = python_executable.resolve()
    workspace = workspace.resolve()
    _validate_preflight_inputs(
        python_executable,
        workspace,
        collect_targets,
        expected_environment_fingerprint,
        max_control_latency_seconds,
        active_formal_treatments,
        command_timeout_seconds,
    )
    environment_probe = _run_command(
        [str(python_executable), "-I", "-c", ENVIRONMENT_PROBE],
        cwd=workspace,
        timeout_seconds=command_timeout_seconds,
    )
    environment = _parse_environment(environment_probe)
    fingerprint = _fingerprint(environment) if environment is not None else None
    pip_check = _run_command(
        [str(python_executable), "-m", "pip", "check"],
        cwd=workspace,
        timeout_seconds=command_timeout_seconds,
    )
    collection = _run_command(
        [
            str(python_executable),
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            *collect_targets,
        ],
        cwd=workspace,
        timeout_seconds=command_timeout_seconds,
    )
    python_probe = _run_command(
        [str(python_executable), "-I", "-c", "pass"],
        cwd=workspace,
        timeout_seconds=command_timeout_seconds,
    )
    git_probe = _run_command(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        cwd=workspace,
        timeout_seconds=command_timeout_seconds,
    )
    collected_node_count = sum("::" in line for line in collection["stdout"].splitlines())
    gates = {
        "environment_observed": _gate(environment is not None),
        "pip_check": _gate(_command_passed(pip_check)),
        "target_collection": _gate(
            _command_passed(collection) and collected_node_count > 0
        ),
        "control_latency": _gate(
            _command_passed(python_probe)
            and _command_passed(git_probe)
            and python_probe["duration_seconds"] <= max_control_latency_seconds
            and git_probe["duration_seconds"] <= max_control_latency_seconds
        ),
        "formal_treatment_overlap": _gate(active_formal_treatments == 0),
        "competing_workload": _gate(not competing_workload_observed),
        "environment_match": _gate(
            expected_environment_fingerprint is None
            or fingerprint == expected_environment_fingerprint
        ),
    }
    blocked_reasons = sorted(name for name, value in gates.items() if value == "failed")
    return {
        "schema_version": 2,
        "experiment_version": "V2",
        "status": "blocked" if blocked_reasons else "ready",
        "observed_at": _now(),
        "environment_fingerprint": fingerprint,
        "environment": environment,
        "collect_targets": collect_targets,
        "collected_node_count": collected_node_count,
        "max_control_latency_seconds": max_control_latency_seconds,
        "active_formal_treatments": active_formal_treatments,
        "competing_workload_observed": competing_workload_observed,
        "process_snapshot": _process_snapshot(),
        "commands": {
            "environment_probe": _command_evidence(environment_probe),
            "pip_check": _command_evidence(pip_check),
            "target_collection": _command_evidence(collection),
            "python_startup_probe": _command_evidence(python_probe),
            "git_status_probe": _command_evidence(git_probe),
        },
        "gates": gates,
        "blocked_reasons": blocked_reasons,
        "provider_request_performed": False,
    }


def validate_v2_results(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_ids: set[str] = set()
    treatments: set[tuple[str, str]] = set()
    fingerprints: dict[str, set[str]] = defaultdict(set)
    contracts: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    for record in records:
        _require_fields(record, RESULT_REQUIRED, "V2 result")
        case_id = record["case_id"]
        treatment = record["treatment"]
        run_id = record["run_id"]
        if record["schema_version"] != 2 or record["experiment_version"] != "V2":
            raise ValueError("V2 result schema 非法")
        if not isinstance(case_id, str) or CASE_ID_PATTERN.fullmatch(case_id) is None:
            raise ValueError(f"V2 case_id 非法：{case_id}")
        if treatment not in ("native", "vega"):
            raise ValueError(f"{case_id} treatment 非法")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(f"{case_id}/{treatment} run_id 必须是非空字符串")
        if run_id in run_ids or (case_id, treatment) in treatments:
            raise ValueError(f"{case_id}/{treatment} V2 只允许一次正式运行")
        run_ids.add(run_id)
        treatments.add((case_id, treatment))
        _validate_result(record)
        fingerprints[case_id].add(record["environment_fingerprint"])
        contracts[case_id].add(
            (
                record["baseline_commit"],
                record["model"],
                record["reasoning_effort"],
                record["timeout_seconds"],
            )
        )
    drift = sorted(case_id for case_id, values in fingerprints.items() if len(values) > 1)
    if drift:
        raise ValueError(f"Native 与 Vega 环境 fingerprint 不一致：{drift}")
    contract_drift = sorted(
        case_id for case_id, values in contracts.items() if len(values) > 1
    )
    if contract_drift:
        raise ValueError(f"Native 与 Vega 执行合同不一致：{contract_drift}")
    return records


def build_v2_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    treatments: dict[str, set[str]] = defaultdict(set)
    for record in records:
        treatments[record["case_id"]].add(record["treatment"])
    complete = sorted(
        case_id for case_id, values in treatments.items() if values == {"native", "vega"}
    )
    clean = [
        case_id
        for case_id in complete
        if all(
            item["run_status"] == "completed"
            and item["post_seal_verification_status"] == "passed"
            and item["reviewer_verdict"] != "not_run"
            for item in records
            if item["case_id"] == case_id
        )
    ]
    return {
        "schema_version": 2,
        "experiment_version": "V2",
        "evidence_status": "clean_pair_observed" if clean else "insufficient_evidence",
        "registered_run_count": len(records),
        "complete_pair_count": len(complete),
        "clean_pair_count": len(clean),
        "complete_pairs": complete,
        "clean_pairs": clean,
        "owner_manual_actions_total": sum(item["owner_manual_actions"] for item in records),
        "automation_actions_total": sum(item["automation_actions"] for item in records),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{number} 不是有效 JSON：{exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path.name}:{number} 必须是 object")
        records.append(record)
    return records


def _validate_result(record: dict[str, Any]) -> None:
    label = f"{record['case_id']}/{record['treatment']}"
    if not isinstance(record["baseline_commit"], str) or COMMIT_PATTERN.fullmatch(
        record["baseline_commit"]
    ) is None:
        raise ValueError(f"{label} baseline_commit 非法")
    if record["run_status"] not in RUN_STATUSES:
        raise ValueError(f"{label} run_status 非法")
    if record["final_disposition"] not in FINAL_DISPOSITIONS:
        raise ValueError(f"{label} final_disposition 非法")
    if (record["run_status"] == "completed") == (
        record["final_disposition"] == "not_completed"
    ):
        raise ValueError(f"{label} 运行与终态语义不一致")
    runtime_status = record["runtime_verification_status"]
    post_seal_status = record["post_seal_verification_status"]
    if runtime_status not in VERIFICATION_STATUSES:
        raise ValueError(f"{label} runtime verification 非法")
    if post_seal_status not in VERIFICATION_STATUSES[:-1]:
        raise ValueError(f"{label} post-seal verifier 非法")
    if record["treatment"] == "native" and runtime_status != "not_applicable":
        raise ValueError(f"{label} Native runtime verification 必须是 not_applicable")
    if record["treatment"] == "vega" and runtime_status == "not_applicable":
        raise ValueError(f"{label} Vega runtime verification 不得是 not_applicable")
    if record["reviewer_verdict"] not in REVIEWER_VERDICTS:
        raise ValueError(f"{label} reviewer_verdict 非法")
    if record["final_disposition"] == "success" and (
        post_seal_status != "passed" or record["reviewer_verdict"] != "approve"
    ):
        raise ValueError(f"{label} success 必须有 post-seal passed 与 reviewer approve")
    _non_negative(record["reviewer_independent_findings"], "reviewer finding", True)
    _non_negative(record["wall_clock_seconds"], "wall clock", False)
    _non_negative(record["owner_manual_actions"], "owner manual actions", True)
    _non_negative(record["automation_actions"], "automation actions", True)
    if record["reviewer_verdict"] == "not_run" and record["reviewer_independent_findings"]:
        raise ValueError(f"{label} reviewer 未运行时 finding 必须为 0")
    if not all(isinstance(record[field], bool) for field in ("recovery_used", "artifact_read")):
        raise ValueError(f"{label} 布尔指标非法")
    fingerprint = record["environment_fingerprint"]
    if not isinstance(fingerprint, str) or SHA256_PATTERN.fullmatch(fingerprint) is None:
        raise ValueError(f"{label} environment_fingerprint 非法")
    _validate_tokens(record["tokens"])
    _validate_event_timing(record["event_timing"], label)
    for field in ("preflight_ref", "event_log_ref"):
        _relative_ref(record[field], f"{label} {field}")
    _non_empty_strings(record["evidence_refs"], f"{label} evidence_refs")
    for value in record["evidence_refs"]:
        _relative_ref(value, f"{label} evidence_ref")


def _validate_preflight_inputs(
    python_executable: Path,
    workspace: Path,
    collect_targets: list[str],
    expected_fingerprint: str | None,
    max_latency: float,
    active_treatments: int,
    command_timeout: int,
) -> None:
    if not python_executable.is_file():
        raise ValueError("必须提供明确存在的 Python executable")
    if not workspace.is_dir() or not workspace.joinpath(".git").exists():
        raise ValueError("V2 workspace 必须是独立 Git 工作区")
    _non_empty_strings(collect_targets, "collect_targets")
    for target in collect_targets:
        _relative_ref(target, "collect target")
    if expected_fingerprint is not None and SHA256_PATTERN.fullmatch(
        expected_fingerprint
    ) is None:
        raise ValueError("expected_environment_fingerprint 非法")
    if isinstance(max_latency, bool) or not isinstance(max_latency, (int, float)) or max_latency <= 0:
        raise ValueError("max_control_latency_seconds 必须大于 0")
    _non_negative(active_treatments, "active_formal_treatments", True)
    _positive_int(command_timeout, "command_timeout_seconds")


def _parse_environment(command: dict[str, Any]) -> dict[str, Any] | None:
    if not _command_passed(command):
        return None
    try:
        payload = json.loads(command["stdout"])
    except json.JSONDecodeError:
        return None
    required = {"implementation", "version", "cache_tag", "abi_flags", "platform", "packages"}
    if not isinstance(payload, dict) or required - payload.keys():
        return None
    packages = payload["packages"]
    if not isinstance(packages, list) or any(
        not isinstance(item, dict)
        or set(item) != {"name", "version"}
        or not all(isinstance(item[field], str) and item[field] for field in item)
        for item in packages
    ):
        return None
    if packages != sorted(packages, key=lambda item: (item["name"], item["version"])):
        return None
    return payload


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    process = _start_process(command, cwd=cwd)
    started = time.monotonic()
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        timed_out = False
        termination_confirmed = True
    except subprocess.TimeoutExpired:
        timed_out = True
        termination_confirmed = _terminate_owned_process(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
            termination_confirmed = False
    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "termination_confirmed": termination_confirmed,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout": stdout or "",
        "stderr": stderr or "",
    }


def _start_process(command: list[str], *, cwd: Path) -> subprocess.Popen[str]:
    group_options: dict[str, Any] = (
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **group_options,
    )


def _terminate_owned_process(process: subprocess.Popen[str]) -> bool:
    if process.poll() is not None:
        return True
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return True
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
    return process.poll() is not None


def _process_snapshot() -> dict[str, Any]:
    return {
        "status": "operator_declared",
        "observer_pid": os.getpid(),
        "logical_cpu_count": os.cpu_count(),
    }


def _command_evidence(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "exit_code": command["exit_code"],
        "timed_out": command["timed_out"],
        "termination_confirmed": command["termination_confirmed"],
        "duration_seconds": command["duration_seconds"],
        "stdout_sha256": _sha256_text(command["stdout"]),
        "stderr_sha256": _sha256_text(command["stderr"]),
    }


def _command_passed(command: dict[str, Any]) -> bool:
    return (
        command["exit_code"] == 0
        and not command["timed_out"]
        and command["termination_confirmed"]
    )


def _validate_tokens(tokens: Any) -> None:
    _require_fields(tokens, {"input", "output", "cached_input"}, "tokens")
    if set(tokens) != {"input", "output", "cached_input"}:
        raise ValueError("tokens 包含未知字段")
    for value in tokens.values():
        if value is not None:
            _non_negative(value, "token", True)


def _validate_event_timing(payload: Any, label: str) -> None:
    fields = {"event_count", "invalid_event_count", "first_received_at", "last_received_at"}
    _require_fields(payload, fields, "event_timing")
    if set(payload) != fields:
        raise ValueError(f"{label} event_timing 包含未知字段")
    _non_negative(payload["event_count"], "event_count", True)
    _non_negative(payload["invalid_event_count"], "invalid_event_count", True)
    if payload["invalid_event_count"] > payload["event_count"]:
        raise ValueError(f"{label} invalid_event_count 不得超过 event_count")
    first = payload["first_received_at"]
    last = payload["last_received_at"]
    if payload["event_count"] == 0:
        if first is not None or last is not None:
            raise ValueError(f"{label} 无事件时接收时间必须为空")
        return
    if not isinstance(first, str) or not isinstance(last, str):
        raise ValueError(f"{label} 有事件时必须记录首尾接收时间")
    try:
        first_time = datetime.fromisoformat(first)
        last_time = datetime.fromisoformat(last)
    except ValueError as exc:
        raise ValueError(f"{label} event_timing 时间格式非法") from exc
    if first_time.tzinfo is None or last_time.tzinfo is None or first_time > last_time:
        raise ValueError(f"{label} event_timing 时间顺序非法")


def _relative_ref(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空相对路径")
    normalized = value.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", normalized) or normalized.startswith("/"):
        raise ValueError(f"{label} 不得包含本机绝对路径")
    if ".." in Path(normalized).parts:
        raise ValueError(f"{label} 不得逃逸证据根目录")


def _require_fields(payload: Any, fields: set[str], label: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 必须是 object")
    missing = sorted(fields - payload.keys())
    if missing:
        raise ValueError(f"{label} 缺少字段：{missing}")


def _non_empty_strings(value: Any, label: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{label} 必须是非空字符串列表")


def _non_negative(value: Any, label: str, integer: bool) -> None:
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected) or value < 0:
        raise ValueError(f"{label} 必须是非负数")


def _positive_int(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} 必须是正整数")


def _gate(condition: bool) -> str:
    return "passed" if condition else "failed"


def _fingerprint(payload: Any) -> str:
    normalized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(normalized)


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vega 日用价值实验 V2 工具。")
    commands = parser.add_subparsers(dest="command_name", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--python", type=Path, required=True, dest="python_executable")
    preflight.add_argument("--workspace", type=Path, required=True)
    preflight.add_argument("--collect-target", action="append", required=True)
    preflight.add_argument("--expected-environment-fingerprint")
    preflight.add_argument("--max-control-latency-seconds", type=float, required=True)
    preflight.add_argument("--active-formal-treatments", type=int, required=True)
    preflight.add_argument("--competing-workload-observed", choices=("true", "false"), required=True)
    preflight.add_argument("--command-timeout-seconds", type=int, default=60)
    preflight.add_argument("--output", type=Path, required=True)

    validate = commands.add_parser("validate-results")
    validate.add_argument("--results", type=Path, required=True)
    validate.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command_name == "preflight":
            payload = build_environment_preflight(
                args.python_executable,
                args.workspace,
                args.collect_target,
                expected_environment_fingerprint=args.expected_environment_fingerprint,
                max_control_latency_seconds=args.max_control_latency_seconds,
                active_formal_treatments=args.active_formal_treatments,
                competing_workload_observed=args.competing_workload_observed == "true",
                command_timeout_seconds=args.command_timeout_seconds,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print(f"V2 环境资格：status={payload['status']}")
            return 0 if payload["status"] == "ready" else 2
        records = validate_v2_results(load_jsonl(args.results))
        summary = build_v2_summary(records)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        print(
            "V2 结果校验完成："
            f"status={summary['evidence_status']}，pairs={summary['complete_pair_count']}"
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"日用价值实验 V2 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
