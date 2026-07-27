from __future__ import annotations


def parse_porcelain_v1_paths(payload: bytes) -> tuple[list[str], list[str]]:
    """从已采集的 porcelain v1 状态中提取 tracked 与 untracked 路径。

    reviewer 快照已经需要读取这份稳定机器格式，无需再启动三次 Git 进程分别枚举
    staged、unstaged 和 untracked 路径。rename 的 NUL 格式先给目标路径、再给源路径；
    两者都属于实际变更范围。copy 只记录新增目标路径，源路径内容没有发生变化。
    """
    tokens = [item for item in payload.split(b"\0") if item]
    tracked: list[str] = []
    untracked: list[str] = []
    index = 0
    while index < len(tokens):
        raw_record = tokens[index]
        index += 1
        if len(raw_record) < 4 or raw_record[2:3] != b" ":
            raise RuntimeError("porcelain v1 路径记录不完整")
        xy = raw_record[:2].decode("ascii", errors="replace")
        path = raw_record[3:].decode("utf-8", errors="replace")
        if xy == "??":
            untracked.append(path)
            continue
        if xy == "!!":
            continue

        change_kind = next(
            (status for status in xy if status in {"R", "C"}),
            None,
        )
        if change_kind is not None:
            if index >= len(tokens):
                raise RuntimeError("porcelain v1 rename/copy 记录缺少源路径")
            original_path = tokens[index].decode("utf-8", errors="replace")
            index += 1
            if change_kind == "R":
                tracked.extend([original_path, path])
            else:
                tracked.append(path)
            continue
        tracked.append(path)
    return (
        list(dict.fromkeys(tracked)),
        list(dict.fromkeys(untracked)),
    )


def parse_porcelain_v2_status(
    payload: bytes,
) -> tuple[str, list[str], list[str], list[str]]:
    """解析 `git status --porcelain=v2 -z --branch` 的稳定机器格式。"""
    tokens = [item for item in payload.split(b"\0") if item]
    head_sha = ""
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    index = 0
    while index < len(tokens):
        record = tokens[index].decode("utf-8", errors="replace")
        index += 1
        if record.startswith("# branch.oid "):
            head_sha = record.removeprefix("# branch.oid ").strip()
            continue
        if record.startswith("# "):
            continue
        index = _consume_porcelain_v2_path_record(
            record,
            tokens,
            index,
            staged,
            unstaged,
            untracked,
        )
    if not head_sha or head_sha == "(initial)":
        raise RuntimeError("git HEAD 不可用")
    return (
        head_sha,
        list(dict.fromkeys(staged)),
        list(dict.fromkeys(unstaged)),
        list(dict.fromkeys(untracked)),
    )


def _consume_porcelain_v2_path_record(
    record: str,
    tokens: list[bytes],
    index: int,
    staged: list[str],
    unstaged: list[str],
    untracked: list[str],
) -> int:
    if record.startswith("1 "):
        _append_ordinary_record(record, staged, unstaged)
    elif record.startswith("2 "):
        index = _append_rename_or_copy_record(
            record,
            tokens,
            index,
            staged,
            unstaged,
        )
    elif record.startswith("u "):
        _append_unmerged_record(record, staged, unstaged)
    elif record.startswith("? "):
        untracked.append(record[2:])
    elif not record.startswith("! "):
        raise RuntimeError("porcelain v2 包含未知记录类型")
    return index


def _append_ordinary_record(
    record: str,
    staged: list[str],
    unstaged: list[str],
) -> None:
    parts = record.split(" ", 8)
    if len(parts) != 9 or len(parts[1]) != 2:
        raise RuntimeError("porcelain v2 普通路径记录不完整")
    _append_status_paths(staged, unstaged, parts[1], parts[8])


def _append_rename_or_copy_record(
    record: str,
    tokens: list[bytes],
    index: int,
    staged: list[str],
    unstaged: list[str],
) -> int:
    parts = record.split(" ", 9)
    if len(parts) != 10 or len(parts[1]) != 2 or index >= len(tokens):
        raise RuntimeError("porcelain v2 rename/copy 记录不完整")
    original_path = tokens[index].decode("utf-8", errors="replace")
    _append_status_paths(
        staged,
        unstaged,
        parts[1],
        parts[9],
        original_path=original_path,
        change_kind=parts[8][:1],
    )
    return index + 1


def _append_unmerged_record(
    record: str,
    staged: list[str],
    unstaged: list[str],
) -> None:
    parts = record.split(" ", 10)
    if len(parts) != 11:
        raise RuntimeError("porcelain v2 unmerged 记录不完整")
    staged.append(parts[10])
    unstaged.append(parts[10])


def _append_status_paths(
    staged: list[str],
    unstaged: list[str],
    xy: str,
    path: str,
    *,
    original_path: str | None = None,
    change_kind: str | None = None,
) -> None:
    staged_status, unstaged_status = xy
    if staged_status != ".":
        _append_changed_path(staged, staged_status, path, original_path, change_kind)
    if unstaged_status != ".":
        _append_changed_path(
            unstaged,
            unstaged_status,
            path,
            original_path,
            change_kind,
        )


def _append_changed_path(
    target: list[str],
    status: str,
    path: str,
    original_path: str | None,
    change_kind: str | None,
) -> None:
    effective_kind = status if status in {"R", "C"} else change_kind
    if effective_kind == "R" and original_path is not None:
        target.extend([original_path, path])
    else:
        # Copy 的源路径没有发生修改；普通增删改也只记录当前路径。
        target.append(path)
