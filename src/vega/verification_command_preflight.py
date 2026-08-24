from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .redaction import redact_text


_COREPACK_PNPM_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<binary>corepack(?:\.cmd|\.exe)?)"
    r"\s+pnpm(?P<version>@[^\s;&|]+)?(?=\s|$)",
    re.IGNORECASE,
)
_PNPM_DIRECTORY_PATTERN = re.compile(
    r"(?:^|\s)(?:--dir|-C)(?:\s+|=)"
    r"(?P<directory>\"[^\"]+\"|'[^']+'|[^\s;&|]+)",
    re.IGNORECASE,
)
_SHELL_CONTROL_PATTERN = re.compile(r"(?:&&|\|\||[;&|\r\n])")
_PINNED_PNPM_PATTERN = re.compile(
    r"^pnpm@(?P<version>[^+\s]+)(?:\+[^\s]+)?$",
    re.IGNORECASE,
)
_PACKAGE_JSON_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class VerificationCommandPreflightIssue:
    code: str
    command_index: int
    message: str
    evidence: str
    suggestion: str


def inspect_verification_commands(
    repo: Path,
    commands: list[str] | tuple[str, ...],
) -> list[VerificationCommandPreflightIssue]:
    """检查依赖仓库清单文件才能判断的验证命令歧义。

    这里只处理能够确定解析的 Corepack + pnpm `--dir/-C` 形式。Vega 不改写
    已批准命令；发现版本歧义时直接要求计划作者提交精确命令。
    """

    root = repo.resolve(strict=True)
    issues: list[VerificationCommandPreflightIssue] = []
    for command_index, command in enumerate(commands, start=1):
        for match in _COREPACK_PNPM_PATTERN.finditer(command):
            directory = _pnpm_directory(command[match.end() :])
            if directory is None:
                continue
            manifest = _package_manifest(root, directory)
            if manifest is None:
                continue
            manifest_path, package_manager = manifest
            pinned = _PINNED_PNPM_PATTERN.fullmatch(package_manager)
            if pinned is None:
                continue
            pinned_version = pinned.group("version")
            explicit = match.group("version")
            explicit_version = explicit[1:].split("+", 1)[0] if explicit else None
            suggestion = (
                command[: match.start()]
                + f"{match.group('binary')} pnpm@{pinned_version}"
                + command[match.end() :]
            )
            relative_manifest = manifest_path.relative_to(root).as_posix()
            if explicit_version is None:
                issues.append(
                    VerificationCommandPreflightIssue(
                        code="corepack_package_manager_version_ambiguous",
                        command_index=command_index,
                        message=(
                            "Corepack 会在 pnpm 处理 --dir/-C 前选择版本；"
                            f"{relative_manifest} 已固定 pnpm@{pinned_version}，"
                            "当前命令没有显式版本。"
                        ),
                        evidence=relative_manifest,
                        suggestion=suggestion,
                    )
                )
            elif explicit_version != pinned_version:
                issues.append(
                    VerificationCommandPreflightIssue(
                        code="corepack_package_manager_version_mismatch",
                        command_index=command_index,
                        message=(
                            f"命令固定 pnpm@{explicit_version}，但 "
                            f"{relative_manifest} 要求 pnpm@{pinned_version}。"
                        ),
                        evidence=relative_manifest,
                        suggestion=suggestion,
                    )
                )
    return issues


def require_verification_commands_preflight(
    repo: Path,
    commands: list[str] | tuple[str, ...],
) -> None:
    issues = inspect_verification_commands(repo, commands)
    if not issues:
        return
    issue = issues[0]
    raise ValueError(
        "验证命令预检失败："
        f"verification[{issue.command_index}] {issue.message}"
        f" 建议改为：`{redact_text(issue.suggestion)}`"
    )


def _pnpm_directory(command_tail: str) -> str | None:
    # 不跨 shell 控制符寻找 --dir，避免把后续另一条 pnpm 命令的参数错绑到当前命令。
    command_segment = _SHELL_CONTROL_PATTERN.split(command_tail, maxsplit=1)[0]
    match = _PNPM_DIRECTORY_PATTERN.search(command_segment)
    if match is None:
        return None
    value = match.group("directory").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip() or None


def _package_manifest(root: Path, directory: str) -> tuple[Path, str] | None:
    normalized = directory.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized or "."
    candidate = PurePosixPath(normalized)
    if (
        candidate.is_absolute()
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".."} for part in candidate.parts)
    ):
        return None
    directory_path = root if normalized == "." else root / candidate
    manifest_path = (directory_path / "package.json").resolve(strict=False)
    if not manifest_path.is_relative_to(root) or not manifest_path.is_file():
        return None
    try:
        if manifest_path.stat().st_size > _PACKAGE_JSON_MAX_BYTES:
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    package_manager = payload.get("packageManager")
    if not isinstance(package_manager, str):
        return None
    return manifest_path, package_manager.strip()
