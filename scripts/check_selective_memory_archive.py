from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ARCHIVE_FILES = frozenset(
    {
        "docs/experiments/SELECTIVE-MEMORY-REMINDER-PLAN.md",
        "docs/experiments/selective-memory/EVAL-REPORT.md",
        "docs/experiments/selective-memory/PHASE-0-BASELINE.md",
        "docs/experiments/selective-memory/PHASE-1-REPORT.md",
        "docs/experiments/selective-memory/PHASE-2-DECISION.md",
        "docs/experiments/selective-memory/PUBLIC-ARCHIVE.md",
        "docs/experiments/selective-memory/README.md",
        "docs/experiments/selective-memory/SOURCE-EVAL-REPORT.md",
        "docs/experiments/selective-memory/SOURCE-PHASE-2-DECISION.md",
        "docs/experiments/selective-memory/metrics.json",
        "docs/experiments/selective-memory/source-metrics.json",
        "eval/selective_memory/__init__.py",
        "eval/selective_memory/candidates.py",
        "eval/selective_memory/cases/active-conflict.json",
        "eval/selective_memory/cases/approval-revoked.json",
        "eval/selective_memory/cases/conditional-tool-retry.json",
        "eval/selective_memory/cases/disproved-hypotheses.json",
        "eval/selective_memory/cases/pending-approval.json",
        "eval/selective_memory/cases/prompt-injection-stale-evidence.json",
        "eval/selective_memory/cases/requirement-change.json",
        "eval/selective_memory/cases/scope-creep-dependency.json",
        "eval/selective_memory/cases/session-resume.json",
        "eval/selective_memory/cases/unverified-inference.json",
        "eval/selective_memory/evaluator.py",
        "eval/selective_memory/event_store.py",
        "eval/selective_memory/generate_phase2_dataset.py",
        "eval/selective_memory/golden/active-conflict.json",
        "eval/selective_memory/golden/approval-revoked.json",
        "eval/selective_memory/golden/conditional-tool-retry.json",
        "eval/selective_memory/golden/disproved-hypotheses.json",
        "eval/selective_memory/golden/pending-approval.json",
        "eval/selective_memory/golden/prompt-injection-stale-evidence.json",
        "eval/selective_memory/golden/requirement-change.json",
        "eval/selective_memory/golden/scope-creep-dependency.json",
        "eval/selective_memory/golden/session-resume.json",
        "eval/selective_memory/golden/unverified-inference.json",
        "eval/selective_memory/models.py",
        "eval/selective_memory/policy.py",
        "eval/selective_memory/projector.py",
        "tests/experimental/selective_memory/conftest.py",
        "tests/experimental/selective_memory/test_candidates.py",
        "tests/experimental/selective_memory/test_evaluator.py",
        "tests/experimental/selective_memory/test_event_store.py",
        "tests/experimental/selective_memory/test_models.py",
        "tests/experimental/selective_memory/test_policy.py",
        "tests/experimental/selective_memory/test_projector.py",
        "tests/experimental/selective_memory/test_security.py",
    }
)

INTEGRATION_FILES = frozenset(
    {
        ".github/workflows/selective-memory-archive.yml",
        "scripts/check_selective_memory_archive.py",
        "scripts/run_selective_memory_phase2.py",
        "tests/conftest.py",
    }
)

ALLOWED_CHANGED_FILES = SOURCE_ARCHIVE_FILES | INTEGRATION_FILES
ALLOWED_HEX_IDENTIFIERS = frozenset(
    {
        "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "ece7cb06caefa5fff74198d8649806c4678c61a1",
    }
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

HEX_ID_PATTERN = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])",
    re.IGNORECASE,
)
HOSTNAME_PATTERN = re.compile(r"\b(?:DESKTOP|LAPTOP)-[A-Za-z0-9-]+\b")
PRIVATE_ROOT_PATTERN = re.compile(
    r"\b[A-Za-z]:[\\/](?:workspace(?:-new)?|tools|environment)(?:[\\/]|$)",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)
TOKEN_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b"
)
BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.IGNORECASE)
URL_CREDENTIAL_PATTERN = re.compile(r"https?://[^/\s:@]+:[^/\s@]+@")
FORBIDDEN_LITERALS = {
    "private_remote": "vegaloom" + "-lab",
}
CODE_FORBIDDEN_LITERALS = {
    "runtime_import": "vega.experimental" + ".selective_memory",
    "long_term_memory_write": "memory/" + "ledger.jsonl",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int


class ArchiveCheckError(RuntimeError):
    """公开归档检查无法可靠完成。"""


def _git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ArchiveCheckError(f"Git 命令失败：git {' '.join(args[:2])}")
    return result.stdout


def _nul_paths(output: bytes) -> set[str]:
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in output.split(b"\0")
        if item
    }


