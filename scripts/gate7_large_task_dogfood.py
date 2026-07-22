from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vega.execution_control import RunnerExecutionContext  # noqa: E402
from vega.project_config import (  # noqa: E402
    CodexExecOptions,
    CodexProviderDescriptor,
)
from vega.redaction import redact_text  # noqa: E402
from vega.runner import CodexExecRunner, RunnerResult  # noqa: E402


RunnerMode = Literal["fake", "real"]
EngineName = Literal["linear", "langgraph"]
MachineName = Literal["machine-e", "machine-f"]
CaseHashMode = Literal["raw-bytes", "canonical-json"]
AuthMode = Literal["chatgpt", "api-key"]
TranscriptAuditStatus = Literal["passed", "failed", "not_applicable"]

CASE_PATH = PROJECT_ROOT / "eval" / "gate-7" / "flask-teardown-case.json"
FROZEN_CASE_SHA256 = "9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9"
FROZEN_PLAN_SHA256 = "ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f"
GRAPH_SCHEMA_VERSION = "gate7-v1"
UPSTREAM_URL = "https://github.com/pallets/flask.git"
BASE_SHA = "7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b"
MERGE_SHA = "c34d6e81fd8e405e6d4178bf24b364918811ef17"
ORACLE_DIFF_SHA256 = "d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b"

PROVIDER = "sandboxproxy"
PROVIDER_BASE_URL = "http://127.0.0.1:18080/v1"
WIRE_API = "responses"
MODEL = "sandbox-model"
REASONING = "high"
AUTH_MODE: AuthMode = "chatgpt"
CODEX_VERSION = "0.144.5"
PROVIDER_SESSION_HARD_LIMIT = 3
WORKER_TIMEOUT_SECONDS = 900
VERIFY_TIMEOUT_SECONDS = 60
DEPENDENCY_SYNC_TIMEOUT_SECONDS = 300
REMOTE_GIT_TIMEOUT_SECONDS = 120
PLANNED_MIGRATION_EXIT_CODE = 75
TRANSCRIPT_AUDIT_PARSER_VERSION = "gate7-r4-v1"
IDENTIFIER_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,95}")
EXEC_RESULT_PATTERN = re.compile(
    r"^\s*(?:succeeded|exited \d+|timed out) in \d+(?:\.\d+)?(?:ms|s):$",
    re.IGNORECASE,
)
SENSITIVE_ARTIFACT_PATTERNS = (
    re.compile(rb"authorization\s*:\s*bearer\s+[a-z0-9._-]{16,}", re.IGNORECASE),
    re.compile(rb"\bsk-[a-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(
        rb"\b(?:openai_api_key|codex_api_key)\s*=\s*[^\s]+",
        re.IGNORECASE,
    ),
)
REAL_OUTPUT_ROOT = PROJECT_ROOT / ".local-validation" / "gate-7"
REAL_FIXTURE_ROOT = PROJECT_ROOT / ".tmp" / "gate-7"

CP02_BEHAVIOR_PROBE = r"""
from flask import Flask
from flask.signals import appcontext_tearing_down, request_tearing_down


def assert_group(exc: BaseException, label: str, expected: int) -> None:
    if not isinstance(exc, BaseExceptionGroup):
        raise AssertionError(f"{label}: expected BaseExceptionGroup, got {type(exc)!r}")
    if len(exc.exceptions) != expected:
        raise AssertionError(f"{label}: expected {expected}, got {len(exc.exceptions)}")


app = Flask(__name__)
request_events: list[str] = []


@app.teardown_request
def request_first(exc: BaseException | None) -> None:
    request_events.append("request-first")
    raise ValueError("request-first")


@app.teardown_request
def request_second(exc: BaseException | None) -> None:
    request_events.append("request-second")
    raise TypeError("request-second")


def request_signal(sender: Flask, exc: BaseException | None) -> None:
    request_events.append("request-signal")
    raise RuntimeError("request-signal")


request_ctx = app.test_request_context("/")
with request_tearing_down.connected_to(request_signal, app):
    try:
        app.do_teardown_request(request_ctx, None)
    except BaseException as exc:
        assert_group(exc, "request", 3)
    else:
        raise AssertionError("request teardown did not raise")

if request_events != ["request-second", "request-first", "request-signal"]:
    raise AssertionError(request_events)

app_events: list[str] = []


@app.teardown_appcontext
def app_first(exc: BaseException | None) -> None:
    app_events.append("app-first")
    raise ValueError("app-first")


@app.teardown_appcontext
def app_second(exc: BaseException | None) -> None:
    app_events.append("app-second")
    raise TypeError("app-second")


def app_signal(sender: Flask, exc: BaseException | None) -> None:
    app_events.append("app-signal")
    raise RuntimeError("app-signal")


app_ctx = app.app_context()
with appcontext_tearing_down.connected_to(app_signal, app):
    try:
        app.do_teardown_appcontext(app_ctx, None)
    except BaseException as exc:
        assert_group(exc, "app", 3)
    else:
        raise AssertionError("app teardown did not raise")

if app_events != ["app-second", "app-first", "app-signal"]:
    raise AssertionError(app_events)

print("GATE7_CP02_BEHAVIOR_PASS")
""".strip()


class Gate7Failure(RuntimeError):
    pass


class Gate7Blocked(Gate7Failure):
    pass


@dataclass(frozen=True)
class Gate7ExperimentSpec:
    name: str
    case_path: Path
    frozen_case_sha256: str
    frozen_plan_sha256: str
    graph_schema_version: str
    case_hash_mode: CaseHashMode = "raw-bytes"
    auth_mode: AuthMode = AUTH_MODE


DEFAULT_EXPERIMENT = Gate7ExperimentSpec(
    name="gate7-v1",
    case_path=CASE_PATH,
    frozen_case_sha256=FROZEN_CASE_SHA256,
    frozen_plan_sha256=FROZEN_PLAN_SHA256,
    graph_schema_version=GRAPH_SCHEMA_VERSION,
    case_hash_mode="raw-bytes",
)


@dataclass
class CommandEvidence:
    command: list[str]
    returncode: int
    output: str
    elapsed_seconds: float

    @property
    def output_sha256(self) -> str:
        return _sha256_text(self.output)


@dataclass
class CheckpointEvidence:
    checkpoint: str
    machine: MachineName
    attempt_id: str
    logical_operation_id: str
    prompt_sha256: str
    output_sha256: str
    tokens_used: int | None
    changed_files: list[str]
    diff_lines: int
    commit_sha: str
    tree_sha: str
    parent_sha: str
    ref_name: str
    verification: list[dict[str, Any]]
    elapsed_seconds: float
    transcript_audit: dict[str, Any] = field(
        default_factory=lambda: {
            "status": "not_applicable",
            "parser_version": TRANSCRIPT_AUDIT_PARSER_VERSION,
        }
    )


@dataclass
class ArmState:
    runner_mode: RunnerMode
    engine: EngineName
    session: str
    output_dir: Path
    fixture_dir: Path
    case_path: Path
    case_sha256: str
    plan_sha256: str
    graph_schema_version: str
    case_hash_mode: CaseHashMode
    auth_mode: AuthMode
    baseline_sha: str | None = None
    consumed_tag: str | None = None
    checkpoint_evidence: list[CheckpointEvidence] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class Gate7CursorState(TypedDict):
    phase: str
    completed_checkpoints: list[str]
    checkpoint_evidence: list[dict[str, Any]]
    source_commit: str
    source_tree: str
    migration_id: str
    case_sha256: str
    plan_sha256: str


class EventLedger:
    """追加式 hash chain，机器恢复只信这条事件链。"""

    def __init__(self, path: Path, *, run_id: str, engine: EngineName) -> None:
        self.path = path
        self.run_id = run_id
        self.engine = engine
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        event: str,
        *,
        operation_kind: str,
        step_id: str | None = None,
        attempt_id: str | None = None,
        logical_operation_id: str | None = None,
        idempotency_key: str | None = None,
        parent_attempt_id: str | None = None,
        retry_of_attempt_id: str | None = None,
        recovery_of_attempt_id: str | None = None,
        migration_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entries = self.read()
        prev_hash = entries[-1]["event_hash"] if entries else "0" * 64
        body = {
            "event_id": uuid4().hex,
            "prev_event_hash": prev_hash,
            "event": event,
            "operation_kind": operation_kind,
            "run_id": self.run_id,
            "engine": self.engine,
            "step_id": step_id,
            "attempt_id": attempt_id,
            "logical_operation_id": logical_operation_id,
            "idempotency_key": idempotency_key,
            "parent_attempt_id": parent_attempt_id,
            "retry_of_attempt_id": retry_of_attempt_id,
            "recovery_of_attempt_id": recovery_of_attempt_id,
            "migration_id": migration_id,
            "recorded_at": datetime.now(UTC).isoformat(),
            "payload": payload or {},
        }
        event_hash = _sha256_text(_canonical_json(body))
        entry = {**body, "event_hash": event_hash}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(entry))
            handle.write("\n")
        return entry

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        validate_event_chain(entries)
        return entries


class ProviderBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0

    def reserve(self, checkpoint: str) -> None:
        if self.used >= self.maximum:
            raise Gate7Blocked(
                f"provider session budget exceeded before {checkpoint}: "
                f"{self.used}/{self.maximum}"
            )
        self.used += 1


class DeterministicOracleRunner:
    """只用于真实调用前的本地协议预检，不参与真实结论。"""

    def __init__(self, oracle_git_dir: Path) -> None:
        self.oracle_git_dir = oracle_git_dir

    def run(self, checkpoint: dict[str, Any], repo: Path) -> RunnerResult:
        diff = subprocess.run(
            [
                "git",
                f"--git-dir={self.oracle_git_dir}",
                "diff",
                "--binary",
                "--full-index",
                BASE_SHA,
                MERGE_SHA,
                "--",
                *checkpoint["scope"],
            ],
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=repo,
            input=diff,
            capture_output=True,
            timeout=30,
        )
        if applied.returncode != 0:
            return RunnerResult(
                status="error",
                output=applied.stdout.decode("utf-8", errors="replace"),
                error=applied.stderr.decode("utf-8", errors="replace"),
                command=["git", "apply", "-"],
            )
        return RunnerResult(
            status="success",
            output=f"deterministic oracle slice applied for {checkpoint['id']}",
            command=["git", "apply", "-"],
        )


class BoundedCodexExecRunner(CodexExecRunner):
    """仅为 Gate 7 R4 增加经过白名单验证的 Codex 减量配置。"""

    def __init__(
        self,
        *,
        executable: str,
        options: CodexExecOptions,
        tool_output_token_limit: int,
        model_verbosity: str,
        model_reasoning_summary: str,
    ) -> None:
        super().__init__(executable=executable, options=options)
        self.tool_output_token_limit = tool_output_token_limit
        self.model_verbosity = model_verbosity
        self.model_reasoning_summary = model_reasoning_summary

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        command = super().build_command(repo_path, sandbox)
        command[-1:-1] = [
            "--config",
            f"tool_output_token_limit={self.tool_output_token_limit}",
            "--config",
            f'model_verbosity="{self.model_verbosity}"',
            "--config",
            f'model_reasoning_summary="{self.model_reasoning_summary}"',
        ]
        return command

    def execution_identity(self, sandbox: str) -> dict[str, str]:
        identity = super().execution_identity(sandbox)
        identity.update(
            {
                "tool_output_token_limit": str(self.tool_output_token_limit),
                "model_verbosity": self.model_verbosity,
                "model_reasoning_summary": self.model_reasoning_summary,
            }
        )
        return identity


