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


def scan_worktree(repo_root: Path) -> list[HygieneViolation]:
    violations: list[HygieneViolation] = []
    for relative_path in _tracked_paths(repo_root):
        filename_violation = find_sensitive_filename_violation(relative_path)
        if filename_violation is not None:
            violations.append(filename_violation)

        data = _read_worktree_file(repo_root, relative_path)
        text = _decode_text(data)
        if text is not None:
            violations.extend(find_text_violations(relative_path, text))
    return violations


def scan_history(repo_root: Path, base_ref: str) -> list[HygieneViolation]:
    base_sha = _resolve_base_ref(repo_root, base_ref)
    commit_output = _run_git(repo_root, "rev-list", "--reverse", f"{base_sha}..HEAD")
    commits = [line for line in commit_output.decode("ascii").splitlines() if line]
    violations: list[HygieneViolation] = []

    for commit in commits:
        revision = commit[:12]
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


def _run_git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        command = " ".join(args[:2])
        raise HygieneCheckError(f"Git 命令失败：{command}")
    return result.stdout


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
        violations = scan_worktree(repo_root)
        if args.base_ref:
            violations.extend(scan_history(repo_root, args.base_ref))
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
