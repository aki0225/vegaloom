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


PRICING_PATH = Path("eval/experiments/multi-agent-coordination/pricing/MA-2B-pricing.json")


def test_valid_markdown_execution_binding_loads_and_binds_pricing_manifest(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)

    binding = load_ma2b_execution_binding(
        repo_root=repo,
        binding_path=binding_path.relative_to(repo),
    )

    assert binding.provider_interface == "codex_exec"
    assert binding.premium_model_id == "gpt-5.6-premium-2026-07-25"
    assert binding.budget_model_id == "gpt-5.6-budget-2026-07-25"
    assert binding.pricing_manifest_ref.sha256 == pricing_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("premium_model_id", "gpt-5-latest"),
        ("budget_model_id", "default"),
        ("balanced_reviewer_model_id", "reviewer-auto"),
    ],
)
def test_execution_binding_rejects_unpinned_model_aliases(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(
        repo,
        pricing_sha256=pricing_sha256,
        overrides={field: value},
    )

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "execution_binding_schema_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_family", "https://api.provider.example/v1"),
        ("provider_client_version", "authorization: Bearer abcdefghijklmnop"),
        ("planner_reasoning_configuration", "config from C:/Users/example"),  # repo-path-policy: allow-test-fixture
        ("worker_reasoning_configuration", "../secret/config"),
    ],
)
def test_execution_binding_rejects_endpoint_secret_or_local_path_text(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(
        repo,
        pricing_sha256=pricing_sha256,
        overrides={field: value},
    )

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "execution_binding_schema_invalid"


def test_execution_binding_rejects_extra_secret_field(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(
        repo,
        pricing_sha256=pricing_sha256,
        overrides={"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
    )

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "execution_binding_schema_invalid"


def test_execution_binding_rejects_pricing_manifest_hash_mismatch(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256="f" * 64)

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "pricing_manifest_hash_mismatch"


def test_execution_binding_rejects_pricing_manifest_model_mismatch(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(
        repo,
        entry_overrides={
            "worker_budget": {"model_id": "gpt-5.6-other-budget-2026-07-25"},
        },
    )
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "pricing_manifest_model_binding_mismatch"


def test_execution_binding_rejects_pricing_observed_after_binding_observation(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(
        repo,
        overrides={"observed_at_utc": "2026-07-25T10:45:00Z"},
    )
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "pricing_manifest_observed_after_binding"


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_window_start_utc": "2026-07-25T12:00:00Z"},
        {"availability_observed_at_utc": "2026-07-25T12:30:00Z"},
        {"execution_window_end_utc": "2026-07-25T12:00:00+08:00"},
    ],
)
def test_execution_binding_requires_ordered_utc_window(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(
        repo,
        pricing_sha256=pricing_sha256,
        overrides=overrides,
    )

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "execution_binding_schema_invalid"


def test_execution_binding_rejects_escaped_pricing_reference(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(
        repo,
        pricing_sha256=pricing_sha256,
        pricing_path="../pricing.json",
    )

    with pytest.raises(MA2BExecutionBindingError) as exc_info:
        load_ma2b_execution_binding(
            repo_root=repo,
            binding_path=binding_path.relative_to(repo),
        )

    assert exc_info.value.issue_code == "execution_binding_schema_invalid"


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    return repo


def _write_pricing_manifest(
    repo: Path,
    *,
    overrides: dict[str, Any] | None = None,
    entry_overrides: dict[str, dict[str, Any]] | None = None,
) -> str:
    payload = {
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
                "model_id": "gpt-5.6-premium-2026-07-25",
                "billing_unit": "usd_per_1m_tokens",
                "input_usd_per_1m_tokens": "1.25",
                "output_usd_per_1m_tokens": "10.00",
                "cached_input_usd_per_1m_tokens": "0.125",
            },
            {
                "role": "worker_budget",
                "model_id": "gpt-5.6-budget-2026-07-25",
                "billing_unit": "usd_per_1m_tokens",
                "input_usd_per_1m_tokens": "0.25",
                "output_usd_per_1m_tokens": "2.00",
                "cached_input_usd_per_1m_tokens": "0.025",
            },
            {
                "role": "reviewer_balanced",
                "model_id": "gpt-5.6-balanced-2026-07-25",
                "billing_unit": "usd_per_1m_tokens",
                "input_usd_per_1m_tokens": "0.75",
                "output_usd_per_1m_tokens": "6.00",
                "cached_input_usd_per_1m_tokens": "0.075",
            },
        ],
    }
    payload.update(overrides or {})
    for entry in payload["model_pricing"]:
        entry.update((entry_overrides or {}).get(entry["role"], {}))
    path = repo / PRICING_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_binding(
    repo: Path,
    *,
    pricing_sha256: str,
    pricing_path: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "schema_version": 1,
        "provider_family": "Codex Provider",
        "provider_interface": "codex_exec",
        "provider_client_version": "codex-cli-1.2.3",
        "premium_model_id": "gpt-5.6-premium-2026-07-25",
        "budget_model_id": "gpt-5.6-budget-2026-07-25",
        "balanced_reviewer_model_id": "gpt-5.6-balanced-2026-07-25",
        "planner_reasoning_configuration": "effort-high",
        "worker_reasoning_configuration": "effort-medium",
        "reviewer_reasoning_configuration": "effort-medium",
        "tool_policy_sha256": "a" * 64,
        "pricing_manifest_ref": {
            "relative_path": pricing_path or PRICING_PATH.as_posix(),
            "sha256": pricing_sha256,
        },
        "availability_observed_at_utc": "2026-07-25T10:30:00Z",
        "execution_window_start_utc": "2026-07-25T11:00:00Z",
        "execution_window_end_utc": "2026-07-25T12:00:00Z",
    }
    payload.update(overrides or {})
    binding_path = (
        repo
        / "eval"
        / "experiments"
        / "multi-agent-coordination"
        / "MA-2B-execution-binding.md"
    )
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(
        "# MA-2B execution binding\n\n```yaml\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```\n",
        encoding="utf-8",
        newline="\n",
    )
    return binding_path
