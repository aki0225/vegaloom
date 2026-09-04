from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .agent_change_run import managed_worktree_from_metadata
from .agent_contract import AgentState
from .agent_git_worktree import ManagedChangeWorktree
from .agent_persistence import AgentArtifactError, load_agent_state
from .git_read import run_git_bytes
from .run_utils import resolve_runs_root


ACTIVE_CHANGE_PHASES = frozenset(
    {
        "planning",
        "awaiting_approval",
        "ready",
        "acting",
        "observing",
        "needs_human",
        "finalizing",
    }
)


@dataclass(frozen=True)
class RepositoryChangeRun:
    run_dir: Path
    state: AgentState

    @property
    def is_active(self) -> bool:
        return self.state.phase in ACTIVE_CHANGE_PHASES


class ChangeRunSelectionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        candidates: tuple[RepositoryChangeRun, ...] = (),
    ) -> None:
        super().__init__(message)
        self.candidates = candidates


def resolve_repository_root(location: Path) -> Path:
    """从仓库内任意目录解析 Git 根目录，不要求调用方先切到根目录。"""

    try:
        current = location.resolve(strict=True)
    except OSError as exc:
        raise ChangeRunSelectionError("当前目录无法解析。") from exc
    if not current.is_dir():
        raise ChangeRunSelectionError("当前路径必须是目录。")
    try:
        raw_root = run_git_bytes(
            current,
            ["git", "rev-parse", "--show-toplevel"],
        ).decode("utf-8", errors="strict").strip()
        root = Path(raw_root).resolve(strict=True)
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise ChangeRunSelectionError("当前目录不在可读取的 Git 仓库中。") from exc
    if not raw_root or not root.is_dir():
        raise ChangeRunSelectionError("Git 返回的仓库根目录无效。")
    return root


def list_repository_change_runs(
    location: Path,
) -> tuple[RepositoryChangeRun, ...]:
    """列出绑定当前源仓库的 ChangeRun，并拒绝相关损坏 Artifact。"""

    repository_root = resolve_repository_root(location)
    runs_root = resolve_runs_root(repository_root)
    if runs_root is None:
        return ()
    candidates: list[RepositoryChangeRun] = []
    for run_dir in _safe_agent_run_dirs(runs_root):
        candidate = _load_matching_change_run(run_dir, repository_root)
        if candidate is not None:
            candidates.append(candidate)
    return tuple(
        sorted(
            candidates,
            key=lambda item: (_updated_at_utc(item), item.run_dir.name),
            reverse=True,
        )
    )


def select_repository_change_run(
    location: Path,
) -> RepositoryChangeRun | None:
    """选择当前仓库唯一未终态 ChangeRun，否则返回最近更新的终态 Run。"""

    candidates = list_repository_change_runs(location)
    active = tuple(item for item in candidates if item.is_active)
    if len(active) > 1:
        run_ids = "、".join(item.run_dir.name for item in active)
        raise ChangeRunSelectionError(
            f"当前仓库存在多个未完成 ChangeRun，拒绝自动选择：{run_ids}",
            candidates=active,
        )
    if active:
        return active[0]
    return candidates[0] if candidates else None


def select_named_repository_change_run(
    location: Path,
    run: str,
) -> RepositoryChangeRun:
    """显式选择当前仓库中的一个 ChangeRun，不接受任意路径。"""

    run_path = Path(run.strip())
    if (
        not run.strip()
        or run_path.is_absolute()
        or any(part == ".." for part in run_path.parts)
        or not (
            len(run_path.parts) == 1
            or len(run_path.parts) == 2
            and run_path.parts[0] == "runs"
        )
    ):
        raise ChangeRunSelectionError("--run 必须是 run_id 或 runs/<run_id>")
    matches = [
        item
        for item in list_repository_change_runs(location)
        if item.run_dir.name == run_path.name
    ]
    if len(matches) != 1:
        raise ChangeRunSelectionError(
            "指定 Run 不属于当前仓库的可验证 ChangeRun"
        )
    return matches[0]


def _safe_agent_run_dirs(runs_root: Path) -> tuple[Path, ...]:
    try:
        entries = tuple(runs_root.iterdir())
    except OSError as exc:
        raise ChangeRunSelectionError("无法读取当前仓库的 runs 目录。") from exc
    result: list[Path] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            resolved = entry.resolve(strict=True)
        except OSError:
            continue
        if resolved != entry or resolved.parent != runs_root:
            continue
        if not (
            (resolved / "agent-state.json").is_file()
            or (resolved / "agent-run.json").is_file()
        ):
            continue
        result.append(resolved)
    return tuple(result)


