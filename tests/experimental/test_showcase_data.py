from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import yaml

from scripts.build_showcase_data import (
    _read_release_source,
    build_payload,
    render_payload,
    validate_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = REPO_ROOT / "site/data/cases.json"
INDEX_PATH = REPO_ROOT / "site/index.html"
APP_PATH = REPO_ROOT / "site/app.js"
STYLE_PATH = REPO_ROOT / "site/styles.css"
OG_IMAGE_PATH = REPO_ROOT / "site/assets/og-image.png"
CI_PATH = REPO_ROOT / ".github/workflows/ci.yml"
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


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [
            int(color[index : index + 2], 16) / 255
            for index in (1, 3, 5)
        ]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    high, low = sorted(
        (luminance(foreground), luminance(background)),
        reverse=True,
    )
    return (high + 0.05) / (low + 0.05)


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


def test_ci_fetches_release_tag_before_jobs_execute_showcase_tests() -> None:
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    py311_steps = jobs["tests-py311"]["steps"]
    py311_fetch = next(
        step for step in py311_steps if step.get("name") == "获取展示站发布证据 Tag"
    )
    assert py311_fetch["if"] == "github.event_name != 'pull_request'"
    assert "refs/tags/v0.2.0:refs/tags/v0.2.0" in py311_fetch["run"]

    experimental_job = jobs["tests-experimental"]
    assert experimental_job["if"] == "github.event_name != 'pull_request'"
    py312_steps = experimental_job["steps"]
    py312_fetch = next(
        step for step in py312_steps if step.get("name") == "获取展示站发布证据 Tag"
    )
    assert "refs/tags/v0.2.0:refs/tags/v0.2.0" in py312_fetch["run"]


def test_three_cases_keep_their_real_evidence_outcomes() -> None:
    payload = load_cases()
    assert payload["schema_version"] == 4
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


def test_agent_replay_keeps_structured_release_acceptance_sequence() -> None:
    payload = load_cases()
    replay = payload["agent_replay"]

    assert replay["final_run_id"] == "20260818-231923-agent-resume"
    assert replay["related_run_ids"] == ["20260818-221144-agent-resume"]
    assert replay["terminal_status"] == "ready_to_commit"
    assert replay["release"] == {
        "tag": "v0.2.0",
        "commit": "2fb1bd856df55907a4d3ef1039ea62658b30b2b4",
    }

    scenarios = replay["scenarios"]
    assert [scenario["id"] for scenario in scenarios] == [
        "handoff-and-provider-failure",
        "reviewer-rejection-and-replan",
        "evidence-to-finish",
    ]
    assert [scenario["result"] for scenario in scenarios] == [
        "needs_human",
        "replanned",
        "ready_to_commit",
    ]
    assert [scenario["run_ids"] for scenario in scenarios] == [
        ["20260818-221144-agent-resume"],
        ["20260818-231923-agent-resume"],
        ["20260818-231923-agent-resume"],
    ]
    assert all(len(scenario["events"]) == 6 for scenario in scenarios)
    assert {
        event["status_card"]["work_item"]
        for scenario in scenarios
        for event in scenario["events"]
    } == {"单项任务"}
    assert scenarios[0]["events"][4]["status_card"]["workspace"] == "not_disclosed"
    assert scenarios[0]["events"][4]["status_card"]["checkpoint"] == "not_disclosed"
    assert scenarios[0]["events"][5]["status_card"]["workspace"] == "not_disclosed"
    assert all(
        event["status_card"]["checkpoint"] == "not_disclosed"
        for event in scenarios[1]["events"]
    )
    assert all(
        event["status_card"]["risk"] == "not_disclosed"
        for event in scenarios[1]["events"][:3]
    )
    assert scenarios[0]["events"][-1]["status_card"]["finish"] == "needs_human"
    assert scenarios[1]["events"][1]["status_card"]["reviewer"] == "needs_human"
    assert scenarios[-1]["events"][-1]["status_card"]["finish"] == "ready_to_commit"
    assert (
        scenarios[-1]["events"][-1]["status_card"]["checkpoint"]
        == "checkpoint-006"
    )
    finish_text = json.dumps(scenarios[-1], ensure_ascii=False)
    assert "361 passed" in finish_text
    assert "7 passed" in finish_text
    assert "180 passed" in finish_text

    proof = replay["proof"]
    assert proof["format"] == "structured-event-replay-v1"
    assert proof["event_count"] == 18
    assert proof["duration_ms"] == sum(
        scenario["duration_ms"] for scenario in scenarios
    )
    assert len(proof["sha256"]) == 64
    assert "发布验收证据编排的低频状态回放" in proof["disclosure"]
    assert "不是原始 Trace" in proof["disclosure"]
    assert "不是浏览器实时录制" in proof["disclosure"]

    source_paths = {
        source["path"]
        for source in replay["source_links"]
    }
    assert "docs/RELEASE-NOTES-0.2.0.md" in source_paths
    assert "docs/RELEASE-SUMMARY-0.2.0.md" in source_paths
    assert "eval/real-world-runs.md" in source_paths
    for relative_path in source_paths:
        assert (REPO_ROOT / relative_path).is_file()


def test_agent_replay_validates_the_same_tag_content_linked_by_the_page() -> None:
    current_record = (REPO_ROOT / "eval/real-world-runs.md").read_text(
        encoding="utf-8"
    )
    release_record = _read_release_source(
        "eval/real-world-runs.md",
        allow_agent_source=True,
    )
    post_release_anchor = "main@2fb1bd856df55907a4d3ef1039ea62658b30b2b4"

    assert post_release_anchor in current_record
    assert post_release_anchor not in release_record
    assert "20260818-231923-agent-resume" in release_record


def test_agent_replay_rejects_unapproved_public_source_link() -> None:
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


def test_agent_replay_rejects_tampered_proof_hash() -> None:
    payload = build_payload()
    payload["agent_replay"]["proof"]["sha256"] = "0" * 64

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "sha256" in str(exc)
    else:
        raise AssertionError("篡改 proof hash 必须失败")


def test_agent_replay_rejects_hidden_related_run() -> None:
    payload = build_payload()
    payload["agent_replay"]["related_run_ids"] = []

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "关联 run" in str(exc)
    else:
        raise AssertionError("Provider 失败的关联 run 必须公开")


def test_agent_replay_rejects_non_monotonic_event_time() -> None:
    payload = build_payload()
    payload["agent_replay"]["scenarios"][0]["events"][1]["at_ms"] = 0

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "严格递增" in str(exc)
    else:
        raise AssertionError("篡改事件时间必须失败")


def test_agent_replay_rejects_unapproved_event_source_ref() -> None:
    payload = build_payload()
    payload["agent_replay"]["scenarios"][0]["events"][0]["source_refs"] = [
        "unapproved"
    ]

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "source_refs" in str(exc)
    else:
        raise AssertionError("回放事件不得引用未经核准的来源")


def test_agent_replay_rejects_status_card_with_missing_field() -> None:
    payload = build_payload()
    del payload["agent_replay"]["scenarios"][0]["events"][0]["status_card"]["finish"]

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "status_card" in str(exc)
    else:
        raise AssertionError("status_card 缺少字段时必须失败")


def test_agent_replay_rejects_rewritten_terminal_status() -> None:
    payload = build_payload()
    payload["agent_replay"]["terminal_status"] = "needs_human"

    try:
        validate_payload(payload)
    except ValueError as exc:
        assert "终态" in str(exc)
    else:
        raise AssertionError("篡改 Agent 回放终态必须失败")


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


def test_showcase_page_uses_the_v4_agent_player_contract() -> None:
    index = INDEX_PATH.read_text(encoding="utf-8")
    app = APP_PATH.read_text(encoding="utf-8")

    assert 'data-agent-player' in index
    assert 'data-player-play' in index
    assert 'role="progressbar"' in index
    assert 'data-status-field="checkpoint"' in index
    assert 'data-status-field="verification"' in index
    assert 'data-status-field="reviewer"' in index
    assert 'data-status-field="finish"' in index
    assert 'data-player-field="run_ids"' in index
    assert 'data-evidence-field="final_run_id"' in index
    assert 'data-evidence-field="related_run_ids"' in index
    assert "SCENARIOS JSON SHA-256" in index
    assert "payload.schema_version !== 4" in app
    assert "agent_replay.steps" not in app
    assert "fallbackPayload" not in app
    assert "showStaticFallback" in app
    assert 'const EVIDENCE_REF = "v0.2.0";' in app
    assert (
        'const EVIDENCE_COMMIT = "2fb1bd856df55907a4d3ef1039ea62658b30b2b4";'
        in app
    )
    assert 'function makeEvidenceLink(source, ref = EVIDENCE_REF)' in app
    assert "replay.release?.tag !== EVIDENCE_REF" in app
    assert "replay.release?.commit !== EVIDENCE_COMMIT" in app
    assert "replay.final_run_id !== FINAL_RUN_ID" in app
    assert "isReplayScenario(scenario, sourceKinds)" in app
    assert "isStatusCard(event.status_card)" in app
    assert "payload.cases.every(isReviewCase)" in app
    assert "showStaticFallback(error);" in app
    assert "setInteractiveControlsDisabled(false);" in app
    assert "公开 Run 引用" in index
    assert "<span>WORKER</span>" in index
    assert re.search(r"data-player-play[^>]*disabled", index)
    assert re.search(r"data-player-rewind[^>]*disabled", index)
    assert 'setText(evidenceNodes, "sha256", replay.proof.sha256);' in app
    assert len(app.encode("utf-8")) < 50_000


def test_static_fallback_does_not_freeze_generated_proof_values() -> None:
    index = INDEX_PATH.read_text(encoding="utf-8")
    app = APP_PATH.read_text(encoding="utf-8")

    assert 'data-evidence-field="event_count">加载数据后显示' in index
    assert 'data-evidence-field="sha256">加载数据后显示' in index
    assert re.search(r"\b[0-9a-f]{64}\b", index) is None
    assert re.search(r"\b[0-9a-f]{64}\b", app) is None
    assert "/blob/v0.2.0/docs/RELEASE-NOTES-0.2.0.md" in index


def test_light_theme_small_labels_keep_aa_contrast() -> None:
    styles = STYLE_PATH.read_text(encoding="utf-8")
    faint = re.search(r"--faint:\s*(#[0-9a-fA-F]{6})", styles)
    paper = re.search(r"--paper:\s*(#[0-9a-fA-F]{6})", styles)

    assert faint is not None
    assert paper is not None
    assert contrast_ratio(faint.group(1), paper.group(1)) >= 4.5


def test_showcase_copy_does_not_restore_retired_slogans() -> None:
    public_copy = "\n".join(
        (
            INDEX_PATH.read_text(encoding="utf-8"),
            APP_PATH.read_text(encoding="utf-8"),
            DATA_PATH.read_text(encoding="utf-8"),
            (REPO_ROOT / "site/assets/og-image.svg").read_text(encoding="utf-8"),
        )
    )
    retired_phrases = (
        "任务做到哪了，不能只靠聊天记录",
        "宿主 Agent 干活",
        "麻雀虽小",
        "happy path",
        "人工只补证据，不扩大产品范围",
        "停止 partial WIP 后只经 Git 恢复",
        "Worker 中断后从记录恢复",
        "人工确认没有未记录的外部副作用",
        "Provider 429、Reviewer request_changes",
    )

    for phrase in retired_phrases:
        assert phrase not in public_copy


def test_open_graph_image_keeps_the_expected_dimensions() -> None:
    data = OG_IMAGE_PATH.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (1200, 630)
