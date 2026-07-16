from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import ChangePlanState
from .project_config import budget_for_scope, load_project_config
from .project_context import build_project_context
from .redaction import redact_text, redact_value, write_redacted_text
from .run_utils import create_run_dir
from .trace import TraceWriter

CHANGE_PLAN_ARTIFACTS = [
    "state.json",
    "trace.jsonl",
    "change-plan.md",
    "scope-profile.md",
    "phase-plan.md",
    "risk.md",
    "project-context.md",
    "eval.md",
]


class ChangePlanRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, repo_path: Path, goal_text: str, input_source: str, scope_profile: str | None = None) -> Path:
        safe_goal_text = redact_text(goal_text)
        safe_input_source = redact_text(input_source)
        base_run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-change-plan"
        run_id, run_dir = create_run_dir(self.workspace, base_run_id)
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = ChangePlanState(
            run_id=run_id,
            repo_path=str(repo_path.resolve()),
            input_source=safe_input_source,
            scope_profile=scope_profile,
            status="running",
            current_step="plan",
        )
        state.save(run_dir / "state.json")
        trace.write("change_plan_started", repo_path=str(repo_path.resolve()), scope_profile=scope_profile)

        config = load_project_config(repo_path)
        budget = budget_for_scope(config, scope_profile)
        project_context = build_project_context(self.workspace, repo_path, safe_goal_text)
        inputs = redact_value({
            "repo_path": str(repo_path.resolve()),
            "repo_name": repo_path.resolve().name,
            "goal_text": safe_goal_text.strip(),
            "scope_profile": scope_profile or "default",
            "budget": budget.model_dump(),
        })
        write_redacted_text(run_dir / "project-context.md", project_context)
        write_redacted_text(run_dir / "change-plan.md", render_change_plan(inputs))
        write_redacted_text(run_dir / "scope-profile.md", render_scope_profile(inputs))
        write_redacted_text(run_dir / "phase-plan.md", render_phase_plan(inputs))
        write_redacted_text(run_dir / "risk.md", render_risk_plan(inputs))
        trace.write("change_plan_written", artifacts=CHANGE_PLAN_ARTIFACTS)

        state.current_step = "eval"
        write_redacted_text(run_dir / "eval.md", "# Eval\n\n(pending)\n")
        eval_results = _run_change_plan_eval(run_dir)
        write_redacted_text(run_dir / "eval.md", _render_eval(eval_results))
        state.eval_results = eval_results
        state.artifacts = CHANGE_PLAN_ARTIFACTS
        state.status = "failed" if any(item.startswith("FAIL:") for item in eval_results) else "success"
        state.current_step = "done"
        state.save(run_dir / "state.json")
        trace.write("eval_written", results=eval_results)
        trace.write("run_finished", status=state.status)
        return run_dir


def render_change_plan(inputs: dict) -> str:
    return redact_text("\n".join(
        [
            "# Change Plan",
            "",
            f"- 仓库：`{inputs['repo_name']}`",
            f"- scope profile：`{inputs['scope_profile']}`",
            "",
            "## Goal",
            "",
            inputs["goal_text"],
            "",
            "## Scope Contract",
            "",
            "- 本计划用于把大目标先变成可审查范围，不直接修改目标仓库。",
            "- 进入实现前，建议人工确认 scope、阶段和预算是否符合预期。",
            "- 批准后再按 phase 拆成多个 `vega do` / `vega loop` 任务。",
            "",
            "## 建议审批命令",
            "",
            "```powershell",
            "vega decision approve --run <change_plan_run> --type custom --reason \"批准本次 scope 和阶段计划\" --ref change-plan.md",
            "```",
        ]
    ).rstrip() + "\n")


def render_scope_profile(inputs: dict) -> str:
    budget = inputs["budget"]
    return redact_text("\n".join(
        [
            "# Scope Profile",
            "",
            f"- profile：`{inputs['scope_profile']}`",
            "",
            "## Change Budget",
            "",
            f"- 最大变更文件数：`{budget.get('max_changed_files') or '未限制'}`",
            f"- 最大 diff 行数：`{budget.get('max_diff_lines') or '未限制'}`",
            f"- 最大新增文件数：`{budget.get('max_new_files') or '未限制'}`",
            f"- 最大文件大小：`{budget.get('max_file_bytes')}` bytes",
            f"- 禁止新增依赖：`{budget.get('forbid_new_dependencies')}`",
            f"- 禁止大体量生成文件：`{budget.get('forbid_large_generated_files')}`",
        ]
    ).rstrip() + "\n")


def render_phase_plan(inputs: dict) -> str:
    return redact_text("\n".join(
        [
            "# Phase Plan",
            "",
            "## Phase 1：确认边界",
            "",
            "- 阅读 goal 和项目上下文。",
            "- 明确不做事项、兼容性约束和风险路径。",
            "- 如果范围过大，先拆分成更小 phase。",
            "",
            "## Phase 2：最小实现",
            "",
            "- 只实现当前 phase 的最小闭环。",
            "- 避免同时重构无关模块。",
            "- 如果需要新增依赖或跨层改动，先回到人工确认。",
            "",
            "## Phase 3：验证与审查",
            "",
            "- 运行 `.vega.yaml` 或 project profile 中的验证命令。",
            "- 运行 risk gate，确认是否超出预算。",
            "- 使用隔离 reviewer 审查 diff。",
            "",
            "## Phase 4：交付与沉淀",
            "",
            "- 生成 finish report。",
            "- 人工检查 diff 后再自行 commit。",
            "- 仅将可复用经验人工 accept 到 memory。",
        ]
    ).rstrip() + "\n")


def render_risk_plan(inputs: dict) -> str:
    budget_json = json.dumps(inputs["budget"], ensure_ascii=False, indent=2)
    return redact_text("\n".join(
        [
            "# Risk Plan",
            "",
            "## 主要风险",
            "",
            "- goal 过大导致一次性修改过多文件。",
            "- AI 为了完成任务引入不必要抽象或依赖。",
            "- 重构跨越模块边界，reviewer 难以定位真实风险。",
            "- 验证不足时误以为已经完成。",
            "",
            "## Harness",
            "",
            "- 使用 scope profile 限制本次 phase 的变更预算。",
            "- 超预算时升级 human-review，而不是阻止用户继续。",
            "- 每个 phase 单独 finish 和 decision，保留可追溯证据。",
            "",
            "## 当前预算 JSON",
            "",
            "```json",
            budget_json,
            "```",
        ]
    ).rstrip() + "\n")


def _run_change_plan_eval(run_dir: Path) -> list[str]:
    return [f"{'PASS' if (run_dir / artifact).exists() else 'FAIL'}: artifact 存在：{artifact}" for artifact in CHANGE_PLAN_ARTIFACTS]


def _render_eval(results: list[str]) -> str:
    return redact_text("# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n")
