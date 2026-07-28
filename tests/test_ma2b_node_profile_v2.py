from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vega.experimental.ma2b.probe_harness import (
    build_probe_worker_prompt,
    load_probe_candidate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE_PROFILE_V2_ROOT = (
    PROJECT_ROOT
    / "eval"
    / "experiments"
    / "multi-agent-coordination"
    / "fixtures"
    / "ma2b"
    / "probe-candidates"
    / "node-profile-v2"
)


def test_node_profile_v2_manifest_hashes_and_prompt_boundaries() -> None:
    manifest = json.loads(
        NODE_PROFILE_V2_ROOT.joinpath("candidate-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for item in manifest["frozen_files"]:
        assert _sha256(NODE_PROFILE_V2_ROOT / item["relative_path"]) == item["sha256"]
    for item in manifest["harness_files"]:
        assert _sha256(PROJECT_ROOT / item["relative_path"]) == item["sha256"]
    assert _sha256(PROJECT_ROOT / manifest["verifier"]["relative_path"]) == (
        manifest["verifier"]["sha256"]
    )

    candidate = load_probe_candidate(NODE_PROFILE_V2_ROOT)
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

    assert candidate.candidate_id == "MA2B-NODE-PROFILE-V2"
    assert "NODE_PROFILE_DETECTION_PACKET_V2" in node_prompt
    assert "PROFILE_ISSUE_CONTEXT_PACKET_V2" not in node_prompt
    assert "PROFILE_ISSUE_CONTEXT_PACKET_V2" in context_prompt
    assert "NODE_PROFILE_DETECTION_PACKET_V2" not in context_prompt
    assert "NODE_PROFILE_DETECTION_PACKET_V2" in sequential_prompt
    assert "PROFILE_ISSUE_CONTEXT_PACKET_V2" in sequential_prompt
    assert "verifier/" not in sequential_prompt.casefold()
    assert "reference patch" not in sequential_prompt.casefold()
    assert manifest["authorization"]["provider_calls_authorized_by_this_candidate"] == 0
    assert manifest["preflight"]["initial_workspace"]["verifier_failed"] == 11
    assert manifest["preflight"]["reference_implementation"]["verifier_passed"] == 11


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
