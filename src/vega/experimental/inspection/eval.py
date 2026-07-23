from __future__ import annotations

import json
from pathlib import Path

from .loop_spec import default_engineering_change_spec
from ...models import LoopSpec, RunState


def run_basic_eval(run_dir: Path, spec: LoopSpec | None = None) -> list[str]:
    active_spec = spec or default_engineering_change_spec()
    results: list[str] = []
    results.extend(_artifact_checks(run_dir, active_spec))
    results.extend(_state_checks(run_dir))
    if active_spec.eval.require_report_sections:
        results.extend(_report_section_checks(run_dir, active_spec))
    results.extend(_trace_event_checks(run_dir, active_spec))
    if active_spec.eval.require_tool_policy:
        results.extend(_tool_policy_checks(run_dir, active_spec))
        results.extend(_required_git_checks(run_dir, active_spec))
    if active_spec.eval.require_review_pass:
        results.extend(_review_checks(run_dir))
    if active_spec.eval.require_no_automatic_memory_write:
        results.extend(_memory_policy_checks(run_dir))
    return results


def write_eval(run_dir: Path, results: list[str]) -> None:
    content = "# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n"
    run_dir.joinpath("eval.md").write_text(content, encoding="utf-8")


def _artifact_checks(run_dir: Path, spec: LoopSpec) -> list[str]:
    results: list[str] = []
    for item in spec.eval.artifact_checks:
        file_name = _artifact_name(item)
        ok = run_dir.joinpath(file_name).exists()
        results.append(f"{'PASS' if ok else 'FAIL'}: artifact 存在：{file_name}")
    return results


def _state_checks(run_dir: Path) -> list[str]:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return ["FAIL: state.json 可读取"]
    try:
        RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"FAIL: state.json schema 不合法：{type(exc).__name__}"]
    return ["PASS: state.json schema 合法"]


def _report_section_checks(run_dir: Path, spec: LoopSpec) -> list[str]:
    report_path = run_dir / "report.md"
    if not report_path.exists():
        return ["FAIL: report.md 包含必需章节"]

    report = report_path.read_text(encoding="utf-8", errors="replace")
    missing = [section for section in spec.report.required_sections if f"## {section}" not in report]
    if missing:
        return [f"FAIL: report.md 缺少章节：{', '.join(missing)}"]
    return ["PASS: report.md 包含必需章节"]


def _trace_event_checks(run_dir: Path, spec: LoopSpec) -> list[str]:
    trace_path = run_dir / "trace.jsonl"
    if not trace_path.exists():
        return ["FAIL: trace.jsonl 包含必需事件"]

    events: list[str] = []
    try:
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line).get("event", ""))
    except json.JSONDecodeError as exc:
        return [f"FAIL: trace.jsonl 不是合法 JSONL：{exc.msg}"]

    missing = [event for event in spec.eval.trace_events if event not in events]
    if missing:
        return [f"FAIL: trace.jsonl 缺少事件：{', '.join(missing)}"]
    return ["PASS: trace.jsonl 包含必需事件"]


def _tool_policy_checks(run_dir: Path, spec: LoopSpec) -> list[str]:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return ["FAIL: tool policy 无法检查，state.json 不存在"]
    state = RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
    used = {result.tool for result in state.tool_results}
    unexpected = sorted(used - set(spec.tools.allowed))
    if unexpected:
        return [f"FAIL: 出现未授权工具：{', '.join(unexpected)}"]
    return ["PASS: 工具调用符合 allowlist"]


def _required_git_checks(run_dir: Path, spec: LoopSpec) -> list[str]:
    if "repo.run_check" not in spec.tools.allowed:
        return ["PASS: repo.run_check 未启用，跳过必需 Git 检查"]

    state_path = run_dir / "state.json"
    if not state_path.exists():
        return ["FAIL: 必需 Git 检查无法读取 state.json"]
    state = RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
    expected = set(spec.inspect.git_checks)
    results_by_check: dict[str, object] = {}
    failed: list[str] = []
    for result in state.tool_results:
        if result.tool != "repo.run_check":
            continue
        output = result.output if isinstance(result.output, dict) else {}
        check_id = output.get("check_id") if isinstance(output, dict) else None
        if not isinstance(check_id, str):
            continue
        results_by_check[check_id] = result
        exit_code = output.get("exit_code")
        if result.status != "ok" or not isinstance(exit_code, int) or exit_code != 0:
            failed.append(check_id)

    missing = sorted(expected - set(results_by_check))
    if missing:
        return [f"FAIL: 缺少必需 Git 检查：{', '.join(missing)}"]
    if failed:
        return [f"FAIL: 必需 Git 检查失败：{', '.join(sorted(set(failed)))}"]
    return ["PASS: 必需 Git 检查全部通过"]


def _review_checks(run_dir: Path) -> list[str]:
    review_path = run_dir / "review.md"
    if not review_path.exists():
        return ["FAIL: review.md 不存在"]
    review = review_path.read_text(encoding="utf-8", errors="replace")
    if "FAIL:" in review:
        return ["FAIL: reviewer pass 存在失败项"]
    return ["PASS: reviewer pass 无失败项"]


def _memory_policy_checks(run_dir: Path) -> list[str]:
    if run_dir.joinpath("memory.jsonl").exists():
        return ["FAIL: run 目录出现长期 memory 写入"]

    proposals_path = run_dir / "memory-proposals.jsonl"
    if not proposals_path.exists():
        return ["PASS: 未自动生成 Memory Proposal"]
    try:
        proposals = [
            json.loads(line)
            for line in proposals_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as exc:
        return [f"FAIL: memory-proposals.jsonl 不是合法 JSONL：{exc.msg}"]
    if proposals:
        return ["FAIL: engineering-change 出现自动 Memory Proposal"]
    return ["PASS: 未自动生成 Memory Proposal"]


def _artifact_name(item: str) -> str:
    legacy = {
        "state_json_exists": "state.json",
        "trace_jsonl_exists": "trace.jsonl",
        "plan_md_exists": "plan.md",
        "report_md_exists": "report.md",
        "eval_md_exists": "eval.md",
        "memory_proposals_exists": "memory-proposals.jsonl",
    }
    return legacy.get(item, item)
