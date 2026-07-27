from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from vega.experimental.ma2b.execution_binding import load_ma2b_execution_binding
from vega.experimental.ma2b.readiness import (
    MA2B_EXECUTION_AUTHORIZATION_PATH,
    MA2B_EXECUTION_BINDING_PATH,
    MA2B_PILOT_CASE_IDS,
    MA2BReadinessError,
    check_ma2b_pilot_readiness,
    compute_ma2b_case_set_sha256,
    load_ma2b_execution_authorization,
)
from vega.experimental.ma2b.task_pack import MA2BTaskPackError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PILOT_CANDIDATE_ROOT = Path(
    "eval/experiments/multi-agent-coordination/fixtures/ma2b/"
    "pilot-candidates/v1"
)
PILOT_TASK_PACK_ROOT = PILOT_CANDIDATE_ROOT / "task-pack"
PILOT_GROUND_TRUTH_ROOT = PILOT_CANDIDATE_ROOT / "ground-truth"
PRICING_PATH = Path("eval/experiments/multi-agent-coordination/pricing/MA-2B-pricing.json")


def test_readiness_blocks_when_pilot_artifacts_are_missing_or_partial(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    result = check_ma2b_pilot_readiness(repo_root=repo)

    assert result.status == "blocked"
    assert result.case_set_sha256 is None
    assert result.execution_binding_loaded is False
    assert result.authorization_loaded is False
    assert "pilot_case:MA2B-C01:task_pack_root_invalid" in result.issue_codes
    assert "execution_binding_path_invalid" in result.issue_codes
    assert "execution_authorization_path_invalid" in result.issue_codes
    _assert_complete_candidates_do_not_relax_twelve_case_readiness()


def test_readiness_is_ready_only_when_cases_binding_pricing_and_authorization_match(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    packages = _fake_packages()
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)
    _write_authorization(
        repo,
        execution_binding_sha256=_sha256_file(binding_path),
        pricing_manifest_sha256=pricing_sha256,
        case_set_sha256=compute_ma2b_case_set_sha256(packages),
    )

    result = check_ma2b_pilot_readiness(
        repo_root=repo,
        case_loader=_fake_case_loader(packages),
    )

    assert result.status == "ready"
    assert result.issue_codes == []
    assert result.loaded_case_ids == list(MA2B_PILOT_CASE_IDS)
    assert result.execution_binding_loaded is True
    assert result.authorization_loaded is True


def test_readiness_blocks_when_any_pilot_case_cannot_load(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    packages = _fake_packages()
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)
    _write_authorization(
        repo,
        execution_binding_sha256=_sha256_file(binding_path),
        pricing_manifest_sha256=pricing_sha256,
        case_set_sha256=compute_ma2b_case_set_sha256(packages),
    )

    result = check_ma2b_pilot_readiness(
        repo_root=repo,
        case_loader=_fake_case_loader(packages, missing_case_id="MA2B-C04"),
    )

    assert result.status == "blocked"
    assert "pilot_case:MA2B-C04:case_directory_invalid" in result.issue_codes
    assert "execution_authorization_case_set_unverifiable" in result.issue_codes
    assert result.case_set_sha256 is None


def test_readiness_blocks_fake_fixture_role_even_when_loader_returns_package(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    packages = _fake_packages(package_role="fake_driver_fixture")
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)
    _write_authorization(
        repo,
        execution_binding_sha256=_sha256_file(binding_path),
        pricing_manifest_sha256=pricing_sha256,
        case_set_sha256=compute_ma2b_case_set_sha256(packages),
    )

    result = check_ma2b_pilot_readiness(
        repo_root=repo,
        case_loader=_fake_case_loader(packages),
    )

    assert result.status == "blocked"
    assert "pilot_case:MA2B-C01:package_role_mismatch" in result.issue_codes


def test_readiness_blocks_authorization_case_set_hash_mismatch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    packages = _fake_packages()
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)
    _write_authorization(
        repo,
        execution_binding_sha256=_sha256_file(binding_path),
        pricing_manifest_sha256=pricing_sha256,
        case_set_sha256="f" * 64,
    )

    result = check_ma2b_pilot_readiness(
        repo_root=repo,
        case_loader=_fake_case_loader(packages),
    )

    assert result.status == "blocked"
    assert "execution_authorization_case_set_hash_mismatch" in result.issue_codes


