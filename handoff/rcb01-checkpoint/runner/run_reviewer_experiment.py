from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

sys.dont_write_bytecode = True

LOCAL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = LOCAL_ROOT.parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.rcb01_materializer import (  # noqa: E402
    EXPERIMENT_ID,
    MaterializationError,
    validate_materialization,
)
from vega.execution_control import RunnerExecutionContext  # noqa: E402
from vega.project_config import CodexExecOptions  # noqa: E402
from vega.redaction import redact_text  # noqa: E402
from vega.review_contract import ReviewVerdict  # noqa: E402
from vega.review_coverage import build_review_file_coverage  # noqa: E402
from vega.risk_review_reporting import verdict_output_schema  # noqa: E402
from vega.risk_review_runtime import enforce_review_file_coverage  # noqa: E402
from vega.runner import CodexExecRunner, RunnerResult  # noqa: E402


SCHEMA_VERSION = 1
MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "high"
SANDBOX = "read-only"
TIMEOUT_SECONDS = 900
MODEL_AVAILABILITY_PATH = LOCAL_ROOT / "model-availability.json"
FORMAL_RUNS_ROOT = LOCAL_ROOT / "formal-runs"
FREEZE_PATH = LOCAL_ROOT / "experiment-freeze.json"
FAKE_EXECUTABLE = LOCAL_ROOT / "fake_codex.cmd"
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")

WRITE_COMMAND_PATTERN = re.compile(
    r"(?i)(?:"
    r"\b(?:set-content|add-content|out-file|remove-item|move-item|copy-item|"
    r"new-item|clear-content|tee|del|erase|rm|mv|cp)\b"
    r"|(?:^|[\s;&|])(?:>>?|2>>?)\s*[^&|]"
    r")"
)
READ_COMMAND_PATTERN = re.compile(
    r"(?i)(?:"
    r"\b(?:get-content|select-string|cat|head|tail|grep|rg)\b"
    r"|\bsed\s+-n\b"
    r"|\bgit\s+(?:show|grep|blame)\b"
    r")"
)


class ExperimentError(ValueError):
    """正式运行前置条件或本地 Artifact 合同不满足。"""


@dataclass(frozen=True)
class RunSpec:
    sequence: int
    case_id: str
    arm: Literal["A", "B"]
    repetition: int

    @property
    def label(self) -> str:
        return f"{self.sequence:02d}-{self.case_id}-{self.arm}{self.repetition}"

    @property
    def confirmation(self) -> str:
        return f"RCB-01-RUN-{self.sequence:02d}"


@dataclass(frozen=True)
class CaseMaterial:
    case_id: str
    case_dir: Path
    manifest: dict[str, Any]
    core_bytes: bytes
    appendix_bytes: bytes
    arm_a_bytes: bytes
    arm_b_bytes: bytes
    changed_files: tuple[str, ...]
    candidates: tuple[str, ...]

    def prompt_bytes(self, arm: Literal["A", "B"]) -> bytes:
        return self.arm_a_bytes if arm == "A" else self.arm_b_bytes


RUN_ORDER = (
    RunSpec(1, "C1", "A", 1),
    RunSpec(2, "C1", "B", 1),
    RunSpec(3, "C2", "B", 1),
    RunSpec(4, "C2", "A", 1),
    RunSpec(5, "C3", "A", 1),
    RunSpec(6, "C3", "B", 1),
    RunSpec(7, "C4", "B", 1),
    RunSpec(8, "C4", "A", 1),
    RunSpec(9, "C5", "A", 1),
    RunSpec(10, "C5", "B", 1),
    RunSpec(11, "C5", "B", 2),
    RunSpec(12, "C5", "A", 2),
    RunSpec(13, "C4", "A", 2),
    RunSpec(14, "C4", "B", 2),
    RunSpec(15, "C3", "B", 2),
    RunSpec(16, "C3", "A", 2),
    RunSpec(17, "C2", "A", 2),
    RunSpec(18, "C2", "B", 2),
    RunSpec(19, "C1", "B", 2),
    RunSpec(20, "C1", "A", 2),
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"无法读取 JSON：{path.name}") from exc
    if not isinstance(payload, dict):
        raise ExperimentError(f"JSON 顶层必须是对象：{path.name}")
    return payload


def _write_bytes_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)


def _write_json_exclusive(path: Path, payload: object) -> None:
    _write_bytes_exclusive(path, _canonical_json_bytes(payload))


def _write_text_exclusive(path: Path, text: str) -> None:
    _write_bytes_exclusive(path, text.encode("utf-8"))


def _run_git(
    cwd: Path,
    *args: str,
    allowed_returncodes: set[int] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        },
        check=False,
    )
    allowed = allowed_returncodes or {0}
    if result.returncode not in allowed:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise ExperimentError(
            f"Git 命令失败（exit={result.returncode}）："
            f"git {' '.join(args)}；{redact_text(diagnostic)}"
        )
    return result


