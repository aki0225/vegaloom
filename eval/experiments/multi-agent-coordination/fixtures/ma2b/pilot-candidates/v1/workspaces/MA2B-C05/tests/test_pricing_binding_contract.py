from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from vega.ma2b_execution_binding import (
    MA2BExecutionBindingError,
    load_ma2b_execution_binding,
)
from vega.ma2b_pricing_manifest import (
    MA2BPricingManifestError,
    parse_ma2b_pricing_manifest,
)


PRICING_PATH = Path("eval/pricing/MA-2B-pricing.json")
EXPECTED_MODELS = {
    "planner_premium": "gpt-5.6-premium-2026-07-25",
    "worker_budget": "gpt-5.6-budget-2026-07-25",
    "reviewer_balanced": "gpt-5.6-balanced-2026-07-25",
}


def test_pricing_manifest_requires_frozen_twelve_by_three_shape() -> None:
    payload = _pricing_payload()

    manifest = parse_ma2b_pricing_manifest(
        _json_bytes(payload),
        expected_model_ids=EXPECTED_MODELS,
        maximum_observed_at_utc="2026-07-25T10:30:00Z",
    )

    assert manifest.case_count == 12
    assert manifest.treatment_count == 3

    payload["case_count"] = 4
    with pytest.raises(MA2BPricingManifestError) as exc_info:
        parse_ma2b_pricing_manifest(_json_bytes(payload))

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


def test_execution_binding_rejects_pricing_model_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _pricing_payload()
    payload["model_pricing"][1]["model_id"] = "gpt-5.6-other-budget-2026-07-25"
    pricing_sha256 = _write_pricing_manifest(repo, payload)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "pricing_manifest_model_binding_mismatch"


def test_execution_binding_accepts_matching_pricing_contract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    pricing_sha256 = _write_pricing_manifest(repo, _pricing_payload())
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)

    binding = load_ma2b_execution_binding(
        repo_root=repo,
        binding_path=binding_path.relative_to(repo),
    )

    assert binding.pricing_manifest_ref.sha256 == pricing_sha256


def _pricing_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "currency": "USD",
        "source_kind": "published_rate_snapshot",
        "source_label": "Public pricing snapshot without endpoint",
        "observed_at_utc": "2026-07-25T10:00:00Z",
        "effective_start_utc": "2026-07-25T09:00:00Z",
        "effective_end_utc": "2026-07-26T09:00:00Z",
        "case_count": 12,
        "treatment_count": 3,
        "maximum_case_cost_usd": "1.00",
        "maximum_total_cost_usd": "36.00",
        "model_pricing": [
            {
                "role": "planner_premium",
                "model_id": EXPECTED_MODELS["planner_premium"],
                "billing_unit": "usd_per_1m_tokens",
                "input_usd_per_1m_tokens": "1.25",
                "output_usd_per_1m_tokens": "10.00",
                "cached_input_usd_per_1m_tokens": "0.125",
            },
            {
                "role": "worker_budget",
                "model_id": EXPECTED_MODELS["worker_budget"],
                "billing_unit": "usd_per_1m_tokens",
                "input_usd_per_1m_tokens": "0.25",
                "output_usd_per_1m_tokens": "2.00",
                "cached_input_usd_per_1m_tokens": "0.025",
            },
            {
                "role": "reviewer_balanced",
                "model_id": EXPECTED_MODELS["reviewer_balanced"],
                "billing_unit": "usd_per_1m_tokens",
                "input_usd_per_1m_tokens": "0.75",
                "output_usd_per_1m_tokens": "6.00",
                "cached_input_usd_per_1m_tokens": "0.075",
            },
        ],
    }


def _write_pricing_manifest(repo: Path, payload: dict[str, Any]) -> str:
    path = repo / PRICING_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(_json_bytes(payload))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_binding(repo: Path, *, pricing_sha256: str) -> Path:
    payload = {
        "schema_version": 1,
        "provider_family": "Codex Provider",
        "provider_interface": "codex_exec",
        "provider_client_version": "codex-cli-1.2.3",
        "premium_model_id": EXPECTED_MODELS["planner_premium"],
        "budget_model_id": EXPECTED_MODELS["worker_budget"],
        "balanced_reviewer_model_id": EXPECTED_MODELS["reviewer_balanced"],
        "planner_reasoning_configuration": "effort-high",
        "worker_reasoning_configuration": "effort-medium",
        "reviewer_reasoning_configuration": "effort-medium",
        "tool_policy_sha256": "a" * 64,
        "pricing_manifest_ref": {
            "relative_path": PRICING_PATH.as_posix(),
            "sha256": pricing_sha256,
        },
        "availability_observed_at_utc": "2026-07-25T10:30:00Z",
        "execution_window_start_utc": "2026-07-25T11:00:00Z",
        "execution_window_end_utc": "2026-07-25T12:00:00Z",
    }
    path = repo / "eval" / "MA-2B-execution-binding.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# MA-2B execution binding\n\n```yaml\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
