from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ALLOW_TEST_FIXTURE_MARKER = "# repo-path-policy:" + " allow-test-fixture"

_BASE_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_PATH_RULES = (
    (
        "windows-drive-absolute",
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
        "禁止提交 Windows 盘符绝对路径，请改用仓库相对路径或变量占位符",
    ),
    (
        "windows-unc",
        re.compile(
            r"(?<![A-Za-z0-9._\\-])(?:\\\\){1,2}"
            r"[A-Za-z0-9._$-]+\\+[A-Za-z0-9._$-]+"
        ),
        "禁止提交 Windows UNC 本机或共享路径，请改用仓库相对路径或变量占位符",
    ),
    (
        "posix-user-home",
        re.compile(
            r"(?<![A-Za-z0-9:])/(?:mnt/[A-Za-z]/)?"
            r"(?:home|Users)/[^/\s\"'`<>]+(?:/|$)"
        ),
        "禁止提交真实 POSIX 用户目录，请改用仓库相对路径或变量占位符",
    ),
)

_SENSITIVE_EXACT_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    "client_secret.json",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
_SENSITIVE_SUFFIXES = {
    ".db",
    ".docx",
    ".kdbx",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".xls",
    ".xlsx",
}


@dataclass(frozen=True)
class HygieneViolation:
    relative_path: str
    line_number: int | None
    rule: str
    message: str
    revision: str | None = None


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    object_type: str
    object_id: str
    relative_path: str


@dataclass(frozen=True)
class GitIndexEntry:
    mode: str
    object_id: str
    stage: int
    relative_path: str


class HygieneCheckError(RuntimeError):
    """仓库卫生检查无法可靠完成。"""


def find_text_violations(
    relative_path: str,
    text: str,
    *,
    revision: str | None = None,
) -> list[HygieneViolation]:
    violations: list[HygieneViolation] = []
    is_test_file = relative_path.startswith("tests/")

    for line_number, line in enumerate(text.splitlines(), 1):
        matches = [
            (rule, message)
            for rule, pattern, message in _PATH_RULES
            if pattern.search(line)
        ]
        has_fixture_marker = ALLOW_TEST_FIXTURE_MARKER in line

        if has_fixture_marker:
            if not is_test_file:
                violations.append(
                    HygieneViolation(
                        relative_path=relative_path,
                        line_number=line_number,
                        rule="invalid-fixture-exemption",
                        message="测试夹具豁免标记只能出现在 tests/ 下",
                        revision=revision,
                    )
                )
                continue
            if not matches:
                violations.append(
                    HygieneViolation(
                        relative_path=relative_path,
                        line_number=line_number,
                        rule="unused-fixture-exemption",
                        message="测试夹具豁免标记没有对应的受限路径，应删除失效标记",
                        revision=revision,
                    )
                )
                continue
            continue

        for rule, message in matches:
            violations.append(
                HygieneViolation(
                    relative_path=relative_path,
                    line_number=line_number,
                    rule=rule,
                    message=message,
                    revision=revision,
                )
            )

    return violations


def find_sensitive_filename_violation(
    relative_path: str,
    *,
    revision: str | None = None,
) -> HygieneViolation | None:
    path = PurePosixPath(relative_path)
    name = path.name.lower()
    suffix = path.suffix.lower()

    if name == ".env.example":
        return None
    if name == ".env" or name.startswith(".env."):
        message = "禁止跟踪真实环境文件；仅允许脱敏的 .env.example"
    elif name in _SENSITIVE_EXACT_NAMES:
        message = "禁止跟踪凭据、包管理器认证配置或私钥文件"
    elif suffix in _SENSITIVE_SUFFIXES:
        message = "禁止跟踪私钥、数据库或 Office 本地文件"
    else:
        return None

    return HygieneViolation(
        relative_path=relative_path,
        line_number=None,
        rule="sensitive-filename",
        message=message,
        revision=revision,
    )