def _changed_paths(base_ref: str) -> set[str]:
    base = _git("rev-parse", "--verify", f"{base_ref}^{{commit}}").decode().strip()
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode != 0:
        raise ArchiveCheckError(f"HEAD 不是从 {base_ref} 建立")

    paths = _nul_paths(_git("diff", "--name-only", "-z", f"{base}...HEAD"))
    paths |= _nul_paths(_git("diff", "--name-only", "-z"))
    paths |= _nul_paths(_git("diff", "--cached", "--name-only", "-z"))
    paths |= _nul_paths(
        _git("ls-files", "--others", "--exclude-standard", "-z")
    )
    return paths


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in HEX_ID_PATTERN.finditer(text):
        if match.group(1).lower() not in ALLOWED_HEX_IDENTIFIERS:
            findings.append(
                Finding("private_or_unknown_git_identity", path, _line_number(text, match.start()))
            )
    for rule, pattern in (
        ("machine_hostname", HOSTNAME_PATTERN),
        ("private_absolute_root", PRIVATE_ROOT_PATTERN),
        ("credential_token", TOKEN_PATTERN),
        ("authorization_bearer", BEARER_PATTERN),
        ("credential_in_url", URL_CREDENTIAL_PATTERN),
    ):
        for match in pattern.finditer(text):
            findings.append(Finding(rule, path, _line_number(text, match.start())))
    for match in EMAIL_PATTERN.finditer(text):
        if match.group(1).lower() not in ALLOWED_EMAIL_DOMAINS:
            findings.append(
                Finding("personal_email", path, _line_number(text, match.start()))
            )
    for rule, literal in FORBIDDEN_LITERALS.items():
        offset = text.find(literal)
        while offset >= 0:
            findings.append(Finding(rule, path, _line_number(text, offset)))
            offset = text.find(literal, offset + len(literal))
    if path.startswith("eval/selective_memory/") or path == (
        "scripts/run_selective_memory_phase2.py"
    ):
        for rule, literal in CODE_FORBIDDEN_LITERALS.items():
            offset = text.find(literal)
            while offset >= 0:
                findings.append(Finding(rule, path, _line_number(text, offset)))
                offset = text.find(literal, offset + len(literal))
    return findings


def _scan_files(paths: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for relative in sorted(paths):
        path = PROJECT_ROOT.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file():
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        findings.extend(
            _scan_text(relative, content.decode("utf-8", errors="replace"))
        )
    return findings


def _scan_commit_metadata(base_ref: str) -> list[Finding]:
    commits = _git("rev-list", "--reverse", f"{base_ref}..HEAD").decode().splitlines()
    findings: list[Finding] = []
    for commit in commits:
        email = _git("show", "-s", "--format=%ae", commit).decode().strip()
        domain = email.rsplit("@", maxsplit=1)[-1].lower() if "@" in email else ""
        if domain not in ALLOWED_EMAIL_DOMAINS:
            findings.append(Finding("commit_author_email", "<commit-metadata>", 1))
        message = _git("show", "-s", "--format=%B", commit).decode(
            "utf-8",
            errors="replace",
        )
        findings.extend(_scan_text("<commit-message>", message))
    return findings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查 Selective Memory 公开归档的范围和隐私边界",
    )
    parser.add_argument("--base-ref", default="origin/main")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        changed = _changed_paths(args.base_ref)
        unexpected = sorted(changed - ALLOWED_CHANGED_FILES)
        missing = sorted(path for path in SOURCE_ARCHIVE_FILES if not (PROJECT_ROOT / path).is_file())
        findings = _scan_files(changed & ALLOWED_CHANGED_FILES)
        findings.extend(_scan_commit_metadata(args.base_ref))
    except (ArchiveCheckError, OSError) as exc:
        print(f"Selective Memory 公开归档检查无法完成：{exc}", file=sys.stderr)
        return 2

    if unexpected:
        print(f"公开归档包含白名单外文件：{unexpected}", file=sys.stderr)
        return 1
    if missing:
        print(f"公开归档缺少预期文件：{missing}", file=sys.stderr)
        return 1
    if findings:
        for finding in sorted(findings, key=lambda item: (item.path, item.line, item.rule)):
            print(
                f"{finding.path}:{finding.line}: 公开归档检查失败 [{finding.rule}]",
                file=sys.stderr,
            )
        return 1

    print(
        "Selective Memory 公开归档检查通过："
        f"changed={len(changed)}, archive_files={len(SOURCE_ARCHIVE_FILES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
