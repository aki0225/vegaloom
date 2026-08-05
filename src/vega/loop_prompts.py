from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .models import BriefInput, GateResult, ReviewVerdict
from .redaction import redact_text
from .scope_gate import LoopScopeGateEvidence

if TYPE_CHECKING:
    from .workspace_check import WorkspaceCheckResult


@dataclass(frozen=True)
class AssistWorkspaceFailureGuidance:
    current_step: str
    conclusion: str
    fix_prompt: str
    report_untracked_files: bool = False


def render_loop_plan(
    brief_input: BriefInput,
    automation_mode: str,
    max_iterations: int,
) -> str:
    return "\n".join(
        [
            "# Loop Plan",
            "",
            f"- 任务类型：`{brief_input.mode}`",
            f"- 自动化模式：`{automation_mode}`",
            f"- 最大迭代轮数：`{max_iterations}`",
            "",
            "## 流程",
            "",
            "1. 生成 agent brief，编译 AGENTS.md 和已接受 memory。",
            "2. 生成 project-context.md，稳定注入项目画像、验证命令、AGENTS.md 和 accepted memory。",
            "3. auto 模式先确认启动前不存在 tracked diff，再启动 worker。",
            "4. worker 结束后检查工作区污染，超过预算则停止并交给人工判断。",
            "5. 执行 verification 前的精确路径范围门禁；越界 diff 不进入后续流程。",
            "6. 自动执行项目画像识别出的最小验证命令。",
            "7. 验证后再次执行精确路径范围门禁；验证脚本造成越界也不进入 Reflect。",
            "8. Reflect 收集当前 diff、验证日志和复盘材料；其确定性检查失败时停止。",
            "9. Reflect 后再次执行精确路径范围门禁，绑定即将进入 review 的工作区状态。",
            "10. 运行风险/变更预算门禁；需要人工确认时不启动自动 reviewer。",
            "11. 隔离 reviewer 使用只读 runner 审查 review-pack。",
            "12. approve 则生成 final-report；request_changes 则生成 fix-prompt。",
            "",
            "## 禁止动作",
            "",
            "- 不自动 commit / push / release。",
            "- 不自动接受 memory proposal。",
            "- reviewer 只读，不修改目标仓库。",
        ]
    ).rstrip() + "\n"


def build_worker_prompt(
    brief_input: BriefInput,
    brief_run: Path,
    previous_verdict: ReviewVerdict | None,
    iteration: int,
) -> tuple[str, dict[str, str]]:
    brief_text = redact_text(
        brief_run.joinpath("agent-brief.md").read_text(
            encoding="utf-8",
            errors="replace",
        )
    )
    project_context = redact_text(_read_optional_text(brief_run / "project-context.md"))
    lines = [
        "# Worker Prompt",
        "",
        f"你是第 {iteration} 轮 worker，请基于 agent brief 完成最小必要修改。",
        "",
        "硬性约束：",
        "- 只修改满足需求所需的文件。",
        "- 不要 git commit、git push、发布或改长期 memory。",
        "- Vega 会在 worker 返回后独立执行 Runtime 策略中的固定验证命令。",
        "- Worker 自检不得新增或修改 ignored、未跟踪文件或 Git 控制状态；"
        "仓库内的 `.tmp/`、`target/` 等路径也不属于 Workspace Gate 豁免区。",
        "- 不得为了绕过检查把自检产物写到仓库父目录、工作区集合根目录、兄弟仓库或盘符根目录。",
        "- 不要运行带 `{{vega_verification_temp}}` 的 harness-owned 命令，"
        "也不要清理 harness 临时目录。",
        "- 只运行不会额外留下文件或 Git 状态变化的最小自检，并在输出中记录结果；"
        "否则跳过并说明，交给 Vega 固定 verification。",
        "- 如果需求或环境阻塞，停止并明确说明。",
        "",
        "## 项目上下文",
        "",
        project_context or "- 未找到 project-context.md，请基于仓库现状保守判断项目规范。",
        "",
        "## Agent Brief",
        "",
        brief_text,
    ]
    previous_findings = ""
    if previous_verdict:
        previous_findings = render_fix_prompt(previous_verdict, iteration)
        lines.extend(["", "## 上一轮 Review Findings", "", previous_findings])
    prompt = redact_text("\n".join(lines).rstrip() + "\n")
    return prompt, {
        "project_context": project_context,
        "agent_brief": brief_text,
        "previous_findings": previous_findings,
    }


