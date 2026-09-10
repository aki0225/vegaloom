from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .git_read import run_git_bytes
from .review_contract import normalize_review_path


def project_final_change_impacts(
    review: dict[str, Any], integration: dict[str, Any] | None, repo: Path, candidate_sha: str,
) -> dict[str, Any]:
    """累计审查覆盖最终 Candidate 时优先采用它，否则只展示当前受信子流程意见。"""

    reviews = (
        [batch["verdict"] for batch in integration.get("batches", [])
         if isinstance(batch, dict) and isinstance(batch.get("verdict"), dict)]
        if integration is not None else [review]
    )
    return project_change_impacts(
        [impact for item in reviews for impact in item.get("change_impacts", [])
         if isinstance(impact, dict)], repo, candidate_sha,
    )


def project_change_impacts(
    impacts: list[dict[str, Any]], repo: Path | None, candidate_sha: str | None,
) -> dict[str, Any]:
    """只验证冻结 Git 对象里的引用，功能解释仍明确属于模型意见。"""

    bound = repo is not None and bool(
        candidate_sha and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", candidate_sha)
    )
    counts: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    for impact in impacts:
        summary = impact.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        locations: list[dict[str, Any]] = []
        rejected = 0
        for location in impact.get("locations") or []:
            if not isinstance(location, dict):
                rejected += 1
                continue
            raw_path, line = location.get("file"), location.get("line")
            path = normalize_review_path(raw_path) if isinstance(raw_path, str) else ""
            safe_path = (
                bool(path) and not path.startswith("/") and ":" not in path
                and all(part not in {"", ".", ".."} for part in path.split("/"))
                and not any(ord(char) < 32 or ord(char) == 127 for char in path)
            )
            if not bound or not safe_path or type(line) is not int or line <= 0:
                rejected += 1
                continue
            if path not in counts:
                assert repo is not None and candidate_sha is not None
                counts[path] = _candidate_line_count(repo, candidate_sha, path)
            if line > counts[path]:
                rejected += 1
                continue
            reference = {"file": path, "line": line}
            if reference not in locations:
                locations.append(reference)
        items.append({
            "summary": summary.strip(), "locations": locations,
            "unverified_location_count": rejected,
        })
    return {
        "source": "reviewer_model", "candidate_sha": candidate_sha if bound else None,
        "items": items,
    }


def _candidate_line_count(repo: Path, candidate_sha: str, path: str) -> int:
    try:
        entries = run_git_bytes(repo, ["git", "ls-tree", "-z", candidate_sha, "--", path])
        # symlink 和 submodule 不是 Candidate 中可按源码行号引用的常规文件。
        expected = path.encode("utf-8")
        regular = any(
            name == expected and metadata.split()[:2] in (
                [b"100644", b"blob"], [b"100755", b"blob"],
            )
            for entry in entries.split(b"\0") if entry
            for metadata, _, name in [entry.partition(b"\t")]
        )
        if not regular:
            return 0
        data = run_git_bytes(repo, ["git", "cat-file", "blob", f"{candidate_sha}:{path}"])
        if b"\0" in data:
            return 0
        data.decode("utf-8")
        return data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        # 报告引用不可确认时只隐藏引用，绝不凭浮动工作区补造，也不重裁决门禁。
        return 0


def render_change_impacts(projection: dict[str, Any]) -> list[str]:
    lines = ["", "## 功能变化与影响（模型意见）", "",
             "- 以下说明来自同次 Reviewer，不等于机器验证或安全保证。"]
    items = projection.get("items") or []
    if not items:
        return [*lines, "- Reviewer 未提供功能影响说明；不根据文件名推测功能。"]
    for item in items:
        lines.append(f"- {item['summary']}")
        if item["locations"]:
            refs = "、".join(f"`{loc['file']}:{loc['line']}`" for loc in item["locations"])
            lines.append(f"  - Candidate 已核验位置：{refs}")
        else:
            lines.append("  - 未提供可核验的 Candidate 位置。")
        if item["unverified_location_count"]:
            lines.append("  - 无法核验的模型引用已隐藏；这不改变门禁结果。")
    return lines