def scan_worktree(
    repo_root: Path,
    *,
    head_commit: str | None = None,
) -> list[HygieneViolation]:
    trusted_head = head_commit or _read_head_commit(repo_root)
    violations: list[HygieneViolation] = []
    for relative_path in _tracked_paths(repo_root):
        filename_violation = find_sensitive_filename_violation(relative_path)
        if filename_violation is not None:
            violations.append(filename_violation)

        data = _read_worktree_file(repo_root, relative_path)
        text = _decode_text(data)
        if text is not None:
            violations.extend(find_text_violations(relative_path, text))
    violations.extend(_eval_worktree_violations(repo_root, trusted_head))
    return violations


def scan_index(
    repo_root: Path,
    *,
    head_commit: str | None = None,
) -> list[HygieneViolation]:
    """直接检查下一次 commit 会消费的 index blob，而不是同名工作区文件。"""
    trusted_head = head_commit or _read_head_commit(repo_root)
    _, entries = _index_snapshot(repo_root)
    violations: list[HygieneViolation] = []
    stage_zero_entries: dict[str, GitIndexEntry] = {}
    readable_object_ids: list[str] = []

    for entry in entries:
        filename_violation = find_sensitive_filename_violation(entry.relative_path)
        if filename_violation is not None:
            violations.append(filename_violation)
        if entry.stage != 0:
            violations.append(_index_unmerged_violation(entry.relative_path))
            continue
        stage_zero_entries[entry.relative_path] = entry
        expected_type = _index_entry_object_type(entry)
        if expected_type == "unknown":
            violations.append(_index_unreadable_violation(entry.relative_path))
            continue
        if expected_type != "blob":
            # gitlink 的 commit 对象不要求存在于 superproject；这里只检查路径和 mode。
            continue
        readable_object_ids.append(entry.object_id)

    objects = _read_git_objects(repo_root, readable_object_ids)
    for entry in stage_zero_entries.values():
        expected_type = _index_entry_object_type(entry)
        if expected_type != "blob":
            continue
        current_object = objects.get(entry.object_id)
        if current_object is None:
            violations.append(_index_unreadable_violation(entry.relative_path))
            continue
        object_type, data = current_object
        if expected_type != object_type:
            violations.append(_index_unreadable_violation(entry.relative_path))
            continue
        text = _decode_text(data)
        if text is not None:
            violations.extend(find_text_violations(entry.relative_path, text))

    violations.extend(
        _eval_index_violations(
            repo_root,
            trusted_head,
            stage_zero_entries,
            objects,
        )
    )
    return violations


def scan_history(
    repo_root: Path,
    base_ref: str,
    *,
    head_commit: str | None = None,
) -> list[HygieneViolation]:
    base_sha = _resolve_base_ref(repo_root, base_ref)
    trusted_head = head_commit or _read_head_commit(repo_root)
    commit_output = _run_git(
        repo_root,
        "rev-list",
        "--reverse",
        f"{base_sha}..{trusted_head}",
    )
    commits = [line for line in commit_output.decode("ascii").splitlines() if line]
    violations: list[HygieneViolation] = []

    for commit in commits:
        revision = commit[:12]
        violations.extend(_eval_revision_violations(repo_root, commit, revision))
        commit_message = _run_git(
            repo_root,
            "show",
            "-s",
            "--format=%B",
            commit,
        ).decode("utf-8", errors="replace")
        violations.extend(
            find_text_violations(
                "<commit-message>",
                commit_message,
                revision=revision,
            )
        )

        for relative_path in _changed_paths(repo_root, commit):
            filename_violation = find_sensitive_filename_violation(
                relative_path,
                revision=revision,
            )
            if filename_violation is not None:
                violations.append(filename_violation)

            data = _read_revision_file(repo_root, commit, relative_path)
            if data is None:
                continue
            text = _decode_text(data)
            if text is not None:
                violations.extend(
                    find_text_violations(
                        relative_path,
                        text,
                        revision=revision,
                    )
                )
    return violations


