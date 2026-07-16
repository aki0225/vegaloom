from __future__ import annotations

import os
import stat
from pathlib import Path


def resolve_runs_root(workspace: Path, *, create: bool = False) -> Path | None:
    """解析当前 workspace 的 runs 根目录，并拒绝链接或 reparse 边界。"""

    if create:
        workspace_root = workspace.resolve()
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建 workspace：{workspace_root}") from exc
        workspace_root = workspace_root.resolve(strict=True)
    else:
        try:
            workspace_root = workspace.resolve(strict=True)
        except FileNotFoundError:
            return None
    if not workspace_root.is_dir():
        raise ValueError(f"workspace 不是目录：{workspace_root}")

    runs_dir = workspace_root / "runs"
    if not os.path.lexists(runs_dir):
        if not create:
            return None
        try:
            runs_dir.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise ValueError(f"无法创建 runs 根目录：{runs_dir}") from exc

    if _is_link_or_reparse_point(runs_dir):
        raise ValueError(
            "runs 根目录不能是符号链接、junction 或其他 reparse point；"
            "请改用 workspace 内的真实目录。"
        )

    try:
        resolved = runs_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"无法解析 runs 根目录：{runs_dir}") from exc
    if not resolved.is_dir():
        raise ValueError(f"runs 根目录不是目录：{runs_dir}")
    if resolved.parent != workspace_root:
        raise ValueError(f"runs 根目录越过 workspace 边界：{runs_dir}")
    return resolved


def create_run_dir(
    workspace: Path,
    base_run_id: str,
    *,
    max_attempts: int = 100,
) -> tuple[str, Path]:
    if max_attempts < 1:
        raise ValueError("max_attempts 必须大于 0。")

    normalized = base_run_id.strip()
    if not normalized:
        raise ValueError("base run_id 不能为空。")
    if Path(normalized).name != normalized:
        raise ValueError(f"base run_id 不能包含路径分隔符：{base_run_id}")

    runs_dir = resolve_runs_root(workspace, create=True)
    assert runs_dir is not None
    for attempt in range(max_attempts):
        run_id = normalized if attempt == 0 else f"{normalized}-{attempt + 1:02d}"
        run_dir = runs_dir / run_id
        try:
            run_dir.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return run_id, run_dir

    raise RuntimeError(
        f"无法创建唯一 run_id：base={normalized}，已尝试 {max_attempts} 次，请稍后重试。"
    )


def resolve_run_dir(workspace: Path, run: str) -> Path:
    if not run.strip():
        raise FileNotFoundError("run 不能为空。")
    run_path = Path(run.strip())
    if run_path.is_absolute():
        raise FileNotFoundError(f"run 不能是绝对路径：{run}")
    if any(part == ".." for part in run_path.parts):
        raise FileNotFoundError(f"run 不能包含上级路径：{run}")

    workspace_root = workspace.resolve()
    runs_dir = resolve_runs_root(workspace)
    if runs_dir is None:
        raise FileNotFoundError(
            f"run 不存在于当前 workspace `{workspace_root}`：{run}；"
            "请回到创建该 run 的 workspace，或显式切换工作目录。"
        )

    parts = run_path.parts
    if len(parts) == 1:
        candidate = runs_dir / parts[0]
    elif len(parts) == 2 and parts[0] == "runs":
        candidate = runs_dir / parts[1]
    else:
        raise FileNotFoundError(f"run 必须是 run_id 或 runs/<run_id>：{run}")

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"run 不存在于当前 workspace `{workspace_root}`：{run}；"
            "请回到创建该 run 的 workspace，或显式切换工作目录。"
        ) from exc

    if not resolved.is_dir():
        raise FileNotFoundError(f"run 不是目录：{run}")
    try:
        resolved.relative_to(runs_dir)
    except ValueError as exc:
        raise FileNotFoundError(f"run 路径越过 workspace/runs 边界：{run}") from exc
    return resolved


def run_name(run_dir: Path) -> str:
    return run_dir.resolve().name


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(file_attributes & reparse_flag)
