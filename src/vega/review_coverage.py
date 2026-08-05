from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .review_contract import ReviewVerdict, normalize_review_path


def build_review_file_coverage(
    changed_files: Iterable[str],
    reviewed_files: Iterable[str],
) -> dict[str, Any]:
    expected = _normalized_unique(changed_files)
    reviewed = _normalized_unique(reviewed_files)
    expected_set = set(expected)
    reviewed_set = set(reviewed)
    missing = [path for path in expected if path not in reviewed_set]
    unexpected = [path for path in reviewed if path not in expected_set]
    return {
        "complete": not missing and not unexpected,
        "expected_count": len(expected),
        "reviewed_count": len(reviewed_set & expected_set),
        "expected_files": expected,
        "reviewed_files": reviewed,
        "missing_files": missing,
        "unexpected_files": unexpected,
    }


def review_file_coverage_issues(coverage: dict[str, Any]) -> list[str]:
    return [
        *[
            f"reviewed_files_missing:{path}"
            for path in coverage.get("missing_files") or []
        ],
        *[
            f"reviewed_files_unknown:{path}"
            for path in coverage.get("unexpected_files") or []
        ],
    ]


def review_file_coverage_issues_for_verdict(
    changed_files: Iterable[str],
    verdict: ReviewVerdict,
) -> list[str]:
    coverage = build_review_file_coverage(changed_files, verdict.reviewed_files)
    return review_file_coverage_issues(coverage)


def run_review_file_coverage_eval(
    run_dir: Path,
    artifacts: list[str],
    changed_files: list[str],
    prompt_text: str,
) -> list[str]:
    verdict_path = run_dir / "review-verdict.json"
    if "review-verdict.json" not in artifacts or not verdict_path.exists():
        required_markers = ['"reviewed_files"', *changed_files]
        missing_markers = [
            marker for marker in required_markers if marker not in prompt_text
        ]
        if missing_markers:
            return [
                "FAIL: review prompt 缺少完整变更文件覆盖约束："
                + "、".join(missing_markers)
            ]
        return ["PASS: review prompt 已绑定完整变更文件覆盖"]
    try:
        verdict = ReviewVerdict.model_validate_json(
            verdict_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError):
        return ["FAIL: reviewer file coverage verdict 格式不合法"]
    issues = review_file_coverage_issues_for_verdict(changed_files, verdict)
    if issues:
        return [
            f"FAIL: reviewer file coverage：{issue}"
            for issue in issues
        ]
    return ["PASS: reviewer 已声明覆盖全部变更文件"]


def _normalized_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_review_path(str(value))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
