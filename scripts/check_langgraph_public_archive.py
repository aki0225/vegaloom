from __future__ import annotations

import argparse
import io
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

HEX_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])([0-9a-f]{7,40})(?![A-Za-z0-9_-])"
)
HOSTNAME_PATTERN = re.compile(r"\b(?:DESKTOP|LAPTOP)-[A-Za-z0-9-]+\b")
PRIVATE_ROOT_PATTERN = re.compile(
    r"\bE:[\\/](?:workspace(?:-new)?|tools|environment)(?:[\\/]|$)",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)

ALLOWED_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.invalid",
        "example.net",
        "example.org",
        "example.test",
        "users.noreply.github.com",
        "vega.invalid",
    }
)

# 这些值是公开 Action pin、synthetic fixture、tree identity 或测试常量，不是源实验提交。
ALLOWED_NON_COMMIT_IDENTIFIERS = frozenset(
    {
        "0123456789abcdef",
        "026c5e6c70530b3b4698d5534e264f264aca1d3e",
        "222011918143a84bbb8013c81dfb546231df392c",
        "66eb9eeaf93a8726241cf91598731c65a09e7f74",
        "7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b",
        "8c68d4c87dc54d38861f5114e920c3de2efa5876",
        "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "a5b249e710d1253bee4c099faf91e45f9ebfbddd",
        "b81ba34c3e9ca3dad29dab80ce2d148bf72a7576",
        "c34d6e81fd8e405e6d4178bf24b364918811ef17",
        "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "ed5a84cdcd9bd2427fba24b7b012bd71011efab8",
        "f367577d877e9499cbd342b02effac65eadad88e",
        "f4f0b3c1c1d0f10127afd047a8fe8417b4cb7fa7",
        "f515ef6dce107cd09d96895061dcad98b4b32f9b",
    }
)

# 公开主线和发布提交不一定属于 v0.1.0 归档分支的可达历史。
ALLOWED_PUBLIC_COMMIT_IDENTIFIERS = frozenset(
    {
        "176ac381",
        "da1ac290addd0042f8782476cdb5ece4e53f2aa8",
    }
)

FORBIDDEN_LITERALS = {
    "private_remote": "vegaloom" + "-lab",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    ref: str


def _git(*args: str, text: bool = True) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=text,
    )


def _require_git(*args: str) -> str:
    result = _git(*args)
    if result.returncode != 0:
        raise SystemExit(f"Git 命令失败：git {' '.join(args)}")
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def _is_public_commit(identifier: str, cache: dict[str, bool]) -> bool:
    if identifier in ALLOWED_PUBLIC_COMMIT_IDENTIFIERS:
        return True
    if identifier in cache:
        return cache[identifier]
    result = _git("rev-parse", "--verify", "--quiet", f"{identifier}^{{commit}}")
    cache[identifier] = result.returncode == 0
    return cache[identifier]


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _scan_text(
    *,
    path: str,
    text: str,
    ref: str,
    commit_cache: dict[str, bool],
) -> list[Finding]:
    findings: list[Finding] = []

    for match in HEX_IDENTIFIER_PATTERN.finditer(text):
        identifier = match.group(1)
        if not any(character in "abcdef" for character in identifier):
            continue
        if identifier in ALLOWED_NON_COMMIT_IDENTIFIERS:
            continue
        if _is_public_commit(identifier, commit_cache):
            continue
        findings.append(
            Finding(
                rule="unknown_git_identity",
                path=path,
                line=_line_number(text, match.start()),
                ref=ref,
            )
        )

    for match in HOSTNAME_PATTERN.finditer(text):
        findings.append(
            Finding(
                rule="machine_hostname",
                path=path,
                line=_line_number(text, match.start()),
                ref=ref,
            )
        )

    for match in PRIVATE_ROOT_PATTERN.finditer(text):
        findings.append(
            Finding(
                rule="private_absolute_root",
                path=path,
                line=_line_number(text, match.start()),
                ref=ref,
            )
        )

    for rule, literal in FORBIDDEN_LITERALS.items():
        offset = text.find(literal)
        while offset >= 0:
            findings.append(
                Finding(
                    rule=rule,
                    path=path,
                    line=_line_number(text, offset),
                    ref=ref,
                )
            )
            offset = text.find(literal, offset + len(literal))

    for match in EMAIL_PATTERN.finditer(text):
        if match.group(1).lower() in ALLOWED_EMAIL_DOMAINS:
            continue
        findings.append(
            Finding(
                rule="personal_email",
                path=path,
                line=_line_number(text, match.start()),
                ref=ref,
            )
        )

    return findings


