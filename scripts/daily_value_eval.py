from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "eval/experiments/daily-value-validation/cases.jsonl"
Record = dict[str, Any]
CaseMap = dict[str, Record]
CASE_ID_PATTERN = re.compile(r"DV-(?P<type>[BF])\d{2}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
CASE_REQUIRED = {
    "schema_version", "case_id", "revision", "task_type", "status", "repository",
    "issue_number", "issue_url", "title", "task_summary", "baseline_commit",
    "oracle_ref", "treatment_order", "allowed_paths", "verification_commands",
    "execution_contract", "qualification", "notes",
}
RESULT_REQUIRED = {
    "schema_version", "run_id", "case_id", "treatment", "baseline_commit", "model",
    "reasoning_effort", "timeout_seconds", "run_status", "final_disposition",
    "verification_status", "reviewer_verdict", "reviewer_independent_findings",
    "wall_clock_seconds", "tokens", "manual_actions", "recovery_used", "artifact_read",
    "evidence_refs", "notes",
}
RUN_STATUSES = ("completed", "stopped", "timed_out", "infrastructure_failure")
FINAL_DISPOSITIONS = ("success", "needs_human", "failed", "not_completed")
VERIFICATION_STATUSES = ("passed", "failed", "not_run", "invalid")
REVIEWER_VERDICTS = ("approve", "request_changes", "needs_human", "not_run", "invalid")
def load_jsonl(path: Path, *, allow_empty: bool = False) -> list[Record]:
    if not path.is_file():
        raise ValueError(f"JSONL 文件不存在：{path}")
    records: list[Record] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{number} 不是有效 JSON：{exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path.name}:{number} 必须是 JSON object")
        records.append(record)
    if not records and not allow_empty:
        raise ValueError(f"JSONL 文件为空：{path}")
    return records
def validate_case_ledger(records: list[Record]) -> CaseMap:
    grouped: dict[str, list[Record]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for record in records:
        _require(record, CASE_REQUIRED, "case")
        case_id = record["case_id"]
        match = CASE_ID_PATTERN.fullmatch(case_id) if isinstance(case_id, str) else None
        revision = record["revision"]
        if record["schema_version"] != 1 or match is None:
            raise ValueError(f"非法 case schema 或 case_id：{case_id}")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError(f"{case_id} revision 必须从 1 开始")
        if (case_id, revision) in seen:
            raise ValueError(f"{case_id} revision 不可重复")
        seen.add((case_id, revision))
        expected_type = "bug" if match.group("type") == "B" else "feature"
        if record["task_type"] != expected_type:
            raise ValueError(f"{case_id} task_type 必须是 {expected_type}")
        if record["status"] not in ("candidate_not_frozen", "runnable", "retired"):
            raise ValueError(f"{case_id} status 非法")
        if record["status"] == "runnable":
            _validate_runnable(record)
        grouped[case_id].append(record)
    latest: CaseMap = {}
    identity = ("task_type", "repository", "issue_number", "issue_url", "title", "treatment_order")
    for case_id, revisions in grouped.items():
        revisions.sort(key=lambda item: item["revision"])
        if [item["revision"] for item in revisions] != list(range(1, len(revisions) + 1)):
            raise ValueError(f"{case_id} revision 必须连续")
        first = revisions[0]
        if any(item[field] != first[field] for item in revisions[1:] for field in identity):
            raise ValueError(f"{case_id} 后续 revision 不得改写任务身份")
        latest[case_id] = revisions[-1]
    counts = {kind: sum(case["task_type"] == kind and case["status"] != "retired"
                        for case in latest.values()) for kind in ("bug", "feature")}
    if any(count > 3 for count in counts.values()):
        raise ValueError(f"V1 同时最多保留 3 个 Bug 和 3 个 Feature：当前为 {counts}")
    return latest
def validate_results(cases: CaseMap, records: list[Record]) -> list[Record]:
    run_ids: set[str] = set()
    treatments: set[tuple[str, str]] = set()
    for record in records:
        _require(record, RESULT_REQUIRED, "result")
        case_id = record["case_id"]
        treatment = record["treatment"]
        run_id = record["run_id"]
        case = cases.get(case_id) if isinstance(case_id, str) else None
        if record["schema_version"] != 1 or case is None:
            raise ValueError(f"结果 schema 或 case 非法：{case_id}")
        if case["status"] != "runnable":
            raise ValueError(f"{case_id} 尚未达到 runnable，不得登记正式结果")
        if not isinstance(treatment, str) or treatment not in ("native", "vega"):
            raise ValueError(f"{case_id} treatment 非法")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(f"{case_id}/{treatment} run_id 必须是非空字符串")
        if run_id in run_ids or (case_id, treatment) in treatments:
            raise ValueError(f"{case_id}/{treatment} V1 只允许一次正式运行")
        run_ids.add(run_id)
        treatments.add((case_id, treatment))
        execution = case["execution_contract"]
        if record["baseline_commit"] != case["baseline_commit"]:
            raise ValueError(f"{case_id}/{treatment} baseline_commit 与 case 不一致")
        _non_negative(record["timeout_seconds"], "timeout", integer=True, minimum=1)
        contract_fields = ("model", "reasoning_effort", "timeout_seconds")
        if any(record[field] != execution[field] for field in contract_fields):
            raise ValueError(f"{case_id}/{treatment} 执行合同与 case 不一致")
        if record["run_status"] not in RUN_STATUSES:
            raise ValueError(f"{case_id}/{treatment} run_status 非法")
        if record["final_disposition"] not in FINAL_DISPOSITIONS:
            raise ValueError(f"{case_id}/{treatment} final_disposition 非法")
        completed = record["run_status"] == "completed"
        not_completed = record["final_disposition"] == "not_completed"
        if completed == not_completed:
            raise ValueError(f"{case_id}/{treatment} 运行与终态语义不一致")
        if record["verification_status"] not in VERIFICATION_STATUSES:
            raise ValueError(f"{case_id}/{treatment} verification_status 非法")
        if record["reviewer_verdict"] not in REVIEWER_VERDICTS:
            raise ValueError(f"{case_id}/{treatment} reviewer_verdict 非法")
        _non_negative(record["reviewer_independent_findings"], "reviewer finding", integer=True)
        _non_negative(record["wall_clock_seconds"], "wall clock")
        _non_negative(record["manual_actions"], "manual actions", integer=True)
        if record["reviewer_verdict"] == "not_run" and record["reviewer_independent_findings"]:
            raise ValueError(f"{case_id}/{treatment} reviewer 未运行时 finding 必须为 0")
        tokens = record["tokens"]
        _require(tokens, {"input", "output", "cached_input"}, "tokens")
        if tokens.keys() != {"input", "output", "cached_input"}:
            raise ValueError("tokens 包含未知字段")
        for value in tokens.values():
            if value is not None:
                _non_negative(value, "token", integer=True)
        if not all(isinstance(record[field], bool) for field in ("recovery_used", "artifact_read")):
            raise ValueError(f"{case_id}/{treatment} 布尔指标非法")
        _non_empty_strings(record["evidence_refs"], f"{case_id}/{treatment} evidence_refs")
    return records
def build_summary(cases: CaseMap, results: list[Record]) -> Record:
    comparable_keys = {(item["case_id"], item["treatment"]) for item in results
                       if item["run_status"] != "infrastructure_failure"}
    runnable = sorted((case for case in cases.values() if case["status"] == "runnable"),
                      key=lambda item: item["case_id"])
    pairs = [
        {"case_id": case["case_id"], "complete": all(
            (case["case_id"], value) in comparable_keys for value in ("native", "vega")
        )}
        for case in runnable
    ]
    complete_count = sum(pair["complete"] for pair in pairs)
    status = ("not_started" if not runnable else
              "paired_results_complete" if complete_count == len(runnable) == 6 else
              "insufficient_evidence")
    return {
        "schema_version": 1,
        "evidence_status": status,
        "registered_case_count": len(cases),
        "runnable_case_count": len(runnable),
        "candidate_case_count": sum(case["status"] == "candidate_not_frozen"
                                    for case in cases.values()),
        "complete_pair_count": complete_count,
        "treatments": {treatment: _aggregate(
            [item for item in results if item["treatment"] == treatment])
            for treatment in ("native", "vega")},
        "pairs": pairs,
        "conclusion_boundary": ("六个 pair 已完整，只允许人工做方向性判断。"
                                if status == "paired_results_complete"
                                else "样本不足，只能保留 insufficient_evidence。"),
    }
def render_markdown(summary: Record) -> str:
    lines = [
        "# Vega 日用价值实验摘要",
        "",
        f"- 证据状态：`{summary['evidence_status']}`",
        f"- 完整 pair：{summary['complete_pair_count']}/{summary['runnable_case_count']}",
        f"- 结论边界：{summary['conclusion_boundary']}",
        "",
        "| Treatment | 运行 | Verified | False success | Reviewer 发现 | 人工操作 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for treatment in ("native", "vega"):
        item = summary["treatments"][treatment]
        lines.append(
            f"| {treatment} | {item['run_count']} | {item['verified_success_count']} | "
            f"{item['false_success_count']} | {item['reviewer_independent_findings']} | "
            f"{item['manual_actions_total']} |"
        )
    return "\n".join(lines) + "\n"
def _validate_runnable(record: Record) -> None:
    case_id = record["case_id"]
    baseline = record["baseline_commit"]
    execution = record["execution_contract"]
    qualification = record["qualification"]
    if not isinstance(baseline, str) or COMMIT_PATTERN.fullmatch(baseline) is None:
        raise ValueError(f"{case_id} runnable 时必须固定 40 位 baseline_commit")
    if not isinstance(record["oracle_ref"], str) or not record["oracle_ref"].strip():
        raise ValueError(f"{case_id} runnable 时必须固定 oracle")
    if record["treatment_order"] not in (["native", "vega"], ["vega", "native"]):
        raise ValueError(f"{case_id} runnable 时必须固定有效 treatment_order")
    _non_empty_strings(record["allowed_paths"], f"{case_id} allowed_paths")
    _non_empty_strings(record["verification_commands"], f"{case_id} verification_commands")
    _require(execution, {"model", "reasoning_effort", "timeout_seconds"}, "execution_contract")
    if not execution["model"] or not execution["reasoning_effort"]:
        raise ValueError(f"{case_id} runnable 时必须固定模型合同")
    _non_negative(execution["timeout_seconds"], "timeout", integer=True, minimum=1)
    _require(
        qualification,
        {"baseline_verifier", "oracle_verifier", "windows", "dependencies"},
        "qualification",
    )
    if any(value != "passed" for value in qualification.values()):
        raise ValueError(f"{case_id} runnable 时全部 qualification 必须 passed")
def _aggregate(records: list[Record]) -> Record:
    verified = sum(
        item["final_disposition"] == "success" and item["verification_status"] == "passed"
        for item in records
    )
    return {
        "run_count": len(records),
        "infrastructure_failure_count": sum(
            item["run_status"] == "infrastructure_failure" for item in records
        ),
        "verified_success_count": verified,
        "false_success_count": sum(i["final_disposition"] == "success" for i in records) - verified,
        "reviewer_independent_findings": sum(
            item["reviewer_independent_findings"] for item in records
        ),
        "wall_clock_seconds_total": sum(item["wall_clock_seconds"] for item in records),
        "tokens": {
            field: {
                "known_count": sum(item["tokens"][field] is not None for item in records),
                "known_total": sum(
                    item["tokens"][field]
                    for item in records
                    if item["tokens"][field] is not None
                ),
            }
            for field in ("input", "output", "cached_input")
        },
        "manual_actions_total": sum(item["manual_actions"] for item in records),
        "recovery_count": sum(item["recovery_used"] for item in records),
        "artifact_read_count": sum(item["artifact_read"] for item in records),
    }
def _require(payload: Any, fields: set[str], label: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 必须是 object")
    missing = sorted(fields - payload.keys())
    if missing:
        raise ValueError(f"{label} 缺少字段：{missing}")
def _non_empty_strings(value: Any, label: str) -> None:
    invalid_item = isinstance(value, list) and any(
        not isinstance(item, str) or not item.strip() for item in value
    )
    if not isinstance(value, list) or not value or invalid_item:
        raise ValueError(f"{label} 必须是非空字符串列表")
def _non_negative(value: Any, label: str, *, integer: bool = False,
                  minimum: int = 0) -> None:
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected) or value < minimum:
        raise ValueError(f"{label} 必须是不小于 {minimum} 的数值")
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验并聚合 Vega 日用价值配对实验。")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="case JSONL。")
    parser.add_argument("--results", type=Path, help="result JSONL。")
    parser.add_argument("--output-dir", type=Path, help="写入 summary.json 与 SUMMARY.md。")
    return parser.parse_args(argv)
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cases = validate_case_ledger(load_jsonl(args.cases))
        if args.results is None:
            if args.output_dir is not None:
                raise ValueError("--output-dir 必须与 --results 一起使用")
            print(f"case ledger 有效：{len(cases)} 个 case，当前均未登记正式结果。")
            return 0
        results = validate_results(cases, load_jsonl(args.results, allow_empty=True))
        summary = build_summary(cases, results)
        if args.output_dir is not None:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            args.output_dir.joinpath("summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            args.output_dir.joinpath("SUMMARY.md").write_text(render_markdown(summary),
                                                              encoding="utf-8")
        print(f"实验结果校验完成：status={summary['evidence_status']}，"
              f"pairs={summary['complete_pair_count']}/{summary['runnable_case_count']}")
        return 0
    except ValueError as exc:
        print(f"日用价值实验校验失败：{exc}", file=sys.stderr)
        return 1
if __name__ == "__main__":
    raise SystemExit(main())