def render_worker_prompt(
    brief_input: BriefInput,
    brief_run: Path,
    previous_verdict: ReviewVerdict | None,
    iteration: int,
) -> str:
    return build_worker_prompt(brief_input, brief_run, previous_verdict, iteration)[0]


def render_fix_prompt(verdict: ReviewVerdict, next_iteration: int) -> str:
    lines = [
        "# Fix Prompt",
        "",
        f"- 下一轮：`{next_iteration}`",
        f"- reviewer 结论：`{verdict.verdict}`",
        f"- 摘要：{verdict.summary}",
        "",
        "请只修复以下被 reviewer 标出的具体问题，不要扩大范围：",
        "",
    ]
    if verdict.findings:
        for finding in verdict.findings:
            location = f"{finding.file}:{finding.line}" if finding.file else "未指定位置"
            lines.extend(
                [
                    f"- [{finding.severity}] {finding.title}",
                    f"  - 位置：`{location}`",
                    f"  - 证据：{finding.evidence or '未提供'}",
                    f"  - 建议：{finding.recommendation or '未提供'}",
                ]
            )
    else:
        lines.append("- reviewer 未给出具体 finding；请人工判断是否继续。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_verification_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：自动验证失败、未执行，或缺少受信的结构化通过证据。",
            "",
            "如存在 `verification-summary.md` 和 `test-summary.md`，请先读取并修复具体失败；",
            "如本轮跳过验证或零命令，请补充可识别的最小验证命令并重新执行。",
            "形成至少一条受信且全部通过的结构化验证前，Vega 不会把 reviewer approve 视为可交付状态。",
        ]
    ).rstrip() + "\n"


def render_workspace_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：worker 结束后工作区完整性检查失败。",
            "",
            "请先读取本轮 `workspace-check.md`：",
            "- 核对新增未跟踪路径、ignored 路径变化、Git HEAD/控制文件变化，"
            "以及启动基线中的已有路径是否被改动或删除。",
            "- 真实需要新增的文件必须纳入 tracked diff 和明确 scope；"
            "其余自检缓存、构建产物或噪声由人工确认后清理或恢复。",
            "- `.tmp/`、`target/` 等普通 ignored 目录不是豁免区；"
            "Vega 只排除明确的 harness-owned 路径。",
            "- Vega 不会自动删除文件、恢复 Git 状态或终止外部进程。",
            "",
            "清理或确认完成后，再运行 `vega loop continue --repo <repo> --run <run>` 继续复盘与审查。",
        ]
    ).rstrip() + "\n"


def render_tracked_baseline_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：启动 auto worker 前已存在 tracked diff。",
            "",
            "Vega 无法可靠区分这些历史修改与本轮 worker 的产物，因此没有启动 worker。",
            "请先人工检查、提交、暂存外部变更到其他分支或恢复无关 diff；确认工作区干净后再重开 auto loop。",
            "如果这些变更本来就是主会话/人工完成的实现，请使用 `vega loop continue` 或独立 reflect/review 流程，",
            "不要把它们归因给新的 auto worker。",
        ]
    ).rstrip() + "\n"


