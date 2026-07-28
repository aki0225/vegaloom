from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ...execution_control import RunnerExecutionContext
from .probe import ProbeSlice
from .task_pack_models import reject_duplicate_json_keys, validate_contract_path


PROBE_RUN_ROOT_RELATIVE = Path(".tmp", "m2n")
MAX_PROBE_INPUT_BYTES = 128 * 1024

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,23}$")
_EXECUTION_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")
_CANDIDATE_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,99}$")
_FORBIDDEN_PACKET_MARKERS = (
    "verifier/",
    "reference patch",
    "reference.patch",
    "begin reference",
)


class ProbeHarnessError(ValueError):
    """离线探针准备失败；只暴露稳定 issue code。"""

    def __init__(self, issue_code: str) -> None:
        self.issue_code = issue_code
        super().__init__(issue_code)


@dataclass(frozen=True)
class ProbePromptSlice:
    probe_slice: ProbeSlice
    summary: str
    context_packet_path: str
    context_packet: str


@dataclass(frozen=True)
class ProbeCandidate:
    candidate_id: str
    task: str
    slices: tuple[ProbePromptSlice, ...]


def build_probe_run_root(repo_root: Path, run_id: str) -> Path:
    """返回仓库内尚不存在的短 run root。"""

    repo = _resolve_directory(repo_root, "probe_repository_root_invalid")
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ProbeHarnessError("probe_run_id_invalid")
    run_root = repo.joinpath(PROBE_RUN_ROOT_RELATIVE, run_id)
    try:
        resolved = run_root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProbeHarnessError("probe_run_root_invalid") from exc
    if not resolved.is_relative_to(repo):
        raise ProbeHarnessError("probe_run_root_invalid")
    if _existing_ancestor_escapes(repo, run_root):
        raise ProbeHarnessError("probe_run_root_invalid")
    if run_root.exists():
        raise ProbeHarnessError("probe_run_root_exists")
    return run_root


def build_probe_execution_context(
    *,
    repo_root: Path,
    run_root: Path,
    execution_label: str,
    step: str = "ma2b-probe-worker",
    heartbeat_interval_seconds: float = 1.0,
    lease_timeout_seconds: float = 10.0,
    terminate_grace_seconds: float = 3.0,
) -> RunnerExecutionContext:
    """把 execution 身份绑定到物理 run root，避免 stop/recover 串用现场。"""

    repo = _resolve_directory(repo_root, "probe_repository_root_invalid")
    if not _EXECUTION_LABEL_PATTERN.fullmatch(execution_label):
        raise ProbeHarnessError("probe_execution_label_invalid")
    expected_parent = repo.joinpath(PROBE_RUN_ROOT_RELATIVE).resolve(strict=False)
    resolved_run_root = Path(run_root).resolve(strict=False)
    if (
        not expected_parent.is_relative_to(repo)
        or _existing_ancestor_escapes(repo, Path(run_root))
        or resolved_run_root.parent != expected_parent
        or not _RUN_ID_PATTERN.fullmatch(resolved_run_root.name)
    ):
        raise ProbeHarnessError("probe_run_root_invalid")
    return RunnerExecutionContext(
        execution_dir=resolved_run_root / "x" / execution_label,
        run_id=resolved_run_root.name,
        step=step,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        lease_timeout_seconds=lease_timeout_seconds,
        terminate_grace_seconds=terminate_grace_seconds,
    )


def load_probe_candidate(candidate_root: Path) -> ProbeCandidate:
    """只加载 task、plan 与声明的 context packet，不读取 verifier 或 reference。"""

    root = _resolve_directory(candidate_root, "probe_candidate_root_invalid")
    task = _read_candidate_text(root, "task.md")
    _reject_forbidden_prompt_content(task)
    plan = _read_candidate_json(root, "plan.json")
    if set(plan) != {"schema_version", "candidate_id", "slices", "treatments"}:
        raise ProbeHarnessError("probe_plan_schema_invalid")
    if plan.get("schema_version") != 1:
        raise ProbeHarnessError("probe_plan_schema_invalid")
    candidate_id = plan.get("candidate_id")
    if not isinstance(candidate_id, str) or not _CANDIDATE_ID_PATTERN.fullmatch(
        candidate_id
    ):
        raise ProbeHarnessError("probe_plan_schema_invalid")
    raw_slices = plan.get("slices")
    if not isinstance(raw_slices, list) or not 1 <= len(raw_slices) <= 2:
        raise ProbeHarnessError("probe_plan_schema_invalid")

    slices: list[ProbePromptSlice] = []
    for raw_slice in raw_slices:
        if not isinstance(raw_slice, dict) or set(raw_slice) != {
            "allowed_write_paths",
            "context_packet",
            "slice_id",
            "summary",
        }:
            raise ProbeHarnessError("probe_plan_schema_invalid")
        summary = raw_slice.get("summary")
        context_packet_path = raw_slice.get("context_packet")
        allowed_write_paths = raw_slice.get("allowed_write_paths")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 500
            or not isinstance(context_packet_path, str)
            or not context_packet_path.startswith("context/")
            or not isinstance(allowed_write_paths, list)
            or not all(isinstance(item, str) for item in allowed_write_paths)
        ):
            raise ProbeHarnessError("probe_plan_schema_invalid")
        try:
            probe_slice = ProbeSlice(
                slice_id=raw_slice.get("slice_id"),
                allowed_write_paths=tuple(allowed_write_paths),
            )
            normalized_packet_path = validate_contract_path(context_packet_path)
        except (TypeError, ValueError) as exc:
            raise ProbeHarnessError("probe_plan_schema_invalid") from exc
        packet = _read_candidate_text(root, normalized_packet_path)
        _reject_forbidden_prompt_content(packet)
        slices.append(
            ProbePromptSlice(
                probe_slice=probe_slice,
                summary=summary.strip(),
                context_packet_path=normalized_packet_path,
                context_packet=packet,
            )
        )

    slice_ids = [item.probe_slice.slice_id for item in slices]
    if len(slice_ids) != len(set(slice_ids)):
        raise ProbeHarnessError("probe_plan_schema_invalid")
    return ProbeCandidate(
        candidate_id=candidate_id,
        task=task,
        slices=tuple(slices),
    )