def main(
    argv: list[str] | None = None,
    *,
    experiment: Gate7ExperimentSpec = DEFAULT_EXPERIMENT,
) -> int:
    args = _parse_args(argv)
    if args.internal_machine:
        return _run_internal_machine(Path(args.config))

    session = args.session or (
        f"gate7-{args.engine}-{args.runner}-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    if not IDENTIFIER_PATTERN.fullmatch(session):
        raise SystemExit("--session 只能包含小写字母、数字、点、下划线和连字符")

    output_root = _project_path(Path(args.output_root), "output root")
    fixture_root = _project_path(Path(args.fixture_root), "fixture root")
    output_dir = output_root / session
    fixture_dir = fixture_root / session
    if output_dir.exists() or fixture_dir.exists():
        raise SystemExit("session 目录已存在，禁止覆盖既有 Gate 7 证据")

    try:
        case, case_sha256, plan_sha256 = _load_and_validate_case(
            experiment.case_path,
            frozen_case_sha256=experiment.frozen_case_sha256,
            frozen_plan_sha256=experiment.frozen_plan_sha256,
            case_hash_mode=experiment.case_hash_mode,
        )
        if case["schema_version"] == 2:
            contract = case["r2_contract"]
            if experiment.case_hash_mode != contract["case_hash_mode"]:
                raise Gate7Blocked("R2 experiment case_hash_mode 与 case 不一致")
            if experiment.graph_schema_version != contract["graph_schema_version"]:
                raise Gate7Blocked("R2 experiment graph_schema_version 与 case 不一致")
        case_auth_mode = _auth_mode_from_case(case)
        if experiment.auth_mode != case_auth_mode:
            raise Gate7Blocked("experiment auth_mode 与 case 不一致")
        state = ArmState(
            runner_mode=args.runner,
            engine=args.engine,
            session=session,
            output_dir=output_dir,
            fixture_dir=fixture_dir,
            case_path=experiment.case_path.resolve(),
            case_sha256=case_sha256,
            plan_sha256=plan_sha256,
            graph_schema_version=experiment.graph_schema_version,
            case_hash_mode=experiment.case_hash_mode,
            auth_mode=experiment.auth_mode,
        )
        if args.runner == "real":
            if not args.confirm_real:
                raise Gate7Blocked("real mode 需要显式提供 --confirm-real")
            expected_session = (
                case["case_identity"]["real_session"]
                if args.engine == "linear"
                else case["case_identity"]["langgraph_session"]
            )
            if session != expected_session:
                raise Gate7Blocked(
                    f"real session 必须使用预注册值：{expected_session}"
                )
            if output_root != REAL_OUTPUT_ROOT.resolve():
                raise Gate7Blocked("real output root 必须是 .local-validation/gate-7")
            if fixture_root != REAL_FIXTURE_ROOT.resolve():
                raise Gate7Blocked("real fixture root 必须是 .tmp/gate-7")
            state.baseline_sha = _assert_real_baseline(
                args.engine,
                case,
                case_sha256=case_sha256,
                plan_sha256=plan_sha256,
                expected_auth_mode=experiment.auth_mode,
            )
        _run_arm(state, case)
        return 0
    except Gate7Blocked as exc:
        _write_terminal_state(
            output_dir,
            terminal_status="blocked",
            stop_reason=str(exc),
        )
        print(f"Gate 7 blocked: {exc}", file=sys.stderr)
        return 2
    except Gate7Failure as exc:
        _write_terminal_state(
            output_dir,
            terminal_status="failed",
            stop_reason=str(exc),
        )
        print(f"Gate 7 failed: {exc}", file=sys.stderr)
        return 1


def _run_arm(state: ArmState, case: dict[str, Any]) -> None:
    state.output_dir.mkdir(parents=True, exist_ok=False)
    state.fixture_dir.mkdir(parents=True, exist_ok=False)
    authority = state.fixture_dir / "authority"
    remote = authority / "source.git"
    oracle = authority / "oracle.git"
    exchange = state.fixture_dir / "exchange"
    machine_e_root = state.fixture_dir / "machine-e"
    machine_f_root = state.fixture_dir / "machine-f"
    coordinator_ledger = EventLedger(
        state.output_dir / "coordinator-events.jsonl",
        run_id=state.session,
        engine=state.engine,
    )
    coordinator_ledger.append(
        "arm_started",
        operation_kind="control",
        payload={
            "runner_mode": state.runner_mode,
            "case_sha256": state.case_sha256,
            "plan_sha256": state.plan_sha256,
            "auth_mode": state.auth_mode,
        },
    )

    _prepare_source_remote(remote)
    if state.runner_mode == "fake":
        _prepare_oracle_remote(oracle)

    repo_e = machine_e_root / "repo"
    _clone_ref(remote, "refs/heads/base", repo_e)
    _configure_fixture_repo(repo_e)
    worker_git_config_e = machine_e_root / "worker-gitconfig"
    _configure_worker_git_config(repo_e, worker_git_config_e)
    _sync_dependencies(repo_e, state.output_dir / "preflight-machine-e.json")
    baseline = _run_command(
        ["uv", "run", "--offline", "--locked", "pytest", "-q"],
        cwd=repo_e,
        timeout=VERIFY_TIMEOUT_SECONDS,
    )
    _require_command(baseline, returncode=0, contains="494 passed", label="base full suite")

    claim = _claim_authority(remote, repo_e, state.session, state.engine)
    duplicate_claim_rejected = _assert_duplicate_claim_rejected(
        remote,
        repo_e,
        state.session,
        state.engine,
    )
    coordinator_ledger.append(
        "authority_claimed",
        operation_kind="claim",
        payload={
            "claim_ref": claim["ref"],
            "claim_commit": claim["commit"],
            "duplicate_claim_rejected": duplicate_claim_rejected,
        },
    )

    private_root = machine_e_root / "private"
    private_root.mkdir(parents=True, exist_ok=False)
    source_canary = f"GATE7_SOURCE_CHAT_CANARY_{uuid4().hex}"
    memory_canary = f"GATE7_MEMORY_CANARY_{uuid4().hex}"
    path_canary = f"GATE7_MACHINE_E_PATH_CANARY_{uuid4().hex}"
    _write_text(private_root / "source-chat.txt", source_canary)
    _write_text(private_root / "memory-ledger.txt", memory_canary)
    _write_text(private_root / "machine-path.txt", path_canary)

    exchange.mkdir(parents=True, exist_ok=False)
    inspection_contract = _inspection_contract_from_case(case)
    machine_e_config = {
        "schema_version": 1,
        "machine": "machine-e",
        "runner_mode": state.runner_mode,
        "engine": state.engine,
        "session": state.session,
        "case_path": str(state.case_path),
        "case_sha256": state.case_sha256,
        "plan_sha256": state.plan_sha256,
        "graph_schema_version": state.graph_schema_version,
        "case_hash_mode": state.case_hash_mode,
        "repo": str(repo_e),
        "worker_git_config": str(worker_git_config_e),
        "remote": str(remote),
        "oracle": str(oracle) if state.runner_mode == "fake" else None,
        "output_dir": str(state.output_dir / "machine-e"),
        "exchange": str(exchange),
        "private_root": str(private_root),
        "inspection_contract": inspection_contract,
    }
    config_e_path = state.fixture_dir / "machine-e-config.json"
    _write_json(config_e_path, machine_e_config)
    if state.runner_mode == "real":
        if state.baseline_sha is None:
            raise Gate7Blocked("real run 缺少 execution baseline")
        state.consumed_tag = _claim_real_execution(
            state.engine,
            state.baseline_sha,
            case,
        )
    process_e = _spawn_machine(config_e_path)
    if process_e.returncode != PLANNED_MIGRATION_EXIT_CODE:
        raise Gate7Failure(
            "Machine E 未在 CP02 后进入 planned migration："
            f"returncode={process_e.returncode}, {_process_output_tail(process_e)}"
        )

    bundle_path = exchange / "handoff-v0001.json"
    bundle = _load_sealed_json(bundle_path)
    _validate_handoff_bundle(
        bundle,
        case_sha256=state.case_sha256,
        plan_sha256=state.plan_sha256,
        remote=remote,
    )
    artifact_scans = [
        _scan_artifacts(
            [source_canary, memory_canary, path_canary],
            [bundle_path, state.output_dir / "machine-e", exchange],
        )
    ]
    coordinator_ledger.append(
        "planned_migration_accepted",
        operation_kind="planned_migration",
        migration_id=bundle["migration_id"],
        payload={
            "handoff_sha256": bundle["self_sha256"],
            "source_commit": bundle["source_commit"],
            "source_tree": bundle["source_tree"],
        },
    )

    machine_f_config = {
        "schema_version": 1,
        "machine": "machine-f",
        "runner_mode": state.runner_mode,
        "engine": state.engine,
        "session": state.session,
        "case_path": str(state.case_path),
        "case_sha256": state.case_sha256,
        "plan_sha256": state.plan_sha256,
        "graph_schema_version": state.graph_schema_version,
        "case_hash_mode": state.case_hash_mode,
        "repo": str(machine_f_root / "repo"),
        "worker_git_config": str(machine_f_root / "worker-gitconfig"),
        "remote": str(remote),
        "oracle": str(oracle) if state.runner_mode == "fake" else None,
        "output_dir": str(state.output_dir / "machine-f"),
        "exchange": str(exchange),
        "handoff": str(bundle_path),
        "inspection_contract": inspection_contract,
    }
    config_f_path = state.fixture_dir / "machine-f-config.json"
    _write_json(config_f_path, machine_f_config)
    process_f = _spawn_machine(config_f_path)
    if process_f.returncode != 0:
        raise Gate7Failure(
            "Machine F 未完成 CP03："
            f"returncode={process_f.returncode}, {_process_output_tail(process_f)}"
        )

    result_e = json.loads(
        (state.output_dir / "machine-e" / "machine-result.json").read_text(encoding="utf-8")
    )
    result_f = json.loads(
        (state.output_dir / "machine-f" / "machine-result.json").read_text(encoding="utf-8")
    )
    artifact_scans.append(
        _scan_artifacts(
            [source_canary, memory_canary, path_canary],
            [
                state.output_dir / "machine-f",
                machine_f_root / "repo",
                exchange,
            ],
        )
    )
    final_identity = _validate_final_remote(remote, result_e, result_f, case)
    metrics = _build_metrics(
        state,
        result_e,
        result_f,
        duplicate_claim_rejected,
        artifact_scans,
    )
    if metrics["automatic_retry_count"] != 0:
        raise Gate7Failure("automatic retry 不是 0")
    if metrics["provider_sessions_used"] != (
        PROVIDER_SESSION_HARD_LIMIT if state.runner_mode == "real" else 0
    ):
        raise Gate7Failure("provider session 数与冻结预算不一致")
    if metrics["canary_leak_count"] != 0:
        raise Gate7Failure("检测到 canary 泄漏")
    if metrics["sensitive_material_hit_count"] != 0:
        raise Gate7Failure("检测到敏感材料泄漏")
    if (
        state.runner_mode == "real"
        and state.graph_schema_version == "gate7-r4-v1"
        and not metrics["transcript_audit_passed"]
    ):
        raise Gate7Failure("真实 checkpoint transcript 审计未全部通过")

    coordinator_ledger.append(
        "arm_completed",
        operation_kind="control",
        payload={
            "final_commit": result_f["checkpoints"][-1]["commit_sha"],
            "metrics_sha256": _sha256_text(_canonical_json(metrics)),
        },
    )
    summary = {
        "schema_version": 1,
        "status": "success",
        "runner_mode": state.runner_mode,
        "engine": state.engine,
        "session": state.session,
        "case_sha256": state.case_sha256,
        "plan_sha256": state.plan_sha256,
        "case_hash_mode": state.case_hash_mode,
        "graph_schema_version": state.graph_schema_version,
        "auth_mode": state.auth_mode,
        "codex_version": CODEX_VERSION,
        "worker_multi_agent": (
            "disabled-by-cli"
            if state.runner_mode == "real"
            else "not-applicable"
        ),
        "baseline_sha": state.baseline_sha,
        "consumed_tag": state.consumed_tag,
        "single_host_dual_node_simulation": True,
        "physical_machine_migration_proven": False,
        "authority_claim": claim,
        "handoff_sha256": bundle["self_sha256"],
        "handoff_engine_state": bundle["engine_state"],
        "final_identity": final_identity,
        "artifact_scans": artifact_scans,
        "machine_e": result_e,
        "machine_f": result_f,
        "metrics": metrics,
    }
    _write_json(state.output_dir / "summary.json", summary)
    _write_text(state.output_dir / "report.md", _render_report(summary))
    print(_canonical_json({"status": "success", "summary": str(state.output_dir / "summary.json")}))


def _run_internal_machine(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    machine = config["machine"]
    if machine == "machine-e":
        return _run_machine_e(config)
    if machine == "machine-f":
        return _run_machine_f(config)
    raise SystemExit(f"未知 internal machine：{machine}")


def _run_machine_e(config: dict[str, Any]) -> int:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=False)
    repo = Path(config["repo"])
    remote = Path(config["remote"])
    exchange = Path(config["exchange"])
    _configure_worker_git_config(repo, Path(config["worker_git_config"]))
    case, case_sha256, plan_sha256 = _load_and_validate_case(
        Path(config["case_path"]),
        frozen_case_sha256=config["case_sha256"],
        frozen_plan_sha256=config["plan_sha256"],
        case_hash_mode=config["case_hash_mode"],
    )
    if case_sha256 != config["case_sha256"] or plan_sha256 != config["plan_sha256"]:
        raise Gate7Blocked("Machine E case/plan hash 漂移")

    ledger = EventLedger(
        output_dir / "events.jsonl",
        run_id=config["session"],
        engine=config["engine"],
    )
    budget = ProviderBudget(PROVIDER_SESSION_HARD_LIMIT)
    migration_id = f"migration-{uuid4().hex}"
    if config["engine"] == "langgraph":
        checkpoints, engine_state = _run_langgraph_source_checkpoints(
            case=case,
            config=config,
            repo=repo,
            remote=remote,
            output_dir=output_dir,
            exchange=exchange,
            ledger=ledger,
            budget=budget,
            migration_id=migration_id,
            case_sha256=case_sha256,
            plan_sha256=plan_sha256,
        )
    else:
        checkpoints = []
        for checkpoint in case["checkpoints"][:2]:
            evidence = _run_checkpoint(
                checkpoint=checkpoint,
                machine="machine-e",
                config=config,
                repo=repo,
                remote=remote,
                output_dir=output_dir,
                ledger=ledger,
                budget=budget,
            )
            checkpoints.append(evidence)
        engine_state = _seal_linear_cursor(
            exchange=exchange,
            session=config["session"],
            source_commit=checkpoints[-1].commit_sha,
            source_tree=checkpoints[-1].tree_sha,
            migration_id=migration_id,
            case_sha256=case_sha256,
            plan_sha256=plan_sha256,
            checkpoints=checkpoints,
        )

    if budget.used != 2:
        raise Gate7Failure("Machine E execution slot 数不等于 2")
    source_commit = checkpoints[-1].commit_sha
    source_tree = checkpoints[-1].tree_sha
    source_ref = checkpoints[-1].ref_name
    bundle_core = {
        "schema_version": 1,
        "case_id": case["case_identity"]["case_id"],
        "case_sha256": case_sha256,
        "plan_sha256": plan_sha256,
        "source_machine": "machine-e",
        "target_machine": "machine-f",
        "completed_checkpoints": ["CP01", "CP02"],
        "next_checkpoint": "CP03",
        "source_ref": source_ref,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "source_parent": checkpoints[-1].parent_sha,
        "checkpoint_evidence_sha256": [
            _sha256_text(_canonical_json(_checkpoint_payload(item))) for item in checkpoints
        ],
        "migration_id": migration_id,
        "source_closed": True,
        "engine_state": engine_state,
        "worker_context": {
            "goal": "完成 Flask teardown 错误聚合与上下文收口。",
            "completed": ["测试行为已冻结", "request/app teardown 聚合已验证"],
            "remaining": "仅执行 CP03 冻结范围与验证。",
        },
    }
    bundle = {**bundle_core, "self_sha256": _sha256_text(_canonical_json(bundle_core))}
    _write_json_exclusive(exchange / "handoff-v0001.json", bundle)
    ledger.append(
        "machine_e_closed",
        operation_kind="planned_migration",
        migration_id=migration_id,
        payload={
            "source_ref": source_ref,
            "source_commit": source_commit,
            "handoff_sha256": bundle["self_sha256"],
        },
    )
    result = {
        "machine": "machine-e",
        "status": "planned_migration",
        "execution_slots_used": budget.used,
        "provider_sessions_used": budget.used if config["runner_mode"] == "real" else 0,
        "checkpoints": [_checkpoint_payload(item) for item in checkpoints],
        "last_event_hash": ledger.read()[-1]["event_hash"],
        "migration_id": migration_id,
    }
    _write_json(output_dir / "machine-result.json", result)
    return PLANNED_MIGRATION_EXIT_CODE


def _run_machine_f(config: dict[str, Any]) -> int:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=False)
    remote = Path(config["remote"])
    repo = Path(config["repo"])
    bundle = _load_sealed_json(Path(config["handoff"]))
    case, case_sha256, plan_sha256 = _load_and_validate_case(
        Path(config["case_path"]),
        frozen_case_sha256=config["case_sha256"],
        frozen_plan_sha256=config["plan_sha256"],
        case_hash_mode=config["case_hash_mode"],
    )
    if case_sha256 != config["case_sha256"] or plan_sha256 != config["plan_sha256"]:
        raise Gate7Blocked("Machine F case/plan hash 漂移")
    _validate_handoff_bundle(
        bundle,
        case_sha256=case_sha256,
        plan_sha256=plan_sha256,
        remote=remote,
    )
    _clone_ref(remote, bundle["source_ref"], repo)
    _configure_fixture_repo(repo)
    _configure_worker_git_config(repo, Path(config["worker_git_config"]))
    if _git_output(repo, "rev-parse", "HEAD") != bundle["source_commit"]:
        raise Gate7Blocked("Machine F clone commit 与 handoff 不一致")
    if _git_output(repo, "rev-parse", "HEAD^{tree}") != bundle["source_tree"]:
        raise Gate7Blocked("Machine F clone tree 与 handoff 不一致")
    _sync_dependencies(repo, output_dir / "preflight-machine-f.json")

    ledger = EventLedger(
        output_dir / "events.jsonl",
        run_id=config["session"],
        engine=config["engine"],
    )
    ledger.append(
        "machine_f_reconciled",
        operation_kind="recovery",
        recovery_of_attempt_id=None,
        migration_id=bundle["migration_id"],
        payload={
            "source_commit": bundle["source_commit"],
            "source_tree": bundle["source_tree"],
            "handoff_sha256": bundle["self_sha256"],
        },
    )
    budget = ProviderBudget(1)
    if config["engine"] == "langgraph":
        engine_resume, evidence = _resume_langgraph_target_checkpoint(
            case=case,
            config=config,
            repo=repo,
            remote=remote,
            output_dir=output_dir,
            exchange=Path(config["exchange"]),
            ledger=ledger,
            budget=budget,
            bundle=bundle,
        )
    else:
        engine_resume = _resume_linear_cursor(
            exchange=Path(config["exchange"]),
            bundle=bundle,
        )
        checkpoint = case["checkpoints"][2]
        evidence = _run_checkpoint(
            checkpoint=checkpoint,
            machine="machine-f",
            config=config,
            repo=repo,
            remote=remote,
            output_dir=output_dir,
            ledger=ledger,
            budget=budget,
        )
        engine_resume["target_external_attempts"] = 1
    result = {
        "machine": "machine-f",
        "status": "success",
        "execution_slots_used": budget.used,
        "provider_sessions_used": budget.used if config["runner_mode"] == "real" else 0,
        "checkpoints": [_checkpoint_payload(evidence)],
        "last_event_hash": ledger.read()[-1]["event_hash"],
        "migration_id": bundle["migration_id"],
        "engine_resume": engine_resume,
    }
    _write_json(output_dir / "machine-result.json", result)
    return 0


