from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .agent_contract import AgentState
from .agent_persistence import load_agent_state
from .comparison_binding import require_comparison_binding_from_mapping
from .redaction import write_redacted_json
from .repository_identity import repository_scope, resolve_git_revision
from .workspace_check import capture_review_workspace
from .workspace_inventory import workspace_ignored_path_exclusions
from .workspace_snapshot import ReviewWorkspaceSnapshot


def require_git_root(repo: Path) -> Path:
    root = repo.resolve(strict=True)
    process = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if process.returncode != 0 or Path(process.stdout.strip()).resolve() != root:
        raise ValueError("目标目录必须是 Git 仓库根目录")
    return root


def load_run_metadata(run_dir: Path) -> dict[str, object]:
    try:
        metadata = json.loads(
            (run_dir / "agent-run.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Agent run metadata 无法读取") from exc
    if not isinstance(metadata, dict):
        raise ValueError("Agent run metadata 必须是 JSON object")
    return metadata


def bound_repo(run_dir: Path) -> Path:
    state = load_agent_state(run_dir / "agent-state.json")
    return validate_run_repository_binding(run_dir, state, load_run_metadata(run_dir))


def capture_bound_workspace(run_dir: Path) -> ReviewWorkspaceSnapshot:
    """按已验证的 run metadata 采集当前 Workspace。"""

    state = load_agent_state(run_dir / "agent-state.json")
    metadata = load_run_metadata(run_dir)
    repo = validate_run_repository_binding(run_dir, state, metadata)
    exclusions = workspace_ignored_path_exclusions(run_dir.parent.parent, repo)
    comparison_base, comparison_paths = require_comparison_binding_from_mapping(
        metadata,
        base_key="comparison_base_revision",
    )
    return capture_review_workspace(
        repo,
        ignored_path_exclusions=exclusions,
        comparison_base_sha=comparison_base,
        comparison_paths=comparison_paths,
    )


def validate_run_repository_binding(
    run_dir: Path,
    state: AgentState,
    metadata: dict[str, object],
) -> Path:
    """把本机 run、Agent State 与真实 Git 仓库绑定为同一身份。"""

    _validate_run_identity(run_dir, state, metadata)
    repo = _validate_repo_identity(state, metadata)
    _validate_revision_binding(repo, metadata, "base_revision", "base revision")
    _validate_task_card_binding(repo, metadata)
    _validate_revision_binding(
        repo,
        metadata,
        "comparison_base_revision",
        "comparison base",
    )
    return repo


def write_run_metadata(
    run_dir: Path,
    repo: Path,
    base_revision: str,
    *,
    task_card: str | None = None,
    comparison_base_revision: str | None = None,
    comparison_paths: list[str] | None = None,
    task_card_sha256: str | None = None,
) -> None:
    repo_root = require_git_root(repo)
    _require_revision(repo_root, base_revision, "base revision 不属于目标仓库")
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "repo_path": str(repo_root),
        "base_revision": base_revision,
        "task_card": task_card,
        "task_card_sha256": task_card_sha256,
    }
    if comparison_base_revision is not None:
        payload["comparison_base_revision"] = comparison_base_revision
        payload["comparison_paths"] = list(comparison_paths or [])
        require_comparison_binding_from_mapping(
            payload,
            base_key="comparison_base_revision",
        )
        _require_revision(
            repo_root,
            comparison_base_revision,
            "comparison base 不属于目标仓库",
        )
    write_redacted_json(run_dir / "agent-run.json", payload)


def _validate_run_identity(
    run_dir: Path,
    state: AgentState,
    metadata: dict[str, object],
) -> None:
    if metadata.get("schema_version") != 1:
        raise ValueError("Agent run metadata schema 无法验证")
    if metadata.get("run_id") != state.run_id or state.run_id != run_dir.name:
        raise ValueError("Agent run metadata、State 与目录身份不一致")


def _validate_repo_identity(
    state: AgentState,
    metadata: dict[str, object],
) -> Path:
    raw_repo = metadata.get("repo_path")
    if not isinstance(raw_repo, str) or not raw_repo.strip():
        raise ValueError("Agent run 缺少 repo binding")
    try:
        repo = require_git_root(Path(raw_repo))
    except (OSError, ValueError) as exc:
        raise ValueError("Agent run 的 repo binding 无法验证") from exc
    if repository_scope(repo) != state.repository_id:
        raise ValueError("Agent State 与 repo binding 指向不同仓库")
    return repo


def _validate_revision_binding(
    repo: Path,
    metadata: dict[str, object],
    key: str,
    label: str,
) -> None:
    revision = metadata.get(key)
    if revision is None and key == "comparison_base_revision":
        return
    if not isinstance(revision, str) or not revision:
        raise ValueError(f"Agent run 缺少 {label}")
    resolved = resolve_git_revision(repo, revision)
    if resolved is None or resolved.commit.lower() != revision.lower():
        raise ValueError(f"Agent run 的 {label} 不属于绑定仓库")


def _validate_task_card_binding(
    repo: Path,
    metadata: dict[str, object],
) -> None:
    task_card = metadata.get("task_card")
    if task_card is None:
        return
    if (
        not isinstance(task_card, str)
        or Path(task_card).is_absolute()
        or any(part in {"", ".", ".."} for part in Path(task_card).parts)
    ):
        raise ValueError("Agent run 的 task_card binding 无效")
    digest = metadata.get("task_card_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("Agent run 的 Task Card binding 缺少或无效内容摘要")
    try:
        task_path = (repo / task_card).resolve(strict=True)
    except OSError as exc:
        raise ValueError("Agent run 绑定的 Task Card 不存在") from exc
    if not task_path.is_relative_to(repo) or not task_path.is_file():
        raise ValueError("Agent run 绑定的 Task Card 越过仓库或不是普通文件")
    if hashlib.sha256(task_path.read_bytes()).hexdigest() != digest:
        raise ValueError("Agent run 绑定的 Task Card 内容已变化")


def _require_revision(repo: Path, revision: str, message: str) -> None:
    resolved = resolve_git_revision(repo, revision)
    if resolved is None or resolved.commit.lower() != revision.lower():
        raise ValueError(message)