def render_untracked_files_fix_prompt(
    next_iteration: int,
    paths: list[str],
) -> str:
    lines = [
        "# Fix Prompt",
        "",
        f"- 下一轮：`{next_iteration}`",
        "- 阻塞原因：当前工作区存在未跟踪文件，隔离 reviewer 不会读取其内容。",
        "",
        "## 仅路径清单",
        "",
    ]
    lines.extend(f"- `{path}`" for path in paths)
    lines.extend(
        [
            "",
            "请人工确认这些路径属于本次实现，并将需要审查的实现纳入 tracked diff 后再继续。",
            "Vega 不会读取、删除、暂存或提交这些未跟踪文件。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def assist_workspace_failure_guidance(
    result: WorkspaceCheckResult,
    next_iteration: int,
) -> AssistWorkspaceFailureGuidance:
    if result.baseline_head_changed:
        return AssistWorkspaceFailureGuidance(
            current_step="workspace_head_changed",
            conclusion=(
                "Git HEAD 在 loop 启动后发生变化；当前 diff 无法继续归因，"
                "已在 scope gate 前停止。"
            ),
            fix_prompt=render_workspace_fix_prompt(next_iteration),
        )
    if result.new_untracked_count:
        return AssistWorkspaceFailureGuidance(
            current_step="untracked_files",
            conclusion=(
                "当前工作区存在未跟踪文件；reviewer 不读取其内容，"
                "已在 verification 前转人工确认。"
            ),
            fix_prompt=render_untracked_files_fix_prompt(
                next_iteration,
                result.new_untracked_files,
            ),
            report_untracked_files=True,
        )
    return AssistWorkspaceFailureGuidance(
        current_step="workspace_check_failed",
        conclusion="工作区完整性检查失败，未继续 verification、reflect 或 review。",
        fix_prompt=render_workspace_fix_prompt(next_iteration),
    )


def render_scope_gate_fix_prompt(
    next_iteration: int,
    evidence: LoopScopeGateEvidence,
) -> str:
    result = evidence.result
    report_artifact, result_artifact = {
        "pre_verification": ("scope-gate-report.md", "scope-gate-result.json"),
        "post_verification": (
            "scope-gate-post-verification-report.md",
            "scope-gate-post-verification-result.json",
        ),
        "pre_review": (
            "scope-gate-pre-review-report.md",
            "scope-gate-pre-review-result.json",
        ),
    }[result.phase]
    lines = [
        "# Fix Prompt",
        "",
        f"- 下一轮：`{next_iteration}`",
        f"- 门禁阶段：`{result.phase}`",
        "- 阻塞原因：当前 tracked diff 超出 `.vega.yaml` 的精确路径范围。",
        "",
        f"请先阅读本轮 `{report_artifact}` 与 `{result_artifact}`：",
        "- 撤回、拆分或人工确认越界路径；不要通过修改 `.vega.yaml` 绕过本轮门禁。",
        "- 仅保留命中 allowed_paths 且未命中 forbidden_paths 的必要修改。",
        "- Vega 不会自动回滚、暂存、删除或提交 worker 已产生的改动。",
    ]
    if result.violations:
        lines.extend(["", "## 越界路径", ""])
        lines.extend(
            f"- `{violation.code}`：`{violation.path}`"
            for violation in result.violations
        )
    elif result.failure_code:
        lines.extend(["", f"- 门禁异常：`{result.failure_code}`"])
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_risk_gate_fix_prompt(
    next_iteration: int,
    result: GateResult | None,
) -> str:
    lines = [
        "# Fix Prompt",
        "",
        f"- 下一轮：`{next_iteration}`",
        "- 阻塞原因：风险门禁未允许继续自动隔离审查。",
        "",
        "请先阅读本轮 `risk-gate-report.md` 和 `risk-gate-result.json`，再决定是否缩小范围、补测试、",
        "拆分变更或由人工完成审查。Vega 不会自动忽略预算、风险路径或项目要求。",
    ]
    if result:
        lines.extend(
            [
                "",
                "## 门禁结果",
                "",
                f"- 风险：`{result.risk}`",
                f"- 建议：`{result.recommendation}`",
                "- 原因：" + "、".join(reason.code for reason in result.reasons),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_no_diff_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：本轮没有可审查的 tracked diff。",
            "",
            "请确认 worker 是否实际完成了任务；产生明确 diff 后再继续 verification 和隔离 reviewer。",
        ]
    ).rstrip() + "\n"


def _read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")