def _decode_text(content: bytes) -> str | None:
    if b"\0" in content:
        return None
    return content.decode("utf-8", errors="replace")


def _scan_archive(ref: str, commit_cache: dict[str, bool]) -> tuple[list[Finding], int]:
    result = _git("archive", "--format=tar", ref, text=False)
    if result.returncode != 0:
        raise SystemExit(f"无法读取 Git tree：{ref}")
    assert isinstance(result.stdout, bytes)

    findings: list[Finding] = []
    scanned = 0
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            text = _decode_text(extracted.read())
            if text is None:
                continue
            scanned += 1
            findings.extend(
                _scan_text(
                    path=member.name,
                    text=text,
                    ref=ref[:12],
                    commit_cache=commit_cache,
                )
            )
    return findings, scanned


def _scan_working_tree(commit_cache: dict[str, bool]) -> tuple[list[Finding], int]:
    candidates = _require_git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    findings: list[Finding] = []
    scanned = 0
    for relative in candidates.split("\0"):
        if not relative:
            continue
        path = PROJECT_ROOT / relative
        try:
            content = path.read_bytes()
        except OSError:
            continue
        text = _decode_text(content)
        if text is None:
            continue
        scanned += 1
        findings.extend(
            _scan_text(
                path=relative,
                text=text,
                ref="working-tree",
                commit_cache=commit_cache,
            )
        )
    return findings, scanned


def _validate_history(base_ref: str) -> tuple[list[str], list[Finding]]:
    base = _require_git("rev-parse", "--verify", f"{base_ref}^{{commit}}")
    ancestor = _git("merge-base", "--is-ancestor", base, "HEAD")
    if ancestor.returncode != 0:
        raise SystemExit(f"归档分支不是从 {base_ref} 建立")

    commits = _require_git("rev-list", "--reverse", f"{base}..HEAD").splitlines()
    if len(commits) != 1:
        raise SystemExit(
            f"公开归档必须是 {base_ref} 之上的单一提交，实际为 {len(commits)} 个"
        )

    findings: list[Finding] = []
    for commit in commits:
        email = _require_git("show", "-s", "--format=%ae", commit)
        domain = email.rsplit("@", maxsplit=1)[-1].lower() if "@" in email else ""
        if domain not in ALLOWED_EMAIL_DOMAINS:
            findings.append(
                Finding(
                    rule="commit_author_email",
                    path="<commit-metadata>",
                    line=1,
                    ref=commit[:12],
                )
            )
    return commits, findings


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 LangGraph 公开归档身份脱敏边界")
    parser.add_argument("--history-base", default="v0.1.0")
    parser.add_argument(
        "--working-tree-only",
        action="store_true",
        help="扫描当前已跟踪和未忽略的未跟踪文件；用于提交前验证",
    )
    args = parser.parse_args()

    commit_cache: dict[str, bool] = {}
    if args.working_tree_only:
        findings, scanned = _scan_working_tree(commit_cache)
        commits: list[str] = []
    else:
        commits, metadata_findings = _validate_history(args.history_base)
        findings = list(metadata_findings)
        scanned = 0
        for commit in commits:
            commit_findings, commit_scanned = _scan_archive(commit, commit_cache)
            findings.extend(commit_findings)
            scanned += commit_scanned

    if findings:
        for finding in sorted(
            findings,
            key=lambda item: (item.ref, item.path, item.line, item.rule),
        ):
            print(
                f"{finding.ref}:{finding.path}:{finding.line}: "
                f"公开归档审计失败 [{finding.rule}]"
            )
        return 1

    mode = "working-tree" if args.working_tree_only else f"{len(commits)} commit"
    print(f"LangGraph 公开归档审计通过：mode={mode}, files={scanned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
