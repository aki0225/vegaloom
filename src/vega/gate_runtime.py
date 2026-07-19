from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .goal_evidence import validate_reflect_evidence_freshness
from .models import GateReason, GateResult, GateState
from .project_config import (
    ProjectConfigCheckResult,
    budget_for_scope,
    check_project_config,
    load_project_config,
    render_project_config_check,
)
from .project_knowledge import load_project_knowledge
from .redaction import redact_text, redact_value, write_redacted_json, write_redacted_text
from .reflect_runtime import collect_git_reflection
from .run_utils import create_run_dir, resolve_run_dir
from .trace import TraceWriter
from .workspace_check import collect_tracked_diff_parts, render_tracked_diff_sections

GATE_ARTIFACTS = ["state.json", "trace.jsonl", "gate-report.md", "gate-result.json", "eval.md"]
HIGH_RISK_PATH_KEYWORDS = [
    "auth",
    "authentication",
    "authorization",
    "authn",
    "authz",
    "oauth",
    "security",
    "permission",
    "permissions",
    "role",
    "roles",
    "rbac",
    "payment",
    "payments",
    "billing",
    "migration",
    "migrations",
    "schema",
    "schemas",
    "database",
    "databases",
    "secret",
    "secrets",
    "token",
    "tokens",
    "credential",
    "credentials",
]
MEDIUM_RISK_PATH_KEYWORDS = [
    "admin",
    "administrator",
    "administrators",
    "administration",
    "config",
    "configs",
    "configuration",
    "deploy",
    "deployment",
    "docker",
    "dockerfile",
    "ci",
    ".github",
]
_RISK_COMPOUND_SUFFIXES = (
    "api",
    "backup",
    "cache",
    "client",
    "controller",
    "filter",
    "gateway",
    "guard",
    "handler",
    "manager",
    "middleware",
    "model",
    "policy",
    "provider",
    "repository",
    "server",
    "service",
    "store",
    "storage",
    "vault",
)
DEPENDENCY_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "Cargo.toml",
    "Cargo.lock",
}


class GateRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, repo_path: Path, source_run: str, scope_profile: str | None = None) -> Path:
        repo = repo_path.resolve()
        base_run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-gate"
        run_id, run_dir = create_run_dir(self.workspace, base_run_id)
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = GateState(
            run_id=run_id,
            repo_path=str(repo),
            source_run=redact_text(source_run),
            status="running",
            current_step="resolve_source",
            scope_profile=scope_profile,
        )
        state.save(run_dir / "state.json")
        trace.write("gate_started", repo_path=str(repo), source_run=source_run)

        source_diagnostics: dict[str, object] = {}
        try:
            source_dir = resolve_run_dir(self.workspace, source_run)
            source_run_id = source_dir.name
            source_diagnostics = _collect_source_diagnostics(source_dir)
            state.source_run = source_run_id
            state.current_step = "project_config_check"
            state.save(run_dir / "state.json")

            config_check = check_project_config(repo)
            trace.write(
                "project_config_checked",
                status=config_check.status,
                issues=[issue.code for issue in config_check.issues],
            )
            if config_check.has_errors:
                return _finish_gate_config_failure(
                    run_dir,
                    trace,
                    state,
                    config_check,
                    source_diagnostics=source_diagnostics,
                )

            state.current_step = "risk_evaluation"
            state.save(run_dir / "state.json")
            result = evaluate_risk(
                self.workspace,
                repo,
                source_run_id,
                scope_profile=scope_profile,
            )
        except Exception as exc:  # noqa: BLE001 - Gate 必须保留结构化失败证据
            return _finish_gate_failure(
                run_dir,
                trace,
                state,
                code="risk_evaluation_failed",
                message="风险评估失败，Gate 未生成可信放行结论。",
                diagnostic=f"{type(exc).__name__}: {exc}",
                source_diagnostics=source_diagnostics,
            )

        safe_result = GateResult.model_validate(
            redact_value(result.model_dump(mode="json"))
        )
        state.changed_files = safe_result.changed_files
        state.risk = safe_result.risk
        state.recommendation = safe_result.recommendation
        gate_result_payload = {
            **safe_result.model_dump(mode="json"),
            "run_id": run_id,
            "repo_path": str(repo),
            "source_run": source_run_id,
        }
        write_redacted_json(
            run_dir / "gate-result.json",
            gate_result_payload,
        )
        write_redacted_text(run_dir / "gate-report.md", render_gate_report(safe_result))
        trace.write(
            "gate_result_written",
            risk=safe_result.risk,
            recommendation=safe_result.recommendation,
            reasons=[reason.code for reason in safe_result.reasons],
        )

        state.current_step = "eval"
        state.save(run_dir / "state.json")
        write_redacted_text(run_dir / "eval.md", "# Eval\n\n(pending)\n")
        eval_results = _run_gate_eval(run_dir)
        write_redacted_text(run_dir / "eval.md", _render_eval(eval_results))
        state.eval_results = eval_results
        state.artifacts = GATE_ARTIFACTS
        state.status = "failed" if any(item.startswith("FAIL:") for item in eval_results) else "success"
        state.current_step = "done"
        state.save(run_dir / "state.json")
        trace.write("eval_written", results=eval_results)
        trace.write("run_finished", status=state.status)
        return run_dir


