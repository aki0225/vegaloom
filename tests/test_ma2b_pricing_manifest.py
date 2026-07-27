from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vega.experimental.ma2b.pricing import (
    MA2BPricingManifestError,
    load_ma2b_pricing_manifest,
    parse_ma2b_pricing_manifest,
)


PRICING_PATH = Path("eval/experiments/multi-agent-coordination/pricing/MA-2B-pricing.json")
EXPECTED_MODELS = {
    "planner_premium": "gpt-5.6-premium-2026-07-25",
    "worker_budget": "gpt-5.6-budget-2026-07-25",
    "reviewer_balanced": "gpt-5.6-balanced-2026-07-25",
}


def test_valid_pricing_manifest_loads_and_binds_expected_models(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo)

    manifest = load_ma2b_pricing_manifest(
        repo_root=repo,
        manifest_path=PRICING_PATH,
        expected_model_ids=EXPECTED_MODELS,
        maximum_observed_at_utc="2026-07-25T10:30:00Z",
    )

    assert manifest.currency == "USD"
    assert manifest.case_count == 12
    assert [entry.role for entry in manifest.model_pricing] == list(EXPECTED_MODELS)


def test_pricing_manifest_rejects_duplicate_json_keys() -> None:
    raw = b'{"schema_version":1,"schema_version":1}'

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        parse_ma2b_pricing_manifest(raw)

    assert exc_info.value.issue_code == "pricing_manifest_duplicate_key"


@pytest.mark.parametrize(
    ("role", "model_id"),
    [
        ("planner_premium", "gpt-5-latest"),
        ("worker_budget", "default"),
        ("reviewer_balanced", "reviewer-auto"),
    ],
)
def test_pricing_manifest_rejects_unpinned_model_aliases(
    tmp_path: Path,
    role: str,
    model_id: str,
) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo, entry_overrides={role: {"model_id": model_id}})

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=PRICING_PATH)

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


@pytest.mark.parametrize(
    "source_label",
    [
        "https://provider.example/pricing",
        "Authorization: Bearer abcdefghijklmnop",
        "local C:/Users/example/pricing",  # repo-path-policy: allow-test-fixture
    ],
)
def test_pricing_manifest_rejects_endpoint_secret_or_local_path_text(
    tmp_path: Path,
    source_label: str,
) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo, overrides={"source_label": source_label})

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=PRICING_PATH)

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


def test_pricing_manifest_rejects_extra_secret_field(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo, overrides={"api_key": "sk-abcdefghijklmnopqrstuvwxyz"})

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=PRICING_PATH)

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


def test_pricing_manifest_rejects_duplicate_model_pricing_roles(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(
        repo,
        entry_overrides={"worker_budget": {"role": "planner_premium"}},
    )

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=PRICING_PATH)

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


def test_pricing_manifest_rejects_out_of_order_model_pricing_roles(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    payload = _pricing_payload()
    payload["model_pricing"] = [
        payload["model_pricing"][1],
        payload["model_pricing"][0],
        payload["model_pricing"][2],
    ]
    _write_manifest_payload(repo, payload)

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=PRICING_PATH)

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


def test_pricing_manifest_rejects_expected_model_binding_mismatch(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo)

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(
            repo_root=repo,
            manifest_path=PRICING_PATH,
            expected_model_ids={
                **EXPECTED_MODELS,
                "worker_budget": "gpt-5.6-other-budget-2026-07-25",
            },
        )

    assert exc_info.value.issue_code == "pricing_manifest_model_binding_mismatch"


def test_pricing_manifest_rejects_non_string_prices(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(
        repo,
        entry_overrides={"planner_premium": {"input_usd_per_1m_tokens": 1.25}},
    )

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=PRICING_PATH)

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


@pytest.mark.parametrize(
    "overrides",
    [
        {"case_count": 2},
        {"effective_start_utc": "2026-07-26T10:00:00Z"},
        {"maximum_total_cost_usd": "0.50"},
    ],
)
def test_pricing_manifest_rejects_invalid_window_case_count_or_budget(
    tmp_path: Path,
    overrides: dict[str, Any],
) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo, overrides=overrides)

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=PRICING_PATH)

    assert exc_info.value.issue_code == "pricing_manifest_schema_invalid"


def test_pricing_manifest_rejects_observation_after_execution_binding_time(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo, overrides={"observed_at_utc": "2026-07-25T10:45:00Z"})

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(
            repo_root=repo,
            manifest_path=PRICING_PATH,
            maximum_observed_at_utc="2026-07-25T10:30:00Z",
        )

    assert exc_info.value.issue_code == "pricing_manifest_observed_after_binding"


def test_pricing_manifest_rejects_escaped_manifest_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo)

    with pytest.raises(MA2BPricingManifestError) as exc_info:
        load_ma2b_pricing_manifest(repo_root=repo, manifest_path=Path("../pricing.json"))

    assert exc_info.value.issue_code == "pricing_manifest_path_invalid"


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    return repo


def _write_pricing_manifest(
    repo: Path,
    *,
    overrides: dict[str, Any] | None = None,
    entry_overrides: dict[str, dict[str, Any]] | None = None,
) -> Path:
    payload = _pricing_payload()
    payload.update(overrides or {})
    for entry in payload["model_pricing"]:
        entry.update((entry_overrides or {}).get(entry["role"], {}))
    return _write_manifest_payload(repo, payload)


def _write_manifest_payload(repo: Path, payload: dict[str, Any]) -> Path:
    path = repo / PRICING_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


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