def test_readiness_keeps_loaded_binding_when_binding_hash_becomes_unreadable(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    packages = _fake_packages()
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)
    execution_binding_sha256 = _sha256_file(binding_path)
    binding = load_ma2b_execution_binding(
        repo_root=repo,
        binding_path=MA2B_EXECUTION_BINDING_PATH,
    )
    _write_authorization(
        repo,
        execution_binding_sha256=execution_binding_sha256,
        pricing_manifest_sha256=pricing_sha256,
        case_set_sha256=compute_ma2b_case_set_sha256(packages),
    )
    binding_path.unlink()

    result = check_ma2b_pilot_readiness(
        repo_root=repo,
        case_loader=_fake_case_loader(packages),
        execution_binding_loader=lambda **_: binding,
    )

    assert result.status == "blocked"
    assert result.execution_binding_loaded is True
    assert result.authorization_loaded is True
    assert result.issue_codes == [
        "execution_binding:MA2BReadinessError",
        "execution_authorization_binding_unverifiable",
    ]


@pytest.mark.parametrize(
    ("authorized_at_utc", "expected_issue"),
    [
        (
            "2026-07-25T10:20:00Z",
            "execution_authorization_before_binding_observation",
        ),
        (
            "2026-07-25T11:10:00Z",
            "execution_authorization_after_execution_window_start",
        ),
    ],
)
def test_readiness_blocks_authorization_outside_binding_window(
    tmp_path: Path,
    authorized_at_utc: str,
    expected_issue: str,
) -> None:
    repo = _repo(tmp_path)
    packages = _fake_packages()
    pricing_sha256 = _write_pricing_manifest(repo)
    binding_path = _write_binding(repo, pricing_sha256=pricing_sha256)
    _write_authorization(
        repo,
        execution_binding_sha256=_sha256_file(binding_path),
        pricing_manifest_sha256=pricing_sha256,
        case_set_sha256=compute_ma2b_case_set_sha256(packages),
        overrides={"authorized_at_utc": authorized_at_utc},
    )

    result = check_ma2b_pilot_readiness(
        repo_root=repo,
        case_loader=_fake_case_loader(packages),
    )

    assert result.status == "blocked"
    assert expected_issue in result.issue_codes


@pytest.mark.parametrize(
    "authorized_by",
    [
        "https://review.example/approval",
        "Authorization: Bearer abcdefghijklmnop",
        "local C:/Users/example/review",  # repo-path-policy: allow-test-fixture
    ],
)
def test_execution_authorization_rejects_endpoint_secret_or_local_path_text(
    tmp_path: Path,
    authorized_by: str,
) -> None:
    repo = _repo(tmp_path)
    _write_authorization(
        repo,
        execution_binding_sha256="a" * 64,
        pricing_manifest_sha256="b" * 64,
        case_set_sha256="c" * 64,
        overrides={"authorized_by": authorized_by},
    )

    with pytest.raises(MA2BReadinessError) as exc_info:
        load_ma2b_execution_authorization(
            repo_root=repo,
            authorization_path=MA2B_EXECUTION_AUTHORIZATION_PATH,
        )

    assert exc_info.value.issue_code == "execution_authorization_schema_invalid"


def test_execution_authorization_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / MA2B_EXECUTION_AUTHORIZATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schema_version":1,"schema_version":1}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(MA2BReadinessError) as exc_info:
        load_ma2b_execution_authorization(
            repo_root=repo,
            authorization_path=MA2B_EXECUTION_AUTHORIZATION_PATH,
        )

    assert exc_info.value.issue_code == "execution_authorization_duplicate_key"


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    return repo


def _assert_complete_candidates_do_not_relax_twelve_case_readiness() -> None:
    assert MA2B_PILOT_CASE_IDS == tuple(
        f"MA2B-C{index:02d}" for index in range(1, 13)
    )

    result = check_ma2b_pilot_readiness(
        repo_root=PROJECT_ROOT,
        task_pack_root=PILOT_TASK_PACK_ROOT,
        ground_truth_root=PILOT_GROUND_TRUTH_ROOT,
    )

    assert result.status == "blocked"
    assert result.loaded_case_ids == list(MA2B_PILOT_CASE_IDS)
    assert (
        result.case_set_sha256
        == "33b2caa335b417b47ee45bb5de7051aef20682bbf938eddf5d2e4ad5d3d4f137"
    )
    assert result.issue_codes == [
        "execution_binding_path_invalid",
        "execution_authorization_path_invalid",
    ]
    assert result.execution_binding_loaded is False
    assert result.authorization_loaded is False


