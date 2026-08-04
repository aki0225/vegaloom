from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from scripts.build_showcase_data import build_payload, render_payload, validate_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "site/data/cases.json"
SOURCE_PATH = REPO_ROOT / "eval/real-world-runs.md"
SUBPROCESS_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def load_cases() -> dict[str, object]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


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


def test_three_cases_keep_their_real_finish_outcomes() -> None:
    payload = load_cases()
    cases = {case["id"]: case for case in payload["cases"]}

    assert set(cases) == {"anyio-1231", "packaging-1232", "crwp-v1-02"}
    assert cases["anyio-1231"]["status"] == "ready_to_commit"
    assert cases["packaging-1232"]["status"] == "ready_to_commit"
    assert cases["crwp-v1-02"]["status"] == "needs_human"
    assert cases["crwp-v1-02"]["changed_files"] == 0
    assert "未启动" in cases["crwp-v1-02"]["verification_summary"]
    assert "未启动" in cases["crwp-v1-02"]["reviewer_summary"]


def test_every_case_has_source_and_limitations() -> None:
    payload = load_cases()
    source_text = SOURCE_PATH.read_text(encoding="utf-8")

    for case in payload["cases"]:
        assert case["source_record"] == "eval/real-world-runs.md"
        assert case["source_heading"] in source_text
        assert case["limitations"]
        assert len(case["timeline"]) >= 6


def test_public_data_contains_no_local_path_or_obvious_secret() -> None:
    text = DATA_PATH.read_text(encoding="utf-8")
    forbidden = (
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]",
        r"\\\\[^\\\s]+\\[^\\\s]+",
        r"(?<![A-Za-z0-9:])/(?:home|Users)/[^/\s\"'`<>]+(?:/|$)",
        r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}\b",
        r"\bsk-[A-Za-z0-9_-]{12,}\b",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )

    for pattern in forbidden:
        assert re.search(pattern, text, re.IGNORECASE) is None


def test_payload_validator_rejects_fabricated_success() -> None:
    payload = build_payload()
    stopped = next(case for case in payload["cases"] if case["id"] == "crwp-v1-02")
    stopped["status"] = "ready_to_commit"

    source_text = SOURCE_PATH.read_text(encoding="utf-8")
    try:
        validate_payload(payload, source_text)
    except ValueError as exc:
        assert "CRWP-V1-02" in str(exc)
    else:
        raise AssertionError("伪造的成功状态必须被拒绝")


def test_payload_has_no_success_rate_field() -> None:
    rendered = render_payload(build_payload())

    assert "success_rate" not in rendered
    assert "成功率：" not in rendered