def _eval_worktree_violations(
    repo_root: Path,
    head_commit: str,
) -> list[HygieneViolation]:
    baseline = _tree_entries(repo_root, head_commit, "eval")
    violations: list[HygieneViolation] = []
    for relative_path, entry in baseline.items():
        path = repo_root.joinpath(*PurePosixPath(relative_path).parts)
        current_kind = _worktree_entry_kind(path)
        expected_kind = _git_entry_kind(entry)
        if current_kind != expected_kind:
            violations.append(_eval_append_only_violation(relative_path))
            continue
        baseline_data = _read_git_blob(repo_root, entry.object_id)
        current_data = _read_worktree_file(repo_root, relative_path)
        if not _has_append_only_prefix(baseline_data, current_data):
            violations.append(_eval_append_only_violation(relative_path))
    return violations


def _eval_index_violations(
    repo_root: Path,
    head_commit: str,
    current: dict[str, GitIndexEntry],
    objects: dict[str, tuple[str, bytes]],
) -> list[HygieneViolation]:
    baseline = _tree_entries(repo_root, head_commit, "eval")
    violations: list[HygieneViolation] = []
    for relative_path, baseline_entry in baseline.items():
        current_entry = current.get(relative_path)
        if (
            current_entry is None
            or current_entry.mode != baseline_entry.mode
            or _index_entry_object_type(current_entry) != baseline_entry.object_type
        ):
            violations.append(_eval_append_only_violation(relative_path))
            continue
        if current_entry.object_id == baseline_entry.object_id:
            continue
        current_object = objects.get(current_entry.object_id)
        if current_object is None or current_object[0] != "blob":
            violations.append(_eval_append_only_violation(relative_path))
            continue
        baseline_data = _read_git_blob(repo_root, baseline_entry.object_id)
        if not _has_append_only_prefix(baseline_data, current_object[1]):
            violations.append(_eval_append_only_violation(relative_path))
    return violations


def _eval_revision_violations(
    repo_root: Path,
    commit: str,
    revision: str,
) -> list[HygieneViolation]:
    parent = _first_parent(repo_root, commit)
    if parent is None:
        return []
    baseline = _tree_entries(repo_root, parent, "eval")
    current = _tree_entries(repo_root, commit, "eval")
    violations: list[HygieneViolation] = []
    for relative_path, baseline_entry in baseline.items():
        current_entry = current.get(relative_path)
        if (
            current_entry is None
            or current_entry.mode != baseline_entry.mode
            or current_entry.object_type != baseline_entry.object_type
        ):
            violations.append(
                _eval_append_only_violation(relative_path, revision=revision)
            )
            continue
        if current_entry.object_id == baseline_entry.object_id:
            continue
        baseline_data = _read_git_blob(repo_root, baseline_entry.object_id)
        current_data = _read_git_blob(repo_root, current_entry.object_id)
        if not current_data.startswith(baseline_data):
            violations.append(
                _eval_append_only_violation(relative_path, revision=revision)
            )
    return violations


def _eval_append_only_violation(
    relative_path: str,
    *,
    revision: str | None = None,
) -> HygieneViolation:
    return HygieneViolation(
        relative_path=relative_path,
        line_number=None,
        rule="eval-not-append-only",
        message="eval/ 证据文件只允许尾部追加，禁止删除、重命名、改写或改变文件类型",
        revision=revision,
    )


def _index_unmerged_violation(relative_path: str) -> HygieneViolation:
    return HygieneViolation(
        relative_path=relative_path,
        line_number=None,
        rule="index-unmerged",
        message="Git index 包含未解决冲突，无法可靠检查下一次提交候选",
    )


def _index_unreadable_violation(relative_path: str) -> HygieneViolation:
    return HygieneViolation(
        relative_path=relative_path,
        line_number=None,
        rule="index-entry-unreadable",
        message="Git index 条目缺少可验证对象，无法可靠检查下一次提交候选",
    )


def _tree_entries(
    repo_root: Path,
    revision: str,
    relative_path: str,
) -> dict[str, GitTreeEntry]:
    output = _run_git(
        repo_root,
        "ls-tree",
        "-r",
        "-z",
        revision,
        "--",
        relative_path,
    )
    entries: dict[str, GitTreeEntry] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise HygieneCheckError("无法解析 eval/ Git 树条目")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        entries[path] = GitTreeEntry(
            mode=fields[0].decode("ascii"),
            object_type=fields[1].decode("ascii"),
            object_id=fields[2].decode("ascii"),
            relative_path=path,
        )
    return entries


