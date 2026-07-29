from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts.daily_value_codex_preflight import (
    REQUIRED_DISABLED_FEATURES,
    build_exec_args,
    build_preflight,
    inspect_codex_profile,
    validate_exec_args,
)
from scripts.daily_value_eval import (
    DEFAULT_CASES,
    PROJECT_ROOT,
    build_summary,
    load_jsonl,
    main,
    validate_case_ledger,
    validate_results,
)


BASELINE = "a" * 40


def test_codex_preflight_preserves_provider_route_without_exposing_endpoint(
    tmp_path: Path,
) -> None:
    codex_home = _write_codex_profile(tmp_path)

    payload = build_preflight(
        codex_home,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        sandbox="workspace-write",
        profile_name="vega-daily-value-v1",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["status"] == "ready"
    assert payload["profile"]["model_provider"] == "test-provider"
    assert payload["profile"]["wire_api"] == "responses"
    assert payload["profile"]["execution_profile"] == "vega-daily-value-v1"
    assert payload["provider_request_performed"] is False
    assert "--ignore-user-config" not in payload["exec_args"]
    assert "https://provider.example.test/v1" not in rendered
    assert set(payload["disabled_features"]) == set(REQUIRED_DISABLED_FEATURES)


def test_codex_preflight_rejects_missing_provider_route(tmp_path: Path) -> None:
    codex_home = _write_codex_profile(tmp_path)
    codex_home.joinpath("config.toml").write_text(
        'model = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )

    try:
        inspect_codex_profile(codex_home, "gpt-5.6-sol", "vega-daily-value-v1")
    except ValueError as exc:
        assert "model_provider" in str(exc)
    else:
        raise AssertionError("缺失 Provider 路由时必须 fail-closed")


def test_codex_preflight_rejects_profile_drift(tmp_path: Path) -> None:
    codex_home = _write_codex_profile(tmp_path)
    profile = inspect_codex_profile(
        codex_home,
        "gpt-5.6-sol",
        "vega-daily-value-v1",
    )

    try:
        build_preflight(
            codex_home,
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            sandbox="read-only",
            profile_name="vega-daily-value-v1",
            expected_profile_fingerprint="0" * 64,
        )
    except ValueError as exc:
        assert "fingerprint" in str(exc)
    else:
        raise AssertionError("Provider profile 漂移时必须停止")

    review = build_preflight(
        codex_home,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        sandbox="read-only",
        profile_name="vega-daily-value-v1",
        expected_profile_fingerprint=profile["profile_fingerprint"],
    )
    assert review["sandbox"] == "read-only"


def test_codex_exec_args_reject_overbroad_user_config_isolation() -> None:
    args = build_exec_args(
        "gpt-5.6-sol",
        "medium",
        "workspace-write",
        "vega-daily-value-v1",
    )
    args.append("--ignore-user-config")

    try:
        validate_exec_args(args)
    except ValueError as exc:
        assert "--ignore-user-config" in str(exc)
    else:
        raise AssertionError("过宽配置隔离必须被拒绝")


def test_repository_daily_value_cases_keep_honest_qualification_status() -> None:
    cases = validate_case_ledger(load_jsonl(DEFAULT_CASES))
    results_path = (
        PROJECT_ROOT
        / "eval"
        / "experiments"
        / "daily-value-validation"
        / "results.jsonl"
    )
    results = validate_results(cases, load_jsonl(results_path))
    summary = build_summary(cases, results)

    assert len(cases) == 7
    assert len(
        PROJECT_ROOT.joinpath("scripts", "daily_value_eval.py")
        .read_text(encoding="utf-8")
        .splitlines()
    ) <= 300
    assert {case["task_type"] for case in cases.values()} == {"bug", "feature"}
    assert cases["DV-B01"]["status"] == "retired"
    assert cases["DV-B01"]["baseline_commit"] == (
        "814a0d1259444a21ed318e64edaf6a530c2aeeb8"
    )
    assert cases["DV-B01"]["qualification"]["oracle_verifier"] == "unavailable"
    assert cases["DV-B04"]["status"] == "runnable"
    assert cases["DV-B04"]["baseline_commit"] == (
        "8c95c73bd5ef89eac638f85f1904a104ba4b1a32"
    )
    assert cases["DV-B04"]["qualification"] == {
        "baseline_verifier": "passed",
        "oracle_verifier": "passed",
        "windows": "passed",
        "dependencies": "passed",
    }
    assert cases["DV-B02"]["status"] == "runnable"
    assert cases["DV-B02"]["baseline_commit"] == (
        "ee0f19b696c60064c58cdc08b3265aef56d49ff8"
    )
    assert cases["DV-B02"]["oracle_ref"] == (
        "e21793e90a25c7ea47a9c0369150067cc8322de0"
    )
    assert cases["DV-B02"]["qualification"]["provider_profile"] == "passed"
    assert all(
        case["status"] == "candidate_not_frozen" and case["baseline_commit"] is None
        for case_id, case in cases.items()
        if case_id not in {"DV-B01", "DV-B02", "DV-B04"}
    )
    assert sum(
        case["task_type"] == "bug" and case["status"] != "retired"
        for case in cases.values()
    ) == 3
    assert [cases[case_id]["treatment_order"][0] for case_id in sorted(cases)] == [
        "native",
        "vega",
        "native",
        "native",
        "vega",
        "native",
        "vega",
    ]
    assert len(results) == 3
    by_treatment = {
        (result["case_id"], result["treatment"]): result for result in results
    }
    assert by_treatment["DV-B04", "native"]["run_status"] == (
        "infrastructure_failure"
    )
    assert by_treatment["DV-B02", "vega"]["run_status"] == "timed_out"
    assert by_treatment["DV-B02", "vega"]["verification_status"] == "failed"
    assert by_treatment["DV-B02", "native"]["run_status"] == "timed_out"
    assert by_treatment["DV-B02", "native"]["verification_status"] == "passed"
    assert all(
        result["final_disposition"] == "not_completed"
        and result["reviewer_verdict"] == "not_run"
        for result in results
    )
    assert summary["complete_pair_count"] == 1
    assert summary["treatments"]["native"]["infrastructure_failure_count"] == 1
    assert summary["treatments"]["native"]["run_count"] == 2
    assert summary["treatments"]["vega"]["run_count"] == 1


def test_case_ledger_accepts_append_only_runnable_revision() -> None:
    candidate = _candidate_case("DV-B01", "bug")
    runnable = _runnable_case(candidate)

    cases = validate_case_ledger([candidate, runnable, *_other_candidates()])

    assert cases["DV-B01"]["revision"] == 2
    assert cases["DV-B01"]["status"] == "runnable"


def test_retired_case_can_be_replaced_without_expanding_active_v1() -> None:
    candidate = _candidate_case("DV-B01", "bug")
    replacement = _candidate_case("DV-B04", "bug")

    try:
        validate_case_ledger([candidate, replacement, *_other_candidates()])
    except ValueError as exc:
        assert "同时最多保留 3 个 Bug" in str(exc)
    else:
        raise AssertionError("V1 不得同时保留四个活跃 Bug")

    retired = deepcopy(candidate)
    retired.update(revision=2, status="retired")
    cases = validate_case_ledger(
        [candidate, retired, replacement, *_other_candidates()]
    )

    assert cases["DV-B01"]["status"] == "retired"
    assert cases["DV-B04"]["status"] == "candidate_not_frozen"


def test_runnable_revision_requires_all_qualification_evidence() -> None:
    candidate = _candidate_case("DV-B01", "bug")
    runnable = _runnable_case(candidate)
    runnable["qualification"]["windows"] = "pending"

    try:
        validate_case_ledger([candidate, runnable, *_other_candidates()])
    except ValueError as exc:
        assert "全部 qualification 必须 passed" in str(exc)
    else:
        raise AssertionError("不完整资格不得成为 runnable")


def test_runnable_revision_requires_valid_treatment_order() -> None:
    candidate = _candidate_case("DV-B01", "bug")
    runnable = _runnable_case(candidate)
    runnable["treatment_order"] = ["native", "native"]

    try:
        validate_case_ledger([candidate, runnable, *_other_candidates()])
    except ValueError as exc:
        assert "treatment_order" in str(exc)
    else:
        raise AssertionError("未固定有效顺序的 case 不得成为 runnable")


def test_results_reject_unqualified_candidate() -> None:
    cases = validate_case_ledger(
        [_candidate_case("DV-B01", "bug"), *_other_candidates()]
    )

    try:
        validate_results(cases, [_result("DV-B01", "native")])
    except ValueError as exc:
        assert "尚未达到 runnable" in str(exc)
    else:
        raise AssertionError("candidate_not_frozen 不得登记正式结果")


def test_summary_requires_complete_pairs_and_counts_false_success() -> None:
    cases = _all_runnable_cases()
    results = []
    for case_id in sorted(cases):
        results.append(_result(case_id, "native"))
        results.append(_result(case_id, "vega"))
    results[0]["verification_status"] = "failed"

    validated = validate_results(cases, results)
    summary = build_summary(cases, validated)

    assert summary["evidence_status"] == "paired_results_complete"
    assert summary["complete_pair_count"] == 6
    assert summary["treatments"]["native"]["false_success_count"] == 1
    assert summary["treatments"]["native"]["verified_success_count"] == 5
    assert summary["treatments"]["vega"]["verified_success_count"] == 6
    assert summary["treatments"]["vega"]["manual_actions_total"] == 12


def test_summary_keeps_missing_pair_as_insufficient_evidence() -> None:
    cases = _all_runnable_cases()
    results = [_result(case_id, "native") for case_id in sorted(cases)]

    summary = build_summary(cases, validate_results(cases, results))

    assert summary["evidence_status"] == "insufficient_evidence"
    assert summary["complete_pair_count"] == 0


def test_result_token_null_is_not_counted_as_zero() -> None:
    cases = _all_runnable_cases()
    result = _result("DV-B01", "native")
    result["tokens"]["input"] = None

    summary = build_summary(cases, validate_results(cases, [result]))
    token_summary = summary["treatments"]["native"]["tokens"]["input"]

    assert token_summary == {"known_count": 0, "known_total": 0}


def test_result_run_id_must_be_non_empty_string() -> None:
    cases = _all_runnable_cases()
    for value in ("", "   ", None, []):
        result = _result("DV-B01", "native")
        result["run_id"] = value
        try:
            validate_results(cases, [result])
        except ValueError as exc:
            assert "run_id 必须是非空字符串" in str(exc)
        else:
            raise AssertionError(f"非法 run_id 未被拒绝：{value!r}")


def test_result_invalid_scalar_types_fail_closed() -> None:
    cases = _all_runnable_cases()
    for field in ("case_id", "treatment", "run_status", "final_disposition"):
        result = _result("DV-B01", "native")
        result[field] = []
        try:
            validate_results(cases, [result])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{field} 类型非法时必须 fail-closed")


def test_result_evidence_refs_must_be_non_empty_strings() -> None:
    cases = _all_runnable_cases()
    for value in ([], [""], [1]):
        result = _result("DV-B01", "native")
        result["evidence_refs"] = value
        try:
            validate_results(cases, [result])
        except ValueError as exc:
            assert "evidence_refs 必须是非空字符串列表" in str(exc)
        else:
            raise AssertionError(f"非法 evidence_refs 未被拒绝：{value!r}")


def test_result_tokens_reject_unexpected_fields() -> None:
    cases = _all_runnable_cases()
    result = _result("DV-B01", "native")
    result["tokens"]["total"] = 130

    try:
        validate_results(cases, [result])
    except ValueError as exc:
        assert "tokens 包含未知字段" in str(exc)
    else:
        raise AssertionError("未参与聚合的 Token 字段必须被拒绝")


def test_result_timeout_must_match_case_contract() -> None:
    cases = _all_runnable_cases()
    result = _result("DV-B01", "native")
    result["timeout_seconds"] = 120

    try:
        validate_results(cases, [result])
    except ValueError as exc:
        assert "执行合同与 case 不一致" in str(exc)
    else:
        raise AssertionError("treatment timeout 不得偏离预注册合同")


def test_infrastructure_failure_does_not_complete_pair() -> None:
    cases = _all_runnable_cases()
    results = [
        _result(case_id, treatment)
        for case_id in sorted(cases)
        for treatment in ("native", "vega")
    ]
    failed = results[0]
    failed.update(
        run_status="infrastructure_failure",
        final_disposition="not_completed",
        verification_status="not_run",
        reviewer_verdict="not_run",
        reviewer_independent_findings=0,
    )

    summary = build_summary(cases, validate_results(cases, results))

    assert summary["evidence_status"] == "insufficient_evidence"
    assert summary["complete_pair_count"] == 5
    assert summary["treatments"]["native"]["infrastructure_failure_count"] == 1


def test_duplicate_treatment_is_rejected() -> None:
    cases = _all_runnable_cases()
    first = _result("DV-B01", "native")
    duplicate = deepcopy(first)
    duplicate["run_id"] = "second-run"

    try:
        validate_results(cases, [first, duplicate])
    except ValueError as exc:
        assert "只允许一次正式运行" in str(exc)
    else:
        raise AssertionError("同一 treatment 的隐藏重跑必须被拒绝")


def test_cli_writes_local_json_and_markdown_summary(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    output_dir = tmp_path / "output"
    cases = _all_runnable_case_records()
    results = [
        _result(case_id, treatment)
        for case_id in sorted({item["case_id"] for item in cases})
        for treatment in ("native", "vega")
    ]
    _write_jsonl(cases_path, cases)
    _write_jsonl(results_path, results)

    exit_code = main(
        [
            "--cases",
            str(cases_path),
            "--results",
            str(results_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(output_dir.joinpath("summary.json").read_text(encoding="utf-8"))[
        "evidence_status"
    ] == "paired_results_complete"
    assert "# Vega 日用价值实验摘要" in output_dir.joinpath("SUMMARY.md").read_text(
        encoding="utf-8"
    )


def _candidate_case(case_id: str, task_type: str) -> dict:
    return {
        "schema_version": 1,
        "case_id": case_id,
        "revision": 1,
        "task_type": task_type,
        "status": "candidate_not_frozen",
        "repository": f"example/{case_id.lower()}",
        "issue_number": int(case_id[-2:]),
        "issue_url": f"https://github.com/example/{case_id.lower()}/issues/1",
        "title": f"{case_id} title",
        "task_summary": f"{case_id} summary",
        "baseline_commit": None,
        "oracle_ref": None,
        "treatment_order": (
            ["native", "vega"]
            if case_id in {"DV-B01", "DV-B03", "DV-F02"}
            else ["vega", "native"]
        ),
        "allowed_paths": [],
        "verification_commands": [],
        "execution_contract": {
            "model": None,
            "reasoning_effort": None,
            "timeout_seconds": None,
        },
        "qualification": {
            "baseline_verifier": "pending",
            "oracle_verifier": "pending",
            "windows": "pending",
            "dependencies": "pending",
        },
        "notes": "候选。",
    }


def _runnable_case(candidate: dict) -> dict:
    runnable = deepcopy(candidate)
    runnable.update(
        {
            "revision": candidate["revision"] + 1,
            "status": "runnable",
            "baseline_commit": BASELINE,
            "oracle_ref": "refs/tags/oracle",
            "allowed_paths": ["src/"],
            "verification_commands": ["python -m pytest tests/test_case.py"],
            "execution_contract": {
                "model": "test-model",
                "reasoning_effort": "medium",
                "timeout_seconds": 60,
            },
            "qualification": {
                "baseline_verifier": "passed",
                "oracle_verifier": "passed",
                "windows": "passed",
                "dependencies": "passed",
            },
            "notes": "资格已确认。",
        }
    )
    return runnable


def _other_candidates() -> list[dict]:
    return [
        _candidate_case("DV-B02", "bug"),
        _candidate_case("DV-B03", "bug"),
        _candidate_case("DV-F01", "feature"),
        _candidate_case("DV-F02", "feature"),
        _candidate_case("DV-F03", "feature"),
    ]


def _all_runnable_case_records() -> list[dict]:
    candidates = [
        _candidate_case("DV-B01", "bug"),
        *_other_candidates(),
    ]
    records: list[dict] = []
    for candidate in candidates:
        records.extend([candidate, _runnable_case(candidate)])
    return records


def _all_runnable_cases() -> dict[str, dict]:
    return validate_case_ledger(_all_runnable_case_records())


def _result(case_id: str, treatment: str) -> dict:
    return {
        "schema_version": 1,
        "run_id": f"{case_id.lower()}-{treatment}",
        "case_id": case_id,
        "treatment": treatment,
        "baseline_commit": BASELINE,
        "model": "test-model",
        "reasoning_effort": "medium",
        "timeout_seconds": 60,
        "run_status": "completed",
        "final_disposition": "success",
        "verification_status": "passed",
        "reviewer_verdict": "approve",
        "reviewer_independent_findings": 1 if treatment == "vega" else 0,
        "wall_clock_seconds": 10.0 if treatment == "native" else 12.0,
        "tokens": {"input": 100, "output": 20, "cached_input": 10},
        "manual_actions": 3 if treatment == "native" else 2,
        "recovery_used": False,
        "artifact_read": treatment == "vega",
        "evidence_refs": [f"runs/{case_id.lower()}-{treatment}/summary.json"],
        "notes": "",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )


def _write_codex_profile(tmp_path: Path) -> Path:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    codex_home.joinpath("config.toml").write_text(
        "\n".join(
            [
                'model = "gpt-5.6-sol"',
                'model_provider = "test-provider"',
                "",
                '[model_providers.test-provider]',
                'base_url = "https://provider.example.test/v1"',
                'wire_api = "responses"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    codex_home.joinpath("custom-models.json").write_text(
        json.dumps({"models": [{"slug": "gpt-5.6-sol"}]}),
        encoding="utf-8",
    )
    codex_home.joinpath("vega-daily-value-v1.config.toml").write_text(
        "\n".join(
            [
                "project_root_markers = []",
                'approval_policy = "never"',
                "",
                "[sandbox_workspace_write]",
                "network_access = false",
                "",
                "[features]",
                *[f"{feature} = false" for feature in REQUIRED_DISABLED_FEATURES],
                "",
            ]
        ),
        encoding="utf-8",
    )
    return codex_home
