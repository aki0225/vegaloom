from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from scripts.build_showcase_data import build_payload, render_payload, validate_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "site/data/cases.json"
SUBPROCESS_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def load_cases() -> dict[str, object]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def iter_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def test_committed_showcase_data_matches_generator() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_showcase_data.py", "--check"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=SUBPROCESS_ENV,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "一致" in result.stdout


def test_generator_is_deterministic(tmp_path: Path) -> None:
    output_path = tmp_path / "cases.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_showcase_data.py",
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=SUBPROCESS_ENV,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output_path.read_text(encoding="utf-8") == DATA_PATH.read_text(encoding="utf-8")


def test_three_cases_keep_their_real_evidence_outcomes() -> None:
    payload = load_cases()
    assert payload["schema_version"] == 3
    cases = {case["id"]: case for case in payload["cases"]}

    assert set(cases) == {
        "pycodestyle-1187-rejection",
        "pycodestyle-1187-success",
        "click-2939-success",
    }
    rejected = cases["pycodestyle-1187-rejection"]
    assert rejected["status"] == "request_changes"
    assert rejected["review"]["verdict"] == "request_changes"
    assert rejected["review"]["severity"] == "major"
    assert "768 passed" in rejected["verification"]["headline"]
    assert "needs_human" in rejected["gates"]["finish"]

    assert cases["pycodestyle-1187-success"]["status"] == "ready_to_commit"
    assert cases["click-2939-success"]["status"] == "ready_to_commit"


def test_agent_replay_keeps_release_acceptance_sequence() -> None:
    payload = load_cases()
    replay = payload["agent_replay"]

    assert replay["run_id"] == "20260818-231923-agent-resume"
    assert replay["terminal_status"] == "ready_to_commit"
    assert [step["id"] for step in replay["steps"]] == [
        "plan-approved",
        "partial-wip",
        "git-handoff",
        "provider-failure",
        "reviewer-rejection",
        "plan-revision",
        "trusted-finish",
    ]
    assert replay["steps"][3]["status"] == "fail_closed"
    assert replay["steps"][4]["status"] == "request_changes"
    assert replay["steps"][-1]["status"] == "ready_to_commit"
    assert "361 passed" in replay["steps"][-1]["observation"]
    assert "180 passed" in replay["steps"][-1]["observation"]

    source_paths = {
        source["path"]
        for source in replay["source_links"]
    }
    assert "docs/RELEASE-NOTES-0.2.0.md" in source_paths
    assert "docs/RELEASE-SUMMARY-0.2.0.md" in source_paths
    assert "eval/real-world-runs.md" in source_paths
    for relative_path in source_paths:
        assert (REPO_ROOT / relative_path).is_file()


def test_agent_replay_rejects_unapproved_public_source() -> None:
    payload = build_payload()
    payload["agent_replay"]["source_links"][1] = {
        "kind": "run",
        "label": "其他运行记录",
        "path": "eval/cases.jsonl",
    }

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "人工核准清单" in str(exc)
    else:
        raise AssertionError("Agent 回放不得链接未经核准的公开来源")


def test_agent_replay_rejects_malformed_step() -> None:
    payload = build_payload()
    del payload["agent_replay"]["steps"][0]["decision"]

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "缺少字段" in str(exc)
    else:
        raise AssertionError("Agent 回放节点缺少决定字段时必须失败")


def test_review_case_cannot_reuse_agent_document_allowlist() -> None:
    payload = build_payload()
    payload["cases"][0]["source_links"][0]["path"] = (
        "docs/RELEASE-NOTES-0.2.0.md"
    )

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "越过允许目录" in str(exc)
    else:
        raise AssertionError("Reviewer 案例只能引用专用脱敏证据目录")


def test_every_case_links_real_evidence_and_diff_excerpt() -> None:
    payload = load_cases()

    for case in payload["cases"]:
        assert case["limitations"]
        assert len(case["source_links"]) >= 4

        sources = {item["kind"]: item["path"] for item in case["source_links"]}
        diff_path = REPO_ROOT / sources["diff"]
        assert diff_path.is_file()
        assert case["diff"]["excerpt"] in diff_path.read_text(encoding="utf-8")

        for relative_path in sources.values():
            source_path = (REPO_ROOT / relative_path).resolve()
            assert source_path.is_file()
            assert source_path.is_relative_to(
                (REPO_ROOT / "examples/evidence").resolve()
            )


def test_public_data_contains_no_local_path_or_obvious_secret() -> None:
    payload = load_cases()
    forbidden = (
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]",
        r"\\\\[^\\\s]+\\[^\\\s]+",
        r"(?<![A-Za-z0-9:])/(?:home|Users)/[^/\s\"'`<>]+(?:/|$)",
        r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}\b",
        r"\bsk-[A-Za-z0-9_-]{12,}\b",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )

    for text in iter_strings(payload):
        for pattern in forbidden:
            assert re.search(pattern, text, re.IGNORECASE) is None


def test_payload_validator_rejects_fabricated_success() -> None:
    payload = build_payload()
    rejected = next(
        case
        for case in payload["cases"]
        if case["id"] == "pycodestyle-1187-rejection"
    )
    rejected["status"] = "ready_to_commit"

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "pycodestyle-1187-rejection" in str(exc)
    else:
        raise AssertionError("伪造的成功状态必须被拒绝")


def test_payload_has_no_success_rate_field() -> None:
    rendered = render_payload(build_payload())

    assert "success_rate" not in rendered
    assert "成功率：" not in rendered