def _run_checkpoint(
    *,
    checkpoint: dict[str, Any],
    machine: MachineName,
    config: dict[str, Any],
    repo: Path,
    remote: Path,
    output_dir: Path,
    ledger: EventLedger,
    budget: ProviderBudget,
) -> CheckpointEvidence:
    checkpoint_id = checkpoint["id"]
    attempt_id = f"{checkpoint_id.lower()}-{uuid4().hex}"
    logical_operation_id = f"{config['session']}:{checkpoint_id}"
    idempotency_key = _sha256_text(
        _canonical_json(
            {
                "case_sha256": config["case_sha256"],
                "plan_sha256": config["plan_sha256"],
                "checkpoint": checkpoint_id,
                "engine": config["engine"],
            }
        )
    )
    before_head = _git_output(repo, "rev-parse", "HEAD")
    before_paths = set(_changed_files(repo, BASE_SHA))
    before_fingerprint = _workspace_fingerprint(repo)
    before_outside_scope = _scope_outside_diff_digest(repo, checkpoint["scope"])
    before_guard = _git_guard_snapshot(repo)
    inspection_policy = _inspection_policy_from_config(
        config,
        checkpoint_id,
        expected_workdir=repo,
    )
    prompt = _build_worker_prompt(
        checkpoint,
        engine=config["engine"],
        machine=machine,
        handoff_path=Path(config["handoff"]) if machine == "machine-f" else None,
        inspection_policy=inspection_policy,
    )
    _assert_prompt_safe(prompt, config, repo)
    prompt_sha256 = _sha256_text(prompt)
    ledger.append(
        "checkpoint_started",
        operation_kind="attempt",
        step_id=checkpoint_id,
        attempt_id=attempt_id,
        logical_operation_id=logical_operation_id,
        idempotency_key=idempotency_key,
        migration_id=(
            _load_sealed_json(Path(config["handoff"]))["migration_id"]
            if machine == "machine-f"
            else None
        ),
        payload={
            "before_head": before_head,
            "before_workspace_fingerprint": before_fingerprint,
            "prompt_sha256": prompt_sha256,
            "scope": checkpoint["scope"],
        },
    )
    runner: CodexExecRunner | None = None
    if config["runner_mode"] == "real":
        runner = _build_real_runner(inspection_policy)
        _assert_multi_agent_disabled(
            runner.build_command(repo, "workspace-write")
        )
    budget.reserve(checkpoint_id)
    started = time.monotonic()
    execution_dir = output_dir / "executions" / checkpoint_id.lower()
    if runner is not None:
        result = runner.run(
            prompt,
            repo,
            sandbox="workspace-write",
            timeout_seconds=(
                inspection_policy["worker_timeout_seconds"]
                if inspection_policy is not None
                else WORKER_TIMEOUT_SECONDS
            ),
            execution_context=RunnerExecutionContext(
                execution_dir=execution_dir,
                run_id=config["session"],
                step=checkpoint_id,
                engine=config["engine"],
                graph_schema_version=config["graph_schema_version"],
                step_id=checkpoint_id,
                attempt_id=attempt_id,
                idempotency_key=idempotency_key,
                replay_class="external_non_replayable",
                base_head=before_head,
                before_workspace_fingerprint=before_fingerprint,
                input_fingerprint=prompt_sha256,
                exclusive_create=True,
                git_config_global=Path(config["worker_git_config"]),
            ),
        )
    else:
        oracle = Path(config["oracle"])
        result = DeterministicOracleRunner(oracle).run(checkpoint, repo)
    elapsed = time.monotonic() - started
    transcript_audit = (
        _audit_codex_transcript(result.output, inspection_policy)
        if runner is not None and inspection_policy is not None
        else _not_applicable_transcript_audit(result.output)
    )
    if runner is not None:
        _write_json(execution_dir / "transcript-audit.json", transcript_audit)
    if result.status != "success":
        ledger.append(
            "checkpoint_failed",
            operation_kind="terminal",
            step_id=checkpoint_id,
            attempt_id=attempt_id,
            logical_operation_id=logical_operation_id,
            idempotency_key=idempotency_key,
            payload={
                "runner_status": result.status,
                "runner_error": result.error,
                "output_sha256": _sha256_text(result.output),
                "transcript_audit_sha256": _sha256_text(
                    _canonical_json(transcript_audit)
                ),
                "transcript_audit_status": transcript_audit["status"],
            },
        )
        raise Gate7Failure(
            f"{checkpoint_id} worker 未成功：status={result.status}, error={result.error}"
        )
    if transcript_audit["status"] == "failed":
        ledger.append(
            "checkpoint_failed",
            operation_kind="terminal",
            step_id=checkpoint_id,
            attempt_id=attempt_id,
            logical_operation_id=logical_operation_id,
            idempotency_key=idempotency_key,
            payload={
                "runner_status": result.status,
                "runner_error": "transcript audit failed",
                "output_sha256": _sha256_text(result.output),
                "transcript_audit_sha256": _sha256_text(
                    _canonical_json(transcript_audit)
                ),
                "transcript_audit_status": transcript_audit["status"],
            },
        )
        raise Gate7Failure(
            f"{checkpoint_id} transcript 审计失败："
            f"{'; '.join(transcript_audit['violations'])}"
        )
    _assert_output_safe(result.output, config, repo)
    _validate_real_runner_output(result, config["runner_mode"])
    _assert_worker_git_guard(repo, before_guard)
    after_outside_scope = _scope_outside_diff_digest(repo, checkpoint["scope"])
    if after_outside_scope != before_outside_scope:
        raise Gate7Failure(f"{checkpoint_id} 修改了当前 scope 之外的既有 diff")

    changed_files = _changed_files(repo, BASE_SHA)
    expected_paths = set(checkpoint["scope"])
    new_paths = set(changed_files) - before_paths
    if new_paths != expected_paths:
        raise Gate7Failure(
            f"{checkpoint_id} scope 不精确：expected={sorted(expected_paths)}, "
            f"observed={sorted(new_paths)}"
        )
    _assert_no_untracked_files(repo)
    verification = _verify_checkpoint(repo, checkpoint_id)
    diff_lines = _diff_line_count(repo, BASE_SHA)
    if diff_lines > 450:
        raise Gate7Failure(f"{checkpoint_id} 累计 diff lines 超过 450：{diff_lines}")

    _git(
        repo,
        "add",
        "--",
        *checkpoint["scope"],
    )
    _git(
        repo,
        "-c",
        "user.email=gate7@example.invalid",
        "-c",
        "user.name=Gate 7 Harness",
        "commit",
        "-m",
        f"gate7: complete {checkpoint_id}",
    )
    commit_sha = _git_output(repo, "rev-parse", "HEAD")
    tree_sha = _git_output(repo, "rev-parse", "HEAD^{tree}")
    parent_sha = _git_output(repo, "rev-parse", "HEAD^")
    ref_name = f"refs/heads/gate7/{config['session']}/{config['engine']}/{checkpoint_id.lower()}"
    _push_exact_ref(repo, remote, ref_name, expected_old=None)
    output_sha256 = _sha256_text(result.output)
    tokens_used = _parse_tokens_used(result.output)
    ledger.append(
        "checkpoint_completed",
        operation_kind="terminal",
        step_id=checkpoint_id,
        attempt_id=attempt_id,
        logical_operation_id=logical_operation_id,
        idempotency_key=idempotency_key,
        payload={
            "commit_sha": commit_sha,
            "tree_sha": tree_sha,
            "parent_sha": parent_sha,
            "ref_name": ref_name,
            "changed_files": changed_files,
            "diff_lines": diff_lines,
            "verification_sha256": _sha256_text(_canonical_json(verification)),
            "output_sha256": output_sha256,
            "tokens_used": tokens_used,
            "transcript_audit_sha256": _sha256_text(
                _canonical_json(transcript_audit)
            ),
            "transcript_audit_status": transcript_audit["status"],
        },
    )
    return CheckpointEvidence(
        checkpoint=checkpoint_id,
        machine=machine,
        attempt_id=attempt_id,
        logical_operation_id=logical_operation_id,
        prompt_sha256=prompt_sha256,
        output_sha256=output_sha256,
        tokens_used=tokens_used,
        changed_files=changed_files,
        diff_lines=diff_lines,
        commit_sha=commit_sha,
        tree_sha=tree_sha,
        parent_sha=parent_sha,
        ref_name=ref_name,
        verification=verification,
        elapsed_seconds=elapsed,
        transcript_audit=transcript_audit,
    )