def _first_parent(repo_root: Path, commit: str) -> str | None:
    output = _run_git(repo_root, "rev-list", "--parents", "-n", "1", commit)
    fields = output.decode("ascii").split()
    return fields[1] if len(fields) > 1 else None


def _read_git_blob(repo_root: Path, object_id: str) -> bytes:
    return _run_git(repo_root, "cat-file", "-p", object_id)


def _git_entry_kind(entry: GitTreeEntry) -> str:
    if entry.object_type != "blob":
        return entry.object_type
    return "symlink" if entry.mode == "120000" else "file"


def _index_entry_object_type(entry: GitIndexEntry) -> str:
    if entry.mode in {"100644", "100755", "120000"}:
        return "blob"
    if entry.mode == "160000":
        return "commit"
    if entry.mode == "040000":
        return "tree"
    return "unknown"


def _worktree_entry_kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.is_dir():
        return "tree"
    return "missing"


def _has_append_only_prefix(baseline: bytes, current: bytes) -> bool:
    if b"\0" in baseline or b"\0" in current:
        return current.startswith(baseline)
    normalized_baseline = baseline.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    normalized_current = current.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return normalized_current.startswith(normalized_baseline)


def render_violations(violations: list[HygieneViolation]) -> str:
    lines = ["仓库路径与私密文件卫生检查失败："]
    for violation in sorted(
        set(violations),
        key=lambda item: (
            item.relative_path,
            item.line_number or 0,
            item.revision or "",
            item.rule,
        ),
    ):
        location = violation.relative_path
        if violation.line_number is not None:
            location += f":{violation.line_number}"
        revision = f"（commit {violation.revision}）" if violation.revision else ""
        lines.append(f"- {location}{revision}：{violation.message}")
    return "\n".join(lines)


def _tracked_paths(repo_root: Path) -> list[str]:
    # 同时扫描未忽略的新文件，避免新文档在 git add 前绕过本地验证。
    output = _run_git(
        repo_root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    return sorted(_decode_nul_paths(output))


def _index_snapshot(
    repo_root: Path,
) -> tuple[bytes, list[GitIndexEntry]]:
    stage_output = _run_git(repo_root, "ls-files", "--stage", "-z")
    entries: list[GitIndexEntry] = []
    for record in stage_output.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise HygieneCheckError("无法解析 Git index 条目")
        try:
            stage = int(fields[2].decode("ascii"))
        except ValueError as exc:
            raise HygieneCheckError("Git index stage 不合法") from exc
        entries.append(
            GitIndexEntry(
                mode=fields[0].decode("ascii"),
                object_id=fields[1].decode("ascii"),
                stage=stage,
                relative_path=raw_path.decode(
                    "utf-8",
                    errors="surrogateescape",
                ),
            )
        )
    return stage_output, entries


def _read_git_objects(
    repo_root: Path,
    object_ids: list[str],
) -> dict[str, tuple[str, bytes]]:
    unique_object_ids = list(dict.fromkeys(object_ids))
    if not unique_object_ids:
        return {}
    request = b"".join(
        object_id.encode("ascii") + b"\n"
        for object_id in unique_object_ids
    )
    result = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=repo_root,
        input=request,
        capture_output=True,
        env=_git_read_environment(),
        check=False,
    )
    if result.returncode != 0:
        raise HygieneCheckError("Git 命令失败：cat-file --batch")

    objects: dict[str, tuple[str, bytes]] = {}
    output = result.stdout
    offset = 0
    for expected_object_id in unique_object_ids:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            raise HygieneCheckError("无法解析 Git 对象批量输出")
        header = output[offset:header_end]
        offset = header_end + 1
        fields = header.split()
        if len(fields) != 3:
            raise HygieneCheckError("Git index 对象不存在或格式不合法")
        object_id = fields[0].decode("ascii")
        object_type = fields[1].decode("ascii")
        try:
            size = int(fields[2].decode("ascii"))
        except ValueError as exc:
            raise HygieneCheckError("Git 对象大小不合法") from exc
        if object_id != expected_object_id or size < 0:
            raise HygieneCheckError("Git 对象批量输出与 index 不一致")
        data_end = offset + size
        if data_end >= len(output) or output[data_end:data_end + 1] != b"\n":
            raise HygieneCheckError("Git 对象批量输出长度不合法")
        objects[object_id] = (object_type, output[offset:data_end])
        offset = data_end + 1
    if offset != len(output):
        raise HygieneCheckError("Git 对象批量输出包含额外数据")
    return objects