def evaluate_risk(
    workspace: Path,
    repo_path: Path,
    source_run: str,
    scope_profile: str | None = None,
) -> GateResult:
    repo = repo_path.resolve()
    source_dir = resolve_run_dir(workspace, source_run)
    freshness = validate_reflect_evidence_freshness(
        workspace,
        repo,
        source_dir.name,
    )
    if not freshness.fresh:
        raise ValueError(
            "Gate source reflect 与目标仓库或当前快照不一致："
            + ", ".join(freshness.issues)
        )
    reflect_state = _read_json(source_dir / "state.json")
    git_data = collect_git_reflection(repo)
    status_all = _git(repo, ["git", "status", "--short", "--untracked-files=all"])
    diff_files = reflect_state.get("changed_files") or git_data["name_only"].splitlines()
    changed_files = _dedupe([*diff_files, *_status_paths(status_all)])
    # 必须与 Reflect/review snapshot 使用相同的 staged + unstaged 双事实流。
    # `git diff HEAD` 在 MM 场景只会给出净差异，预算、删除检查会漏掉 index 中内容。
    staged_name_status, unstaged_name_status = collect_tracked_diff_parts(
        repo,
        ["--name-status"],
    )
    staged_numstat, unstaged_numstat = collect_tracked_diff_parts(repo, ["--numstat"])
    name_status = render_tracked_diff_sections(
        staged_name_status,
        unstaged_name_status,
    )
    numstat = render_tracked_diff_sections(staged_numstat, unstaged_numstat)
    test_summary = _read_text(source_dir / "test-summary.md")
    reflection = _read_text(source_dir / "reflection.md")
    config = load_project_config(repo)
    budget = budget_for_scope(config, scope_profile)
    knowledge = load_project_knowledge(
        workspace,
        repo,
        "\n".join([reflection, test_summary, " ".join(changed_files)]),
        changed_files,
    )

    reasons: list[GateReason] = []
    if not changed_files:
        reasons.append(
            GateReason(
                code="no_diff",
                severity="high",
                message="未发现工作区 diff，无法证明 worker 已完成实现。",
                evidence="git diff --name-only 为空",
            )
        )
    if git_data["check"].strip():
        reasons.append(
            GateReason(
                code="diff_check_failed",
                severity="high",
                message="git diff --check 存在空白或冲突标记问题。",
                evidence=git_data["check"].strip()[:500],
            )
        )
    if any(line.startswith("D") for line in name_status.splitlines()):
        reasons.append(
            GateReason(
                code="deleted_files",
                severity="high",
                message="本次 diff 包含删除文件，auto loop 不应直接放行。",
                evidence=name_status.strip()[:500],
            )
        )
    if len(changed_files) >= 10:
        reasons.append(
            GateReason(
                code="many_files",
                severity="high",
                message="变更文件数量较多，建议人工审查影响面。",
                evidence=f"{len(changed_files)} 个文件",
            )
        )
    elif len(changed_files) >= 5:
        reasons.append(
            GateReason(
                code="several_files",
                severity="medium",
                message="变更文件数量偏多，建议隔离 reviewer 审查。",
                evidence=f"{len(changed_files)} 个文件",
            )
        )
    diff_lines = _diff_line_count(numstat)
    new_files = _new_file_paths(name_status, status_all)
    if budget.max_changed_files is not None and len(changed_files) > budget.max_changed_files:
        reasons.append(
            GateReason(
                code="budget_changed_files",
                severity="high",
                message="变更文件数量超过 .vega.yaml 配置的预算。",
                evidence=f"{len(changed_files)} > {budget.max_changed_files}",
            )
        )
    if budget.max_diff_lines is not None and diff_lines > budget.max_diff_lines:
        reasons.append(
            GateReason(
                code="budget_diff_lines",
                severity="high",
                message="diff 行数超过 .vega.yaml 配置的预算，建议人工确认是否过度实现。",
                evidence=f"{diff_lines} > {budget.max_diff_lines}",
            )
        )
    if budget.max_new_files is not None and len(new_files) > budget.max_new_files:
        reasons.append(
            GateReason(
                code="budget_new_files",
                severity="high",
                message="新增文件数量超过 .vega.yaml 配置的预算。",
                evidence=f"{len(new_files)} > {budget.max_new_files}；{', '.join(new_files[:8])}",
            )
        )
    if budget.forbid_new_dependencies:
        dependency_paths = _dependency_paths(changed_files)
        if dependency_paths:
            reasons.append(
                GateReason(
                    code="new_dependencies",
                    severity="high",
                    message=".vega.yaml 禁止自动引入或修改依赖声明，需要人工确认。",
                    evidence=", ".join(dependency_paths[:8]),
                )
            )
    if budget.forbid_large_generated_files:
        large_files = _large_new_files(repo, new_files, budget.max_file_bytes)
        if large_files:
            reasons.append(
                GateReason(
                    code="large_generated_files",
                    severity="high",
                    message="新增文件超过大小预算，疑似大体量生成物，需要人工确认。",
                    evidence=", ".join(large_files[:8]),
                )
            )
    high_paths = _matched_paths(changed_files, HIGH_RISK_PATH_KEYWORDS)
    configured_high_paths = _matched_config_paths(changed_files, config.risk.high_paths)
    high_paths = _dedupe([*high_paths, *configured_high_paths])
    if high_paths:
        reasons.append(
            GateReason(
                code="high_risk_paths",
                severity="high",
                message="变更命中鉴权、安全、迁移、密钥或数据结构等高风险路径。",
                evidence=", ".join(high_paths[:8]),
            )
        )
    medium_paths = _matched_paths(changed_files, MEDIUM_RISK_PATH_KEYWORDS)
    configured_medium_paths = _matched_config_paths(changed_files, config.risk.medium_paths)
    medium_paths = _dedupe([*medium_paths, *configured_medium_paths])
    if medium_paths:
        reasons.append(
            GateReason(
                code="medium_risk_paths",
                severity="medium",
                message="变更命中配置、部署、管理端或 CI 相关路径。",
                evidence=", ".join(medium_paths[:8]),
            )
        )
    human_review_hits = _matched_human_review_rules(
        "\n".join([reflection, test_summary, name_status, " ".join(changed_files)]),
        config.risk.require_human_review,
    )
    if human_review_hits:
        reasons.append(
            GateReason(
                code="project_requires_human_review",
                severity="high",
                message=".vega.yaml 要求命中这些规则时必须人工确认。",
                evidence=", ".join(human_review_hits[:8]),
            )
        )
    if "未提供测试日志" in test_summary or not test_summary.strip():
        reasons.append(
            GateReason(
                code="missing_tests",
                severity="medium",
                message="未提供测试日志，建议至少补最小验证再结束 loop。",
                evidence="test-summary.md 未包含实际测试输出",
            )
        )
    if knowledge.memory_hits:
        reasons.append(
            GateReason(
                code="memory_hits",
                severity="medium",
                message="命中已接受 memory，需要按历史规则或踩坑核对。",
                evidence=", ".join(hit.title for hit in knowledge.memory_hits[:5]),
            )
        )

    final_freshness = validate_reflect_evidence_freshness(
        workspace,
        repo,
        source_dir.name,
    )
    if not final_freshness.fresh:
        raise ValueError(
            "Gate 评估期间目标仓库快照发生变化："
            + ", ".join(final_freshness.issues)
        )
    risk = _overall_risk(reasons)
    return GateResult(
        risk=risk,
        recommendation=_recommendation_for_risk(risk),
        reasons=reasons
        or [
            GateReason(
                code="no_risk_signal",
                severity="low",
                message="未发现明显风险信号。",
                evidence="文件数量、路径、测试摘要和 diff check 均未触发门禁。",
            )
        ],
        changed_files=changed_files,
        scope_profile=scope_profile,
    )