def _load_matching_change_run(
    run_dir: Path,
    repository_root: Path,
) -> RepositoryChangeRun | None:
    state_path = run_dir / "agent-state.json"
    metadata = _read_run_metadata(run_dir, state_path)
    if metadata is None:
        return None
    if "change_run" not in metadata:
        if _state_declares_change(state_path):
            raise ChangeRunSelectionError(
                f"ChangeRun `{run_dir.name}` 缺少 change_run metadata。"
            )
        return None
    handle = _matching_change_handle(run_dir, metadata, repository_root)
    if handle is None:
        return None
    state = _load_required_state(state_path, run_dir.name)
    if state.run_id != run_dir.name or state.run_kind != "change":
        raise ChangeRunSelectionError(
            f"ChangeRun `{run_dir.name}` 的 State 身份不一致。"
        )
    candidate = RepositoryChangeRun(run_dir=run_dir, state=state)
    _updated_at_utc(candidate)
    return candidate


def _read_run_metadata(
    run_dir: Path,
    state_path: Path,
) -> dict[str, object] | None:
    try:
        metadata = json.loads(
            (run_dir / "agent-run.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if _state_declares_change(state_path):
            raise ChangeRunSelectionError(
                f"ChangeRun `{run_dir.name}` 的 agent-run.json 无法读取。"
            ) from exc
        return None
    if not isinstance(metadata, dict):
        raise ChangeRunSelectionError(
            f"Agent run `{run_dir.name}` 的 metadata 必须是 JSON object。"
        )
    return metadata


def _matching_change_handle(
    run_dir: Path,
    metadata: dict[str, object],
    repository_root: Path,
) -> ManagedChangeWorktree | None:
    raw_change_run = metadata.get("change_run")
    if not isinstance(raw_change_run, dict):
        raise ChangeRunSelectionError(
            f"ChangeRun `{run_dir.name}` 的 change_run metadata 已损坏。"
        )
    raw_source_repo = raw_change_run.get("source_repo_path")
    if not isinstance(raw_source_repo, str) or not raw_source_repo.strip():
        raise ChangeRunSelectionError(
            f"ChangeRun `{run_dir.name}` 缺少源仓库路径。"
        )
    source_repo = Path(raw_source_repo)
    if not source_repo.is_absolute():
        raise ChangeRunSelectionError(
            f"ChangeRun `{run_dir.name}` 的源仓库路径不是绝对路径。"
        )
    if not _same_resolved_path(source_repo, repository_root):
        return None
    try:
        handle = managed_worktree_from_metadata(metadata)
    except (OSError, ValueError) as exc:
        raise ChangeRunSelectionError(
            f"ChangeRun `{run_dir.name}` 的 metadata 无法验证。"
        ) from exc
    if (
        metadata.get("schema_version") != 1
        or metadata.get("run_id") != run_dir.name
        or handle.run_id != run_dir.name
    ):
        raise ChangeRunSelectionError(
            f"ChangeRun `{run_dir.name}` 的 metadata 身份不一致。"
        )
    return handle


def _load_optional_state(path: Path) -> AgentState | None:
    try:
        return load_agent_state(path)
    except (AgentArtifactError, UnicodeError):
        return None


def _state_declares_change(path: Path) -> bool:
    state = _load_optional_state(path)
    if state is not None:
        return state.run_kind == "change"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(raw, dict)
        and isinstance(raw.get("data"), dict)
        and raw["data"].get("run_kind") == "change"
    )


def _load_required_state(path: Path, run_id: str) -> AgentState:
    try:
        return load_agent_state(path)
    except (AgentArtifactError, UnicodeError) as exc:
        raise ChangeRunSelectionError(
            f"ChangeRun `{run_id}` 的 agent-state.json 无法验证。"
        ) from exc


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        left_resolved = left.resolve(strict=True)
        right_resolved = right.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return os.path.normcase(str(left_resolved)) == os.path.normcase(
        str(right_resolved)
    )


def _updated_at_utc(candidate: RepositoryChangeRun) -> datetime:
    try:
        parsed = datetime.fromisoformat(candidate.state.updated_at)
    except ValueError as exc:
        raise ChangeRunSelectionError(
            f"ChangeRun `{candidate.run_dir.name}` 的 updated_at 无法解析。"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