def _build_worker_prompt(
    checkpoint: dict[str, Any],
    *,
    engine: EngineName,
    machine: MachineName,
    handoff_path: Path | None,
    inspection_policy: dict[str, Any] | None = None,
) -> str:
    scope = "\n".join(f"- {path}" for path in checkpoint["scope"])
    acceptance = "\n".join(f"- {item}" for item in checkpoint["acceptance"])
    instructions = "\n".join(f"- {item}" for item in checkpoint["instructions"])
    lines = [
        "你正在执行一个预注册的大任务 checkpoint。",
        "checkpoint 计划由人类冻结，是输入，不允许重排、合并、拆分或扩大。",
        f"machine label: {machine}",
        f"checkpoint: {checkpoint['id']} / {checkpoint['name']}",
        f"goal: {checkpoint['goal']}",
        "",
        "阶段实现职责：",
        instructions,
        "",
        "只允许修改以下路径，必须让每个路径都产生有意义变更：",
        scope,
        "",
        "验收合同：",
        acceptance,
    ]
    if inspection_policy is not None:
        hints = "\n".join(f"- {item}" for item in inspection_policy["hints"])
        allowed_paths = "\n".join(
            f"- {item}"
            for item in inspection_policy["allowed_read_paths"]
        )
        lines.extend(
            [
                "",
                "有界检查合同（与执行引擎无关，违反即判本 checkpoint 失败）：",
                f"- 首次编辑前最多 {inspection_policy['max_tool_waves']} 个工具波次、"
                f"{inspection_policy['max_exec_commands']} 次 exec。",
                f"- 每个 Get-Content 片段最多 {inspection_policy['max_read_lines']} 行；"
                "禁止无界整文件读取。",
                f"- rg 必须显式使用 --max-count，且每文件最多 "
                f"{inspection_policy['max_rg_matches_per_file']} 条命中。",
                f"- 单次命令输出最多 "
                f"{inspection_policy['max_single_command_output_bytes']} 字节；"
                f"累计命令输出最多 "
                f"{inspection_policy['max_cumulative_command_output_bytes']} 字节。",
                f"- 完整 transcript 最多 "
                f"{inspection_policy['max_transcript_bytes']} 字节；"
                f"总 token 最多 {inspection_policy['max_tokens_used']}。",
                "- exec 只允许独立的 git status、带上限的 rg、带上限的 "
                "Get-Content；禁止 Python introspection、递归 Select-String 和写文件命令。",
                "- 每次 exec 必须严格匹配以下一种模板，不得追加其他命令或参数：",
                "  git status --short",
                "  rg -n --max-count <N> -e '<pattern>' -- <registered paths>",
                "  Get-Content <registered path> -First <N>",
                "  Get-Content <registered path> | Select-Object -Skip <N> -First <N>",
                "- 相同或等价查询不得重跑；rg exit 1 且无匹配是正常空结果，不得重试。",
                "- 第二个工具波次结束后必须立即使用 apply_patch 编辑，或明确停止为证据不足。",
                "- 修改只能使用 apply_patch；不得通过 exec、重定向、Set-Content 或脚本写文件。",
                "",
                "人工预注册定位提示（只缩小检查范围，不包含官方 diff 或实现答案）：",
                hints,
                "",
                "只允许读取以下已注册路径：",
                allowed_paths,
            ]
        )
    reading_rule = (
        "- 先按有界检查合同阅读当前仓库代码和测试，再实现本 checkpoint。"
        if inspection_policy is not None
        else "- 先阅读当前仓库代码和测试，再实现本 checkpoint。"
    )
    lines.extend(
        [
        "",
        "执行规则：",
        reading_rule,
        "- 不运行 git commit、git push、git tag、git reset、git checkout。",
        "- 不访问网络，不安装或更新依赖，不读取仓库外文件。",
        "- 不调用或尝试调用多代理、collab、sub-agent 或委派工具。",
        "- 不创建 AGENTS.md、任务文档、handoff、memory 或额外测试文件。",
        "- 不读取官方 PR、merge commit、gold diff 或 oracle。",
        "- 完成代码改动后停止；验证由 coordinator 单独执行。",
        ]
    )
    if handoff_path is not None:
        lines.extend(
            [
                "",
                "你是 planned migration 后的新会话。",
                "上一个节点的私有聊天、memory 和工作目录不可用。",
                "跨节点状态只来自当前 Git checkout 和 coordinator 已验证的封存 handoff 摘要。",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _verify_checkpoint(repo: Path, checkpoint_id: str) -> list[dict[str, Any]]:
    evidence: list[CommandEvidence] = []
    if checkpoint_id in {"CP01", "CP02"}:
        robust = _run_command(
            [
                "uv",
                "run",
                "--offline",
                "--locked",
                "pytest",
                "-q",
                "tests/test_appctx.py::test_robust_teardown",
            ],
            cwd=repo,
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        if robust.returncode == 0:
            raise Gate7Failure(f"{checkpoint_id} robust regression 应失败但通过")
        if "test_robust_teardown" not in robust.output:
            raise Gate7Failure(f"{checkpoint_id} robust regression 失败身份不明确")
        evidence.append(robust)
        remaining = _run_command(
            [
                "uv",
                "run",
                "--offline",
                "--locked",
                "pytest",
                "-q",
                "-k",
                "not test_robust_teardown",
            ],
            cwd=repo,
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        _require_command(
            remaining,
            returncode=0,
            contains="494 passed, 1 deselected",
            label=f"{checkpoint_id} remaining suite",
        )
        evidence.append(remaining)
        if checkpoint_id == "CP02":
            probe = _run_command(
                ["uv", "run", "--offline", "--locked", "python", "-"],
                cwd=repo,
                timeout=VERIFY_TIMEOUT_SECONDS,
                input_text=CP02_BEHAVIOR_PROBE,
            )
            _require_command(
                probe,
                returncode=0,
                contains="GATE7_CP02_BEHAVIOR_PASS",
                label="CP02 direct behavior probe",
            )
            evidence.append(probe)
    elif checkpoint_id == "CP03":
        robust = _run_command(
            [
                "uv",
                "run",
                "--offline",
                "--locked",
                "pytest",
                "-q",
                "tests/test_appctx.py::test_robust_teardown",
            ],
            cwd=repo,
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        _require_command(robust, returncode=0, contains="1 passed", label="CP03 robust")
        evidence.append(robust)
        full = _run_command(
            ["uv", "run", "--offline", "--locked", "pytest", "-q"],
            cwd=repo,
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
        _require_command(full, returncode=0, contains="495 passed", label="CP03 full suite")
        evidence.append(full)
    else:
        raise Gate7Failure(f"未知 checkpoint：{checkpoint_id}")
    return [
        {
            "command": item.command,
            "returncode": item.returncode,
            "output_sha256": item.output_sha256,
            "elapsed_seconds": round(item.elapsed_seconds, 3),
            "summary": _last_nonempty_line(item.output),
        }
        for item in evidence
    ]


def _inspection_contract_from_case(case: dict[str, Any]) -> dict[str, Any] | None:
    if case.get("schema_version") != 2:
        return None
    r2_contract = case.get("r2_contract")
    if not isinstance(r2_contract, dict):
        return None
    inspection_contract = r2_contract.get("inspection_contract")
    if inspection_contract is None:
        return None
    _validate_inspection_contract(inspection_contract)
    return json.loads(json.dumps(inspection_contract, ensure_ascii=False))


def _inspection_policy_from_config(
    config: dict[str, Any],
    checkpoint_id: str,
    *,
    expected_workdir: Path | str | None = None,
) -> dict[str, Any] | None:
    contract = config.get("inspection_contract")
    if contract is None:
        return None
    _validate_inspection_contract(contract)
    policy = {
        key: value
        for key, value in contract.items()
        if key not in {"checkpoint_hints", "checkpoint_read_paths"}
    }
    policy["hints"] = list(contract["checkpoint_hints"][checkpoint_id])
    policy["allowed_read_paths"] = list(
        contract["checkpoint_read_paths"][checkpoint_id]
    )
    if expected_workdir is not None:
        normalized_workdir = _normalize_exec_workdir(str(expected_workdir))
        if normalized_workdir is None:
            raise Gate7Blocked("Gate 7 R4 expected workdir 非法")
        policy["expected_workdir"] = normalized_workdir
    return policy


def _validate_inspection_contract(contract: Any) -> None:
    if not isinstance(contract, dict):
        raise Gate7Blocked("Gate 7 R4 inspection contract 必须是对象")
    expected_keys = {
        "schema_version",
        "parser_version",
        "worker_timeout_seconds",
        "max_tool_waves",
        "max_exec_commands",
        "max_read_lines",
        "max_rg_matches_per_file",
        "max_single_command_output_bytes",
        "max_cumulative_command_output_bytes",
        "max_transcript_bytes",
        "max_tokens_used",
        "max_duplicate_commands",
        "cli",
        "checkpoint_hints",
        "checkpoint_read_paths",
    }
    if set(contract) != expected_keys:
        raise Gate7Blocked("Gate 7 R4 inspection contract 字段漂移")
    if contract["schema_version"] != 1:
        raise Gate7Blocked("Gate 7 R4 inspection contract schema 漂移")
    if contract["parser_version"] != TRANSCRIPT_AUDIT_PARSER_VERSION:
        raise Gate7Blocked("Gate 7 R4 transcript parser version 漂移")
    numeric_ranges = {
        "worker_timeout_seconds": (60, WORKER_TIMEOUT_SECONDS),
        "max_tool_waves": (1, 4),
        "max_exec_commands": (1, 20),
        "max_read_lines": (1, 200),
        "max_rg_matches_per_file": (1, 100),
        "max_single_command_output_bytes": (1024, 32768),
        "max_cumulative_command_output_bytes": (4096, 131072),
        "max_transcript_bytes": (8192, 262144),
        "max_tokens_used": (1000, 100000),
        "max_duplicate_commands": (0, 0),
    }
    for key, (minimum, maximum) in numeric_ranges.items():
        value = contract.get(key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise Gate7Blocked(f"Gate 7 R4 inspection contract 数值非法：{key}")
    if (
        contract["max_single_command_output_bytes"]
        > contract["max_cumulative_command_output_bytes"]
        or contract["max_cumulative_command_output_bytes"]
        > contract["max_transcript_bytes"]
    ):
        raise Gate7Blocked("Gate 7 R4 output budget 层级非法")
    cli = contract.get("cli")
    if not isinstance(cli, dict) or set(cli) != {
        "tool_output_token_limit",
        "model_verbosity",
        "model_reasoning_summary",
    }:
        raise Gate7Blocked("Gate 7 R4 Codex CLI 减量配置漂移")
    if (
        type(cli["tool_output_token_limit"]) is not int
        or not 256 <= cli["tool_output_token_limit"] <= 8192
        or cli["model_verbosity"] != "low"
        or cli["model_reasoning_summary"] != "none"
    ):
        raise Gate7Blocked("Gate 7 R4 Codex CLI 减量配置非法")
    checkpoint_hints = contract.get("checkpoint_hints")
    if not isinstance(checkpoint_hints, dict) or set(checkpoint_hints) != {
        "CP01",
        "CP02",
        "CP03",
    }:
        raise Gate7Blocked("Gate 7 R4 checkpoint hints 不完整")
    for checkpoint_id, hints in checkpoint_hints.items():
        if (
            not isinstance(hints, list)
            or not 1 <= len(hints) <= 12
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 300
                for item in hints
            )
        ):
            raise Gate7Blocked(f"Gate 7 R4 checkpoint hint 非法：{checkpoint_id}")
    checkpoint_read_paths = contract.get("checkpoint_read_paths")
    if not isinstance(checkpoint_read_paths, dict) or set(checkpoint_read_paths) != {
        "CP01",
        "CP02",
        "CP03",
    }:
        raise Gate7Blocked("Gate 7 R4 checkpoint read paths 不完整")
    for checkpoint_id, paths in checkpoint_read_paths.items():
        if (
            not isinstance(paths, list)
            or not paths
            or len(paths) != len(set(paths))
            or any(
                not isinstance(item, str)
                or not item
                or Path(item).is_absolute()
                or ".." in PurePosixPath(item).parts
                for item in paths
            )
        ):
            raise Gate7Blocked(f"Gate 7 R4 checkpoint read path 非法：{checkpoint_id}")


def _load_case_document(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    schema_version = document.get("schema_version")
    if schema_version == 1:
        return document
    if schema_version != 2:
        raise Gate7Blocked("Gate 7 case schema_version 只能是 1 或 2")

    allowed_keys = {
        "schema_version",
        "base_case",
        "case_identity",
        "provider_budget",
        "topology",
        "r2_contract",
    }
    if set(document) - allowed_keys:
        raise Gate7Blocked("Gate 7 R2 overlay 含未注册字段")
    base_ref = document.get("base_case")
    if not isinstance(base_ref, str):
        raise Gate7Blocked("Gate 7 R2 overlay 缺少 base_case")
    base_path = (path.parent / base_ref).resolve()
    try:
        base_path.relative_to(path.parent.resolve())
    except ValueError as exc:
        raise Gate7Blocked("Gate 7 R2 base_case 越界") from exc
    if base_path == path.resolve():
        raise Gate7Blocked("Gate 7 R2 base_case 不能自引用")
    base = json.loads(base_path.read_text(encoding="utf-8"))
    if base.get("schema_version") != 1:
        raise Gate7Blocked("Gate 7 R2 base_case 必须是 schema_version 1")

    resolved = json.loads(json.dumps(base, ensure_ascii=False))
    resolved["schema_version"] = 2
    for section in ("case_identity", "provider_budget"):
        overrides = document.get(section, {})
        if not isinstance(overrides, dict):
            raise Gate7Blocked(f"Gate 7 R2 {section} override 必须是对象")
        resolved[section].update(overrides)
    topology_overrides = document.get("topology", {})
    if not isinstance(topology_overrides, dict):
        raise Gate7Blocked("Gate 7 R2 topology override 必须是对象")
    for key, overrides in topology_overrides.items():
        if not isinstance(overrides, dict):
            raise Gate7Blocked(f"Gate 7 R2 topology.{key} override 必须是对象")
        current = resolved["topology"].get(key)
        if current is not None and not isinstance(current, dict):
            raise Gate7Blocked(f"Gate 7 base topology.{key} 不是对象")
        resolved["topology"][key] = {**(current or {}), **overrides}
    r2_contract = document.get("r2_contract")
    if not isinstance(r2_contract, dict):
        raise Gate7Blocked("Gate 7 R2 overlay 缺少 r2_contract")
    resolved["r2_contract"] = r2_contract
    return resolved


def _load_and_validate_case(
    path: Path = CASE_PATH,
    *,
    frozen_case_sha256: str = FROZEN_CASE_SHA256,
    frozen_plan_sha256: str = FROZEN_PLAN_SHA256,
    case_hash_mode: CaseHashMode = "raw-bytes",
) -> tuple[dict[str, Any], str, str]:
    raw = path.read_bytes()
    case = _load_case_document(path)
    if case_hash_mode == "raw-bytes":
        case_sha256 = _sha256_bytes(raw)
    elif case_hash_mode == "canonical-json":
        case_sha256 = _sha256_text(_canonical_json(case))
    else:
        raise Gate7Blocked(f"未知 case hash mode：{case_hash_mode}")
    if frozen_case_sha256 and case_sha256 != frozen_case_sha256:
        raise Gate7Blocked(
            "冻结 case SHA-256 漂移："
            f"expected={frozen_case_sha256}, observed={case_sha256}"
        )
    if case["schema_version"] not in {1, 2}:
        raise Gate7Blocked("Gate 7 case schema_version 只能是 1 或 2")
    identity = case["case_identity"]
    expected_identity = {
        "base": BASE_SHA,
        "merge": MERGE_SHA,
        "diff_sha256": ORACLE_DIFF_SHA256,
        "changed_files": 10,
    }
    for key, expected in expected_identity.items():
        if identity.get(key) != expected:
            raise Gate7Blocked(f"case identity 漂移：{key}")
    for key in ("case_id", "real_session", "langgraph_session"):
        value = identity.get(key)
        if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
            raise Gate7Blocked(f"case identity 非法：{key}")
    if identity["real_session"] == identity["langgraph_session"]:
        raise Gate7Blocked("linear 与 LangGraph real session 不得相同")
    for engine in ("linear", "langgraph"):
        _baseline_tag_from_case(case, engine)
        _consumed_tag_from_case(case, engine)
    if case["schema_version"] == 2:
        contract = case.get("r2_contract")
        expected_contract = {
            "v1_case_immutable": True,
            "case_hash_mode": "canonical-json",
            "worker_multi_agent": "disabled-by-cli",
            "provider_calls_before_consumed_claim": 0,
        }
        if not isinstance(contract, dict):
            raise Gate7Blocked("Gate 7 R2 contract 缺失")
        for key, expected in expected_contract.items():
            if contract.get(key) != expected:
                raise Gate7Blocked(f"Gate 7 R2 contract 漂移：{key}")
        graph_schema_version = contract.get("graph_schema_version")
        if graph_schema_version not in {
            "gate7-r2-v1",
            "gate7-r3-v1",
            "gate7-r4-v1",
        }:
            raise Gate7Blocked("Gate 7 overlay graph_schema_version 未注册")
        inspection_contract = contract.get("inspection_contract")
        if graph_schema_version == "gate7-r4-v1":
            if contract.get("repair_revision") != "r4-bounded-inspection":
                raise Gate7Blocked("Gate 7 R4 repair revision 漂移")
            _validate_inspection_contract(inspection_contract)
        elif inspection_contract is not None:
            raise Gate7Blocked("只有 Gate 7 R4 可以声明 inspection contract")
    exact_files = case["exact_files"]["files"]
    if len(exact_files) != 10 or len(set(exact_files)) != 10:
        raise Gate7Blocked("Gate 7 exact_files 必须恰好 10 个且不重复")
    checkpoints = case["checkpoints"]
    if [item["id"] for item in checkpoints] != ["CP01", "CP02", "CP03"]:
        raise Gate7Blocked("Gate 7 checkpoint 顺序漂移")
    flattened = [path for item in checkpoints for path in item["scope"]]
    if flattened != exact_files:
        raise Gate7Blocked("checkpoint scope 拼接必须与 exact_files 完全一致")
    plan_fields = [
        "id",
        "name",
        "goal",
        "instructions",
        "scope",
        "acceptance",
    ]
    if case["schema_version"] == 2:
        plan_fields.extend(["expected_state", "verification"])
    plan = {
        "case_id": identity["case_id"],
        "checkpoint_ids": [item["id"] for item in checkpoints],
        "checkpoints": [
            {
                key: item[key]
                for key in plan_fields
            }
            for item in checkpoints
        ],
    }
    plan_sha256 = _sha256_text(_canonical_json(plan))
    if frozen_plan_sha256 and plan_sha256 != frozen_plan_sha256:
        raise Gate7Blocked(
            f"冻结 plan SHA-256 漂移：expected={frozen_plan_sha256}, "
            f"observed={plan_sha256}"
        )
    return case, case_sha256, plan_sha256


def _prepare_source_remote(remote: Path) -> None:
    if remote.exists():
        raise Gate7Blocked("source bare remote 已存在")
    remote.parent.mkdir(parents=True, exist_ok=True)
    _run_checked(["git", "init", "--bare", str(remote)], cwd=remote.parent, timeout=30)
    _run_checked(
        [
            "git",
            f"--git-dir={remote}",
            "fetch",
            "--depth=1",
            "--no-tags",
            UPSTREAM_URL,
            f"{BASE_SHA}:refs/heads/base",
        ],
        cwd=remote.parent,
        timeout=120,
    )
    if _git_dir_output(remote, "rev-parse", "refs/heads/base") != BASE_SHA:
        raise Gate7Blocked("source bare remote 未固定到预注册 base")
    oracle_probe = subprocess.run(
        ["git", f"--git-dir={remote}", "cat-file", "-e", f"{MERGE_SHA}^{{commit}}"],
        cwd=remote.parent,
        capture_output=True,
        timeout=30,
    )
    if oracle_probe.returncode == 0:
        raise Gate7Blocked("source bare remote 意外包含 oracle commit")


def _prepare_oracle_remote(oracle: Path) -> None:
    if oracle.exists():
        raise Gate7Blocked("oracle bare remote 已存在")
    _run_checked(["git", "init", "--bare", str(oracle)], cwd=oracle.parent, timeout=30)
    _run_checked(
        [
            "git",
            f"--git-dir={oracle}",
            "fetch",
            "--depth=2",
            "--no-tags",
            UPSTREAM_URL,
            f"{MERGE_SHA}:refs/heads/oracle",
        ],
        cwd=oracle.parent,
        timeout=120,
    )
    diff = subprocess.run(
        [
            "git",
            f"--git-dir={oracle}",
            "diff",
            "--binary",
            "--full-index",
            BASE_SHA,
            MERGE_SHA,
        ],
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout
    if _sha256_bytes(diff) != ORACLE_DIFF_SHA256:
        raise Gate7Blocked("fake oracle diff SHA-256 与预注册不一致")


def _clone_ref(remote: Path, ref: str, destination: Path) -> None:
    if destination.exists():
        raise Gate7Blocked(f"clone destination 已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_checked(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            str(remote),
            str(destination),
        ],
        cwd=destination.parent,
        timeout=120,
    )
    _git(destination, "config", "core.autocrlf", "false")
    _git(destination, "config", "core.eol", "lf")
    resolved = _git_dir_output(remote, "rev-parse", ref)
    _git(destination, "checkout", "--detach", resolved)


def _configure_fixture_repo(repo: Path) -> None:
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "core.eol", "lf")
    _git(repo, "config", "fetch.prune", "true")
    if _git_output(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise Gate7Blocked("fixture clone 初始工作树不干净")


def _configure_worker_git_config(repo: Path, config_path: Path) -> None:
    """为 Codex worker 创建只包含当前 fixture safe.directory 的临时配置。"""

    repo = repo.resolve()
    config_path = config_path.resolve()
    expected_path = repo.as_posix()
    expected_text = f"[safe]\n\tdirectory = {expected_path}\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        if config_path.is_symlink() or config_path.read_text(encoding="utf-8") != expected_text:
            raise Gate7Blocked("worker Git 临时配置已存在但内容不一致")
    else:
        with config_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(expected_text)

    environment = os.environ.copy()
    environment["GIT_CONFIG_GLOBAL"] = str(config_path)
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if status.returncode != 0:
        raise Gate7Blocked(
            "worker Git safe.directory 配置未生效："
            f"stdout={status.stdout[-500:]}, stderr={status.stderr[-500:]}"
        )
    configured = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if configured.returncode != 0 or configured.stdout.splitlines() != [expected_path]:
        raise Gate7Blocked("worker Git safe.directory 配置内容不唯一")


def _sync_dependencies(repo: Path, evidence_path: Path) -> None:
    command = _run_command(
        ["uv", "sync", "--offline", "--locked", "--link-mode=copy"],
        cwd=repo,
        timeout=DEPENDENCY_SYNC_TIMEOUT_SECONDS,
    )
    if command.returncode != 0:
        command = _run_command(
            ["uv", "sync", "--locked", "--link-mode=copy"],
            cwd=repo,
            timeout=DEPENDENCY_SYNC_TIMEOUT_SECONDS,
        )
    _require_command(command, returncode=0, label="uv sync")
    _write_json(
        evidence_path,
        {
            "command": command.command,
            "returncode": command.returncode,
            "output_sha256": command.output_sha256,
            "elapsed_seconds": round(command.elapsed_seconds, 3),
        },
    )


def _claim_authority(
    remote: Path,
    repo: Path,
    session: str,
    engine: EngineName,
) -> dict[str, str]:
    claim_ref = f"refs/tags/gate7-claims/{session}/{engine}"
    _git(
        repo,
        "-c",
        "user.email=gate7@example.invalid",
        "-c",
        "user.name=Gate 7 Harness",
        "tag",
        "-a",
        f"gate7-claim-{engine}",
        "-m",
        f"consume Gate 7 {engine} arm for {session}",
        BASE_SHA,
    )
    tag_object = _git_output(repo, "rev-parse", f"refs/tags/gate7-claim-{engine}")
    push = subprocess.run(
        [
            "git",
            "push",
            str(remote),
            f"{tag_object}:{claim_ref}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if push.returncode != 0:
        raise Gate7Blocked(f"无法创建共享 authority claim：{push.stderr.strip()}")
    observed = _git_dir_output(remote, "rev-parse", claim_ref)
    if observed != tag_object:
        raise Gate7Failure("共享 authority claim object 不一致")
    return {"ref": claim_ref, "commit": tag_object}


def _assert_duplicate_claim_rejected(
    remote: Path,
    repo: Path,
    session: str,
    engine: EngineName,
) -> bool:
    duplicate_tag = f"gate7-claim-duplicate-{engine}"
    _git(
        repo,
        "-c",
        "user.email=gate7@example.invalid",
        "-c",
        "user.name=Gate 7 Harness",
        "tag",
        "-a",
        duplicate_tag,
        "-m",
        "duplicate claim must fail",
        BASE_SHA,
    )
    duplicate_object = _git_output(repo, "rev-parse", f"refs/tags/{duplicate_tag}")
    claim_ref = f"refs/tags/gate7-claims/{session}/{engine}"
    push = subprocess.run(
        ["git", "push", str(remote), f"{duplicate_object}:{claim_ref}"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return push.returncode != 0


def _push_exact_ref(
    repo: Path,
    remote: Path,
    ref_name: str,
    *,
    expected_old: str | None,
) -> None:
    if expected_old is not None:
        observed = _git_dir_output(remote, "rev-parse", "--verify", ref_name)
        if observed != expected_old:
            raise Gate7Failure(f"remote ref 前置 SHA 漂移：{ref_name}")
    push = subprocess.run(
        ["git", "push", str(remote), f"HEAD:{ref_name}"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if push.returncode != 0:
        raise Gate7Failure(f"push checkpoint ref 失败：{push.stderr.strip()}")
    observed = _git_dir_output(remote, "rev-parse", ref_name)
    expected = _git_output(repo, "rev-parse", "HEAD")
    if observed != expected:
        raise Gate7Failure(f"remote ref 未绑定 checkpoint commit：{ref_name}")


def _validate_handoff_bundle(
    bundle: dict[str, Any],
    *,
    case_sha256: str,
    plan_sha256: str,
    remote: Path,
) -> None:
    self_sha256 = bundle.get("self_sha256")
    core = {key: value for key, value in bundle.items() if key != "self_sha256"}
    if self_sha256 != _sha256_text(_canonical_json(core)):
        raise Gate7Blocked("handoff self SHA-256 无效")
    if bundle.get("case_sha256") != case_sha256:
        raise Gate7Blocked("handoff case SHA-256 漂移")
    if bundle.get("plan_sha256") != plan_sha256:
        raise Gate7Blocked("handoff plan SHA-256 漂移")
    if bundle.get("source_machine") != "machine-e":
        raise Gate7Blocked("handoff source machine 错误")
    if bundle.get("target_machine") != "machine-f":
        raise Gate7Blocked("handoff target machine 错误")
    if bundle.get("completed_checkpoints") != ["CP01", "CP02"]:
        raise Gate7Blocked("handoff completed checkpoint 漂移")
    observed = _git_dir_output(remote, "rev-parse", bundle["source_ref"])
    if observed != bundle["source_commit"]:
        raise Gate7Blocked("handoff ref/commit 对账失败")
    observed_tree = _git_dir_output(remote, "rev-parse", f"{observed}^{{tree}}")
    if observed_tree != bundle["source_tree"]:
        raise Gate7Blocked("handoff commit/tree 对账失败")
    engine_state = bundle.get("engine_state")
    if not isinstance(engine_state, dict):
        raise Gate7Blocked("handoff 缺少 engine state")
    expected_kind = (
        "langgraph-sqlite"
        if engine_state.get("engine") == "langgraph"
        else "linear-json"
    )
    if engine_state.get("kind") != expected_kind:
        raise Gate7Blocked("handoff engine state kind 不一致")
    if engine_state.get("phase") != "cp02_completed":
        raise Gate7Blocked("handoff engine state phase 不一致")
    if engine_state.get("next") != ["cp03"]:
        raise Gate7Blocked("handoff engine state next node 不一致")
    if (
        engine_state.get("checkpoint_evidence_sha256")
        != bundle.get("checkpoint_evidence_sha256")
    ):
        raise Gate7Blocked("handoff engine state evidence hash 不一致")


def _seal_linear_cursor(
    *,
    exchange: Path,
    session: str,
    source_commit: str,
    source_tree: str,
    migration_id: str,
    case_sha256: str,
    plan_sha256: str,
    checkpoints: list[CheckpointEvidence],
) -> dict[str, Any]:
    del session
    payloads = [_checkpoint_payload(item) for item in checkpoints]
    if [item["checkpoint"] for item in payloads] != ["CP01", "CP02"]:
        raise Gate7Failure("linear cursor checkpoint evidence 不完整")
    core = {
        "schema_version": 1,
        "engine": "linear",
        "phase": "cp02_completed",
        "completed_checkpoints": ["CP01", "CP02"],
        "checkpoint_evidence": payloads,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "migration_id": migration_id,
        "case_sha256": case_sha256,
        "plan_sha256": plan_sha256,
    }
    cursor = {**core, "self_sha256": _sha256_text(_canonical_json(core))}
    relative = PurePosixPath("engine-state", "linear-cursor.json").as_posix()
    path = exchange / Path(relative)
    _write_json_exclusive(path, cursor)
    return {
        "engine": "linear",
        "kind": "linear-json",
        "cursor_ref": relative,
        "cursor_sha256": _sha256_bytes(path.read_bytes()),
        "state_bytes": path.stat().st_size,
        "checkpoint_count": 0,
        "checkpoint_evidence_sha256": [
            _sha256_text(_canonical_json(item)) for item in payloads
        ],
        "phase": "cp02_completed",
        "next": ["cp03"],
    }


def _resume_linear_cursor(
    *,
    exchange: Path,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    expected = bundle["engine_state"]
    if expected.get("engine") != "linear":
        raise Gate7Blocked("linear cursor 与执行 engine 不一致")
    cursor_ref = _safe_relative_ref(expected["cursor_ref"])
    path = exchange / cursor_ref
    if _sha256_bytes(path.read_bytes()) != expected["cursor_sha256"]:
        raise Gate7Blocked("linear cursor 文件 hash 漂移")
    cursor = _load_sealed_json(path)
    self_sha256 = cursor.pop("self_sha256")
    if self_sha256 != _sha256_text(_canonical_json(cursor)):
        raise Gate7Blocked("linear cursor self hash 无效")
    _assert_cursor_matches_bundle(cursor, bundle)
    return {
        "engine": "linear",
        "before_phase": cursor["phase"],
        "after_phase": "cp03_authorized",
        "recovery_elapsed_seconds": round(time.monotonic() - started, 6),
        "state_bytes_before": expected["state_bytes"],
        "state_bytes_after": expected["state_bytes"],
        "checkpoint_count_before": 0,
        "checkpoint_count_after": 0,
        "resume_external_attempts": 0,
        "replayed_external_attempts": 0,
    }


def _run_langgraph_source_checkpoints(
    *,
    case: dict[str, Any],
    config: dict[str, Any],
    repo: Path,
    remote: Path,
    output_dir: Path,
    exchange: Path,
    ledger: EventLedger,
    budget: ProviderBudget,
    migration_id: str,
    case_sha256: str,
    plan_sha256: str,
) -> tuple[list[CheckpointEvidence], dict[str, Any]]:
    run_dir = (exchange / "engine-state" / f"{config['session']}-langgraph").resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        from vega.loop_graph_checkpoint import (
            checkpoint_config,
            open_sqlite_checkpointer,
            validate_checkpoint_manifest,
        )
    except ModuleNotFoundError as exc:
        raise Gate7Blocked(
            "LangGraph arm 需要 langgraph 与 langgraph-checkpoint-sqlite"
        ) from exc

    def execute(checkpoint: dict[str, Any]) -> CheckpointEvidence:
        return _run_checkpoint(
            checkpoint=checkpoint,
            machine="machine-e",
            config=config,
            repo=repo,
            remote=remote,
            output_dir=output_dir,
            ledger=ledger,
            budget=budget,
        )

    initial: Gate7CursorState = {
        "phase": "ready",
        "completed_checkpoints": [],
        "checkpoint_evidence": [],
        "source_commit": _git_output(repo, "rev-parse", "HEAD"),
        "source_tree": _git_output(repo, "rev-parse", "HEAD^{tree}"),
        "migration_id": migration_id,
        "case_sha256": case_sha256,
        "plan_sha256": plan_sha256,
    }
    graph_config = checkpoint_config(run_dir.name)
    with open_sqlite_checkpointer(run_dir) as checkpointer:
        graph = _build_langgraph_orchestration_graph(
            case["checkpoints"],
            execute,
        ).compile(
            checkpointer=checkpointer,
            interrupt_after=["cp02"],
        )
        invoked = graph.invoke(initial, graph_config)
        snapshot = graph.get_state(graph_config)
    _assert_checkpoint_state(invoked, ["CP01", "CP02"])
    if invoked.get("phase") != "cp02_completed" or snapshot.next != ("cp03",):
        raise Gate7Failure("LangGraph source 未停在 CP02 后的迁移边界")
    checkpoints = [
        _checkpoint_evidence_from_payload(item)
        for item in invoked["checkpoint_evidence"]
    ]
    manifest = validate_checkpoint_manifest(run_dir)
    relative = run_dir.relative_to(exchange.resolve()).as_posix()
    manifest_path = run_dir / "graph" / "checkpoint-manifest.json"
    return checkpoints, {
        "engine": "langgraph",
        "kind": "langgraph-sqlite",
        "run_dir_ref": relative,
        "manifest_ref": "graph/checkpoint-manifest.json",
        "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
        "checkpoint_sha256": manifest.checkpoint_sha256,
        "checkpoint_bytes": manifest.checkpoint_bytes,
        "checkpoint_count": manifest.checkpoint_count,
        "checkpoint_evidence_sha256": [
            _sha256_text(_canonical_json(item))
            for item in invoked["checkpoint_evidence"]
        ],
        "state_bytes": _directory_bytes(run_dir),
        "phase": invoked["phase"],
        "next": list(snapshot.next),
    }


def _resume_langgraph_target_checkpoint(
    *,
    case: dict[str, Any],
    config: dict[str, Any],
    repo: Path,
    remote: Path,
    output_dir: Path,
    exchange: Path,
    ledger: EventLedger,
    budget: ProviderBudget,
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], CheckpointEvidence]:
    started = time.monotonic()
    expected = bundle["engine_state"]
    if expected.get("engine") != "langgraph":
        raise Gate7Blocked("LangGraph cursor 与执行 engine 不一致")
    try:
        from vega.loop_graph_checkpoint import (
            checkpoint_config,
            open_sqlite_checkpointer,
            validate_checkpoint_manifest,
        )
    except ModuleNotFoundError as exc:
        raise Gate7Blocked(
            "LangGraph arm 需要 langgraph 与 langgraph-checkpoint-sqlite"
        ) from exc
    run_dir_ref = _safe_relative_ref(expected["run_dir_ref"])
    run_dir = (exchange / run_dir_ref).resolve()
    try:
        run_dir.relative_to(exchange.resolve())
    except ValueError as exc:
        raise Gate7Blocked("LangGraph cursor 路径越过 exchange") from exc
    manifest_path = run_dir / _safe_relative_ref(expected["manifest_ref"])
    if _sha256_bytes(manifest_path.read_bytes()) != expected["manifest_sha256"]:
        raise Gate7Blocked("LangGraph checkpoint manifest hash 漂移")
    manifest_before = validate_checkpoint_manifest(run_dir)
    if manifest_before.checkpoint_sha256 != expected["checkpoint_sha256"]:
        raise Gate7Blocked("LangGraph checkpoint database hash 漂移")

    def execute(checkpoint: dict[str, Any]) -> CheckpointEvidence:
        return _run_checkpoint(
            checkpoint=checkpoint,
            machine="machine-f",
            config=config,
            repo=repo,
            remote=remote,
            output_dir=output_dir,
            ledger=ledger,
            budget=budget,
        )

    graph_config = checkpoint_config(run_dir.name)
    with open_sqlite_checkpointer(run_dir, require_existing=True) as checkpointer:
        graph = _build_langgraph_orchestration_graph(
            case["checkpoints"],
            execute,
        ).compile(checkpointer=checkpointer)
        before = graph.get_state(graph_config)
        if before.values.get("phase") != "cp02_completed":
            raise Gate7Blocked("LangGraph resume 前 phase 不正确")
        if before.next != ("cp03",):
            raise Gate7Blocked("LangGraph resume 前 next node 不正确")
        _assert_cursor_matches_bundle(before.values, bundle)
        resumed = graph.invoke(None, graph_config)
        after = graph.get_state(graph_config)
    _assert_checkpoint_state(resumed, ["CP01", "CP02", "CP03"])
    if resumed.get("phase") != "cp03_completed" or after.next != ():
        raise Gate7Failure("LangGraph 未从持久化 CP02 状态恢复并完成 CP03")
    manifest_after = validate_checkpoint_manifest(run_dir)
    evidence = _checkpoint_evidence_from_payload(resumed["checkpoint_evidence"][-1])
    return {
        "engine": "langgraph",
        "before_phase": before.values["phase"],
        "after_phase": resumed["phase"],
        "recovery_elapsed_seconds": round(time.monotonic() - started, 6),
        "state_bytes_before": expected["state_bytes"],
        "state_bytes_after": _directory_bytes(run_dir),
        "checkpoint_count_before": manifest_before.checkpoint_count,
        "checkpoint_count_after": manifest_after.checkpoint_count,
        "resume_external_attempts": 0,
        "replayed_external_attempts": 0,
        "target_external_attempts": 1,
    }, evidence


def _build_langgraph_orchestration_graph(
    checkpoints: list[dict[str, Any]],
    execute: Callable[[dict[str, Any]], CheckpointEvidence],
) -> Any:
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:
        raise Gate7Blocked(
            "LangGraph arm 需要 langgraph 与 langgraph-checkpoint-sqlite"
        ) from exc
    builder = StateGraph(Gate7CursorState)
    previous = START
    for index, checkpoint in enumerate(checkpoints):
        node_name = checkpoint["id"].casefold()
        builder.add_node(
            node_name,
            _make_checkpoint_graph_node(
                checkpoint,
                index=index,
                checkpoints=checkpoints,
                execute=execute,
            ),
        )
        builder.add_edge(previous, node_name)
        previous = node_name
    builder.add_edge(previous, END)
    return builder


def _make_checkpoint_graph_node(
    checkpoint: dict[str, Any],
    *,
    index: int,
    checkpoints: list[dict[str, Any]],
    execute: Callable[[dict[str, Any]], CheckpointEvidence],
) -> Callable[[Gate7CursorState], dict[str, Any]]:
    expected_completed = [item["id"] for item in checkpoints[:index]]

    def run(state: Gate7CursorState) -> dict[str, Any]:
        if state["completed_checkpoints"] != expected_completed:
            raise Gate7Failure(
                f"{checkpoint['id']} LangGraph 前置 checkpoint 状态不一致"
            )
        evidence = execute(checkpoint)
        payload = _checkpoint_payload(evidence)
        completed = [*expected_completed, checkpoint["id"]]
        return {
            "phase": f"{checkpoint['id'].casefold()}_completed",
            "completed_checkpoints": completed,
            "checkpoint_evidence": [*state["checkpoint_evidence"], payload],
            "source_commit": evidence.commit_sha,
            "source_tree": evidence.tree_sha,
        }

    return run


def _assert_checkpoint_state(
    state: dict[str, Any],
    expected_checkpoints: list[str],
) -> None:
    if state.get("completed_checkpoints") != expected_checkpoints:
        raise Gate7Failure("LangGraph completed checkpoint 状态不一致")
    payloads = state.get("checkpoint_evidence")
    if not isinstance(payloads, list):
        raise Gate7Failure("LangGraph 缺少 checkpoint evidence")
    observed = [item.get("checkpoint") for item in payloads if isinstance(item, dict)]
    if observed != expected_checkpoints:
        raise Gate7Failure("LangGraph checkpoint evidence 顺序不一致")


def _checkpoint_evidence_from_payload(payload: dict[str, Any]) -> CheckpointEvidence:
    return CheckpointEvidence(
        checkpoint=payload["checkpoint"],
        machine=payload["machine"],
        attempt_id=payload["attempt_id"],
        logical_operation_id=payload["logical_operation_id"],
        prompt_sha256=payload["prompt_sha256"],
        output_sha256=payload["output_sha256"],
        tokens_used=payload["tokens_used"],
        changed_files=list(payload["changed_files"]),
        diff_lines=payload["diff_lines"],
        commit_sha=payload["commit_sha"],
        tree_sha=payload["tree_sha"],
        parent_sha=payload["parent_sha"],
        ref_name=payload["ref_name"],
        verification=list(payload["verification"]),
        elapsed_seconds=float(payload["elapsed_seconds"]),
        transcript_audit=dict(
            payload.get(
                "transcript_audit",
                _not_applicable_transcript_audit(""),
            )
        ),
    )


def _assert_cursor_matches_bundle(
    cursor: dict[str, Any],
    bundle: dict[str, Any],
) -> None:
    expected = {
        "source_commit": bundle["source_commit"],
        "source_tree": bundle["source_tree"],
        "migration_id": bundle["migration_id"],
        "case_sha256": bundle["case_sha256"],
        "plan_sha256": bundle["plan_sha256"],
        "completed_checkpoints": ["CP01", "CP02"],
    }
    for key, value in expected.items():
        if cursor.get(key) != value:
            raise Gate7Blocked(f"engine cursor 与 handoff 不一致：{key}")
    if cursor.get("phase") != "cp02_completed":
        raise Gate7Blocked("engine cursor phase 不是 cp02_completed")
    payloads = cursor.get("checkpoint_evidence")
    if not isinstance(payloads, list):
        raise Gate7Blocked("engine cursor 缺少 checkpoint evidence")
    observed_hashes = [
        _sha256_text(_canonical_json(item))
        for item in payloads
        if isinstance(item, dict)
    ]
    if observed_hashes != bundle.get("checkpoint_evidence_sha256"):
        raise Gate7Blocked("engine cursor checkpoint evidence hash 漂移")


def _safe_relative_ref(value: str) -> Path:
    ref = PurePosixPath(value)
    if ref.is_absolute() or ".." in ref.parts or not ref.parts:
        raise Gate7Blocked(f"非法相对 artifact ref：{value}")
    return Path(*ref.parts)


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _load_sealed_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise Gate7Blocked(f"sealed JSON 不存在或不安全：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_final_remote(
    remote: Path,
    result_e: dict[str, Any],
    result_f: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    checkpoints = [*result_e["checkpoints"], *result_f["checkpoints"]]
    if [item["checkpoint"] for item in checkpoints] != ["CP01", "CP02", "CP03"]:
        raise Gate7Failure("最终 checkpoint evidence 不完整")
    previous = BASE_SHA
    for item in checkpoints:
        if item["parent_sha"] != previous:
            raise Gate7Failure(f"{item['checkpoint']} parent chain 不连续")
        observed = _git_dir_output(remote, "rev-parse", item["ref_name"])
        if observed != item["commit_sha"]:
            raise Gate7Failure(f"{item['checkpoint']} remote ref 对账失败")
        previous = item["commit_sha"]
    final_commit = checkpoints[-1]["commit_sha"]
    final_paths = sorted(_git_dir_changed_files(remote, BASE_SHA, final_commit))
    if final_paths != sorted(case["exact_files"]["files"]):
        raise Gate7Failure("最终 exact 10 文件范围不一致")
    final_tree = _git_dir_output(remote, "rev-parse", f"{final_commit}^{{tree}}")
    expected_tree = case["case_identity"]["merge_tree"]
    if final_tree != expected_tree:
        raise Gate7Failure(
            f"最终 tree 与冻结 merge tree 不一致：expected={expected_tree}, "
            f"observed={final_tree}"
        )
    final_diff = _git_dir_bytes(
        remote,
        "diff",
        "--binary",
        "--full-index",
        BASE_SHA,
        final_commit,
    )
    final_diff_bytes = len(final_diff)
    final_diff_sha256 = _sha256_bytes(final_diff)
    expected_bytes = case["case_identity"]["diff_bytes"]
    expected_sha256 = case["case_identity"]["diff_sha256"]
    if final_diff_bytes != expected_bytes or final_diff_sha256 != expected_sha256:
        raise Gate7Failure(
            "最终 canonical diff 身份不一致："
            f"bytes={final_diff_bytes}/{expected_bytes}, "
            f"sha256={final_diff_sha256}/{expected_sha256}"
        )
    return {
        "commit_sha": final_commit,
        "tree_sha": final_tree,
        "changed_files": final_paths,
        "diff_bytes": final_diff_bytes,
        "diff_sha256": final_diff_sha256,
        "oracle_used_as_only_correctness_source": False,
    }


def _build_metrics(
    state: ArmState,
    result_e: dict[str, Any],
    result_f: dict[str, Any],
    duplicate_claim_rejected: bool,
    artifact_scans: list[dict[str, int]],
) -> dict[str, Any]:
    event_paths = [
        state.output_dir / "coordinator-events.jsonl",
        state.output_dir / "machine-e" / "events.jsonl",
        state.output_dir / "machine-f" / "events.jsonl",
    ]
    events = []
    for path in event_paths:
        entries = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        validate_event_chain(entries)
        events.extend(entries)
    checkpoints = [*result_e["checkpoints"], *result_f["checkpoints"]]
    tokens = [item.get("tokens_used") for item in checkpoints]
    transcript_audits = [
        item.get("transcript_audit", {})
        for item in checkpoints
    ]
    migration_ids = {
        item["migration_id"]
        for item in events
        if item["operation_kind"] == "planned_migration" and item.get("migration_id")
    }
    return {
        "execution_slots_used": result_e["execution_slots_used"]
        + result_f["execution_slots_used"],
        "provider_sessions_used": result_e["provider_sessions_used"]
        + result_f["provider_sessions_used"],
        "automatic_retry_count": sum(
            item["operation_kind"] == "retry" for item in events
        ),
        "recovery_event_count": sum(
            item["operation_kind"] == "recovery" for item in events
        ),
        "planned_migration_count": len(migration_ids),
        "unplanned_crash_count": sum(
            item["operation_kind"] == "unplanned_crash" for item in events
        ),
        "duplicate_external_effect_count": _duplicate_effect_count(events),
        "duplicate_claim_rejected": duplicate_claim_rejected,
        "canary_leak_count": sum(item["canary_hit_count"] for item in artifact_scans),
        "sensitive_material_hit_count": sum(
            item["sensitive_material_hit_count"] for item in artifact_scans
        ),
        "artifact_files_scanned": sum(
            item["files_scanned"] for item in artifact_scans
        ),
        "artifact_files_skipped": sum(
            item["files_skipped"] for item in artifact_scans
        ),
        "scope_violation_count": 0,
        "checkpoint_count": len(checkpoints),
        "tokens_used_total": sum(item for item in tokens if item is not None),
        "token_counts_complete": all(item is not None for item in tokens)
        if state.runner_mode == "real"
        else True,
        "transcript_audit_required": (
            state.runner_mode == "real"
            and state.graph_schema_version == "gate7-r4-v1"
        ),
        "transcript_audit_passed": (
            all(
                audit.get("status") == "passed"
                for audit in transcript_audits
            )
            if (
                state.runner_mode == "real"
                and state.graph_schema_version == "gate7-r4-v1"
            )
            else all(
                audit.get("status") == "not_applicable"
                for audit in transcript_audits
            )
        ),
        "transcript_exec_commands_total": sum(
            int(audit.get("command_count", 0))
            for audit in transcript_audits
        ),
        "transcript_tool_waves_total": sum(
            int(audit.get("tool_wave_count", 0))
            for audit in transcript_audits
        ),
        "transcript_command_output_bytes_total": sum(
            int(audit.get("cumulative_command_output_bytes", 0))
            for audit in transcript_audits
        ),
        "transcript_bytes_total": sum(
            int(audit.get("transcript_bytes", 0))
            for audit in transcript_audits
        ),
        "engine_recovery": result_f["engine_resume"],
    }


def validate_event_chain(entries: list[dict[str, Any]]) -> None:
    previous = "0" * 64
    for index, entry in enumerate(entries):
        if entry.get("prev_event_hash") != previous:
            raise Gate7Blocked(f"event hash chain 在 index={index} 断裂")
        body = {key: value for key, value in entry.items() if key != "event_hash"}
        observed = _sha256_text(_canonical_json(body))
        if entry.get("event_hash") != observed:
            raise Gate7Blocked(f"event hash 在 index={index} 无效")
        previous = observed


def _duplicate_effect_count(events: list[dict[str, Any]]) -> int:
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for entry in events:
        if entry["event"] != "checkpoint_started":
            continue
        key = (
            entry.get("logical_operation_id") or "",
            entry.get("idempotency_key") or "",
        )
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


def _not_applicable_transcript_audit(output: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "not_applicable",
        "parser_version": TRANSCRIPT_AUDIT_PARSER_VERSION,
        "parse_complete": True,
        "command_count": 0,
        "result_count": 0,
        "tool_wave_count": 0,
        "duplicate_command_count": 0,
        "unbounded_read_count": 0,
        "max_command_output_bytes": 0,
        "cumulative_command_output_bytes": 0,
        "transcript_bytes": len(output.encode("utf-8")),
        "tokens_used": _parse_tokens_used(output),
        "transcript_sha256": _sha256_text(output),
        "policy_sha256": None,
        "expected_workdir": None,
        "observed_workdirs": [],
        "command_sha256": [],
        "violations": [],
    }


def _audit_codex_transcript(
    output: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    lines = output.splitlines()
    commands, command_parse_complete = _extract_exec_commands(lines)
    result_blocks = _extract_exec_result_blocks(lines)
    tool_wave_count = _count_tool_waves(lines)
    normalized_commands = [_normalize_exec_command(item) for item in commands]
    observed_workdirs = []
    for command in commands:
        parsed_command = _extract_powershell_command(command)
        observed_workdirs.append(
            parsed_command[1] if parsed_command is not None else None
        )
    duplicate_command_count = len(normalized_commands) - len(set(normalized_commands))
    unbounded_read_count = 0
    violations: list[str] = []

    parse_complete = (
        command_parse_complete
        and len(commands) == len(result_blocks)
    )
    if not parse_complete:
        violations.append(
            "transcript exec/result 解析不完整："
            f"commands={len(commands)}, results={len(result_blocks)}"
        )
    if tool_wave_count > policy["max_tool_waves"]:
        violations.append(
            f"工具波次超限：{tool_wave_count}/{policy['max_tool_waves']}"
        )
    if len(commands) > policy["max_exec_commands"]:
        violations.append(
            f"exec 次数超限：{len(commands)}/{policy['max_exec_commands']}"
        )
    if duplicate_command_count > policy["max_duplicate_commands"]:
        violations.append(
            "重复命令超限："
            f"{duplicate_command_count}/{policy['max_duplicate_commands']}"
        )

    for index, command in enumerate(commands, start=1):
        command_violations, command_unbounded_reads = _audit_exec_command(
            command,
            policy,
        )
        unbounded_read_count += command_unbounded_reads
        violations.extend(
            f"exec#{index}: {item}"
            for item in command_violations
        )

    result_sizes = [
        len(block.encode("utf-8"))
        for block in result_blocks
    ]
    max_command_output_bytes = max(result_sizes, default=0)
    cumulative_command_output_bytes = sum(result_sizes)
    transcript_bytes = len(output.encode("utf-8"))
    tokens_used = _parse_tokens_used(output)
    if max_command_output_bytes > policy["max_single_command_output_bytes"]:
        violations.append(
            "单命令输出超限："
            f"{max_command_output_bytes}/"
            f"{policy['max_single_command_output_bytes']}"
        )
    if (
        cumulative_command_output_bytes
        > policy["max_cumulative_command_output_bytes"]
    ):
        violations.append(
            "累计命令输出超限："
            f"{cumulative_command_output_bytes}/"
            f"{policy['max_cumulative_command_output_bytes']}"
        )
    if transcript_bytes > policy["max_transcript_bytes"]:
        violations.append(
            f"transcript 超限：{transcript_bytes}/{policy['max_transcript_bytes']}"
        )
    if tokens_used is None:
        violations.append("transcript 缺少 token 计数")
    elif tokens_used > policy["max_tokens_used"]:
        violations.append(
            f"token 超限：{tokens_used}/{policy['max_tokens_used']}"
        )

    return {
        "schema_version": 1,
        "status": "failed" if violations else "passed",
        "parser_version": TRANSCRIPT_AUDIT_PARSER_VERSION,
        "parse_complete": parse_complete,
        "command_count": len(commands),
        "result_count": len(result_blocks),
        "tool_wave_count": tool_wave_count,
        "duplicate_command_count": duplicate_command_count,
        "unbounded_read_count": unbounded_read_count,
        "max_command_output_bytes": max_command_output_bytes,
        "cumulative_command_output_bytes": cumulative_command_output_bytes,
        "transcript_bytes": transcript_bytes,
        "tokens_used": tokens_used,
        "transcript_sha256": _sha256_text(output),
        "policy_sha256": _sha256_text(_canonical_json(policy)),
        "expected_workdir": policy.get("expected_workdir"),
        "observed_workdirs": observed_workdirs,
        "command_sha256": [
            _sha256_text(item)
            for item in normalized_commands
        ],
        "violations": violations,
    }


def _extract_exec_commands(lines: list[str]) -> tuple[list[str], bool]:
    commands: list[str] = []
    complete = True
    index = 0
    while index < len(lines):
        if lines[index].strip() != "exec":
            index += 1
            continue
        index += 1
        parts: list[str] = []
        while index < len(lines):
            current = lines[index]
            stripped = current.strip()
            if (
                stripped in {"exec", "codex", "apply_patch", "tokens used"}
                or EXEC_RESULT_PATTERN.match(current)
                or current.startswith("ERROR:")
            ):
                break
            parts.append(current)
            index += 1
            if re.search(r"\sin\s+(?:[a-z]:[\\/]|/)", current, re.IGNORECASE):
                break
        command = "\n".join(parts).strip()
        if not command or not re.search(
            r"\sin\s+(?:[a-z]:[\\/]|/)",
            command,
            re.IGNORECASE,
        ):
            complete = False
        commands.append(command)
    return commands, complete


def _extract_exec_result_blocks(lines: list[str]) -> list[str]:
    blocks: list[str] = []
    boundaries = {"exec", "codex", "apply_patch", "tokens used"}
    for index, line in enumerate(lines):
        if not EXEC_RESULT_PATTERN.match(line):
            continue
        content: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            current = lines[cursor]
            stripped = current.strip()
            if (
                stripped in boundaries
                or EXEC_RESULT_PATTERN.match(current)
                or current.startswith("ERROR:")
            ):
                break
            content.append(current)
            cursor += 1
        blocks.append("\n".join(content).rstrip() + ("\n" if content else ""))
    return blocks


def _count_tool_waves(lines: list[str]) -> int:
    waves = 0
    current_wave_has_exec = False
    for line in lines:
        stripped = line.strip()
        if stripped == "codex":
            current_wave_has_exec = False
        elif stripped == "exec" and not current_wave_has_exec:
            waves += 1
            current_wave_has_exec = True
    return waves


def _normalize_exec_command(command: str) -> str:
    without_workdir = re.sub(
        r"\s+in\s+(?:[a-z]:[\\/]|/).*$",
        "",
        command,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(r"\s+", " ", without_workdir).strip().casefold()


def _audit_exec_command(
    command: str,
    policy: dict[str, Any],
) -> tuple[list[str], int]:
    parsed = _extract_powershell_command(command)
    if parsed is None:
        return ["exec wrapper 无法按冻结格式解析"], 0
    body, observed_workdir = parsed
    violations: list[str] = []
    expected_workdir = policy.get("expected_workdir")
    if not isinstance(expected_workdir, str):
        violations.append("exec 审计缺少 expected workdir")
    elif observed_workdir != expected_workdir:
        violations.append(
            "exec 工作目录漂移："
            f"expected={expected_workdir}, observed={observed_workdir}"
        )
    if "$" in body:
        violations.append("exec 含 PowerShell 可展开变量或子表达式")
    if "`" in body:
        violations.append("exec 含 PowerShell 反引号转义")
    try:
        tokens = shlex.split(body, posix=False)
    except ValueError:
        return [*violations, "exec command quoting 无法解析"], 0
    if not tokens:
        return [*violations, "exec command 为空"], 0

    normalized = [item.casefold() for item in tokens]
    if normalized == ["git", "status", "--short"]:
        return violations, 0
    if normalized[0] in {"rg", "rg.exe"}:
        return [*violations, *_audit_rg_tokens(tokens, policy)], 0
    if normalized[0] == "get-content":
        template_violations, unbounded_reads = _audit_get_content_tokens(
            tokens,
            policy,
        )
        return [*violations, *template_violations], unbounded_reads
    return [*violations, "exec 不匹配任何冻结只读模板"], 0


def _extract_powershell_command(command: str) -> tuple[str, str] | None:
    workdir_match = re.search(
        r"\s+in\s+((?:[a-z]:[\\/]|/).+?)\s*$",
        command,
        re.IGNORECASE | re.DOTALL,
    )
    if workdir_match is None:
        return None
    normalized_workdir = _normalize_exec_workdir(workdir_match.group(1))
    if normalized_workdir is None:
        return None
    prefix = command[: workdir_match.start()].strip()
    marker = re.search(r"\s-command\s+", prefix, re.IGNORECASE)
    if marker is None:
        return None
    wrapper = prefix[: marker.start()].strip()
    try:
        wrapper_tokens = shlex.split(wrapper, posix=True)
    except ValueError:
        return None
    if len(wrapper_tokens) != 1:
        return None
    executable = wrapper_tokens[0].replace("\\\\", "\\").replace("\\", "/")
    if executable.rsplit("/", 1)[-1].casefold() not in {"pwsh", "pwsh.exe"}:
        return None
    body = prefix[marker.end() :].strip()
    if (
        len(body) < 2
        or body[0] not in {"'", '"'}
        or body[-1] != body[0]
    ):
        return None
    body = body[1:-1]
    if "\n" in body or "\r" in body:
        return None
    normalized_body = body.replace('\\"', '"').replace("\\'", "'").strip()
    return normalized_body, normalized_workdir


def _normalize_exec_workdir(value: str) -> str | None:
    candidate = value.strip()
    if (
        len(candidate) >= 2
        and candidate[0] in {"'", '"'}
        and candidate[-1] == candidate[0]
    ):
        candidate = candidate[1:-1].strip()
    candidate = candidate.replace("\\\\", "\\")
    if re.match(r"^[a-z]:[\\/]", candidate, re.IGNORECASE):
        path = PureWindowsPath(candidate)
        if ".." in path.parts:
            return None
        return path.as_posix().rstrip("/").casefold()
    if candidate.startswith("/"):
        path = PurePosixPath(candidate)
        if ".." in path.parts:
            return None
        normalized = path.as_posix().rstrip("/")
        return normalized or "/"
    return None


def _audit_rg_tokens(
    tokens: list[str],
    policy: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    try:
        separator = tokens.index("--")
    except ValueError:
        return ["rg 必须使用 -- 分隔 pattern options 与已注册路径"]
    options = tokens[1:separator]
    paths = tokens[separator + 1 :]
    if not paths:
        violations.append("rg 缺少已注册读取路径")
    allowed_paths = set(policy["allowed_read_paths"])
    for path in paths:
        normalized_path = path.replace("\\", "/")
        if normalized_path not in allowed_paths:
            violations.append(f"rg 读取未注册路径：{normalized_path}")

    index = 0
    saw_line_numbers = False
    max_count: int | None = None
    pattern_count = 0
    while index < len(options):
        token = options[index]
        lowered = token.casefold()
        if lowered == "-n":
            if saw_line_numbers:
                violations.append("rg -n 重复")
            saw_line_numbers = True
            index += 1
            continue
        if lowered == "--max-count":
            if index + 1 >= len(options):
                violations.append("rg --max-count 缺少数值")
                break
            max_count = _parse_nonnegative_int(options[index + 1])
            index += 2
            continue
        if lowered.startswith("--max-count="):
            max_count = _parse_nonnegative_int(token.split("=", 1)[1])
            index += 1
            continue
        if lowered == "-e":
            if index + 1 >= len(options) or not options[index + 1]:
                violations.append("rg -e 缺少 pattern")
                break
            pattern = options[index + 1]
            if (
                len(pattern) < 2
                or pattern[0] != "'"
                or pattern[-1] != "'"
            ):
                violations.append("rg -e pattern 必须使用 PowerShell 单引号字面量")
            elif "'" in pattern[1:-1]:
                violations.append("rg -e pattern 不允许内嵌单引号")
            pattern_count += 1
            index += 2
            continue
        violations.append(f"rg 含未注册参数：{token}")
        index += 1
    if not saw_line_numbers:
        violations.append("rg 缺少 -n")
    if max_count is None:
        violations.append("rg 缺少 --max-count")
    elif max_count > policy["max_rg_matches_per_file"]:
        violations.append("rg --max-count 超过冻结上限")
    if pattern_count == 0:
        violations.append("rg 必须至少使用一个 -e pattern")
    return violations


def _audit_get_content_tokens(
    tokens: list[str],
    policy: dict[str, Any],
) -> tuple[list[str], int]:
    violations: list[str] = []
    if len(tokens) < 4:
        return ["Get-Content 缺少冻结读取上限"], 1
    path = tokens[1].replace("\\", "/")
    if path not in set(policy["allowed_read_paths"]):
        violations.append(f"Get-Content 读取未注册路径：{path}")

    normalized = [item.casefold() for item in tokens]
    read_lines: int | None = None
    if len(tokens) == 4 and normalized[2] == "-first":
        read_lines = _parse_nonnegative_int(tokens[3])
    elif (
        len(tokens) == 8
        and normalized[2:5] == ["|", "select-object", "-skip"]
        and normalized[6] == "-first"
    ):
        skip_lines = _parse_nonnegative_int(tokens[5])
        read_lines = _parse_nonnegative_int(tokens[7])
        if skip_lines is None:
            violations.append("Select-Object -Skip 必须是非负整数")
    else:
        violations.append("Get-Content 不匹配冻结读取模板")
    if read_lines is None:
        violations.append("Get-Content -First 必须是非负整数")
        return violations, 1
    if read_lines > policy["max_read_lines"]:
        violations.append("Get-Content 读取行数超过冻结上限")
    return violations, 0


def _parse_nonnegative_int(value: str) -> int | None:
    if not value.isdigit():
        return None
    return int(value)


def _assert_prompt_safe(
    prompt: str,
    config: dict[str, Any],
    repo: Path,
) -> None:
    forbidden = [
        MERGE_SHA,
        ORACLE_DIFF_SHA256,
        str(PROJECT_ROOT.resolve()),
        str(Path(config.get("private_root", PROJECT_ROOT)).resolve()),
    ]
    if config.get("oracle"):
        forbidden.append(str(Path(config["oracle"]).resolve()))
    lowered = prompt.casefold()
    for value in forbidden:
        if value and value.casefold() in lowered:
            raise Gate7Blocked(f"prompt 含禁止输入：{value}")
    for canary in _private_canaries(config):
        if canary.casefold() in lowered:
            raise Gate7Blocked("prompt 含私有 canary")
    if machine_path := config.get("private_root"):
        if Path(machine_path).resolve() == repo.resolve():
            raise Gate7Blocked("private root 不得等于 worker repo")


def _assert_output_safe(
    output: str,
    config: dict[str, Any],
    repo: Path,
) -> None:
    forbidden = [
        MERGE_SHA,
        ORACLE_DIFF_SHA256,
        str(PROJECT_ROOT.resolve()),
    ]
    if config.get("oracle"):
        forbidden.append(str(Path(config["oracle"]).resolve()))
    private_root = config.get("private_root")
    if private_root:
        forbidden.append(str(Path(private_root).resolve()))
    allowed = str(repo.resolve()).casefold()
    lowered = output.casefold().replace(allowed, "<worker-repo>")
    for value in forbidden:
        normalized = value.casefold()
        if normalized and normalized in lowered:
            raise Gate7Failure(f"runner output 含禁止路径或 oracle 身份：{value}")
    for canary in _private_canaries(config):
        if canary.casefold() in lowered:
            raise Gate7Failure("runner output 含私有 canary")


def _scan_artifacts(canaries: list[str], roots: list[Path]) -> dict[str, int]:
    files_scanned = 0
    files_skipped = 0
    canary_hits = 0
    sensitive_hits = 0
    for root in roots:
        if not root.exists():
            continue
        paths = [root] if root.is_file() else [item for item in root.rglob("*") if item.is_file()]
        for path in paths:
            relative_parts = path.relative_to(root).parts if root.is_dir() else ()
            if any(part in {".git", ".venv"} for part in relative_parts):
                files_skipped += 1
                continue
            try:
                if path.stat().st_size > 5 * 1024 * 1024:
                    files_skipped += 1
                    continue
                content = path.read_bytes()
            except OSError:
                files_skipped += 1
                continue
            files_scanned += 1
            for canary in canaries:
                if canary.encode("utf-8") in content:
                    canary_hits += 1
                    raise Gate7Failure(f"canary 泄漏到：{path}")
            for pattern in SENSITIVE_ARTIFACT_PATTERNS:
                if pattern.search(content):
                    sensitive_hits += 1
                    raise Gate7Failure(f"敏感材料泄漏到：{path}")
    return {
        "files_scanned": files_scanned,
        "files_skipped": files_skipped,
        "canary_hit_count": canary_hits,
        "sensitive_material_hit_count": sensitive_hits,
    }


def _validate_real_runner_output(result: RunnerResult, runner_mode: RunnerMode) -> None:
    if runner_mode != "real":
        return
    header = _parse_codex_header(result.output)
    expected = {
        "provider": PROVIDER,
        "model": MODEL,
        "reasoning effort": REASONING,
    }
    for key, value in expected.items():
        if header.get(key) != value:
            raise Gate7Failure(
                f"Codex live header 不匹配：{key}={header.get(key)!r}, expected={value!r}"
            )
    if _parse_tokens_used(result.output) is None:
        raise Gate7Failure("真实 provider output 缺少 token 计数")


def _build_real_runner(
    inspection_policy: dict[str, Any] | None = None,
) -> CodexExecRunner:
    provider = CodexProviderDescriptor(
        name=PROVIDER,
        base_url=PROVIDER_BASE_URL,
        wire_api=WIRE_API,
        requires_openai_auth=True,
        supports_websockets=False,
        request_max_retries=0,
        stream_max_retries=0,
    )
    executable = _resolve_codex_executable("codex")
    options = CodexExecOptions(
        ignore_user_config=True,
        provider=provider,
        windows_sandbox_session_override="elevated",
        disable_multi_agent=True,
        model=MODEL,
        reasoning_effort=REASONING,
        ephemeral=True,
    )
    if inspection_policy is None:
        return CodexExecRunner(executable=executable, options=options)
    cli = inspection_policy["cli"]
    return BoundedCodexExecRunner(
        executable=executable,
        options=options,
        tool_output_token_limit=cli["tool_output_token_limit"],
        model_verbosity=cli["model_verbosity"],
        model_reasoning_summary=cli["model_reasoning_summary"],
    )


def _assert_multi_agent_disabled(command: list[str]) -> None:
    disable_pairs = [
        (command[index], command[index + 1])
        for index in range(len(command) - 1)
        if command[index] == "--disable"
    ]
    if disable_pairs.count(("--disable", "multi_agent")) != 1:
        raise Gate7Blocked("真实 worker 必须且只能显式禁用一次 multi_agent")
    if any(
        item == "features.multi_agent=true"
        or item.startswith("features.multi_agent=true")
        for item in command
    ):
        raise Gate7Blocked("真实 worker 命令重新启用了 multi_agent")


def _arm_topology(case: dict[str, Any], engine: EngineName) -> dict[str, Any]:
    key = "gate_7a" if engine == "linear" else "gate_7c"
    topology = case.get("topology")
    arm = topology.get(key) if isinstance(topology, dict) else None
    if not isinstance(arm, dict):
        raise Gate7Blocked(f"case 缺少 {key} topology")
    return arm


def _auth_mode_from_case(case: dict[str, Any]) -> AuthMode:
    provider_budget = case.get("provider_budget")
    if not isinstance(provider_budget, dict):
        raise Gate7Blocked("case provider_budget 缺失")
    value = provider_budget.get("auth_mode", AUTH_MODE)
    if value == "chatgpt":
        return "chatgpt"
    if value == "api-key":
        return "api-key"
    raise Gate7Blocked("case auth_mode 非法")


def _baseline_tag_from_case(case: dict[str, Any], engine: EngineName) -> str:
    value = _arm_topology(case, engine).get("execution_name")
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise Gate7Blocked(f"{engine} baseline tag 非法")
    return value


def _consumed_tag_from_case(case: dict[str, Any], engine: EngineName) -> str:
    value = _arm_topology(case, engine).get("consumed_name")
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise Gate7Blocked(f"{engine} consumed tag 非法")
    return value


def _assert_real_baseline(
    engine: EngineName,
    case: dict[str, Any],
    *,
    case_sha256: str,
    plan_sha256: str,
    expected_auth_mode: AuthMode,
) -> str:
    baseline_tag = _baseline_tag_from_case(case, engine)
    baseline = _git_output(PROJECT_ROOT, "rev-parse", f"{baseline_tag}^{{commit}}")
    head = _git_output(PROJECT_ROOT, "rev-parse", "HEAD")
    if baseline != head:
        raise Gate7Blocked(
            f"real run 必须从 baseline tag checkout 启动：tag={baseline}, HEAD={head}"
        )
    status = _git_output(
        PROJECT_ROOT,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    observed = {line for line in status.splitlines() if line.strip()}
    if observed:
        raise Gate7Blocked(f"real baseline 工作树不干净：{sorted(observed)}")
    if _git_output(PROJECT_ROOT, "cat-file", "-t", f"refs/tags/{baseline_tag}") != "tag":
        raise Gate7Blocked(f"baseline tag 必须是 annotated tag：{baseline_tag}")
    remote_baseline = _remote_tag_commit(baseline_tag)
    if remote_baseline != baseline:
        raise Gate7Blocked(
            f"remote baseline tag 漂移：tag={baseline_tag}, remote={remote_baseline}"
        )
    consumed_tag = _consumed_tag_from_case(case, engine)
    check = _run_git_control_command(
        ["git", "rev-parse", "--verify", f"refs/tags/{consumed_tag}"],
        timeout=30,
        timeout_label=f"检查本地 consumed tag：{consumed_tag}",
    )
    if check.returncode == 0:
        raise Gate7Blocked(f"真实 arm 已被 consumed tag 锁定：{consumed_tag}")
    if check.returncode not in {1, 128}:
        raise Gate7Blocked(f"无法检查 consumed tag：{consumed_tag}")
    executable = _resolve_codex_executable("codex")
    if _codex_version(executable) != CODEX_VERSION:
        raise Gate7Blocked("Codex CLI version 与预注册不一致")
    observed_auth_mode = _codex_auth_mode(executable)
    if observed_auth_mode != expected_auth_mode:
        raise Gate7Blocked(
            "Codex auth mode 与预注册不一致："
            f"expected={expected_auth_mode}, observed={observed_auth_mode}"
        )
    _assert_provider_endpoint_reachable()
    if engine == "langgraph":
        _assert_gate7a_success_before_langgraph(
            baseline,
            case,
            case_sha256=case_sha256,
            plan_sha256=plan_sha256,
        )
    return baseline


def _claim_real_execution(
    engine: EngineName,
    baseline_sha: str,
    case: dict[str, Any],
) -> str:
    consumed_tag = _consumed_tag_from_case(case, engine)
    remote_check = _run_git_control_command(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{consumed_tag}"],
        timeout=REMOTE_GIT_TIMEOUT_SECONDS,
        timeout_label=f"检查 remote consumed tag：{consumed_tag}",
    )
    if remote_check.returncode != 0:
        raise Gate7Blocked(f"无法检查 remote consumed tag：{remote_check.stderr.strip()}")
    if remote_check.stdout.strip():
        raise Gate7Blocked(f"remote consumed tag 已存在：{consumed_tag}")
    result = _run_git_control_command(
        [
            "git",
            "-c",
            "user.email=gate7@example.invalid",
            "-c",
            "user.name=Gate 7 Harness",
            "tag",
            "-a",
            consumed_tag,
            "-m",
            f"Consume Gate 7 {engine} real provider budget at {baseline_sha}",
            baseline_sha,
        ],
        timeout=30,
        timeout_label=f"创建本地 consumed tag：{consumed_tag}",
    )
    if result.returncode != 0:
        raise Gate7Blocked(f"无法原子创建 consumed tag：{result.stderr.strip()}")
    push = _run_git_control_command(
        ["git", "push", "origin", f"refs/tags/{consumed_tag}"],
        timeout=REMOTE_GIT_TIMEOUT_SECONDS,
        timeout_label=f"推送 consumed tag：{consumed_tag}",
    )
    if push.returncode != 0:
        remote_after_failure = _remote_tag_commit_optional(consumed_tag)
        if remote_after_failure is None:
            _run_checked(
                ["git", "tag", "-d", consumed_tag],
                cwd=PROJECT_ROOT,
                timeout=30,
            )
            raise Gate7Blocked(f"无法推送 consumed tag：{push.stderr.strip()}")
        raise Gate7Blocked(
            f"remote consumed tag 已被其他执行占用：{consumed_tag}"
        )
    if _remote_tag_commit(consumed_tag) != baseline_sha:
        raise Gate7Failure("remote consumed tag 未绑定 execution baseline")
    return consumed_tag


def _revalidate_r4_transcript_evidence(
    *,
    case: dict[str, Any],
    summary_dir: Path,
    fixture_dir: Path,
    checkpoints: list[dict[str, Any]],
) -> None:
    contract = _inspection_contract_from_case(case)
    if contract is None:
        raise Gate7Blocked("Gate 7A R4 缺少 inspection contract")
    events_by_machine: dict[str, list[dict[str, Any]]] = {}
    for machine in ("machine-e", "machine-f"):
        event_path = summary_dir / machine / "events.jsonl"
        try:
            events = [
                json.loads(line)
                for line in event_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise Gate7Blocked(f"Gate 7A {machine} event ledger 无法解析") from exc
        validate_event_chain(events)
        events_by_machine[machine] = events

    for checkpoint in checkpoints:
        checkpoint_id = checkpoint.get("checkpoint")
        machine = checkpoint.get("machine")
        if checkpoint_id not in {"CP01", "CP02", "CP03"}:
            raise Gate7Blocked("Gate 7A R4 transcript checkpoint 身份非法")
        if machine not in {"machine-e", "machine-f"}:
            raise Gate7Blocked("Gate 7A R4 transcript machine 身份非法")
        policy = _inspection_policy_from_config(
            {"inspection_contract": contract},
            checkpoint_id,
            expected_workdir=fixture_dir / machine / "repo",
        )
        if policy is None:
            raise Gate7Blocked("Gate 7A R4 transcript policy 缺失")
        execution_dir = (
            summary_dir
            / machine
            / "executions"
            / checkpoint_id.casefold()
        )
        process_output_path = execution_dir / "process-output.txt"
        audit_path = execution_dir / "transcript-audit.json"
        try:
            output = process_output_path.read_text(encoding="utf-8")
            persisted_audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise Gate7Blocked(
                f"Gate 7A {checkpoint_id} transcript artifact 无法解析"
            ) from exc
        recomputed_audit = _audit_codex_transcript(output, policy)
        checkpoint_audit = checkpoint.get("transcript_audit")
        if (
            recomputed_audit["status"] != "passed"
            or persisted_audit != recomputed_audit
            or checkpoint_audit != recomputed_audit
        ):
            raise Gate7Blocked(
                f"Gate 7A {checkpoint_id} transcript audit 无法复验"
            )
        output_sha256 = _sha256_text(output)
        audit_sha256 = _sha256_text(_canonical_json(recomputed_audit))
        if (
            checkpoint.get("output_sha256") != output_sha256
            or recomputed_audit.get("transcript_sha256") != output_sha256
            or recomputed_audit.get("parser_version")
            != TRANSCRIPT_AUDIT_PARSER_VERSION
        ):
            raise Gate7Blocked(
                f"Gate 7A {checkpoint_id} transcript identity 漂移"
            )
        matching_events = [
            item
            for item in events_by_machine[machine]
            if item.get("event") == "checkpoint_completed"
            and item.get("step_id") == checkpoint_id
            and item.get("attempt_id") == checkpoint.get("attempt_id")
        ]
        if len(matching_events) != 1:
            raise Gate7Blocked(
                f"Gate 7A {checkpoint_id} completion event 不唯一"
            )
        payload = matching_events[0].get("payload")
        if (
            not isinstance(payload, dict)
            or payload.get("output_sha256") != output_sha256
            or payload.get("transcript_audit_sha256") != audit_sha256
            or payload.get("transcript_audit_status") != "passed"
        ):
            raise Gate7Blocked(
                f"Gate 7A {checkpoint_id} completion event 未绑定 audit"
            )


def _assert_gate7a_success_before_langgraph(
    baseline_sha: str,
    case: dict[str, Any],
    *,
    case_sha256: str,
    plan_sha256: str,
) -> None:
    linear_consumed = _consumed_tag_from_case(case, "linear")
    if (
        _git_output(PROJECT_ROOT, "cat-file", "-t", f"refs/tags/{linear_consumed}")
        != "tag"
    ):
        raise Gate7Blocked("Gate 7A consumed tag 不是 annotated tag")
    local = _git_output(
        PROJECT_ROOT,
        "rev-parse",
        f"{linear_consumed}^{{commit}}",
    )
    if local != baseline_sha or _remote_tag_commit(linear_consumed) != baseline_sha:
        raise Gate7Blocked("Gate 7C 需要已消费且已推送的 Gate 7A baseline")
    linear_session = case["case_identity"]["real_session"]
    summary_path = REAL_OUTPUT_ROOT / linear_session / "summary.json"
    if not summary_path.is_file():
        raise Gate7Blocked("Gate 7C 需要 Gate 7A success summary")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Gate7Blocked("Gate 7A summary 无法解析") from exc
    expected_summary = {
        "status": "success",
        "runner_mode": "real",
        "engine": "linear",
        "session": linear_session,
        "case_sha256": case_sha256,
        "plan_sha256": plan_sha256,
        "baseline_sha": baseline_sha,
        "consumed_tag": linear_consumed,
        "single_host_dual_node_simulation": True,
        "physical_machine_migration_proven": False,
    }
    if case["schema_version"] == 2:
        expected_summary.update(
            {
                "case_hash_mode": case["r2_contract"]["case_hash_mode"],
                "graph_schema_version": case["r2_contract"][
                    "graph_schema_version"
                ],
                "worker_multi_agent": case["r2_contract"][
                    "worker_multi_agent"
                ],
            }
        )
    provider_budget = case.get("provider_budget")
    if isinstance(provider_budget, dict) and "auth_mode" in provider_budget:
        expected_summary.update(
            {
                "auth_mode": _auth_mode_from_case(case),
                "codex_version": CODEX_VERSION,
            }
        )
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            raise Gate7Blocked(f"Gate 7A summary 字段不可信：{key}")
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        raise Gate7Blocked("Gate 7A summary 缺少 metrics")
    expected_metrics = {
        "execution_slots_used": 3,
        "provider_sessions_used": 3,
        "automatic_retry_count": 0,
        "planned_migration_count": 1,
        "unplanned_crash_count": 0,
        "duplicate_external_effect_count": 0,
        "duplicate_claim_rejected": True,
        "canary_leak_count": 0,
        "sensitive_material_hit_count": 0,
        "scope_violation_count": 0,
        "checkpoint_count": 3,
        "token_counts_complete": True,
    }
    if case["r2_contract"].get("graph_schema_version") == "gate7-r4-v1":
        expected_metrics["transcript_audit_required"] = True
        expected_metrics["transcript_audit_passed"] = True
    for key, expected in expected_metrics.items():
        if metrics.get(key) != expected:
            raise Gate7Blocked(f"Gate 7A metric 不满足 Gate 7C 前置条件：{key}")
    engine_recovery = metrics.get("engine_recovery")
    if not isinstance(engine_recovery, dict):
        raise Gate7Blocked("Gate 7A 缺少 engine recovery 证据")
    if (
        engine_recovery.get("resume_external_attempts") != 0
        or engine_recovery.get("replayed_external_attempts") != 0
        or engine_recovery.get("target_external_attempts") != 1
    ):
        raise Gate7Blocked("Gate 7A recovery attempt 计数不可信")
    machine_e = summary.get("machine_e")
    machine_f = summary.get("machine_f")
    if not isinstance(machine_e, dict) or not isinstance(machine_f, dict):
        raise Gate7Blocked("Gate 7A 缺少 machine evidence")
    if machine_e.get("status") != "planned_migration":
        raise Gate7Blocked("Gate 7A Machine E 不是 planned migration")
    if machine_f.get("status") != "success":
        raise Gate7Blocked("Gate 7A Machine F 不是 success")
    checkpoints = [
        *machine_e.get("checkpoints", []),
        *machine_f.get("checkpoints", []),
    ]
    if [item.get("checkpoint") for item in checkpoints] != ["CP01", "CP02", "CP03"]:
        raise Gate7Blocked("Gate 7A checkpoint evidence 不完整")

    fixture_dir = REAL_FIXTURE_ROOT / linear_session
    remote = fixture_dir / "authority" / "source.git"
    bundle_path = fixture_dir / "exchange" / "handoff-v0001.json"
    if not remote.is_dir() or not bundle_path.is_file():
        raise Gate7Blocked("Gate 7A fixture authority 或 handoff 已丢失")
    try:
        observed_identity = _validate_final_remote(remote, machine_e, machine_f, case)
        bundle = _load_sealed_json(bundle_path)
        _validate_handoff_bundle(
            bundle,
            case_sha256=case_sha256,
            plan_sha256=plan_sha256,
            remote=remote,
        )
    except Gate7Failure as exc:
        raise Gate7Blocked(f"Gate 7A 机器证据复验失败：{exc}") from exc
    if summary.get("final_identity") != observed_identity:
        raise Gate7Blocked("Gate 7A final identity 与机器证据不一致")
    if summary.get("handoff_sha256") != bundle.get("self_sha256"):
        raise Gate7Blocked("Gate 7A handoff hash 与机器证据不一致")
    if case["r2_contract"].get("graph_schema_version") == "gate7-r4-v1":
        _revalidate_r4_transcript_evidence(
            case=case,
            summary_dir=summary_path.parent,
            fixture_dir=fixture_dir,
            checkpoints=checkpoints,
        )

    private_root = fixture_dir / "machine-e" / "private"
    canaries = _private_canaries({"private_root": str(private_root)})
    if len(canaries) != 3:
        raise Gate7Blocked("Gate 7A 私有 canary 证据不完整")
    _scan_artifacts(
        canaries,
        [
            summary_path.parent / "machine-e",
            summary_path.parent / "machine-f",
            fixture_dir / "exchange",
            fixture_dir / "machine-f" / "repo",
        ],
    )


def _remote_tag_commit(tag: str) -> str:
    commit = _remote_tag_commit_optional(tag)
    if commit is None:
        raise Gate7Blocked(f"remote annotated tag 不存在：{tag}")
    return commit


def _remote_tag_commit_optional(tag: str) -> str | None:
    result = _run_git_control_command(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}^{{}}"],
        timeout=REMOTE_GIT_TIMEOUT_SECONDS,
        timeout_label=f"读取 remote tag：{tag}",
    )
    if result.returncode != 0:
        raise Gate7Blocked(f"无法读取 remote tag：{tag}")
    line = result.stdout.strip()
    if not line:
        return None
    return line.split(maxsplit=1)[0]


def _run_git_control_command(
    command: list[str],
    *,
    timeout: int,
    timeout_label: str,
) -> subprocess.CompletedProcess[str]:
    """将 Git 控制面超时统一转换为可审计的 fail-closed 结果。"""

    try:
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Gate7Blocked(f"{timeout_label}超时（{timeout}s）") from exc


def _spawn_machine(config_path: Path) -> subprocess.CompletedProcess[str]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    script_args = [
        str(Path(__file__).resolve()),
        "--internal-machine",
        "--config",
        str(config_path.resolve()),
    ]
    command = [sys.executable, *script_args]
    if config["engine"] == "langgraph":
        command = [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--with",
            "langgraph>=1.2,<1.3",
            "--with",
            "langgraph-checkpoint-sqlite>=3.1,<3.2",
            "--with",
            "pyyaml>=6.0",
            "python",
            *script_args,
        ]
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=WORKER_TIMEOUT_SECONDS * 3,
    )


def _process_output_tail(process: subprocess.CompletedProcess[str]) -> str:
    """同时保留子控制进程的 stdout/stderr 尾部，避免异常 traceback 被静默丢失。"""

    stdout = process.stdout or ""
    stderr = process.stderr or ""
    return redact_text(
        f"stdout={stdout[-2000:]}, stderr={stderr[-2000:]}"
    )


def _checkpoint_payload(evidence: CheckpointEvidence) -> dict[str, Any]:
    return {
        "checkpoint": evidence.checkpoint,
        "machine": evidence.machine,
        "attempt_id": evidence.attempt_id,
        "logical_operation_id": evidence.logical_operation_id,
        "prompt_sha256": evidence.prompt_sha256,
        "output_sha256": evidence.output_sha256,
        "tokens_used": evidence.tokens_used,
        "changed_files": evidence.changed_files,
        "diff_lines": evidence.diff_lines,
        "commit_sha": evidence.commit_sha,
        "tree_sha": evidence.tree_sha,
        "parent_sha": evidence.parent_sha,
        "ref_name": evidence.ref_name,
        "verification": evidence.verification,
        "elapsed_seconds": round(evidence.elapsed_seconds, 3),
        "transcript_audit": evidence.transcript_audit,
    }


def _render_report(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    final_identity = summary["final_identity"]
    engine_state = summary["handoff_engine_state"]
    recovery = metrics["engine_recovery"]
    checkpoints = [
        *summary["machine_e"]["checkpoints"],
        *summary["machine_f"]["checkpoints"],
    ]
    lines = [
        "# Gate 7 大任务双节点执行报告",
        "",
        f"- 状态：`{summary['status']}`",
        f"- runner：`{summary['runner_mode']}`",
        f"- engine：`{summary['engine']}`",
        f"- session：`{summary['session']}`",
        f"- case SHA-256：`{summary['case_sha256']}`",
        f"- plan SHA-256：`{summary['plan_sha256']}`",
        f"- handoff SHA-256：`{summary['handoff_sha256']}`",
        f"- final tree：`{final_identity['tree_sha']}`",
        f"- final diff bytes：`{final_identity['diff_bytes']}`",
        f"- final diff SHA-256：`{final_identity['diff_sha256']}`",
        "- 拓扑：`单宿主机双节点模拟`",
        "- 真实物理换机已证明：`false`",
        "- request max retries：`0`",
        "- stream max retries：`0`",
        "",
        "## Checkpoints",
        "",
    ]
    for item in checkpoints:
        lines.extend(
            [
                f"### {item['checkpoint']}",
                "",
                f"- machine：`{item['machine']}`",
                f"- commit：`{item['commit_sha']}`",
                f"- tree：`{item['tree_sha']}`",
                f"- ref：`{item['ref_name']}`",
                f"- files：`{len(item['changed_files'])}`",
                f"- cumulative diff lines：`{item['diff_lines']}`",
                f"- tokens：`{item['tokens_used']}`",
                f"- transcript audit：`{item['transcript_audit']['status']}`",
                f"- exec commands：`{item['transcript_audit']['command_count']}`",
                f"- tool waves：`{item['transcript_audit']['tool_wave_count']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Metrics",
            "",
            f"- execution slots：`{metrics['execution_slots_used']}`",
            f"- provider sessions：`{metrics['provider_sessions_used']}`",
            f"- automatic retries：`{metrics['automatic_retry_count']}`",
            f"- recovery events：`{metrics['recovery_event_count']}`",
            f"- planned migration events：`{metrics['planned_migration_count']}`",
            f"- duplicate effects：`{metrics['duplicate_external_effect_count']}`",
            f"- duplicate claim rejected：`{metrics['duplicate_claim_rejected']}`",
            f"- canary leak：`{metrics['canary_leak_count']}`",
            f"- sensitive material hits：`{metrics['sensitive_material_hit_count']}`",
            f"- artifact files scanned：`{metrics['artifact_files_scanned']}`",
            f"- artifact files skipped：`{metrics['artifact_files_skipped']}`",
            f"- tokens total：`{metrics['tokens_used_total']}`",
            f"- transcript audit required：`{metrics['transcript_audit_required']}`",
            f"- transcript audit passed：`{metrics['transcript_audit_passed']}`",
            f"- transcript exec commands：`{metrics['transcript_exec_commands_total']}`",
            f"- transcript tool waves：`{metrics['transcript_tool_waves_total']}`",
            f"- transcript command output bytes："
            f"`{metrics['transcript_command_output_bytes_total']}`",
            f"- transcript bytes：`{metrics['transcript_bytes_total']}`",
            "",
            "## Engine Recovery",
            "",
            f"- state kind：`{engine_state['kind']}`",
            f"- state bytes before：`{recovery['state_bytes_before']}`",
            f"- state bytes after：`{recovery['state_bytes_after']}`",
            f"- checkpoint count before：`{recovery['checkpoint_count_before']}`",
            f"- checkpoint count after：`{recovery['checkpoint_count_after']}`",
            f"- before phase：`{recovery['before_phase']}`",
            f"- after phase：`{recovery['after_phase']}`",
            f"- replayed external attempts：`{recovery['replayed_external_attempts']}`",
            f"- target external attempts：`{recovery['target_external_attempts']}`",
            f"- recovery elapsed seconds：`{recovery['recovery_elapsed_seconds']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gate 7 real-project three-checkpoint dual-node dogfood"
    )
    parser.add_argument("--runner", choices=["fake", "real"], default="fake")
    parser.add_argument("--engine", choices=["linear", "langgraph"], default="linear")
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--session")
    parser.add_argument(
        "--output-root",
        default=".local-validation/gate-7",
    )
    parser.add_argument(
        "--fixture-root",
        default=".tmp/gate-7",
    )
    parser.add_argument("--internal-machine", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.internal_machine and not args.config:
        parser.error("--internal-machine 需要 --config")
    return args


def _project_path(path: Path, label: str) -> Path:
    resolved = path if path.is_absolute() else PROJECT_ROOT / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit(f"{label} 必须位于当前项目内") from exc
    return resolved


def _changed_files(repo: Path, base: str) -> list[str]:
    output = _git_output(repo, "diff", "--name-only", "--diff-filter=ACDMRTUXB", base)
    return [PurePosixPath(item).as_posix() for item in output.splitlines() if item]


def _scope_outside_diff_digest(
    repo: Path,
    scope: list[str],
    *,
    base: str = BASE_SHA,
) -> str:
    exclusions = [f":(exclude){path}" for path in scope]
    return _git_binary_digest(
        repo,
        "diff",
        "--binary",
        "--full-index",
        base,
        "--",
        ".",
        *exclusions,
    )


def _git_guard_snapshot(repo: Path) -> dict[str, str]:
    return {
        "head": _git_output(repo, "rev-parse", "HEAD"),
        "refs": _git_binary_digest(repo, "for-each-ref", "--format=%(refname)%00%(objectname)"),
        "config": _sha256_bytes((repo / ".git" / "config").read_bytes()),
        "index_tree": _git_output(repo, "write-tree"),
        "remote_origin": _git_output(repo, "remote", "get-url", "origin"),
    }


def _assert_worker_git_guard(repo: Path, before: dict[str, str]) -> None:
    current = _git_guard_snapshot(repo)
    changed = [key for key in before if current.get(key) != before[key]]
    if changed:
        raise Gate7Failure(
            "worker 修改了 Git HEAD/index/refs/config/remote：" + ", ".join(changed)
        )


def _private_canaries(config: dict[str, Any]) -> list[str]:
    private_root = config.get("private_root")
    if not private_root:
        return []
    root = Path(private_root)
    values = []
    for name in ("source-chat.txt", "memory-ledger.txt", "machine-path.txt"):
        path = root / name
        if path.exists():
            values.append(path.read_text(encoding="utf-8"))
    return values


def _git_dir_changed_files(git_dir: Path, base: str, head: str) -> list[str]:
    output = _git_dir_output(
        git_dir,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        base,
        head,
    )
    return [PurePosixPath(item).as_posix() for item in output.splitlines() if item]


def _diff_line_count(repo: Path, base: str) -> int:
    output = _git_output(repo, "diff", "--numstat", base)
    total = 0
    for line in output.splitlines():
        added, deleted, _ = line.split("\t", 2)
        if added == "-" or deleted == "-":
            raise Gate7Failure("Gate 7 不允许 binary diff")
        total += int(added) + int(deleted)
    return total


def _assert_no_untracked_files(repo: Path) -> None:
    status = _git_output(repo, "status", "--porcelain=v1", "--untracked-files=all")
    unexpected = [
        line
        for line in status.splitlines()
        if line.startswith("?? ")
        and not _is_known_cache_path(PurePosixPath(line[3:]).as_posix())
    ]
    if unexpected:
        raise Gate7Failure(f"worker 创建了未跟踪文件：{unexpected}")


def _is_known_cache_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(part in {".pytest_cache", "__pycache__", ".venv"} for part in parts)


def _workspace_fingerprint(repo: Path) -> str:
    payload = {
        "head": _git_output(repo, "rev-parse", "HEAD"),
        "status": _git_output(
            repo,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        "diff": _git_binary_digest(repo, "diff", "--binary", "HEAD"),
        "cached": _git_binary_digest(repo, "diff", "--binary", "--cached", "HEAD"),
    }
    return _sha256_text(_canonical_json(payload))


def _git_binary_digest(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return _sha256_bytes(result.stdout)


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    input_text: str | None = None,
) -> CommandEvidence:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_stream_text(exc.stdout)
        stderr = _timeout_stream_text(exc.stderr)
        output = redact_text(
            f"{stdout}{stderr}\ncommand timed out after {timeout} seconds"
        )
        return CommandEvidence(
            command=command,
            returncode=124,
            output=output,
            elapsed_seconds=time.monotonic() - started,
        )
    output = redact_text(f"{result.stdout}{result.stderr}")
    return CommandEvidence(
        command=command,
        returncode=result.returncode,
        output=output,
        elapsed_seconds=time.monotonic() - started,
    )


def _timeout_stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _require_command(
    evidence: CommandEvidence,
    *,
    returncode: int,
    label: str,
    contains: str | None = None,
) -> None:
    if evidence.returncode != returncode:
        raise Gate7Failure(
            f"{label} returncode 错误：{evidence.returncode}, output={evidence.output[-2000:]}"
        )
    if contains is not None and contains not in evidence.output:
        raise Gate7Failure(
            f"{label} 缺少终态文本 {contains!r}：{evidence.output[-2000:]}"
        )


def _last_nonempty_line(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    allow_returncodes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    accepted = allow_returncodes or {0}
    if result.returncode not in accepted:
        raise Gate7Blocked(
            f"命令失败：{command!r}\nstdout={result.stdout[-1000:]}\n"
            f"stderr={result.stderr[-1000:]}"
        )
    return result


def _git(repo: Path, *args: str) -> None:
    _run_checked(["git", *args], cwd=repo, timeout=30)


def _git_output(repo: Path, *args: str) -> str:
    return _run_checked(["git", *args], cwd=repo, timeout=30).stdout.strip()


def _git_dir_output(git_dir: Path, *args: str) -> str:
    return _run_checked(
        ["git", f"--git-dir={git_dir}", *args],
        cwd=git_dir.parent,
        timeout=30,
    ).stdout.strip()


def _git_dir_bytes(git_dir: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        cwd=git_dir.parent,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return result.stdout


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_json_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_terminal_state(
    output_dir: Path,
    *,
    terminal_status: str,
    stop_reason: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "terminal-state.json"
    if path.exists():
        return
    _write_json_exclusive(
        path,
        {
            "schema_version": 1,
            "terminal_status": terminal_status,
            "stop_reason": redact_text(stop_reason),
            "b_started": False,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _resolve_codex_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    if not resolved:
        raise Gate7Blocked(f"未找到 {executable}")
    return resolved


def _codex_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Gate7Blocked(f"Codex 版本检查启动失败：{exc}") from exc
    match = re.search(r"codex-cli\s+([0-9.]+)", f"{result.stdout}{result.stderr}")
    if result.returncode != 0 or match is None:
        raise Gate7Blocked("无法确认 Codex CLI version")
    return match.group(1)


def _codex_auth_mode(executable: str) -> AuthMode | None:
    try:
        result = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Gate7Blocked(f"Codex 登录状态检查启动失败：{exc}") from exc
    output = f"{result.stdout}{result.stderr}".casefold()
    if result.returncode != 0:
        return None
    if "chatgpt" in output:
        return "chatgpt"
    if "api key" in output:
        return "api-key"
    return None


def _assert_provider_endpoint_reachable() -> None:
    parsed = urlsplit(PROVIDER_BASE_URL)
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        raise Gate7Blocked("provider endpoint 缺少 host 或 port")
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError as exc:
        raise Gate7Blocked(f"provider loopback endpoint 不可达：{exc}") from exc


def _parse_codex_header(output: str) -> dict[str, str]:
    header: dict[str, str] = {}
    for line in output.splitlines()[:80]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized = key.strip().casefold()
        if normalized in {"provider", "model", "reasoning effort"}:
            header[normalized] = value.strip()
    return header


def _parse_tokens_used(output: str) -> int | None:
    match = re.search(r"tokens used\s*[\r\n]+\s*([\d,]+)", output, flags=re.IGNORECASE)
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


if __name__ == "__main__":
    raise SystemExit(main())