def render_gate_report(result: GateResult) -> str:
    lines = [
        "# Risk Gate Report",
        "",
        f"- 风险等级：`{result.risk}`",
        f"- 建议：`{result.recommendation}`",
        f"- scope：`{result.scope_profile or 'default'}`",
        "",
        "## 变更文件",
        "",
    ]
    if result.changed_files:
        lines.extend(f"- `{item}`" for item in result.changed_files)
    else:
        lines.append("- 未发现变更文件。")
    lines.extend(["", "## 门禁原因", ""])
    for reason in result.reasons:
        lines.extend(
            [
                f"### {reason.code}",
                "",
                f"- 严重级别：`{reason.severity}`",
                f"- 说明：{reason.message}",
                f"- 证据：{reason.evidence or '无'}",
                "",
            ]
        )
    lines.extend(["## 建议解释", ""])
    if result.recommendation == "self-check":
        lines.append("- 可以由主会话做自检；仍建议保留 reflect/report 证据。")
    elif result.recommendation == "isolated-review":
        lines.append("- 建议运行隔离 reviewer，但不需要直接升级到人工阻塞。")
    else:
        lines.append("- 建议人工判断后再继续 auto loop 或合并变更。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def _finish_gate_config_failure(
    run_dir: Path,
    trace: TraceWriter,
    state: GateState,
    config_check: ProjectConfigCheckResult,
    *,
    source_diagnostics: dict[str, object] | None = None,
) -> Path:
    return _finish_gate_failure(
        run_dir,
        trace,
        state,
        code="project_config_invalid",
        message="项目配置预检失败，Gate 未继续风险评估。",
        diagnostic=render_project_config_check(config_check),
        config_check=config_check,
        source_diagnostics=source_diagnostics,
    )


