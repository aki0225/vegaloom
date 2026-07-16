from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..redaction import (
    assert_not_sensitive_path,
    is_sensitive_path,
    redact_text,
    sensitive_search_exclude_globs,
)


_RG_MATCH_PATTERN = re.compile(r"^(?P<path>.*?):(?P<line>\d+):")


def read_file(repo_path: Path, relative_path: str) -> str:
    assert_not_sensitive_path(relative_path)
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"path escapes repo: {relative_path}")

    repo = repo_path.resolve()
    target = (repo / candidate).resolve()
    if repo not in target.parents and target != repo:
        raise ValueError(f"path escapes repo: {relative_path}")
    assert_not_sensitive_path(target)
    return target.read_text(encoding="utf-8", errors="replace")


def search(repo_path: Path, query: str, max_results: int = 20) -> list[str]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("file.search query 不能为空。")
    if "\x00" in query or "\n" in query or "\r" in query:
        raise ValueError("file.search query 不能包含控制换行或 NUL 字符。")

    command = ["rg", "-n", "--fixed-strings"]
    for pattern in sensitive_search_exclude_globs():
        command.extend(["--glob", pattern])
    command.extend(["--", query, str(repo_path.resolve())])

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("file.search 执行超时。") from None
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        detail = redact_text(result.stderr.strip()) or f"exit code {result.returncode}"
        raise RuntimeError(f"file.search 执行失败：{detail}")

    matches: list[str] = []
    for line in result.stdout.splitlines():
        match = _RG_MATCH_PATTERN.match(line)
        if match is None or is_sensitive_path(match.group("path")):
            continue
        matches.append(redact_text(line))
        if len(matches) >= max_results:
            break
    return matches
