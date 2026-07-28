from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vega.experimental.ma2b.probe_harness import (
    build_probe_worker_prompt,
    load_probe_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE_CANDIDATES_ROOT = (
    PROJECT_ROOT
    / "eval"
    / "experiments"
    / "multi-agent-coordination"
    / "fixtures"
    / "ma2b"
    / "probe-candidates"
)
NODE_PROFILE_V2_ROOT = PROBE_CANDIDATES_ROOT / "node-profile-v2"
NODE_PROFILE_V3_ROOT = PROBE_CANDIDATES_ROOT / "node-profile-v3"


def test_node_profile_v3_freezes_control_plane_and_keeps_v2_inputs() -> None:
    manifest = json.loads(
        NODE_PROFILE_V3_ROOT.joinpath("candidate-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for item in manifest["frozen_files"]:
        assert _sha256(NODE_PROFILE_V3_ROOT / item["relative_path"]) == item["sha256"]
    for item in manifest["harness_files"]:
        assert _sha256(PROJECT_ROOT / item["relative_path"]) == item["sha256"]
    assert _sha256(PROJECT_ROOT / manifest["verifier"]["relative_path"]) == (
        manifest["verifier"]["sha256"]
    )

    for relative_path in (
        "task.md",
        "context/node-profile-detection.md",
        "context/profile-issue-context.md",
    ):
        assert _sha256(NODE_PROFILE_V3_ROOT / relative_path) == _sha256(
            NODE_PROFILE_V2_ROOT / relative_path
        )

    assert manifest["candidate_id"] == "MA2B-NODE-PROFILE-V3"
    assert manifest["previous_candidate_id"] == "MA2B-NODE-PROFILE-V2"
    assert manifest["authorization"] == {
        "maximum_worker_provider_calls": 3,
        "owner_authorization_recorded": True,
        "planner_calls": 0,
        "provider_calls_authorized_by_this_candidate": 3,
        "retry_calls": 0,
        "reviewer_calls": 0,
        "scope": "single_frozen_s_m_probe",
    }
    assert manifest["control_plane"] == {
        "codex_executable_strategy": "windows_native_codex_exe_preferred",
        "forced_taskkill_confirmation_seconds": 30,
        "ignore_rules": True,
        "ignore_user_config": True,
        "jsonl_output": True,
    }
    assert [item["relative_path"] for item in manifest["harness_files"]] == [
        "src/vega/experimental/ma2b/probe.py",
        "src/vega/experimental/ma2b/probe_harness.py",
        "src/vega/execution_control.py",
        "src/vega/runner.py",
    ]
    assert manifest["preflight"]["initial_workspace"]["verifier_failed"] == 11
    assert manifest["preflight"]["reference_implementation"]["verifier_passed"] == 11


def test_node_profile_v3_prompts_keep_slice_context_isolated() -> None:
    candidate = load_probe_candidate(NODE_PROFILE_V3_ROOT)
    node_prompt = build_probe_worker_prompt(
        candidate,
        assigned_slice_ids=("node-profile-detection",),
    )
    context_prompt = build_probe_worker_prompt(
        candidate,
        assigned_slice_ids=("profile-issue-context",),
    )
    sequential_prompt = build_probe_worker_prompt(
        candidate,
        assigned_slice_ids=(
            "node-profile-detection",
            "profile-issue-context",
        ),
    )

    assert candidate.candidate_id == "MA2B-NODE-PROFILE-V3"
    assert "NODE_PROFILE_DETECTION_PACKET_V2" in node_prompt
    assert "PROFILE_ISSUE_CONTEXT_PACKET_V2" not in node_prompt
    assert "PROFILE_ISSUE_CONTEXT_PACKET_V2" in context_prompt
    assert "NODE_PROFILE_DETECTION_PACKET_V2" not in context_prompt
    assert "NODE_PROFILE_DETECTION_PACKET_V2" in sequential_prompt
    assert "PROFILE_ISSUE_CONTEXT_PACKET_V2" in sequential_prompt
    assert "verifier/" not in sequential_prompt.casefold()
    assert "reference patch" not in sequential_prompt.casefold()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