def _finish_gate_failure(
    run_dir: Path,
    trace: TraceWriter,
    state: GateState,
    *,
    code: str,
    message: str,
    diagnostic: str,
    config_check: ProjectConfigCheckResult | None = None,
    source_diagnostics: dict[str, object] | None = None,
) -> Path:
    safe_code = redact_text(code)
    safe_message = redact_text(message)
    safe_diagnostic = redact_text(diagnostic)
    result_payload: dict[str, object] = {
        "status": "failed",
        "code": safe_code,
        "message": safe_message,
        "diagnostic": safe_diagnostic,
        "run_id": state.run_id,
        "repo_path": state.repo_path,
        "source_run": state.source_run,
        "scope_profile": state.scope_profile,
    }
    if config_check is not None:
        result_payload["config_check"] = config_check.model_dump(mode="json")
    if source_diagnostics:
        result_payload["source_diagnostics"] = source_diagnostics
    write_redacted_json(run_dir / "gate-result.json", result_payload)

    report_lines = [
        "# Risk Gate Report",
        "",
        "- 状态：`failed`",
        f"- 错误代码：`{safe_code}`",
        f"- 说明：{safe_message}",
        "",
        "## Diagnostic",
        "",
        safe_diagnostic,
    ]
    if source_diagnostics:
        report_lines.extend(
            [
                "",
                "## Source Diagnostics",
                "",
                "```json",
                json.dumps(
                    redact_value(source_diagnostics),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )
    write_redacted_text(
        run_dir / "gate-report.md",
        "\n".join(report_lines).rstrip() + "\n",
    )

    eval_results = [f"FAIL: {safe_message}"]
    if config_check is not None:
        eval_results.extend(
            f"FAIL: {issue.code}：{issue.message}"
            for issue in config_check.issues
            if issue.severity == "error"
        )
    else:
        eval_results.append(f"FAIL: {safe_code}：{safe_diagnostic[:500]}")
    write_redacted_text(run_dir / "eval.md", _render_eval(eval_results))

    state.eval_results = eval_results
    state.artifacts = GATE_ARTIFACTS
    state.status = "failed"
    state.current_step = (
        safe_code if safe_code.endswith("_failed") else f"{safe_code}_failed"
    )
    state.save(run_dir / "state.json")
    trace.write("gate_failed", code=safe_code, diagnostic=safe_diagnostic)
    trace.write("eval_written", results=eval_results)
    trace.write("run_finished", status=state.status)
    return run_dir


def _collect_source_diagnostics(source_dir: Path) -> dict[str, object]:
    state = _read_json(source_dir / "state.json")
    evidence = _read_json(source_dir / "review-evidence.json")
    changed_files = state.get("changed_files")
    untracked_files = evidence.get("untracked_files")
    return {
        "reflect_status": state.get("status"),
        "changed_files": changed_files if isinstance(changed_files, list) else [],
        "untracked_files": untracked_files if isinstance(untracked_files, list) else [],
    }


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _git(repo_path: Path, command: list[str]) -> str:
    result = subprocess.run(
        command,
        cwd=repo_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=False,
    )
    return (result.stdout or "") + (result.stderr or "")


def _matched_paths(paths: list[str], keywords: list[str]) -> list[str]:
    matched = []
    lowered_keywords = {keyword.lower() for keyword in keywords}
    for path in paths:
        if any(
            _risk_token_matches_keyword(token, keyword)
            for token in _risk_path_tokens(path)
            for keyword in lowered_keywords
        ):
            matched.append(path)
    return matched


def _risk_token_matches_keyword(token: str, keyword: str) -> bool:
    """只接受精确、纯数字后缀或有限技术复合边界，不做任意子串匹配。"""

    if token == keyword:
        return True
    if not token.startswith(keyword):
        return False

    remainder = token[len(keyword) :]
    without_leading_digits = remainder.lstrip("0123456789")
    if remainder and not without_leading_digits:
        return True

    compound = without_leading_digits.rstrip("0123456789")
    return compound in _RISK_COMPOUND_SUFFIXES


def _risk_path_tokens(path: str) -> set[str]:
    """按路径组件和文件名 token 提取风险词，避免普通子串造成误报。

    例如 `pricing.py` 不应因为包含 `ci` 被视为 CI 文件，`author.py` 也不应因为
    包含 `auth` 被视为鉴权代码；但精确 token、CamelCase、数字后缀和有限技术复合词
    仍可由 `_risk_token_matches_keyword` 识别。
    """

    components = [component for component in path.replace("\\", "/").split("/") if component]
    tokens = {component.lower() for component in components}
    for component in components:
        separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", component)
        separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", separated)
        tokens.update(
            token.lower()
            for token in re.split(r"[^A-Za-z0-9]+", separated)
            if token
        )
    return tokens


def _matched_config_paths(paths: list[str], patterns: list[str]) -> list[str]:
    matched = []
    normalized_patterns = [item.replace("\\", "/").strip().lower() for item in patterns if item.strip()]
    for path in paths:
        normalized = path.replace("\\", "/").lower()
        if any(normalized.startswith(pattern) or pattern in normalized for pattern in normalized_patterns):
            matched.append(path)
    return matched


def _matched_human_review_rules(text: str, patterns: list[str]) -> list[str]:
    normalized_text = text.lower()
    return [pattern for pattern in patterns if pattern.strip() and pattern.strip().lower() in normalized_text]


def _diff_line_count(numstat_text: str) -> int:
    total = 0
    for line in numstat_text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        for value in parts[:2]:
            if value.isdigit():
                total += int(value)
    return total


def _new_file_paths(name_status_text: str, status_text: str) -> list[str]:
    paths: list[str] = []
    for line in name_status_text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if parts and parts[0].startswith("A") and len(parts) >= 2:
            paths.append(parts[-1].strip())
    for line in status_text.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path_text = line[3:].strip()
        if status == "??" or "A" in status:
            paths.append(path_text)
    return _dedupe(paths)


def _dependency_paths(paths: list[str]) -> list[str]:
    result = []
    for path in paths:
        name = Path(path.replace("\\", "/")).name
        if name in DEPENDENCY_FILES:
            result.append(path)
    return result


def _large_new_files(repo_path: Path, paths: list[str], max_file_bytes: int) -> list[str]:
    result = []
    for path in paths:
        candidate = (repo_path / path).resolve()
        try:
            if candidate.is_file() and candidate.stat().st_size > max_file_bytes:
                result.append(f"{path} ({candidate.stat().st_size} bytes)")
        except OSError:
            continue
    return result


def _status_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for line in status_text.splitlines():
        if len(line) < 4:
            continue
        path_text = line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        if path_text:
            paths.append(path_text)
    return paths


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.replace("\\", "/")
        if normalized not in seen:
            seen.add(normalized)
            result.append(item)
    return result


def _overall_risk(reasons: list[GateReason]) -> str:
    severities = {reason.severity for reason in reasons}
    if "high" in severities:
        return "high"
    if "medium" in severities:
        return "medium"
    return "low"


def _recommendation_for_risk(risk: str) -> str:
    if risk == "high":
        return "human-review"
    if risk == "medium":
        return "isolated-review"
    return "self-check"


def _run_gate_eval(run_dir: Path) -> list[str]:
    results = [f"{'PASS' if (run_dir / item).exists() else 'FAIL'}: artifact 存在：{item}" for item in GATE_ARTIFACTS]
    result_path = run_dir / "gate-result.json"
    if result_path.exists():
        result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        result = GateResult.model_validate(result_payload)
        results.append("PASS: gate-result.json schema 合法")
        state = GateState.model_validate_json(
            (run_dir / "state.json").read_text(encoding="utf-8")
        )
        identity_matches = (
            state.run_id == run_dir.name
            and result_payload.get("run_id") == run_dir.name
            and Path(state.repo_path).resolve() == Path(result_payload.get("repo_path", "")).resolve()
            and result_payload.get("source_run") == state.source_run
            and result.risk == state.risk
            and result.recommendation == state.recommendation
            and result.changed_files == state.changed_files
            and result.scope_profile == state.scope_profile
        )
        results.append(
            "PASS: gate-result.json 与 GateState 身份和关键字段一致"
            if identity_matches
            else "FAIL: gate-result.json 与 GateState 身份或关键字段不一致"
        )
    else:
        results.append("FAIL: gate-result.json 不存在")
    return results


def _render_eval(results: list[str]) -> str:
    return redact_text("# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n")
