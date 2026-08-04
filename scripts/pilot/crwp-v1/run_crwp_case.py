from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import vega.loop_runtime as loop_runtime_module
from vega.brief_generator import BriefInput
from vega.execution_control import (
    ExecutionLease,
    OwnedProcessResult,
    RunnerExecutionContext,
    request_stop_for_run,
    run_owned_process,
)
from vega.finish_runtime import FinishRuntime
from vega.finish_policy import decide_finish_status
from vega.loop_evidence import validate_loop_evidence_snapshot
from vega.loop_integrity import (
    latest_verification_failed,
    trusted_verification_passed,
)
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import LoopAutomationState
from vega.project_config import CodexExecOptions, load_project_config
from vega.recovery_runtime import RecoveryRuntime
from vega.redaction import redact_text
from vega.run_utils import resolve_run_dir
from vega.runner import CodexExecRunner, RunnerResult
from vega.workspace_check import capture_review_workspace


TOTAL_WALL_CLOCK_SECONDS = 30 * 60
RUNNER_TIMEOUT_SECONDS = 15 * 60
STARTUP_TIMEOUT_SECONDS = 5 * 60
SUPERVISOR_PROCESS_TIMEOUT_SECONDS = 40 * 60
STOP_GRACE_SECONDS = 30
STOP_REASON = "CRWP-V1 单项 30 分钟总墙钟已到"
CHILD_LEASE_WAIT_SECONDS = 5
QUALIFICATION_MAX_AGE_SECONDS = 24 * 60 * 60
LAUNCH_NONCE_ENV = "CRWP_V1_SUPERVISOR_NONCE"
FORMAL_EVIDENCE_ROOT = Path(".local-validation/crwp-v1/formal-runs")
CONTROL_MANIFEST_PATH = Path(
    "scripts/pilot/crwp-v1/crwp-v1-case02-control-manifest.json"
)
CONTROL_FILE_PATHS = {
    "controller": "scripts/pilot/crwp-v1/run_crwp_case.py",
    "case_01_task": "scripts/pilot/crwp-v1/tasks/crwp-v1-01-task.md",
    "case_02_task": "scripts/pilot/crwp-v1/tasks/crwp-v1-02-task.md",
    "case_01_oracle": "scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py",
    "case_02_oracle": (
        "scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs"
    ),
    "case_02_blocked_terms": (
        "scripts/pilot/crwp-v1/crwp-v1-02-blocked-terms.txt"
    ),
    "native_driver_helper": "scripts/pilot/crwp-v1/ignore-native-drivers.cjs",
    "control_tests": "tests/test_crwp_v1_control.py",
}
QUALIFICATION_CONTRACTS = {
    "summary": {
        "path": ".local-validation/crwp-v1/preflight-20260804/qualification/summary.json",
        "sha256": "876495381e3c4f6f449debbe0a1692d0cac6214c0ac44b0a40f1c3d4260c09c7",
    },
    "manifest": {
        "path": ".local-validation/crwp-v1/preflight-20260804/qualification/manifest.json",
        "sha256": "be9b00ad4c6269f7430ab73692043aa1c6749461cd291a7665dff3f447a0ea06",
    },
}
CASE_CONTRACTS: dict[str, dict[str, str | None]] = {
    "CRWP-V1-01": {
        "target_head": "1b2084e4cae0e88c7fdabee7a851094832f6d0cf",
        "target_parent": "f26ba3748e79c7225f4aafb757c6f9f1f6b2d733",
        "target_tree": "7735e8269afab4a26b2b7c8cf66e074961f8ce28",
        "config_sha256": "d4322d5ce2c9e86dad259bfcf4795dc70d548a81eb01d115d5f84cc40c2711a7",
        "task_path": "scripts/pilot/crwp-v1/tasks/crwp-v1-01-task.md",
        "task_sha256": "25400d4a907ee90153cf9f69f659a44a06dc197fff7c67789b1b6f971033401c",
        "blocked_path": None,
        "blocked_sha256": None,
        "baseline_path": ".local-validation/crwp-v1/preflight-20260731/case-01/baseline/summary-final.json",
        "baseline_sha256": "52b754c163d5444e722ed5495c91b142a46b016b17b03a3a1024bc427f4645ff",
        "oracle_path": "scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py",
        "oracle_sha256": "a1a9152a9d96f0ac935f6c26baccba7ce4632453b388ba570ceb62731ae65b5f",
        "native_helper_path": None,
        "native_helper_sha256": None,
        "native_path": None,
        "native_sha256": None,
    },
    "CRWP-V1-02": {
        "target_head": "18431b84c44eaa14736a2f4f6e9d92fe812a923e",
        "target_parent": "f0cea95e38b4f2c9096267371ab305d08f7b8497",
        "target_tree": "67f271bb1fbd2506fc556ecab4ea319b827b234f",
        "config_sha256": "844800d61f6dbd016357e796f3db5bb7f371b22c0cdc1e50ccf25d47e92b2024",
        "task_path": "scripts/pilot/crwp-v1/tasks/crwp-v1-02-task.md",
        "task_sha256": "803c3a516e833d41a8cb8c3009595fa66d2386776204ff8d14fea18dc2c22ad6",
        "blocked_path": "scripts/pilot/crwp-v1/crwp-v1-02-blocked-terms.txt",
        "blocked_sha256": "3c6cb6f708588702865b15cab2fa0dc0bb7a0044401e4cd7fe154a6cf40d05d8",
        "baseline_path": ".local-validation/crwp-v1/preflight-20260804/case-02/baseline-after-native-v3/summary.json",
        "baseline_sha256": "229df0aa22bcef2b2b91ceacf18bbe6e43d3c64f044230ac901eb7f10834f59a",
        "oracle_path": "scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs",
        "oracle_sha256": "f784abc3518e12991f3f0b93628773adda1d68c9add4fe2a75d9e93b318e93d0",
        "native_helper_path": "scripts/pilot/crwp-v1/ignore-native-drivers.cjs",
        "native_helper_sha256": "2e6a0f95133df1ba2a928d2f99be5068ee13bad75e2bba01b10213b284020bde",
        "native_path": "node_modules/sqlite3/build/Release/node_sqlite3.node",
        "native_sha256": "5e1d1275e126c3fc584bcf5752fbf747bff89454bfcf8bc76c982b24e7815057",
    },
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """控制证据不得覆盖已有文件，重跑必须使用新的 evidence 目录。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _read_blocked_terms(path: Path | None) -> list[str]:
    if path is None:
        return []
    terms = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not terms:
        raise ValueError("负向词表不能为空")
    if len(terms) != len(set(terms)):
        raise ValueError("负向词表包含重复项")
    return terms


def _prompt_line_count(prompt: str) -> int:
    return len(prompt.splitlines())


class WorkerStartRecorder:
    """记录根 run 身份，并在真正调用第一轮 worker 前发布墙钟起点。"""

    def __init__(self, evidence_dir: Path) -> None:
        self.evidence_dir = evidence_dir
        self._lock = threading.Lock()
        self.created_run_id: str | None = None
        self.run_id: str | None = None

    def record_created(self, run_id: str) -> None:
        with self._lock:
            if self.created_run_id is not None:
                if self.created_run_id != run_id:
                    raise ValueError("根 run 身份已绑定其他 run_id")
                return
            self.created_run_id = run_id
            _write_json_exclusive(
                self.evidence_dir / "run-created.json",
                {
                    "run_created_at": _utc_now(),
                    "run_created_monotonic": time.monotonic(),
                    "run_id": run_id,
                },
            )

    def assert_bound(self, run_id: str) -> None:
        if not run_id:
            raise ValueError("角色调用缺少非空 run_id")
        if self.created_run_id is None:
            raise ValueError("角色调用发生前尚未登记根 run 身份")
        if self.created_run_id != run_id:
            raise ValueError("角色调用 run_id 与已登记根 run 不一致")

    def arm(self, run_id: str) -> None:
        with self._lock:
            self.assert_bound(run_id)
            if self.run_id is not None:
                if self.run_id != run_id:
                    raise ValueError("worker 起点记录已绑定其他 run_id")
                return
            self.run_id = run_id
            _write_json_exclusive(
                self.evidence_dir / "wall-clock-start.json",
                {
                    "deadline_seconds": TOTAL_WALL_CLOCK_SECONDS,
                    "run_id": run_id,
                    "worker_started_at": _utc_now(),
                    "worker_started_monotonic": time.monotonic(),
                },
            )


@contextmanager
def _record_loop_creation(recorder: WorkerStartRecorder) -> Iterator[None]:
    """只在 Pilot 子进程内包裹 run 创建点，不修改产品 Runtime 接口。"""

    original_create_run_dir = loop_runtime_module.create_run_dir

    def recording_create_run_dir(
        workspace: Path,
        base_run_id: str,
        *,
        max_attempts: int = 100,
    ) -> tuple[str, Path]:
        run_id, run_dir = original_create_run_dir(
            workspace,
            base_run_id,
            max_attempts=max_attempts,
        )
        recorder.record_created(run_id)
        return run_id, run_dir

    loop_runtime_module.create_run_dir = recording_create_run_dir
    try:
        yield
    finally:
        loop_runtime_module.create_run_dir = original_create_run_dir


class PromptAuditRunner:
    """在外部角色调用前记录最终 prompt 哈希，并对冻结词表 fail-closed。"""

    def __init__(
        self,
        *,
        role: str,
        inner: CodexExecRunner,
        evidence_dir: Path,
        blocked_terms: list[str],
        worker_start: WorkerStartRecorder,
        expected_repo: Path,
        expected_workspace: Path,
    ) -> None:
        self.role = role
        self.inner = inner
        self.evidence_dir = evidence_dir
        self.blocked_terms = list(blocked_terms)
        self.worker_start = worker_start
        self.expected_repo = expected_repo.resolve()
        self.expected_workspace = expected_workspace.resolve()
        self.call_count = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: Any = None,
    ) -> RunnerResult:
        self.call_count += 1
        normalized_prompt = redact_text(prompt)
        prompt_bytes = normalized_prompt.encode("utf-8")
        matches = [
            term
            for term in self.blocked_terms
            if term.encode("utf-8") in prompt_bytes
        ]
        iteration = getattr(execution_context, "iteration", None)
        run_id = getattr(execution_context, "run_id", None)
        validation_errors: list[str] = []
        expected_sandbox = (
            "workspace-write" if self.role == "worker" else "read-only"
        )
        if not isinstance(execution_context, RunnerExecutionContext):
            validation_errors.append("角色调用缺少 RunnerExecutionContext")
        else:
            if execution_context.step != self.role:
                validation_errors.append("execution step 与角色不一致")
            if not isinstance(iteration, int) or iteration < 1:
                validation_errors.append("角色调用缺少有效 iteration")
        if sandbox != expected_sandbox:
            validation_errors.append("角色 sandbox 与冻结边界不一致")
        if repo_path.resolve() != self.expected_repo:
            validation_errors.append("角色调用目标仓库与冻结路径不一致")
        if not isinstance(run_id, str) or not run_id:
            validation_errors.append("角色调用缺少可核验 run_id")
        else:
            try:
                self.worker_start.assert_bound(run_id)
            except ValueError as exc:
                validation_errors.append(str(exc))
            if isinstance(iteration, int) and iteration >= 1:
                expected_execution_dir = (
                    self.expected_workspace
                    / "runs"
                    / run_id
                    / "iterations"
                    / f"{iteration:02d}"
                    / "executions"
                    / self.role
                ).resolve()
                if execution_context.execution_dir.resolve() != expected_execution_dir:
                    validation_errors.append(
                        "execution_dir 与冻结 run/iteration/role 边界不一致"
                    )
        suffix = (
            f"iteration-{iteration:02d}"
            if isinstance(iteration, int)
            else f"call-{self.call_count:02d}"
        )
        audit_base = self.evidence_dir / "prompt-audits" / f"{self.role}-{suffix}"
        _write_json_exclusive(
            audit_base.with_name(f"{audit_base.name}-precall.json"),
            {
                "allowed_to_invoke": not matches and not validation_errors,
                "blocked_term_count": len(self.blocked_terms),
                "blocked_terms_sha256": _sha256_bytes(
                    "\n".join(self.blocked_terms).encode("utf-8")
                ),
                "matched_term_count": len(matches),
                "matched_term_sha256": [
                    _sha256_bytes(term.encode("utf-8")) for term in matches
                ],
                "prompt_chars": len(normalized_prompt),
                "prompt_lines": _prompt_line_count(normalized_prompt),
                "prompt_sha256": _sha256_bytes(prompt_bytes),
                "prompt_utf8_bytes": len(prompt_bytes),
                "role": self.role,
                "role_boundary_errors": validation_errors,
                "run_id": run_id,
                "sandbox": sandbox,
                "timestamp": _utc_now(),
            },
        )
        if matches:
            return RunnerResult(
                status="error",
                output="",
                error="最终角色输入命中冻结负向词表，未启动外部 runner。",
                command=[],
            )
        if validation_errors:
            return RunnerResult(
                status="error",
                output="",
                error=(
                    "角色调用不满足冻结会话边界，未启动外部 runner："
                    + "；".join(validation_errors)
                ),
                command=[],
            )
        if self.role == "worker":
            self.worker_start.arm(run_id)

        started = time.monotonic()
        try:
            result = self.inner.run(
                normalized_prompt,
                repo_path,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
                execution_context=execution_context,
            )
        except Exception as exc:  # noqa: BLE001 - 控制 runner 必须把异常转成 fail-closed 终态
            _write_json_exclusive(
                audit_base.with_name(f"{audit_base.name}-postcall.json"),
                {
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "error": redact_text(str(exc)),
                    "role": self.role,
                    "status": "exception",
                    "timestamp": _utc_now(),
                },
            )
            return RunnerResult(
                status="error",
                output="",
                error="外部 runner 调用异常，已停止自动流程。",
                command=[],
            )
        _write_json_exclusive(
            audit_base.with_name(f"{audit_base.name}-postcall.json"),
            {
                "duration_seconds": round(time.monotonic() - started, 3),
                "role": self.role,
                "status": result.status,
                "termination_unconfirmed": result.termination_unconfirmed,
                "timestamp": _utc_now(),
            },
        )
        return result


def _assert_runner_config(repo: Path) -> tuple[CodexExecOptions, CodexExecOptions]:
    config = load_project_config(repo)
    worker = config.runner.codex_exec.worker
    reviewer = config.runner.codex_exec.reviewer
    expected_worker = CodexExecOptions(
        model="gpt-5.4",
        reasoning_effort="medium",
        ephemeral=True,
    )
    expected_reviewer = CodexExecOptions(
        model="gpt-5.4",
        reasoning_effort="high",
        ephemeral=True,
    )
    if config.runner.worker != "codex-exec" or config.runner.reviewer != "codex-exec":
        raise ValueError("目标配置未同时固定 worker/reviewer=codex-exec")
    if worker != expected_worker or reviewer != expected_reviewer:
        raise ValueError("目标配置的模型、reasoning 或 ephemeral 参数与 Pilot 合同不一致")
    return worker, reviewer


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"Git 预检失败：{redact_text(completed.stderr.strip())}")
    return completed.stdout


def _git_bytes(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git 预检失败：{redact_text(stderr)}")
    return completed.stdout


def _is_lower_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_file_contract(
    workspace: Path,
    relative_path: str,
    expected_sha256: str,
    label: str,
) -> dict[str, Any]:
    path = (workspace / relative_path).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} 路径越过 Vega 工作区") from exc
    if not path.is_file():
        raise ValueError(f"{label} 缺失")
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(f"{label} SHA-256 与冻结值不一致")
    return {
        "path": relative_path,
        "sha256": actual_sha256,
        "size": path.stat().st_size,
    }


def _assert_registered_preflight(
    case_id: str,
    workspace: Path,
    contract: dict[str, str | None],
) -> dict[str, Any]:
    qualification: dict[str, Any] = {}
    for name, item in QUALIFICATION_CONTRACTS.items():
        qualification[name] = _require_file_contract(
            workspace,
            item["path"],
            item["sha256"],
            f"资格证据 {name}",
        )
    qualification_summary_path = (
        workspace / QUALIFICATION_CONTRACTS["summary"]["path"]
    )
    qualification_payload = json.loads(
        qualification_summary_path.read_text(encoding="utf-8")
    )
    if qualification_payload.get("overall_preflight_component_passed") is not True:
        raise ValueError("资格与 Provider 证据未形成通过终态")
    if qualification_payload.get("active_case_ids") != [case_id]:
        raise ValueError("资格与 Provider 证据未精确限定当前 Case")
    try:
        generated_at = datetime.fromisoformat(
            qualification_payload["generated_at_utc"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("资格证据缺少有效生成时间") from exc
    qualification_age_seconds = (
        datetime.now(UTC) - generated_at
    ).total_seconds()
    if (
        qualification_age_seconds < -300
        or qualification_age_seconds > QUALIFICATION_MAX_AGE_SECONDS
    ):
        raise ValueError("资格与 Provider 证据已过期，正式运行前必须重新复核")

    baseline_path = contract["baseline_path"]
    baseline_sha256 = contract["baseline_sha256"]
    if baseline_path is None or baseline_sha256 is None:
        raise ValueError("Case 缺少冻结 baseline 合同")
    baseline = _require_file_contract(
        workspace,
        baseline_path,
        baseline_sha256,
        "Case baseline 证据",
    )
    payload = json.loads(
        (workspace / baseline_path).read_text(encoding="utf-8")
    )
    if payload.get("case_id") != case_id:
        raise ValueError("Case baseline 的 case_id 与当前运行不一致")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Case baseline 缺少可核验命令记录")
    if not all(item.get("matched_expectation") is True for item in runs):
        raise ValueError("Case baseline 存在未满足冻结预期的命令")
    if case_id == "CRWP-V1-02" and payload.get("final", {}).get("accepted") is not True:
        raise ValueError("Case 02 baseline 未形成 accepted 终态")

    return {
        "baseline": baseline,
        "qualification": qualification,
        "qualification_generated_at": generated_at.isoformat(),
    }


def _assert_runtime_ready(
    workspace: Path,
    registration_head: str,
) -> dict[str, Any]:
    status = _git(
        workspace,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
    )
    if status.strip():
        raise ValueError("Vega Runtime 工作区不是干净的已提交状态")

    if not _is_lower_hex(registration_head, 40):
        raise ValueError("登记提交必须是完整的小写 Git commit SHA")
    head = _git(workspace, "rev-parse", "HEAD").strip()
    if head != registration_head:
        raise ValueError("Vega Runtime 必须精确 checkout 到登记提交")

    manifest_relative = CONTROL_MANIFEST_PATH.as_posix()
    _git(
        workspace,
        "ls-files",
        "--error-unmatch",
        "--",
        manifest_relative,
    )
    manifest_path = workspace / CONTROL_MANIFEST_PATH
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Runtime 冻结 manifest 无法解析") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Runtime 冻结 manifest 顶层必须是对象")
    if set(manifest) != {
        "control_files",
        "pilot_id",
        "runtime_commit",
        "runtime_src_tree",
        "runtime_tree",
        "schema_version",
    }:
        raise ValueError("Runtime 冻结 manifest 字段集合不符合 V1 合同")
    if manifest.get("schema_version") != 1 or manifest.get("pilot_id") != "CRWP-V1":
        raise ValueError("Runtime 冻结 manifest 的版本或 pilot_id 不一致")

    runtime_commit = manifest.get("runtime_commit")
    runtime_tree = manifest.get("runtime_tree")
    runtime_src_tree = manifest.get("runtime_src_tree")
    if not _is_lower_hex(runtime_commit, 40):
        raise ValueError("Runtime 冻结 manifest 缺少完整 runtime_commit")
    if not _is_lower_hex(runtime_tree, 40):
        raise ValueError("Runtime 冻结 manifest 缺少完整 runtime_tree")
    if not _is_lower_hex(runtime_src_tree, 40):
        raise ValueError("Runtime 冻结 manifest 缺少完整 runtime_src_tree")

    registration_line = _git(
        workspace,
        "rev-list",
        "--parents",
        "-n",
        "1",
        registration_head,
    ).strip()
    registration_parts = registration_line.split()
    if (
        len(registration_parts) != 2
        or registration_parts[0] != registration_head
        or registration_parts[1] != runtime_commit
    ):
        raise ValueError("登记提交必须是 runtime_commit 的单父直接子提交")

    changed = [
        line
        for line in _git(
            workspace,
            "diff",
            "--name-status",
            runtime_commit,
            registration_head,
            "--",
        ).splitlines()
        if line
    ]
    if changed != [f"A\t{manifest_relative}"]:
        raise ValueError("登记提交只能新增 Runtime 冻结 manifest")

    registered_manifest_bytes = _git_bytes(
        workspace,
        "show",
        f"{registration_head}:{manifest_relative}",
    )
    if registered_manifest_bytes != manifest_bytes:
        raise ValueError("工作区 manifest 与登记提交中的 Git blob 不一致")

    actual_runtime_tree = _git(
        workspace,
        "rev-parse",
        f"{runtime_commit}^{{tree}}",
    ).strip()
    if actual_runtime_tree != runtime_tree:
        raise ValueError("runtime_commit tree 与 manifest 不一致")
    frozen_src_tree = _git(
        workspace,
        "rev-parse",
        f"{runtime_commit}:src",
    ).strip()
    if frozen_src_tree != runtime_src_tree:
        raise ValueError("runtime_commit src tree 与 manifest 不一致")
    current_src_tree = _git(
        workspace,
        "rev-parse",
        f"{registration_head}:src",
    ).strip()
    if current_src_tree != runtime_src_tree:
        raise ValueError("登记提交的 src tree 与冻结 Runtime 不一致")

    entries = manifest.get("control_files")
    if not isinstance(entries, dict) or set(entries) != set(CONTROL_FILE_PATHS):
        raise ValueError("Runtime 冻结 manifest 的控制文件集合不完整")
    control_files: dict[str, Any] = {}
    for name, expected_path in CONTROL_FILE_PATHS.items():
        entry = entries.get(name)
        if not isinstance(entry, dict) or set(entry) != {
            "blob_oid",
            "path",
            "sha256",
            "size",
        }:
            raise ValueError(f"{name} manifest 条目字段不完整")
        if entry.get("path") != expected_path:
            raise ValueError(f"{name} manifest 路径与冻结合同不一致")
        if not _is_lower_hex(entry.get("blob_oid"), 40):
            raise ValueError(f"{name} manifest 缺少有效 Git blob OID")
        if not _is_lower_hex(entry.get("sha256"), 64):
            raise ValueError(f"{name} manifest 缺少有效 SHA-256")
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"{name} manifest 缺少有效文件大小")

        _git(workspace, "ls-files", "--error-unmatch", "--", expected_path)
        runtime_blob_oid = _git(
            workspace,
            "rev-parse",
            f"{runtime_commit}:{expected_path}",
        ).strip()
        registered_blob_oid = _git(
            workspace,
            "rev-parse",
            f"{registration_head}:{expected_path}",
        ).strip()
        if (
            runtime_blob_oid != entry["blob_oid"]
            or registered_blob_oid != entry["blob_oid"]
        ):
            raise ValueError(f"{name} Git blob 与冻结 manifest 不一致")
        runtime_bytes = _git_bytes(
            workspace,
            "show",
            f"{runtime_commit}:{expected_path}",
        )
        current_bytes = (workspace / expected_path).read_bytes()
        if runtime_bytes != current_bytes:
            raise ValueError(f"{name} 工作区字节与 runtime_commit 不一致")
        actual_sha256 = _sha256_bytes(current_bytes)
        if actual_sha256 != entry["sha256"] or len(current_bytes) != size:
            raise ValueError(f"{name} SHA-256 或大小与冻结 manifest 不一致")
        control_files[name] = dict(entry)

    return {
        "branch": _git(workspace, "branch", "--show-current").strip(),
        "control_files": control_files,
        "head": head,
        "manifest": {
            "path": manifest_relative,
            "sha256": _sha256_bytes(manifest_bytes),
            "size": len(manifest_bytes),
        },
        "registration_head": registration_head,
        "runtime_commit": runtime_commit,
        "runtime_src_tree": runtime_src_tree,
        "runtime_tree": runtime_tree,
    }


def _assert_frozen_target_files(
    repo: Path,
    workspace: Path,
    contract: dict[str, str | None],
) -> dict[str, Any]:
    files = {
        "target_config": (
            repo / ".vega.yaml",
            str(contract["config_sha256"]),
        ),
        "oracle": (
            workspace / str(contract["oracle_path"]),
            str(contract["oracle_sha256"]),
        ),
    }
    if contract["native_path"] is not None:
        files["sqlite_native"] = (
            repo / str(contract["native_path"]),
            str(contract["native_sha256"]),
        )
    if contract["native_helper_path"] is not None:
        files["native_driver_helper"] = (
            workspace / str(contract["native_helper_path"]),
            str(contract["native_helper_sha256"]),
        )
    result: dict[str, Any] = {}
    for name, (path, expected_sha256) in files.items():
        if not path.is_file():
            raise ValueError(f"{name} 控制文件缺失")
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != expected_sha256:
            raise ValueError(f"{name} 控制文件 SHA-256 与冻结值不一致")
        result[name] = {
            "sha256": actual_sha256,
            "size": path.stat().st_size,
        }
    return result


def _assert_target_ready(repo: Path, contract: dict[str, str | None]) -> str:
    if _git(repo, "status", "--porcelain=v2", "--untracked-files=all").strip():
        raise ValueError("目标仓库在正式 Worker 前不是干净工作区")
    if _git(repo, "remote").strip():
        raise ValueError("目标仓库仍保留 Git remote")
    head_sha = _git(repo, "rev-parse", "HEAD").strip()
    parent_sha = _git(repo, "rev-parse", "HEAD^").strip()
    tree_sha = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    if head_sha != contract["target_head"]:
        raise ValueError("目标 HEAD 与冻结 prepared HEAD 不一致")
    if parent_sha != contract["target_parent"]:
        raise ValueError("目标 prepared commit parent 与冻结 upstream base 不一致")
    if tree_sha != contract["target_tree"]:
        raise ValueError("目标 prepared tree 与冻结值不一致")
    config_path = repo / ".vega.yaml"
    if not config_path.is_file():
        raise ValueError("目标缺少冻结 .vega.yaml")
    if _sha256_bytes(config_path.read_bytes()) != contract["config_sha256"]:
        raise ValueError("目标 .vega.yaml SHA-256 与冻结值不一致")
    native_path = contract["native_path"]
    if native_path is not None:
        native_file = repo / native_path
        if not native_file.is_file():
            raise ValueError("Case 02 缺少冻结 sqlite3 本机产物")
        if _sha256_bytes(native_file.read_bytes()) != contract["native_sha256"]:
            raise ValueError("Case 02 sqlite3 本机产物 SHA-256 与冻结值不一致")
    return head_sha


def _assert_control_inputs(
    case_id: str,
    workspace: Path,
    task_path: Path,
    blocked_path: Path | None,
) -> dict[str, str | None]:
    contract = CASE_CONTRACTS[case_id]
    task_relative = task_path.relative_to(workspace).as_posix()
    if task_relative != contract["task_path"]:
        raise ValueError("任务合同路径与冻结值不一致")
    if _sha256_bytes(task_path.read_bytes()) != contract["task_sha256"]:
        raise ValueError("任务合同 SHA-256 与冻结值不一致")
    expected_blocked_path = contract["blocked_path"]
    if expected_blocked_path is None:
        if blocked_path is not None:
            raise ValueError("当前 Case 不应加载负向词表")
        return contract
    if blocked_path is None:
        raise ValueError("当前 Case 缺少冻结负向词表")
    blocked_relative = blocked_path.relative_to(workspace).as_posix()
    if blocked_relative != expected_blocked_path:
        raise ValueError("负向词表路径与冻结值不一致")
    if _sha256_bytes(blocked_path.read_bytes()) != contract["blocked_sha256"]:
        raise ValueError("负向词表 SHA-256 与冻结值不一致")
    return contract


def _resolve_under_workspace(path: Path, workspace: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于 Vega 工作区内") from exc
    return resolved


def _assert_formal_path_boundaries(
    repo: Path,
    evidence_dir: Path,
    workspace: Path,
) -> None:
    formal_root = (workspace / FORMAL_EVIDENCE_ROOT).resolve()
    try:
        relative_evidence = evidence_dir.resolve().relative_to(formal_root)
    except ValueError as exc:
        raise ValueError(
            "正式证据目录必须位于 .local-validation/crwp-v1/formal-runs/ 下"
        ) from exc
    if not relative_evidence.parts:
        raise ValueError("正式证据目录必须使用独立的单次运行子目录")

    resolved_repo = repo.resolve()
    resolved_evidence = evidence_dir.resolve()
    try:
        resolved_evidence.relative_to(resolved_repo)
    except ValueError:
        pass
    else:
        raise ValueError("正式证据目录不得位于目标仓库内")
    try:
        resolved_repo.relative_to(resolved_evidence)
    except ValueError:
        pass
    else:
        raise ValueError("目标仓库不得位于正式证据目录内")


def _capture_preworker_workspace(
    repo: Path,
    evidence_dir: Path,
    expected_head: str,
) -> dict[str, Any]:
    snapshot = capture_review_workspace(repo)
    if snapshot.head_sha != expected_head:
        raise ValueError("最终 workspace snapshot 的 HEAD 与冻结值不一致")
    if (
        snapshot.changed_files
        or snapshot.untracked_files
        or snapshot.unsafe_index_paths
        or snapshot.full_diff
    ):
        raise ValueError("最终 workspace snapshot 不是干净工作区")
    if (
        not snapshot.untracked_content_complete
        or not snapshot.ignored_manifest_complete
        or not snapshot.git_control_complete
    ):
        raise ValueError("最终 workspace snapshot 的控制清单不完整")
    payload = asdict(snapshot)
    _write_json_exclusive(
        evidence_dir / "workspace-preflight.json",
        payload,
    )
    return payload


def _prepare_launch_attestation(
    case_id: str,
    evidence_dir: Path,
    supervisor_root: Path,
    registration_head: str,
    runtime_freeze: dict[str, Any],
) -> str:
    nonce = secrets.token_hex(32)
    _write_json_exclusive(
        evidence_dir / "launch-attestation.json",
        {
            "case_id": case_id,
            "created_at": _utc_now(),
            "expected_owner_pid": os.getpid(),
            "nonce_sha256": _sha256_bytes(nonce.encode("utf-8")),
            "registration_head": registration_head,
            "runtime_manifest_sha256": runtime_freeze["manifest"]["sha256"],
            "runtime_commit": runtime_freeze["runtime_commit"],
            "supervisor_execution": str(
                (
                    supervisor_root
                    / "executions"
                    / "controller"
                    / "execution.json"
                ).relative_to(evidence_dir)
            ).replace("\\", "/"),
            "supervisor_run_id": supervisor_root.name,
        },
    )
    return nonce


def _validate_child_launch(
    args: argparse.Namespace,
    evidence_dir: Path,
) -> dict[str, Any]:
    nonce = os.environ.pop(LAUNCH_NONCE_ENV, None)
    if not nonce:
        raise ValueError("内部 child 缺少 supervisor 一次性启动凭据")
    attestation_path = evidence_dir / "launch-attestation.json"
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("无法读取 supervisor 启动凭据") from exc
    if attestation.get("case_id") != args.case_id:
        raise ValueError("supervisor 启动凭据的 case_id 不一致")
    if attestation.get("registration_head") != args.registration_head:
        raise ValueError("supervisor 启动凭据的登记提交不一致")
    if attestation.get("nonce_sha256") != _sha256_bytes(
        nonce.encode("utf-8")
    ):
        raise ValueError("supervisor 一次性启动凭据不匹配")
    if attestation.get("expected_owner_pid") != os.getppid():
        raise ValueError("内部 child 的父进程与 supervisor 凭据不一致")

    execution_relative = attestation.get("supervisor_execution")
    if not isinstance(execution_relative, str) or not execution_relative:
        raise ValueError("supervisor 启动凭据缺少 execution 路径")
    if (
        attestation.get("supervisor_run_id") != "supervisor-run"
        or execution_relative
        != "supervisor-run/executions/controller/execution.json"
    ):
        raise ValueError("supervisor 启动凭据未绑定冻结的 owned execution 位置")
    execution_path = (evidence_dir / execution_relative).resolve()
    try:
        execution_path.relative_to(evidence_dir.resolve())
    except ValueError as exc:
        raise ValueError("supervisor execution 路径越过证据目录") from exc

    deadline = time.monotonic() + CHILD_LEASE_WAIT_SECONDS
    lease: ExecutionLease | None = None
    while time.monotonic() < deadline:
        try:
            candidate = ExecutionLease.model_validate_json(
                execution_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        if candidate.status == "running" and candidate.child_pid == os.getpid():
            lease = candidate
            break
        time.sleep(0.05)
    if lease is None:
        raise ValueError("无法确认当前 child 由存活的 supervisor owned execution 启动")
    if (
        lease.run_id != attestation.get("supervisor_run_id")
        or lease.step != "crwp-case-controller"
        or lease.owner_pid != os.getppid()
        or not lease.execution_id
        or lease.termination_unconfirmed
    ):
        raise ValueError("supervisor owned execution 身份与启动凭据不一致")
    try:
        lease_expires_at = datetime.fromisoformat(lease.lease_expires_at)
    except ValueError as exc:
        raise ValueError("supervisor lease 到期时间无效") from exc
    if lease_expires_at <= datetime.now(UTC):
        raise ValueError("supervisor owned execution lease 已过期")

    accepted = {
        "accepted_at": _utc_now(),
        "case_id": args.case_id,
        "child_pid": os.getpid(),
        "execution_id": lease.execution_id,
        "owner_pid": lease.owner_pid,
        "registration_head": args.registration_head,
        "runtime_commit": attestation.get("runtime_commit"),
        "runtime_manifest_sha256": attestation.get(
            "runtime_manifest_sha256"
        ),
        "supervisor_execution_sha256": _sha256_file(execution_path),
        "supervisor_run_id": lease.run_id,
    }
    _write_json_exclusive(
        evidence_dir / "launch-accepted.json",
        accepted,
    )
    return accepted


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行单项 CRWP-V1 正式控制运行")
    parser.add_argument("--case-id", required=True, choices=["CRWP-V1-01", "CRWP-V1-02"])
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--blocked-terms", type=Path)
    parser.add_argument(
        "--registration-head",
        required=True,
        help="只新增冻结 manifest 的登记提交完整 SHA",
    )
    parser.add_argument("--execute-child", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def _runtime_environment(
    workspace: Path,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str((workspace / "src").resolve())
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root
        if not current_pythonpath
        else os.pathsep.join([source_root, current_pythonpath])
    )
    if extra:
        environment.update(extra)
    return environment


def _invoke_stop_cli(workspace: Path, run_id: str) -> dict[str, Any]:
    command = [
        sys.executable,
        "-c",
        "from vega.cli import app; app()",
        "stop",
        "--run",
        run_id,
        "--reason",
        STOP_REASON,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=_runtime_environment(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": "vega stop 调用超过 30 秒",
            "returncode": None,
            "stderr": redact_text(str(exc)),
            "stdout": "",
        }
    except OSError as exc:
        return {
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": redact_text(str(exc)),
            "returncode": None,
            "stderr": "",
            "stdout": "",
        }
    return {
        "duration_seconds": round(time.monotonic() - started, 3),
        "error": None,
        "returncode": completed.returncode,
        "stderr": redact_text(completed.stderr),
        "stdout": redact_text(completed.stdout),
    }


def _request_supervisor_stop(supervisor_root: Path, reason: str) -> dict[str, Any]:
    try:
        record = request_stop_for_run(supervisor_root, reason)
    except (OSError, ValueError) as exc:
        return {
            "error": redact_text(str(exc)),
            "requested": False,
        }
    return {
        "error": None,
        "execution_status": record.lease.status,
        "requested": True,
        "step": record.lease.step,
    }


def _recover_interrupted_run(
    workspace: Path,
    run_id: str | None,
    reason: str,
) -> dict[str, Any]:
    if not run_id:
        return {
            "attempted": False,
            "reason": "尚未取得正式 run_id",
            "status": "not_created",
        }
    try:
        run_dir = resolve_run_dir(workspace, run_id)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return {
            "attempted": False,
            "reason": redact_text(str(exc)),
            "status": "failed",
        }
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return {
            "attempted": False,
            "reason": "正式 run 尚未写出 state.json",
            "status": "not_created",
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "attempted": True,
            "recovered": False,
            "reason": f"无法读取 state.json：{redact_text(str(exc))}",
            "status": "failed",
        }
    if not isinstance(state, dict):
        return {
            "attempted": True,
            "recovered": False,
            "reason": "state.json 顶层必须是对象",
            "status": "failed",
        }
    if state.get("run_id") != run_id:
        return {
            "attempted": True,
            "reason": "state.json 的 run_id 与监督记录不一致",
            "recovered": False,
            "status": "failed",
        }
    if state.get("status") in {"success", "failed", "needs_human"}:
        return {
            "attempted": False,
            "reason": f"run 已是终态：{state.get('status')}",
            "status": "not_needed",
        }
    if state.get("status") != "running":
        return {
            "attempted": True,
            "reason": f"run 状态不是可确认终态或 running：{state.get('status')}",
            "recovered": False,
            "status": "failed",
        }
    try:
        recovered = RecoveryRuntime(workspace).recover_loop(
            run_id,
            reason,
        )
    except (OSError, ValueError) as exc:
        return {
            "attempted": True,
            "error": redact_text(str(exc)),
            "recovered": False,
            "status": "failed",
        }
    try:
        recovered_state = json.loads(
            (recovered / "state.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return {
            "attempted": True,
            "error": redact_text(str(exc)),
            "recovered": False,
            "status": "failed",
        }
    if not isinstance(recovered_state, dict):
        return {
            "attempted": True,
            "error": "recover 后的 state.json 顶层必须是对象",
            "recovered": False,
            "status": "failed",
        }
    if (
        recovered_state.get("run_id") != run_id
        or recovered_state.get("status") != "needs_human"
    ):
        return {
            "attempted": True,
            "error": "recover 后未形成同一 run 的 needs_human 终态",
            "recovered": False,
            "status": "failed",
        }
    return {
        "attempted": True,
        "error": None,
        "recovered": True,
        "run_id": recovered.name,
        "status": "recovered",
    }


def _child_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--execute-child",
        "--case-id",
        args.case_id,
        "--repo",
        str(args.repo),
        "--task",
        str(args.task),
        "--evidence-dir",
        str(args.evidence_dir),
        "--registration-head",
        args.registration_head,
    ]
    if args.blocked_terms is not None:
        command.extend(["--blocked-terms", str(args.blocked_terms)])
    return command


def _read_worker_start(
    path: Path,
    *,
    strict: bool = False,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        raise
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError("wall-clock-start.json 不是完整 JSON") from exc
        return None
    if not isinstance(payload, dict):
        raise ValueError("wall-clock-start.json 顶层必须是对象")
    run_id = payload.get("run_id")
    started_monotonic = payload.get("worker_started_monotonic")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("wall-clock-start.json 缺少非空 run_id")
    if (
        isinstance(started_monotonic, bool)
        or not isinstance(started_monotonic, (int, float))
        or not math.isfinite(float(started_monotonic))
        or float(started_monotonic) <= 0
        or float(started_monotonic) > time.monotonic() + 1
    ):
        raise ValueError("wall-clock-start.json 缺少有效 monotonic 起点")
    if payload.get("deadline_seconds") != TOTAL_WALL_CLOCK_SECONDS:
        raise ValueError("wall-clock-start.json 的总墙钟与冻结值不一致")
    return payload


def _read_run_created(
    path: Path,
    *,
    strict: bool = False,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        raise
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError("run-created.json 不是完整 JSON") from exc
        return None
    if not isinstance(payload, dict):
        raise ValueError("run-created.json 顶层必须是对象")
    run_id = payload.get("run_id")
    created_monotonic = payload.get("run_created_monotonic")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run-created.json 缺少非空 run_id")
    if (
        isinstance(created_monotonic, bool)
        or not isinstance(created_monotonic, (int, float))
        or not math.isfinite(float(created_monotonic))
        or float(created_monotonic) <= 0
        or float(created_monotonic) > time.monotonic() + 1
    ):
        raise ValueError("run-created.json 缺少有效 monotonic 时间")
    return payload


def _read_control_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("control-summary.json 无法解析") from exc
    if not isinstance(payload, dict):
        raise ValueError("control-summary.json 顶层必须是对象")
    run_id = payload.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id):
        raise ValueError("control-summary.json 包含无效 run_id")
    return payload


def _validate_success_evidence(
    workspace: Path,
    evidence_dir: Path,
    args: argparse.Namespace,
    run_id: str | None,
    control_summary: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    launch_parse_failed = False
    try:
        launch_accepted = json.loads(
            (evidence_dir / "launch-accepted.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        launch_parse_failed = True
        launch_accepted = None
        errors.append("成功 child 缺少可解析的 launch-accepted.json")
    if isinstance(launch_accepted, dict):
        if launch_accepted.get("case_id") != args.case_id:
            errors.append("launch accepted 的 case_id 与当前运行不一致")
        execution_id = launch_accepted.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            errors.append("launch accepted 缺少 supervisor execution_id")
        if launch_accepted.get("registration_head") != args.registration_head:
            errors.append("launch accepted 的登记提交与当前运行不一致")
        manifest_sha256 = launch_accepted.get("runtime_manifest_sha256")
        try:
            current_manifest_sha256 = _sha256_file(
                workspace / CONTROL_MANIFEST_PATH
            )
        except OSError:
            current_manifest_sha256 = None
        if manifest_sha256 != current_manifest_sha256:
            errors.append("launch accepted 的 Runtime manifest 哈希不一致")
    elif not launch_parse_failed:
        errors.append("launch-accepted.json 顶层必须是对象")

    fingerprint: str | None = None
    if control_summary is None:
        errors.append("成功 child 缺少 control-summary.json")
    else:
        if control_summary.get("case_id") != args.case_id:
            errors.append("control summary 的 case_id 与当前运行不一致")
        if control_summary.get("error") is not None:
            errors.append("control summary 仍包含错误")
        if control_summary.get("run_id") != run_id:
            errors.append("control summary 的 run_id 与监督记录不一致")
        if control_summary.get("registration_head") != args.registration_head:
            errors.append("control summary 的登记提交与当前运行不一致")
        if control_summary.get("finish_completed") is not True:
            errors.append("control summary 未确认 Finish 完成")
        if control_summary.get("finish_status") != "ready_to_commit":
            errors.append("control summary 未形成 ready_to_commit")
        for field in (
            "frozen_inputs_rechecked",
            "registered_preflight_rechecked",
            "runtime_rechecked",
        ):
            if control_summary.get(field) is not True:
                errors.append(f"control summary 未确认 {field}")
        fingerprint = control_summary.get("workspace_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            errors.append("control summary 缺少有效 workspace fingerprint")

    workspace_preflight_parse_failed = False
    try:
        workspace_preflight = json.loads(
            (evidence_dir / "workspace-preflight.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        workspace_preflight_parse_failed = True
        workspace_preflight = None
        errors.append("成功 child 缺少可解析的 workspace-preflight.json")
    if isinstance(workspace_preflight, dict):
        if workspace_preflight.get("fingerprint") != fingerprint:
            errors.append("workspace preflight 与 control summary 指纹不一致")
    elif not workspace_preflight_parse_failed:
        errors.append("workspace-preflight.json 顶层必须是对象")

    input_attestation_parse_failed = False
    try:
        input_attestation = json.loads(
            (evidence_dir / "input-attestation.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        input_attestation_parse_failed = True
        input_attestation = None
        errors.append("成功 child 缺少可解析的 input-attestation.json")
    if isinstance(input_attestation, dict):
        if input_attestation.get("case_id") != args.case_id:
            errors.append("input attestation 的 case_id 与当前运行不一致")
        if input_attestation.get("workspace_fingerprint") != fingerprint:
            errors.append("input attestation 与 control summary 指纹不一致")
        if input_attestation.get("launch") != launch_accepted:
            errors.append("input attestation 与 launch accepted 不一致")
    elif not input_attestation_parse_failed:
        errors.append("input-attestation.json 顶层必须是对象")

    if not run_id:
        errors.append("成功 child 缺少根 run_id")
        return errors
    try:
        run_dir = resolve_run_dir(workspace, run_id)
    except (FileNotFoundError, OSError, ValueError):
        errors.append("成功 child 的根 run_id 无法安全解析")
        return errors
    state_path = run_dir / "state.json"
    try:
        state = LoopAutomationState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        state = None
        errors.append("成功 child 缺少可解析的根 state.json")
    if state is not None:
        if state.run_id != run_id:
            errors.append("根 state 的 run_id 与监督记录不一致")
        if state.status != "success":
            errors.append(f"根 state 不是 success：{state.status}")
        try:
            state_repo = Path(state.repo_path).resolve()
        except (OSError, RuntimeError):
            state_repo = None
        if state_repo != args.repo.resolve():
            errors.append("根 state 的目标仓库与正式参数不一致")

    finish_path = run_dir / "finish-summary.json"
    finish_parse_failed = False
    try:
        finish_summary = json.loads(finish_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        finish_parse_failed = True
        finish_summary = None
        errors.append("成功 child 缺少可解析的真实 finish-summary.json")
    if isinstance(finish_summary, dict):
        if finish_summary.get("run_id") != run_id:
            errors.append("真实 Finish 的 run_id 与监督记录不一致")
        if finish_summary.get("loop_status") != "success":
            errors.append("真实 Finish 的 loop_status 不是 success")
        if finish_summary.get("finish_status") != "ready_to_commit":
            errors.append("真实 Finish 未形成 ready_to_commit")
        if finish_summary.get("repo_path") != str(args.repo):
            errors.append("真实 Finish 的目标仓库与正式参数不一致")
        if finish_summary.get("verification_passed") is not True:
            errors.append("真实 Finish 未确认可信验证通过")
        if finish_summary.get("latest_verification_failed") is not False:
            errors.append("真实 Finish 仍包含最新验证失败")
        artifact_integrity = finish_summary.get("artifact_integrity")
        if (
            not isinstance(artifact_integrity, dict)
            or artifact_integrity.get("valid") is not True
        ):
            errors.append("真实 Finish 未确认 artifact integrity")
        evidence_freshness = finish_summary.get("evidence_freshness")
        if (
            not isinstance(evidence_freshness, dict)
            or evidence_freshness.get("fresh") is not True
        ):
            errors.append("真实 Finish 未确认 evidence freshness")
        latest_verdict = finish_summary.get("latest_verdict")
        if (
            not isinstance(latest_verdict, dict)
            or latest_verdict.get("verdict") != "approve"
        ):
            errors.append("真实 Finish 未确认 Reviewer approve")
    elif not finish_parse_failed:
        errors.append("真实 finish-summary.json 顶层必须是对象")

    if state is not None:
        try:
            validation_snapshot = validate_loop_evidence_snapshot(
                workspace,
                args.repo,
                run_dir,
                state=state,
            )
            integrity = validation_snapshot.artifact_integrity
            freshness = validation_snapshot.evidence_freshness
            verification_passed = trusted_verification_passed(
                state,
                integrity,
            )
            verification_failed = latest_verification_failed(
                state,
                integrity,
            )
            verdicts = list(integrity.review_verdicts)
            latest_verdict = verdicts[-1].verdict if verdicts else None
            recomputed_finish_status = decide_finish_status(
                state.status,
                latest_verdict,
                verification_failed,
                verification_passed=verification_passed,
                evidence_fresh=freshness.fresh,
                artifact_integrity_valid=integrity.valid,
            )
        except Exception as exc:  # noqa: BLE001 - 独立重算失败必须拒绝成功
            errors.append(
                f"无法独立重算 Finish 可信门禁：{redact_text(str(exc))}"
            )
        else:
            if recomputed_finish_status != "ready_to_commit":
                errors.append("独立重算的 Finish 未形成 ready_to_commit")
            if not verification_passed or verification_failed:
                errors.append("独立重算未确认最新确定性验证通过")
            if not integrity.valid:
                errors.append("独立重算未确认 artifact integrity")
            if not freshness.fresh:
                errors.append("独立重算未确认 evidence freshness")
            if latest_verdict != "approve":
                errors.append("独立重算未确认 Reviewer approve")
    return errors


def _record_deadline(
    path: Path,
    *,
    run_id: str,
    source: str,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("wall-clock-deadline.json 无法解析") from exc
        if (
            payload.get("run_id") != run_id
            or payload.get("deadline_seconds") != TOTAL_WALL_CLOCK_SECONDS
        ):
            raise ValueError("已有 deadline artifact 与当前 run 或冻结墙钟不一致")
        return payload
    payload: dict[str, Any] = {
        "deadline_reached_at": _utc_now(),
        "deadline_seconds": TOTAL_WALL_CLOCK_SECONDS,
        "run_id": run_id,
        "source": source,
    }
    if elapsed_seconds is not None:
        payload["elapsed_seconds"] = round(elapsed_seconds, 3)
    _write_json_exclusive(path, payload)
    return payload


def _run_supervisor(args: argparse.Namespace, workspace: Path) -> int:
    evidence_dir: Path = args.evidence_dir
    runtime_supervisor_before = _assert_runtime_ready(
        workspace,
        args.registration_head,
    )
    if evidence_dir.exists() and any(evidence_dir.iterdir()):
        raise ValueError("证据目录已存在内容；为避免覆盖，正式重跑必须使用新目录")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    supervisor_root = evidence_dir / "supervisor-run"
    supervisor_root.mkdir(exist_ok=False)
    launch_nonce = _prepare_launch_attestation(
        args.case_id,
        evidence_dir,
        supervisor_root,
        args.registration_head,
        runtime_supervisor_before,
    )
    context = RunnerExecutionContext(
        execution_root=supervisor_root,
        execution_dir=supervisor_root / "executions" / "controller",
        run_id=supervisor_root.name,
        step="crwp-case-controller",
        heartbeat_interval_seconds=1,
        lease_timeout_seconds=10,
        terminate_grace_seconds=5,
    )
    started_monotonic = time.monotonic()
    controller_done = threading.Event()
    monitor_state: dict[str, Any] = {
        "deadline_reached": False,
        "startup_timeout": False,
        "stop_cli_result": None,
        "supervisor_stop_result": None,
        "worker_start": None,
        "run_created": None,
        "monitor_error": None,
    }

    def monitor() -> None:
        try:
            while not controller_done.is_set():
                run_created = monitor_state["run_created"]
                if run_created is None:
                    run_created = _read_run_created(
                        evidence_dir / "run-created.json"
                    )
                    monitor_state["run_created"] = run_created
                worker_start = monitor_state["worker_start"]
                if worker_start is None:
                    worker_start = _read_worker_start(
                        evidence_dir / "wall-clock-start.json"
                    )
                    monitor_state["worker_start"] = worker_start
                if (
                    run_created is not None
                    and worker_start is not None
                    and run_created["run_id"] != worker_start["run_id"]
                ):
                    raise ValueError("根 run 与 Worker 墙钟记录的 run_id 不一致")

                now = time.monotonic()
                if (
                    worker_start is None
                    and now - started_monotonic >= STARTUP_TIMEOUT_SECONDS
                ):
                    monitor_state["startup_timeout"] = True
                    monitor_state["supervisor_stop_result"] = (
                        _request_supervisor_stop(
                            supervisor_root,
                            "CRWP-V1 在 5 分钟内未进入首个 Worker 调用，停止控制子进程。",
                        )
                    )
                    return
                if (
                    worker_start is not None
                    and now - float(worker_start["worker_started_monotonic"])
                    >= TOTAL_WALL_CLOCK_SECONDS
                ):
                    monitor_state["deadline_reached"] = True
                    _record_deadline(
                        evidence_dir / "wall-clock-deadline.json",
                        run_id=worker_start["run_id"],
                        source="monitor",
                    )
                    stop_cli_result = _invoke_stop_cli(
                        workspace,
                        worker_start["run_id"],
                    )
                    monitor_state["stop_cli_result"] = stop_cli_result
                    if (
                        stop_cli_result["returncode"] == 0
                        and controller_done.wait(STOP_GRACE_SECONDS)
                    ):
                        return
                    monitor_state["supervisor_stop_result"] = (
                        _request_supervisor_stop(
                            supervisor_root,
                            "CRWP-V1 总墙钟已到；Vega stop 未在宽限期内结束控制子进程。",
                        )
                    )
                    return
                controller_done.wait(0.2)
        except Exception as exc:  # noqa: BLE001 - 监督线程异常必须停止 owned child
            monitor_state["monitor_error"] = redact_text(str(exc))
            monitor_state["supervisor_stop_result"] = _request_supervisor_stop(
                supervisor_root,
                "CRWP-V1 墙钟监督异常，停止控制子进程并交还人工。",
            )

    monitor_thread = threading.Thread(
        target=monitor,
        name=f"{args.case_id}-wall-clock-monitor",
        daemon=True,
    )
    monitor_thread.start()
    controller_error: str | None = None
    try:
        child_result = run_owned_process(
            _child_command(args),
            "",
            workspace,
            SUPERVISOR_PROCESS_TIMEOUT_SECONDS,
            context,
            environment=_runtime_environment(
                workspace,
                {LAUNCH_NONCE_ENV: launch_nonce},
            ),
        )
    except Exception as exc:  # noqa: BLE001 - Supervisor 必须保留异常终态
        controller_error = redact_text(str(exc))
        child_result = OwnedProcessResult(
            status="error",
            output="",
            error=controller_error,
            returncode=None,
        )
        if monitor_state["supervisor_stop_result"] is None:
            monitor_state["supervisor_stop_result"] = (
                _request_supervisor_stop(
                    supervisor_root,
                    "CRWP-V1 owned controller 异常，停止其已登记进程并交还人工。",
                )
            )
    finally:
        controller_finished_monotonic = time.monotonic()
        controller_done.set()
        monitor_thread.join(timeout=STOP_GRACE_SECONDS + 35)
    postcheck_errors: list[str] = []
    if controller_error is not None:
        postcheck_errors.append(
            f"owned controller 调用异常：{controller_error}"
        )
    try:
        final_run_created = _read_run_created(
            evidence_dir / "run-created.json",
            strict=True,
        )
        if (
            monitor_state["run_created"] is not None
            and monitor_state["run_created"] != final_run_created
        ):
            postcheck_errors.append(
                "monitor 缓存与最终 run-created.json 不一致"
            )
        run_created = final_run_created
    except (OSError, ValueError) as exc:
        run_created = None
        postcheck_errors.append(redact_text(str(exc)))
    try:
        final_worker_start = _read_worker_start(
            evidence_dir / "wall-clock-start.json",
            strict=True,
        )
        if (
            monitor_state["worker_start"] is not None
            and monitor_state["worker_start"] != final_worker_start
        ):
            postcheck_errors.append(
                "monitor 缓存与最终 wall-clock-start.json 不一致"
            )
        worker_start = final_worker_start
    except (OSError, ValueError) as exc:
        worker_start = None
        postcheck_errors.append(redact_text(str(exc)))
    try:
        control_summary = _read_control_summary(
            evidence_dir / "control-summary.json"
        )
    except ValueError as exc:
        control_summary = None
        postcheck_errors.append(redact_text(str(exc)))
    try:
        runtime_supervisor_after = _assert_runtime_ready(
            workspace,
            args.registration_head,
        )
        if runtime_supervisor_after != runtime_supervisor_before:
            postcheck_errors.append("Supervisor 前后 Runtime 冻结证据不一致")
    except (OSError, ValueError) as exc:
        runtime_supervisor_after = None
        postcheck_errors.append(redact_text(str(exc)))

    run_id = (
        run_created.get("run_id")
        if run_created
        else worker_start.get("run_id")
        if worker_start
        else control_summary.get("run_id")
        if control_summary
        else None
    )
    if (
        run_created is not None
        and worker_start is not None
        and run_created["run_id"] != worker_start["run_id"]
    ):
        postcheck_errors.append("根 run 与 Worker 墙钟记录的 run_id 不一致")
    if (
        control_summary is not None
        and control_summary.get("run_id") is not None
        and run_id != control_summary.get("run_id")
    ):
        postcheck_errors.append("control summary 的 run_id 与监督记录不一致")

    deadline_reached = bool(monitor_state["deadline_reached"])
    startup_timeout = bool(monitor_state["startup_timeout"])
    stop_cli_result = monitor_state["stop_cli_result"]
    supervisor_stop_result = monitor_state["supervisor_stop_result"]
    monitor_error = monitor_state["monitor_error"]
    worker_elapsed_seconds: float | None = None
    if worker_start is not None:
        worker_elapsed_seconds = (
            controller_finished_monotonic
            - float(worker_start["worker_started_monotonic"])
        )
        if worker_elapsed_seconds >= TOTAL_WALL_CLOCK_SECONDS:
            deadline_reached = True
            try:
                _record_deadline(
                    evidence_dir / "wall-clock-deadline.json",
                    run_id=worker_start["run_id"],
                    source="postcheck",
                    elapsed_seconds=worker_elapsed_seconds,
                )
            except ValueError as exc:
                postcheck_errors.append(redact_text(str(exc)))

    controller_termination_unconfirmed = bool(
        child_result.termination_unconfirmed or monitor_thread.is_alive()
    )
    child_returncode = getattr(child_result, "returncode", None)
    if child_returncode == 0:
        if run_created is None:
            postcheck_errors.append("成功 child 缺少 run-created.json")
        if worker_start is None:
            postcheck_errors.append("成功 child 缺少 wall-clock-start.json")
        try:
            success_evidence_errors = _validate_success_evidence(
                workspace,
                evidence_dir,
                args,
                run_id,
                control_summary,
            )
        except Exception as exc:  # noqa: BLE001 - 监督层必须落下 fail-closed 摘要
            success_evidence_errors = [
                f"成功证据独立校验异常：{redact_text(str(exc))}"
            ]
        postcheck_errors.extend(success_evidence_errors)
    must_fail = bool(
        controller_termination_unconfirmed
        or deadline_reached
        or startup_timeout
        or monitor_error
        or postcheck_errors
        or child_returncode != 0
    )
    if deadline_reached:
        recovery_reason = "CRWP-V1 总墙钟已到，控制进程已结束，保留现场并交还人工。"
    elif startup_timeout:
        recovery_reason = "CRWP-V1 Worker 启动超时，控制进程已结束，保留现场并交还人工。"
    elif monitor_error:
        recovery_reason = "CRWP-V1 墙钟监督异常，控制进程已结束，保留现场并交还人工。"
    else:
        recovery_reason = "CRWP-V1 控制运行未满足正式终态，保留现场并交还人工。"
    recovery = (
        _recover_interrupted_run(
            workspace,
            run_id,
            recovery_reason,
        )
        if not controller_termination_unconfirmed
        else {
            "attempted": False,
            "reason": "owned 控制进程终止未确认",
            "status": "failed",
        }
    )
    if recovery.get("status") == "recovered":
        postcheck_errors.append("运行需要 recovery，不能计为正式成功")
        must_fail = True
    elif recovery.get("status") == "failed":
        postcheck_errors.append("运行状态或 recovery 终态无法确认")
        must_fail = True
    elif recovery.get("status") == "not_created" and child_returncode == 0:
        postcheck_errors.append("成功 child 缺少可核验根 run 状态")
        must_fail = True
    _write_json_exclusive(
        evidence_dir / "supervisor-summary.json",
        {
            "case_id": args.case_id,
            "child_returncode": child_returncode,
            "child_status": getattr(child_result, "status", None),
            "controller_error": controller_error,
            "controller_termination_unconfirmed": controller_termination_unconfirmed,
            "deadline_reached": deadline_reached,
            "finished_at": _utc_now(),
            "monitor_error": monitor_error,
            "postcheck_errors": postcheck_errors,
            "recovery": recovery,
            "run_id": run_id,
            "run_created": run_created,
            "runtime_rechecked": (
                runtime_supervisor_after is not None
                and runtime_supervisor_after == runtime_supervisor_before
            ),
            "runtime_registration": runtime_supervisor_before,
            "startup_timeout": startup_timeout,
            "stop_cli": stop_cli_result,
            "supervisor_stop": supervisor_stop_result,
            "worker_elapsed_seconds": (
                round(worker_elapsed_seconds, 3)
                if worker_elapsed_seconds is not None
                else None
            ),
            "worker_start": worker_start,
        },
    )
    return 1 if must_fail else 0


def _execute_child(args: argparse.Namespace, workspace: Path) -> int:
    repo: Path = args.repo
    task_path: Path = args.task
    evidence_dir: Path = args.evidence_dir
    blocked_path: Path | None = args.blocked_terms
    launch_attestation = _validate_child_launch(args, evidence_dir)
    started_at = _utc_now()
    worker_start = WorkerStartRecorder(evidence_dir)
    run_dir: Path | None = None
    finish_dir: Path | None = None
    finish_summary: dict[str, Any] | None = None
    error: str | None = None
    registered_before: dict[str, Any] | None = None
    registered_after: dict[str, Any] | None = None
    runtime_before: dict[str, Any] | None = None
    runtime_after: dict[str, Any] | None = None
    frozen_before: dict[str, Any] | None = None
    frozen_after: dict[str, Any] | None = None
    workspace_preflight: dict[str, Any] | None = None
    try:
        contract = _assert_control_inputs(
            args.case_id,
            workspace,
            task_path,
            blocked_path,
        )
        task_text = task_path.read_text(encoding="utf-8")
        blocked_terms = _read_blocked_terms(blocked_path)
        task_matches = [term for term in blocked_terms if term in task_text]
        if task_matches:
            raise ValueError("任务合同命中冻结负向词表，拒绝创建正式 run")

        control_root = workspace / "scripts" / "pilot" / "crwp-v1"
        os.environ["CRWP_CONTROL_ROOT"] = str(control_root.resolve())
        head_sha = _assert_target_ready(repo, contract)
        worker_options, reviewer_options = _assert_runner_config(repo)
        registered_before = _assert_registered_preflight(
            args.case_id,
            workspace,
            contract,
        )
        runtime_before = _assert_runtime_ready(
            workspace,
            args.registration_head,
        )
        frozen_before = _assert_frozen_target_files(
            repo,
            workspace,
            contract,
        )
        workspace_preflight = _capture_preworker_workspace(
            repo,
            evidence_dir,
            head_sha,
        )
        _write_json_exclusive(
            evidence_dir / "input-attestation.json",
            {
                "blocked_term_count": len(blocked_terms),
                "blocked_terms_sha256": _sha256_bytes(
                    "\n".join(blocked_terms).encode("utf-8")
                ),
                "case_id": args.case_id,
                "frozen_files": frozen_before,
                "launch": launch_attestation,
                "registered_preflight": registered_before,
                "runtime": runtime_before,
                "target_head_sha": head_sha,
                "task_sha256": _sha256_bytes(task_path.read_bytes()),
                "task_utf8_bytes": len(task_text.encode("utf-8")),
                "timestamp": _utc_now(),
                "workspace_fingerprint": workspace_preflight["fingerprint"],
            },
        )

        worker_runner = PromptAuditRunner(
            role="worker",
            inner=CodexExecRunner(options=worker_options),
            evidence_dir=evidence_dir,
            blocked_terms=blocked_terms,
            worker_start=worker_start,
            expected_repo=repo,
            expected_workspace=workspace,
        )
        reviewer_runner = PromptAuditRunner(
            role="reviewer",
            inner=CodexExecRunner(options=reviewer_options),
            evidence_dir=evidence_dir,
            blocked_terms=blocked_terms,
            worker_start=worker_start,
            expected_repo=repo,
            expected_workspace=workspace,
        )
        brief_input = BriefInput(
            mode="bug",
            text=task_text,
            source=task_path.relative_to(workspace).as_posix(),
            repo_path=str(repo),
        )

        with _record_loop_creation(worker_start):
            run_dir = LoopAutomationRuntime(
                workspace=workspace,
                worker_runner=worker_runner,
                reviewer_runner=reviewer_runner,
                timeout_seconds=RUNNER_TIMEOUT_SECONDS,
            ).start(
                brief_input,
                automation_mode="auto",
                worker_name="codex-exec",
                reviewer_name="codex-exec",
                max_iterations=2,
                verify=True,
            )
        finish_dir = FinishRuntime(workspace=workspace).run(run_dir.name)
        summary_path = finish_dir / "finish-summary.json"
        if not summary_path.is_file():
            raise ValueError("Finish 未写出 finish-summary.json")
        finish_summary = json.loads(summary_path.read_text(encoding="utf-8"))

        registered_after = _assert_registered_preflight(
            args.case_id,
            workspace,
            contract,
        )
        runtime_after = _assert_runtime_ready(
            workspace,
            args.registration_head,
        )
        frozen_after = _assert_frozen_target_files(
            repo,
            workspace,
            contract,
        )
        if registered_before != registered_after:
            raise ValueError("正式运行期间登记的 preflight 证据发生变化")
        if runtime_before != runtime_after:
            raise ValueError("正式运行期间 Vega Runtime 或控制输入发生变化")
        if frozen_before != frozen_after:
            raise ValueError("正式运行期间冻结的 target/oracle/helper 输入发生变化")
    except Exception as exc:  # noqa: BLE001 - 保留现场并输出控制端失败证据
        error = redact_text(str(exc))

    _write_json_exclusive(
        evidence_dir / "control-summary.json",
        {
            "case_id": args.case_id,
            "error": error,
            "finish_completed": finish_dir is not None,
            "finish_status": (
                finish_summary.get("finish_status") if finish_summary else None
            ),
            "finished_at": _utc_now(),
            "frozen_inputs_rechecked": (
                frozen_before is not None and frozen_before == frozen_after
            ),
            "registered_preflight_rechecked": (
                registered_before is not None
                and registered_before == registered_after
            ),
            "registration_head": args.registration_head,
            "run_id": (
                run_dir.name
                if run_dir
                else worker_start.created_run_id or worker_start.run_id
            ),
            "runtime_rechecked": (
                runtime_before is not None and runtime_before == runtime_after
            ),
            "started_at": started_at,
            "workspace_fingerprint": (
                workspace_preflight.get("fingerprint")
                if workspace_preflight
                else None
            ),
        },
    )
    if error or finish_summary is None:
        return 1
    return 0 if finish_summary.get("finish_status") == "ready_to_commit" else 1


def main() -> int:
    args = _parse_args()
    workspace = Path(__file__).resolve().parents[3]
    if os.name != "nt":
        raise ValueError("CRWP-V1 正式控制运行只登记并验证 Windows 环境")
    args.repo = _resolve_under_workspace(args.repo, workspace, "目标仓库")
    args.task = _resolve_under_workspace(args.task, workspace, "任务合同")
    args.evidence_dir = _resolve_under_workspace(
        args.evidence_dir,
        workspace,
        "证据目录",
    )
    args.blocked_terms = (
        _resolve_under_workspace(args.blocked_terms, workspace, "负向词表")
        if args.blocked_terms
        else None
    )
    if args.case_id == "CRWP-V1-02" and args.blocked_terms is None:
        raise ValueError("CRWP-V1-02 必须提供冻结负向词表")
    if args.case_id == "CRWP-V1-01" and args.blocked_terms is not None:
        raise ValueError("CRWP-V1-01 不应加载 Sequelize 负向词表")
    _assert_formal_path_boundaries(
        args.repo,
        args.evidence_dir,
        workspace,
    )
    if args.execute_child:
        return _execute_child(args, workspace)
    return _run_supervisor(args, workspace)


if __name__ == "__main__":
    raise SystemExit(main())