def _fake_case_loader(
    packages: list[_FakePackage],
    *,
    missing_case_id: str | None = None,
):
    by_case_id = {package.manifest.case_id: package for package in packages}

    def load_case(**kwargs: object) -> _FakePackage:
        case_id = str(kwargs["case_id"])
        if case_id == missing_case_id:
            raise MA2BTaskPackError("case_directory_invalid")
        return by_case_id[case_id]

    return load_case


def _fake_packages(*, package_role: str = "pilot_case") -> list["_FakePackage"]:
    packages = []
    for case_id in MA2B_PILOT_CASE_IDS:
        task_pack_sha256 = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
        packages.append(
            _FakePackage(
                manifest=_FakeManifest(
                    case_id=case_id,
                    package_role=package_role,
                    case_class=_case_class(case_id),
                ),
                ground_truth=_FakeGroundTruth(
                    case_id=case_id,
                    expected_outcome=_expected_outcome(case_id),
                    quality_scored=case_id <= "MA2B-C08",
                    target_workspace_change=(
                        "allowed" if case_id <= "MA2B-C08" else "forbidden"
                    ),
                    task_pack_sha256=task_pack_sha256,
                ),
                verification=_FakeVerification(
                    commands=[f"python -m pytest tests/{case_id}.py"]
                ),
                task_pack_sha256=task_pack_sha256,
            )
        )
    return packages


def _case_class(case_id: str) -> str:
    case_number = int(case_id[-2:])
    if case_number <= 8:
        return "code_change"
    if case_number <= 10:
        return "human_required"
    if case_number == 11:
        return "stale_evidence"
    return "invalid_verifier"


def _expected_outcome(case_id: str) -> str:
    case_class = _case_class(case_id)
    if case_class == "code_change":
        return "accepted_change"
    if case_class == "human_required":
        return "safe_deferral"
    return "safe_block"


@dataclass(frozen=True)
class _FakeManifest:
    case_id: str
    package_role: str
    case_class: str


@dataclass(frozen=True)
class _FakeGroundTruth:
    case_id: str
    expected_outcome: str
    quality_scored: bool
    target_workspace_change: str
    task_pack_sha256: str


@dataclass(frozen=True)
class _FakeVerification:
    commands: list[str]


@dataclass(frozen=True)
class _FakePackage:
    manifest: _FakeManifest
    ground_truth: _FakeGroundTruth
    verification: _FakeVerification
    task_pack_sha256: str


def _write_pricing_manifest(repo: Path) -> str:
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
    path = repo / PRICING_PATH
    _write_json(path, payload)
    return _sha256_file(path)


def _write_binding(repo: Path, *, pricing_sha256: str) -> Path:
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
            "relative_path": PRICING_PATH.as_posix(),
            "sha256": pricing_sha256,
        },
        "availability_observed_at_utc": "2026-07-25T10:30:00Z",
        "execution_window_start_utc": "2026-07-25T11:00:00Z",
        "execution_window_end_utc": "2026-07-25T12:00:00Z",
    }
    path = repo / MA2B_EXECUTION_BINDING_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# MA-2B execution binding\n\n```yaml\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _write_authorization(
    repo: Path,
    *,
    execution_binding_sha256: str,
    pricing_manifest_sha256: str,
    case_set_sha256: str,
    overrides: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "schema_version": 1,
        "scope": "ma2b_pilot_execution",
        "decision": "authorized",
        "authorized_by": "owner-or-independent-review",
        "authorized_at_utc": "2026-07-25T10:50:00Z",
        "execution_binding_sha256": execution_binding_sha256,
        "pricing_manifest_sha256": pricing_manifest_sha256,
        "case_set_sha256": case_set_sha256,
        "notes": "fake readiness test only",
    }
    payload.update(overrides or {})
    path = repo / MA2B_EXECUTION_AUTHORIZATION_PATH
    _write_json(path, payload)
    return path


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