def build_probe_worker_prompt(
    candidate: ProbeCandidate,
    *,
    assigned_slice_ids: tuple[str, ...],
) -> str:
    """按分配顺序编译 prompt；未分配 packet 不会进入返回文本。"""

    if not assigned_slice_ids or len(assigned_slice_ids) != len(set(assigned_slice_ids)):
        raise ProbeHarnessError("probe_assignment_invalid")
    by_id = {item.probe_slice.slice_id: item for item in candidate.slices}
    if any(slice_id not in by_id for slice_id in assigned_slice_ids):
        raise ProbeHarnessError("probe_assignment_invalid")

    lines = [
        "# MA-2B Worker 输入",
        "",
        f"- Candidate：`{candidate.candidate_id}`",
        "",
        "## 任务",
        "",
        candidate.task.rstrip(),
        "",
        "## 当前分配",
        "",
    ]
    for slice_id in assigned_slice_ids:
        item = by_id[slice_id]
        lines.extend(
            [
                f"### {slice_id}",
                "",
                item.summary,
                "",
                "允许写入：",
                *[f"- `{path}`" for path in item.probe_slice.allowed_write_paths],
                "",
            ]
        )

    lines.extend(["## 窄上下文包", ""])
    for slice_id in assigned_slice_ids:
        item = by_id[slice_id]
        lines.extend(
            [
                f"### {slice_id}",
                "",
                f"来源：`{item.context_packet_path}`",
                "",
                item.context_packet.rstrip(),
                "",
            ]
        )

    lines.extend(
        [
            "## 执行边界",
            "",
            "- 只修改当前分配列出的允许写路径。",
            "- 不执行整仓搜索；只按上下文包明确列出的窄路径做必要补充读取。",
            "- 不读取实验评测实现、ground truth、历史运行结果或参考答案补丁。",
            "- 不 commit、push、release、联网、调用子代理或写长期 Memory。",
            "- 完成后只报告修改文件和实际执行的最小自检；不要声称外部固定验证器已通过。",
            "",
        ]
    )
    return "\n".join(lines)


def _resolve_directory(path: Path, issue_code: str) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProbeHarnessError(issue_code) from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise ProbeHarnessError(issue_code)
    return resolved


def _existing_ancestor_escapes(repo: Path, path: Path) -> bool:
    current = path
    while not current.exists() and current != repo:
        current = current.parent
    try:
        return not current.resolve(strict=True).is_relative_to(repo)
    except (OSError, RuntimeError, ValueError):
        return True


def _read_candidate_text(root: Path, relative_path: str) -> str:
    path = _resolve_candidate_file(root, relative_path)
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_PROBE_INPUT_BYTES:
            raise ProbeHarnessError("probe_candidate_artifact_too_large")
        text = raw.decode("utf-8")
    except ProbeHarnessError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ProbeHarnessError("probe_candidate_artifact_unreadable") from exc
    if not text.strip():
        raise ProbeHarnessError("probe_candidate_artifact_empty")
    return text


def _read_candidate_json(root: Path, relative_path: str) -> dict[str, object]:
    text = _read_candidate_text(root, relative_path)
    try:
        payload = json.loads(text, object_pairs_hook=reject_duplicate_json_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProbeHarnessError("probe_plan_json_invalid") from exc
    if not isinstance(payload, dict):
        raise ProbeHarnessError("probe_plan_schema_invalid")
    return payload


def _resolve_candidate_file(root: Path, relative_path: str) -> Path:
    try:
        normalized = validate_contract_path(relative_path)
        path = root.joinpath(*normalized.split("/"))
        if path.is_symlink():
            raise ProbeHarnessError("probe_candidate_artifact_path_invalid")
        resolved = path.resolve(strict=True)
    except ProbeHarnessError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProbeHarnessError("probe_candidate_artifact_path_invalid") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ProbeHarnessError("probe_candidate_artifact_path_invalid")
    return resolved


def _reject_forbidden_prompt_content(text: str) -> None:
    lowered = text.casefold()
    if any(marker in lowered for marker in _FORBIDDEN_PACKET_MARKERS):
        raise ProbeHarnessError("probe_context_packet_forbidden_content")


__all__ = [
    "MAX_PROBE_INPUT_BYTES",
    "PROBE_RUN_ROOT_RELATIVE",
    "ProbeCandidate",
    "ProbeHarnessError",
    "ProbePromptSlice",
    "build_probe_execution_context",
    "build_probe_run_root",
    "build_probe_worker_prompt",
    "load_probe_candidate",
]