def _git_bytes(cwd: Path, *args: str) -> bytes:
    return _run_git(cwd, *args).stdout


def _git_text(cwd: Path, *args: str) -> str:
    return _git_bytes(cwd, *args).decode("utf-8", errors="replace").strip()


def _assert_repo_state() -> str:
    head = _git_text(REPO_ROOT, "rev-parse", "HEAD")
    if not HEX_40.fullmatch(head):
        raise ExperimentError("当前 HEAD 不是完整 Git SHA")
    status = _git_bytes(
        REPO_ROOT,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
    )
    if status.strip():
        raise ExperimentError("正式实验要求控制仓库保持干净")
    return head


def _codex_version(executable: str = "codex") -> str:
    resolved = shutil.which(executable)
    if not resolved:
        raise ExperimentError(f"未找到 {executable}")
    result = subprocess.run(
        [resolved, "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise ExperimentError(
            f"无法读取 Codex CLI 版本：{redact_text(result.stderr.strip())}"
        )
    output = result.stdout.strip()
    match = re.search(r"(\d+\.\d+\.\d+)", output)
    if match is None:
        raise ExperimentError("Codex CLI 版本输出无法解析")
    return match.group(1)


def _validate_model_availability(codex_version: str) -> dict[str, Any]:
    payload = _read_json_object(MODEL_AVAILABILITY_PATH)
    expected_fields = {
        "formal_model_calls_started": False,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
    }
    for key, expected in expected_fields.items():
        if payload.get(key) != expected:
            raise ExperimentError(f"模型可用性记录字段不一致：{key}")
    if payload.get("availability_status") != "catalog_and_provider_route_confirmed":
        raise ExperimentError("模型可用性记录未确认 catalog 与 Provider 路由")
    if payload.get("codex_version") != codex_version:
        raise ExperimentError(
            "Codex CLI 版本已偏离模型可用性记录，正式调用前必须停止"
        )
    checks = payload.get("required_doctor_checks")
    if not isinstance(checks, dict) or any(
        checks.get(name) != "ok"
        for name in (
            "auth.credentials",
            "config.load",
            "network.provider_reachability",
        )
    ):
        raise ExperimentError("模型可用性记录的必要 Doctor 检查不完整")
    return payload


def _artifact_contract(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ExperimentError("materialization.json 缺少 artifacts")
    item = artifacts.get(name)
    if not isinstance(item, dict):
        raise ExperimentError(f"materialization.json 缺少 Artifact：{name}")
    sha256 = item.get("sha256")
    size = item.get("size")
    if not isinstance(sha256, str) or not HEX_64.fullmatch(sha256):
        raise ExperimentError(f"Artifact SHA-256 不合法：{name}")
    if not isinstance(size, int) or size < 0:
        raise ExperimentError(f"Artifact size 不合法：{name}")
    return item


def _load_case_material(case_id: str) -> CaseMaterial:
    case_dir = LOCAL_ROOT / case_id
    try:
        manifest = validate_materialization(REPO_ROOT, case_dir)
    except (MaterializationError, OSError) as exc:
        raise ExperimentError(f"{case_id} 离线校验失败：{exc}") from exc
    if manifest.get("case_id") != case_id:
        raise ExperimentError(f"{case_id} manifest case_id 不一致")

    contents: dict[str, bytes] = {}
    for name in (
        "core-review-prompt.md",
        "context-appendix.md",
        "arm-a-prompt.md",
        "arm-b-prompt.md",
        "changed-files.json",
        "impact-candidates.json",
        "full-diff.patch",
    ):
        content = case_dir.joinpath(name).read_bytes()
        contract = _artifact_contract(manifest, name)
        if len(content) != contract["size"] or _sha256_bytes(content) != contract["sha256"]:
            raise ExperimentError(f"{case_id} Artifact 哈希漂移：{name}")
        contents[name] = content

    core = contents["core-review-prompt.md"]
    appendix = contents["context-appendix.md"]
    arm_a = contents["arm-a-prompt.md"]
    arm_b = contents["arm-b-prompt.md"]
    if arm_a != core:
        raise ExperimentError(f"{case_id} A 组 Prompt 不等于 Core Review Pack")
    if arm_b != core + b"\n" + appendix:
        raise ExperimentError(f"{case_id} B 组 Prompt 不是 Core 与 Appendix 的唯一追加")

    changed_payload = json.loads(contents["changed-files.json"].decode("utf-8"))
    changed_items = changed_payload.get("files") if isinstance(changed_payload, dict) else None
    if not isinstance(changed_items, list) or not changed_items:
        raise ExperimentError(f"{case_id} changed-files.json 不合法")
    changed_files: list[str] = []
    for item in changed_items:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path or "\\" in path:
            raise ExperimentError(f"{case_id} changed-files.json 包含非法路径")
        changed_files.append(path)
    if len(changed_files) != len(set(changed_files)):
        raise ExperimentError(f"{case_id} changed-files.json 包含重复路径")

    candidate_payload = json.loads(contents["impact-candidates.json"].decode("utf-8"))
    candidate_items = (
        candidate_payload.get("candidates")
        if isinstance(candidate_payload, dict)
        else None
    )
    if not isinstance(candidate_items, list):
        raise ExperimentError(f"{case_id} impact-candidates.json 不合法")
    candidates: list[str] = []
    for item in candidate_items:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path or "\\" in path:
            raise ExperimentError(f"{case_id} impact-candidates.json 包含非法路径")
        candidates.append(path)
    if len(candidates) != len(set(candidates)):
        raise ExperimentError(f"{case_id} impact-candidates.json 包含重复路径")
    if len(candidates) != manifest.get("impact_candidate_count"):
        raise ExperimentError(f"{case_id} 候选数量与 manifest 不一致")

    return CaseMaterial(
        case_id=case_id,
        case_dir=case_dir,
        manifest=manifest,
        core_bytes=core,
        appendix_bytes=appendix,
        arm_a_bytes=arm_a,
        arm_b_bytes=arm_b,
        changed_files=tuple(changed_files),
        candidates=tuple(candidates),
    )


def _load_all_cases() -> dict[str, CaseMaterial]:
    return {case_id: _load_case_material(case_id) for case_id in ("C1", "C2", "C3", "C4", "C5")}


def _schema_bytes() -> bytes:
    schema = verdict_output_schema([])
    return (
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _case_binding(material: CaseMaterial) -> dict[str, Any]:
    manifest_bytes = material.case_dir.joinpath("materialization.json").read_bytes()
    artifacts = material.manifest["artifacts"]
    return {
        "appendix_sha256": artifacts["context-appendix.md"]["sha256"],
        "arm_a_prompt_sha256": artifacts["arm-a-prompt.md"]["sha256"],
        "arm_b_prompt_sha256": artifacts["arm-b-prompt.md"]["sha256"],
        "base_revision": material.manifest["base_revision"],
        "candidate_revision": material.manifest["candidate_revision"],
        "candidate_tree": material.manifest["candidate_tree"],
        "case_id": material.case_id,
        "core_pack_sha256": artifacts["core-review-prompt.md"]["sha256"],
        "diff_sha256": material.manifest["diff_sha256"],
        "generator_revision": material.manifest["generator_revision"],
        "impact_candidates_sha256": artifacts["impact-candidates.json"]["sha256"],
        "materialization_manifest_sha256": _sha256_bytes(manifest_bytes),
        "runtime_commit": material.manifest["runtime_commit"],
        "runtime_src_tree": material.manifest["runtime_src_tree"],
    }


def _run_order_payload() -> list[dict[str, Any]]:
    return [
        {
            "arm": item.arm,
            "case_id": item.case_id,
            "label": item.label,
            "repetition": item.repetition,
            "sequence": item.sequence,
        }
        for item in RUN_ORDER
    ]


def _build_freeze_payload(
    cases: dict[str, CaseMaterial],
    *,
    codex_version: str,
) -> dict[str, Any]:
    case_bindings = [_case_binding(cases[case_id]) for case_id in sorted(cases)]
    run_order = _run_order_payload()
    availability_bytes = MODEL_AVAILABILITY_PATH.read_bytes()
    runner_bytes = Path(__file__).read_bytes()
    return {
        "case_bindings": case_bindings,
        "case_bindings_sha256": _sha256_bytes(_canonical_json_bytes(case_bindings)),
        "codex_version": codex_version,
        "created_at": _utc_now(),
        "ephemeral": True,
        "experiment_id": EXPERIMENT_ID,
        "model": MODEL,
        "model_availability_sha256": _sha256_bytes(availability_bytes),
        "output_schema_sha256": _sha256_bytes(_schema_bytes()),
        "reasoning_effort": REASONING_EFFORT,
        "run_order": run_order,
        "run_order_sha256": _sha256_bytes(_canonical_json_bytes(run_order)),
        "runner_sha256": _sha256_bytes(runner_bytes),
        "sandbox": SANDBOX,
        "schema_version": SCHEMA_VERSION,
        "timeout_seconds": TIMEOUT_SECONDS,
    }


def _freeze_without_timestamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "created_at"}


def _validate_or_prepare_freeze(
    cases: dict[str, CaseMaterial],
    *,
    codex_version: str,
    create: bool,
) -> dict[str, Any]:
    expected = _build_freeze_payload(cases, codex_version=codex_version)
    if FREEZE_PATH.exists():
        actual = _read_json_object(FREEZE_PATH)
        if _freeze_without_timestamp(actual) != _freeze_without_timestamp(expected):
            raise ExperimentError("实验 Freeze 已与当前 Runner、输入或模型合同漂移")
        return actual
    if not create:
        return expected
    _write_json_exclusive(FREEZE_PATH, expected)
    return expected


def _existing_formal_labels() -> list[str]:
    if not FORMAL_RUNS_ROOT.exists():
        return []
    expected = {item.label for item in RUN_ORDER}
    labels: list[str] = []
    for entry in FORMAL_RUNS_ROOT.iterdir():
        if entry.name not in expected:
            raise ExperimentError(f"formal-runs 包含未登记条目：{entry.name}")
        if not entry.is_dir():
            raise ExperimentError(f"formal-runs 条目不是目录：{entry.name}")
        labels.append(entry.name)
    return sorted(labels)


def _next_run_spec() -> RunSpec | None:
    existing = set(_existing_formal_labels())
    seen_gap = False
    for item in RUN_ORDER:
        if item.label in existing:
            if seen_gap:
                raise ExperimentError("formal-runs 顺序存在缺口，不能继续")
            continue
        seen_gap = True
        return item
    return None


def _worktree_status_bytes(worktree: Path) -> bytes:
    return _git_bytes(
        worktree,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignored=matching",
        "-z",
    )


def _worktree_fingerprint(worktree: Path) -> tuple[dict[str, Any], bytes]:
    status = _worktree_status_bytes(worktree)
    head = _git_text(worktree, "rev-parse", "HEAD")
    tree = _git_text(worktree, "rev-parse", "HEAD^{tree}")
    index = _git_bytes(worktree, "ls-files", "-s", "-z")
    payload = {
        "head": head,
        "index_sha256": _sha256_bytes(index),
        "status_sha256": _sha256_bytes(status),
        "status_size": len(status),
        "tree": tree,
    }
    payload["fingerprint_sha256"] = _sha256_bytes(_canonical_json_bytes(payload))
    return payload, status


def _create_candidate_worktree(run_dir: Path, material: CaseMaterial) -> Path:
    worktree = run_dir / "candidate-worktree"
    if worktree.exists():
        raise ExperimentError("candidate worktree 已存在，拒绝复用")
    candidate = material.manifest["candidate_revision"]
    _run_git(
        REPO_ROOT,
        "worktree",
        "add",
        "--detach",
        str(worktree),
        candidate,
    )
    actual = _git_text(worktree, "rev-parse", "HEAD")
    if actual != candidate:
        raise ExperimentError("新建 worktree 未绑定到冻结 candidate")
    tree = _git_text(worktree, "rev-parse", "HEAD^{tree}")
    if tree != material.manifest["candidate_tree"]:
        raise ExperimentError("新建 worktree tree 与 materialization 不一致")
    status = _worktree_status_bytes(worktree)
    if status:
        raise ExperimentError("新建 candidate worktree 不是干净状态")
    return worktree


def _prompt_contract(
    material: CaseMaterial,
    arm: Literal["A", "B"],
) -> tuple[str, dict[str, Any]]:
    prompt_bytes = material.prompt_bytes(arm)
    try:
        prompt = prompt_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExperimentError("冻结 Prompt 不是合法 UTF-8") from exc
    if prompt.encode("utf-8") != prompt_bytes:
        raise ExperimentError("冻结 Prompt UTF-8 往返不一致")
    if redact_text(prompt) != prompt:
        raise ExperimentError("冻结 Prompt 会被 Runtime 脱敏器改写，不能按原字节调用")
    artifact_name = "arm-a-prompt.md" if arm == "A" else "arm-b-prompt.md"
    contract = _artifact_contract(material.manifest, artifact_name)
    if _sha256_bytes(prompt_bytes) != contract["sha256"]:
        raise ExperimentError("冻结 Prompt 哈希与 manifest 不一致")
    return prompt, {
        "artifact": f"{material.case_id}/{artifact_name}",
        "chars": len(prompt),
        "sha256": contract["sha256"],
        "utf8_bytes": len(prompt_bytes),
    }


def _parse_command(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return " ".join(value)
    return None


def _path_in_text(path: str, text: str) -> bool:
    normalized_text = text.replace("\\", "/")
    return path in normalized_text


def _is_completed_read_command(command: str, status: object) -> bool:
    if isinstance(status, str) and status.lower() in {
        "failed",
        "declined",
        "cancelled",
        "canceled",
    }:
        return False
    if WRITE_COMMAND_PATTERN.search(command):
        return False
    return READ_COMMAND_PATTERN.search(command) is not None


def _analyze_jsonl(
    path: Path,
    candidates: tuple[str, ...],
    final_message: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.is_file():
        return (
            {
                "claimed_unverified_paths": [],
                "candidate_count": len(candidates),
                "jsonl_available": False,
                "unknown_paths": list(candidates),
                "verified_paths": [],
                "verified_reads": [],
            },
            {
                "events": [],
                "status": "unavailable",
            },
        )

    started_commands: dict[str, str] = {}
    verified_reads: list[dict[str, Any]] = []
    verified_paths: list[str] = []
    usage_events: list[dict[str, Any]] = []
    invalid_lines: list[int] = []
    sanitization_failures: list[dict[str, Any]] = []

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines.append(line_number)
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if isinstance(event_type, str) and event_type.startswith("vega."):
            sanitization_failures.append(
                {"line": line_number, "type": event_type}
            )
        if event_type == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                usage_events.append({"line": line_number, "usage": usage})
        if event_type not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        item_id = item.get("id")
        command = _parse_command(item.get("command"))
        if (
            event_type == "item.started"
            and isinstance(item_id, str)
            and command is not None
        ):
            started_commands[item_id] = command
            continue
        if event_type != "item.completed":
            continue
        if command is None and isinstance(item_id, str):
            command = started_commands.get(item_id)
        if command is None or not _is_completed_read_command(
            command,
            item.get("status"),
        ):
            continue
        matched = [path for path in candidates if _path_in_text(path, command)]
        for candidate in matched:
            if candidate not in verified_paths:
                verified_paths.append(candidate)
        if matched:
            verified_reads.append(
                {
                    "candidate_paths": matched,
                    "command_sha256": _sha256_bytes(command.encode("utf-8")),
                    "event_line": line_number,
                    "item_id": item_id if isinstance(item_id, str) else None,
                    "proof": "completed_read_command",
                }
            )

    claimed_paths = [
        path
        for path in candidates
        if path not in verified_paths and _path_in_text(path, final_message)
    ]
    unknown_paths = [
        path
        for path in candidates
        if path not in verified_paths and path not in claimed_paths
    ]
    trace = {
        "candidate_count": len(candidates),
        "claimed_unverified_paths": claimed_paths,
        "invalid_jsonl_lines": invalid_lines,
        "jsonl_available": True,
        "sanitization_failures": sanitization_failures,
        "unknown_paths": unknown_paths,
        "verified_paths": verified_paths,
        "verified_reads": verified_reads,
    }
    token = {
        "events": usage_events,
        "status": "available" if usage_events else "unavailable",
    }
    return trace, token


def _parse_verdict(
    final_message: str,
    changed_files: tuple[str, ...],
    result: RunnerResult,
) -> tuple[ReviewVerdict | None, ReviewVerdict | None, dict[str, Any], str | None]:
    if not final_message.strip():
        return None, None, {}, "最终 agent_message 为空"
    try:
        verdict = ReviewVerdict.model_validate_json(final_message)
    except Exception as exc:  # noqa: BLE001 - 不可信模型输出必须转为可记录错误
        return (
            None,
            None,
            {},
            f"{type(exc).__name__}：{redact_text(str(exc))[:1000]}",
        )
    coverage = build_review_file_coverage(changed_files, verdict.reviewed_files)
    trusted = (
        result.status == "success"
        and not result.termination_unconfirmed
        and result.error is None
    )
    effective, issues = enforce_review_file_coverage(
        verdict,
        list(changed_files),
        trusted=trusted,
    )
    contract = {
        "file_coverage": coverage,
        "file_coverage_issues": issues,
        "risk_disclosures_empty": not verdict.risk_disclosures,
        "runner_result_trusted": trusted,
    }
    return verdict, effective, contract, None


def _copy_jsonl_artifact(execution_dir: Path, run_dir: Path) -> Path | None:
    source = execution_dir / "process-output.txt"
    if not source.is_file():
        return None
    target = run_dir / "codex-jsonl.jsonl"
    _write_bytes_exclusive(target, source.read_bytes())
    return target


def _invoke_reviewer(
    *,
    run_dir: Path,
    worktree: Path,
    material: CaseMaterial,
    arm: Literal["A", "B"],
    repetition: int,
    label: str,
    executable: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    prompt, prompt_info = _prompt_contract(material, arm)
    before_fingerprint, before_status = _worktree_fingerprint(worktree)
    if before_status:
        raise ExperimentError("Reviewer 调用前 candidate worktree 已发生变化")
    _write_bytes_exclusive(run_dir / "workspace-status-before.bin", before_status)

    execution_dir = run_dir / "execution"
    schema = verdict_output_schema([])
    schema_sha256 = _sha256_bytes(_schema_bytes())
    precall = {
        "arm": arm,
        "base_revision": material.manifest["base_revision"],
        "candidate_revision": material.manifest["candidate_revision"],
        "candidate_tree": material.manifest["candidate_tree"],
        "case_id": material.case_id,
        "core_pack_sha256": material.manifest["artifacts"]["core-review-prompt.md"]["sha256"],
        "diff_sha256": material.manifest["diff_sha256"],
        "ephemeral": True,
        "experiment_id": EXPERIMENT_ID,
        "generator_revision": material.manifest["generator_revision"],
        "impact_candidates_sha256": material.manifest["artifacts"]["impact-candidates.json"]["sha256"],
        "model": MODEL,
        "output_schema_sha256": schema_sha256,
        "prompt": prompt_info,
        "reasoning_effort": REASONING_EFFORT,
        "repetition": repetition,
        "run_label": label,
        "runner_sha256": _sha256_bytes(Path(__file__).read_bytes()),
        "runtime_commit": material.manifest["runtime_commit"],
        "runtime_src_tree": material.manifest["runtime_src_tree"],
        "sandbox": SANDBOX,
        "started_at": _utc_now(),
        "timeout_seconds": timeout_seconds,
        "worktree_before": before_fingerprint,
    }
    _write_json_exclusive(run_dir / "run-precall.json", precall)

    runner = CodexExecRunner(
        executable=executable,
        options=CodexExecOptions(
            model=MODEL,
            reasoning_effort=REASONING_EFFORT,
            ephemeral=True,
        ),
        output_schema=schema,
    )
    context = RunnerExecutionContext(
        execution_root=run_dir,
        execution_dir=execution_dir,
        run_id=label,
        step="reviewer",
        iteration=repetition,
    )
    started = time.monotonic()
    try:
        result = runner.run(
            prompt,
            worktree,
            sandbox=SANDBOX,
            timeout_seconds=timeout_seconds,
            execution_context=context,
        )
    except Exception as exc:  # noqa: BLE001 - 实验控制面必须形成失败记录
        result = RunnerResult(
            status="error",
            output="",
            error=f"Runner 调用异常：{type(exc).__name__}：{redact_text(str(exc))}",
            command=[],
        )
    duration = round(time.monotonic() - started, 3)

    after_fingerprint, after_status = _worktree_fingerprint(worktree)
    _write_bytes_exclusive(run_dir / "workspace-status-after.bin", after_status)
    workspace_unchanged = (
        before_fingerprint["fingerprint_sha256"]
        == after_fingerprint["fingerprint_sha256"]
        and not after_status
    )

    schema_path = execution_dir / "output-schema.json"
    schema_matches = (
        schema_path.is_file()
        and schema_path.read_bytes() == _schema_bytes()
    )
    jsonl_path = _copy_jsonl_artifact(execution_dir, run_dir)
    final_message = result.output
    if final_message:
        _write_text_exclusive(run_dir / "final-message.txt", final_message + "\n")

    verdict, effective_verdict, verdict_contract, parse_error = _parse_verdict(
        final_message,
        material.changed_files,
        result,
    )
    if verdict is not None:
        _write_bytes_exclusive(
            run_dir / "review-verdict.json",
            _canonical_json_bytes(verdict.model_dump(mode="json")),
        )
    if effective_verdict is not None:
        _write_bytes_exclusive(
            run_dir / "effective-verdict.json",
            _canonical_json_bytes(effective_verdict.model_dump(mode="json")),
        )
    if parse_error is not None:
        _write_text_exclusive(run_dir / "parse-error.txt", parse_error + "\n")

    trace, token = _analyze_jsonl(
        jsonl_path or execution_dir / "process-output.txt",
        material.candidates,
        final_message,
    )
    _write_json_exclusive(run_dir / "read-trace.json", trace)
    _write_json_exclusive(run_dir / "token-usage.json", token)

    execution_payload = (
        _read_json_object(execution_dir / "execution.json")
        if (execution_dir / "execution.json").is_file()
        else None
    )
    result_payload = {
        "arm": arm,
        "case_id": material.case_id,
        "command": result.command or [],
        "codex_jsonl_sha256": (
            _sha256_bytes(jsonl_path.read_bytes()) if jsonl_path is not None else None
        ),
        "duration_seconds": duration,
        "effective_verdict": (
            effective_verdict.verdict if effective_verdict is not None else None
        ),
        "error": result.error,
        "execution": execution_payload,
        "finished_at": _utc_now(),
        "model_process_started": bool(
            isinstance(execution_payload, dict)
            and execution_payload.get("child_pid")
        ),
        "output_schema_matches": schema_matches,
        "parse_error": parse_error,
        "repetition": repetition,
        "run_label": label,
        "runner_status": result.status,
        "termination_unconfirmed": result.termination_unconfirmed,
        "token_status": token["status"],
        "verdict": verdict.verdict if verdict is not None else None,
        "verdict_contract": verdict_contract,
        "workspace_unchanged": workspace_unchanged,
        "worktree_after": after_fingerprint,
    }
    _write_json_exclusive(run_dir / "run-result.json", result_payload)
    return result_payload


def _preflight(*, require_codex: bool) -> tuple[dict[str, CaseMaterial], dict[str, Any]]:
    head = _assert_repo_state()
    cases = _load_all_cases()
    generator_revisions = {
        material.manifest["generator_revision"] for material in cases.values()
    }
    if generator_revisions != {head}:
        raise ExperimentError("五案 generator_revision 未统一绑定当前 HEAD")
    codex_version = (
        _codex_version()
        if require_codex
        else _read_json_object(MODEL_AVAILABILITY_PATH).get("codex_version")
    )
    if not isinstance(codex_version, str):
        raise ExperimentError("无法确定 Codex CLI 版本")
    model_availability = _validate_model_availability(codex_version)
    freeze = _validate_or_prepare_freeze(
        cases,
        codex_version=codex_version,
        create=False,
    )
    next_run = _next_run_spec()
    summary = {
        "case_count": len(cases),
        "codex_version": codex_version,
        "formal_model_calls_started": FREEZE_PATH.exists(),
        "freeze_preview_sha256": _sha256_bytes(
            _canonical_json_bytes(_freeze_without_timestamp(freeze))
        ),
        "generator_revision": head,
        "model": MODEL,
        "model_availability_checked_at": model_availability.get("checked_at"),
        "next_run": next_run.label if next_run is not None else None,
        "reasoning_effort": REASONING_EFFORT,
        "run_count_existing": len(_existing_formal_labels()),
        "sandbox": SANDBOX,
        "timeout_seconds": TIMEOUT_SECONDS,
    }
    return cases, summary


def _remove_fake_worktree(worktree: Path) -> None:
    resolved = worktree.resolve(strict=False)
    fake_root = (LOCAL_ROOT / "fake-smoke").resolve(strict=False)
    try:
        resolved.relative_to(fake_root)
    except ValueError as exc:
        raise ExperimentError("拒绝清理 fake-smoke 范围外的 worktree") from exc
    if worktree.exists():
        _run_git(REPO_ROOT, "worktree", "remove", "--force", str(worktree))


def _fake_smoke() -> dict[str, Any]:
    if not FAKE_EXECUTABLE.is_file():
        raise ExperimentError("fake Codex executable 不存在")
    cases, _ = _preflight(require_codex=False)
    fake_root = LOCAL_ROOT / "fake-smoke"
    fake_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="run-", dir=fake_root) as temp:
        temp_root = Path(temp)
        run_dir = temp_root / "valid"
        run_dir.mkdir()
        worktree = _create_candidate_worktree(run_dir, cases["C1"])
        try:
            result = _invoke_reviewer(
                run_dir=run_dir,
                worktree=worktree,
                material=cases["C1"],
                arm="B",
                repetition=1,
                label="fake-C1-B1",
                executable=str(FAKE_EXECUTABLE),
                timeout_seconds=30,
            )
            trace = _read_json_object(run_dir / "read-trace.json")
            token = _read_json_object(run_dir / "token-usage.json")
            if result["runner_status"] != "success":
                raise ExperimentError("fake valid 路径未返回 success")
            if result["verdict"] != "approve":
                raise ExperimentError("fake valid 路径未解析 approve")
            if result["effective_verdict"] != "approve":
                raise ExperimentError("fake valid 路径未通过文件覆盖合同")
            if result["termination_unconfirmed"]:
                raise ExperimentError("fake valid 路径出现终止未确认")
            if not result["workspace_unchanged"]:
                raise ExperimentError("fake valid 路径改变了 candidate worktree")
            if not result["output_schema_matches"]:
                raise ExperimentError("fake valid 路径 output schema 漂移")
            if token.get("status") != "available":
                raise ExperimentError("fake valid 路径未提取 Provider Token 字段")
            if not trace.get("verified_paths"):
                raise ExperimentError("fake valid 路径未提取候选读取 Trace")
        finally:
            _remove_fake_worktree(worktree)

    with tempfile.TemporaryDirectory(prefix="run-", dir=fake_root) as temp:
        temp_root = Path(temp)
        run_dir = temp_root / "invalid-json"
        run_dir.mkdir()
        worktree = _create_candidate_worktree(run_dir, cases["C1"])
        previous_mode = os.environ.get("RCB01_FAKE_MODE")
        os.environ["RCB01_FAKE_MODE"] = "invalid_json"
        try:
            result = _invoke_reviewer(
                run_dir=run_dir,
                worktree=worktree,
                material=cases["C1"],
                arm="A",
                repetition=1,
                label="fake-C1-A-invalid",
                executable=str(FAKE_EXECUTABLE),
                timeout_seconds=30,
            )
            if result["runner_status"] != "error":
                raise ExperimentError("fake invalid JSON 未 fail-closed")
            if result["model_process_started"] is not True:
                raise ExperimentError("fake invalid JSON 没有形成可核验进程记录")
            if result["verdict"] is not None:
                raise ExperimentError("fake invalid JSON 被错误解析为 verdict")
        finally:
            if previous_mode is None:
                os.environ.pop("RCB01_FAKE_MODE", None)
            else:
                os.environ["RCB01_FAKE_MODE"] = previous_mode
            _remove_fake_worktree(worktree)

    with tempfile.TemporaryDirectory(prefix="case-", dir=LOCAL_ROOT) as temp:
        tampered = Path(temp) / "C1"
        shutil.copytree(LOCAL_ROOT / "C1", tampered)
        prompt_path = tampered / "arm-a-prompt.md"
        prompt_path.write_bytes(prompt_path.read_bytes() + b"\n")
        try:
            validate_materialization(REPO_ROOT, tampered)
        except (MaterializationError, OSError):
            pass
        else:
            raise ExperimentError("篡改 Prompt 未被正式 validator 拒绝")

    return {
        "fake_model_calls": 0,
        "invalid_json_fail_closed": True,
        "prompt_tamper_rejected": True,
        "status": "passed",
        "trace_extraction": True,
        "valid_runner_path": True,
    }


def _run_formal(sequence: int, confirmation: str) -> dict[str, Any]:
    if sequence < 1 or sequence > len(RUN_ORDER):
        raise ExperimentError("sequence 必须在 1 至 20")
    spec = RUN_ORDER[sequence - 1]
    if confirmation != spec.confirmation:
        raise ExperimentError(
            f"正式模型调用需要精确确认：--confirm {spec.confirmation}"
        )
    cases, _ = _preflight(require_codex=True)
    next_spec = _next_run_spec()
    if next_spec is None:
        raise ExperimentError("20 次正式运行已经全部登记")
    if next_spec != spec:
        raise ExperimentError(
            f"固定顺序要求下一次运行 {next_spec.label}，不能启动 {spec.label}"
        )

    FORMAL_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    freeze = _validate_or_prepare_freeze(
        cases,
        codex_version=_codex_version(),
        create=True,
    )
    run_dir = FORMAL_RUNS_ROOT / spec.label
    run_dir.mkdir(exist_ok=False)
    _write_json_exclusive(
        run_dir / "run-registration.json",
        {
            "arm": spec.arm,
            "case_id": spec.case_id,
            "confirmation": spec.confirmation,
            "experiment_freeze_sha256": _sha256_bytes(
                FREEZE_PATH.read_bytes()
            ),
            "registered_at": _utc_now(),
            "repetition": spec.repetition,
            "run_label": spec.label,
            "sequence": spec.sequence,
        },
    )

    material = cases[spec.case_id]
    try:
        worktree = _create_candidate_worktree(run_dir, material)
        return _invoke_reviewer(
            run_dir=run_dir,
            worktree=worktree,
            material=material,
            arm=spec.arm,
            repetition=spec.repetition,
            label=spec.label,
            executable="codex",
            timeout_seconds=TIMEOUT_SECONDS,
        )
    except Exception as exc:
        failure_path = run_dir / "precall-failure.json"
        if not failure_path.exists():
            _write_json_exclusive(
                failure_path,
                {
                    "error": f"{type(exc).__name__}：{redact_text(str(exc))}",
                    "experiment_freeze_sha256": _sha256_bytes(
                        _canonical_json_bytes(freeze)
                    ),
                    "failed_at": _utc_now(),
                    "model_call_started": (run_dir / "execution" / "execution.json").exists(),
                    "run_label": spec.label,
                },
            )
        raise


def _print_json(payload: object) -> None:
    print(_canonical_json_bytes(payload).decode("utf-8"), end="")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "RCB-01 本地 A/B Reviewer Runner。preflight 与 fake-smoke 不调用真实模型；"
            "run 必须提供精确确认字符串。"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="只读复核五案、模型合同、顺序与 Freeze。")
    subparsers.add_parser("fake-smoke", help="使用本地 fake Codex 验证 Runner，不调用模型。")
    run_parser = subparsers.add_parser("run", help="按固定顺序执行一次正式 Reviewer 调用。")
    run_parser.add_argument("--sequence", type=int, required=True)
    run_parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    try:
        if args.command == "preflight":
            _, summary = _preflight(require_codex=True)
            _print_json(summary)
            return 0
        if args.command == "fake-smoke":
            _print_json(_fake_smoke())
            return 0
        _print_json(_run_formal(args.sequence, args.confirm))
        return 0
    except (ExperimentError, MaterializationError, OSError, ValueError) as exc:
        print(f"RCB-01 Runner 停止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