def _changed_paths(repo_root: Path, commit: str) -> list[str]:
    output = _run_git(
        repo_root,
        "diff-tree",
        "--root",
        "-m",
        "--no-commit-id",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACMRT",
        "-r",
        "-z",
        commit,
    )
    return sorted(set(_decode_nul_paths(output)))


def _decode_nul_paths(output: bytes) -> list[str]:
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in output.split(b"\0")
        if item
    ]


def _read_worktree_file(repo_root: Path, relative_path: str) -> bytes:
    path = repo_root.joinpath(*PurePosixPath(relative_path).parts)
    if path.is_symlink():
        return os.readlink(path).encode("utf-8", errors="surrogateescape")
    if not path.exists() or not path.is_file():
        return b""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise HygieneCheckError(f"无法读取跟踪文件：{relative_path}") from exc


def _read_revision_file(
    repo_root: Path,
    revision: str,
    relative_path: str,
) -> bytes | None:
    tree_output = _run_git(
        repo_root,
        "ls-tree",
        "-z",
        revision,
        "--",
        relative_path,
    )
    if not tree_output:
        return None
    metadata, separator, _ = tree_output.partition(b"\t")
    if not separator:
        raise HygieneCheckError(f"无法解析 Git 树条目：{relative_path}")
    fields = metadata.split()
    if len(fields) != 3 or fields[1] != b"blob":
        return None
    return _run_git(repo_root, "cat-file", "-p", fields[2].decode("ascii"))


def _decode_text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _resolve_base_ref(repo_root: Path, base_ref: str) -> str:
    if (
        not _BASE_REF_PATTERN.fullmatch(base_ref)
        or ".." in base_ref
        or base_ref.startswith("-")
    ):
        raise HygieneCheckError("base ref 格式不安全")
    output = _run_git(repo_root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    return output.decode("ascii").strip()


def _read_head_commit(repo_root: Path) -> str:
    return _run_git(
        repo_root,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    ).decode("ascii").strip()


def _run_git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        env=_git_read_environment(),
        check=False,
    )
    if result.returncode != 0:
        command = " ".join(args[:2])
        raise HygieneCheckError(f"Git 命令失败：{command}")
    return result.stdout


def _git_read_environment() -> dict[str, str]:
    # Git 可通过 GIT_* 重定向仓库、index、对象库或配置。
    # 卫生检查必须读取 --repo 指向的真实提交候选，不能继承调用方的重定向。
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    return environment


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="拒绝本机绝对路径和私密文件进入公开仓库",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="待检查的 Git 仓库，默认当前目录",
    )
    parser.add_argument(
        "--base-ref",
        help="可选；同时检查该基线到 HEAD 的每个提交，防止先提交后删除",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repo_root = args.repo.resolve(strict=True)
        head_before = _read_head_commit(repo_root)
        index_before, _ = _index_snapshot(repo_root)
        violations = scan_index(repo_root, head_commit=head_before)
        violations.extend(scan_worktree(repo_root, head_commit=head_before))
        if args.base_ref:
            violations.extend(
                scan_history(
                    repo_root,
                    args.base_ref,
                    head_commit=head_before,
                )
            )
        index_after, _ = _index_snapshot(repo_root)
        head_after = _read_head_commit(repo_root)
        if head_after != head_before:
            raise HygieneCheckError("Git HEAD 在仓库卫生检查期间发生变化")
        if index_after != index_before:
            raise HygieneCheckError("Git index 在仓库卫生检查期间发生变化")
    except (HygieneCheckError, OSError) as exc:
        print(f"仓库路径与私密文件卫生检查无法完成：{exc}", file=sys.stderr)
        return 2

    if violations:
        print(render_violations(violations), file=sys.stderr)
        return 1

    print("仓库路径与私密文件卫生检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
