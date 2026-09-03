from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeSideEffectPolicy,
    ExecutionWorkItem,
)
from .agent_contract import AgentPlan, AgentState, GitOidText, StrictAgentModel
from .agent_persistence import load_agent_checkpoint
from .git_read import run_git_bytes
from .redaction import assert_not_sensitive_path, redact_text
from .scope_path_matching import path_matches_pattern


PlanningText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
PlanningShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]
PlanningCommand = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]
PlanningSourceKind = Literal["file", "symbol", "test", "command"]

PLANNING_REQUEST_ARTIFACT = "planning-request.json"
PLANNING_CONTEXT_ARTIFACT = "project-context.md"
PLANNING_PROPOSAL_ARTIFACT = "planning-proposal.json"
PLANNING_REPORT_ARTIFACT = "planning-proposal.md"


class PlanningSourceRef(StrictAgentModel):
    """Planner 对观察事实给出的可追查来源，不替代当前 Git 或验证证据。"""

    kind: PlanningSourceKind
    path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    symbol: PlanningShortText | None = None
    command: PlanningCommand | None = None
    summary: PlanningShortText

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return _normalize_repo_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_shape(self) -> PlanningSourceRef:
        has_lines = self.line_start is not None or self.line_end is not None
        if self.kind in {"file", "symbol", "test"} and self.path is None:
            raise ValueError(f"{self.kind} 引用必须包含仓库相对路径")
        if self.kind == "symbol" and self.symbol is None:
            raise ValueError("symbol 引用必须包含 symbol")
        if self.kind == "command" and self.command is None:
            raise ValueError("command 引用必须包含 command")
        if self.kind != "command" and self.command is not None:
            raise ValueError("只有 command 引用可以包含 command")
        if self.kind == "command" and self.symbol is not None:
            raise ValueError("command 引用不能包含 symbol")
        if has_lines and (
            self.path is None
            or self.line_start is None
            or self.line_end is None
            or self.line_end < self.line_start
        ):
            raise ValueError("行号必须成对出现，且 line_end 不能小于 line_start")
        return self


class PlanningObservedFact(StrictAgentModel):
    statement: PlanningText
    refs: list[PlanningSourceRef] = Field(min_length=1, max_length=12)


class PlanningContractProposal(StrictAgentModel):
    """LLM 提出的合同语义；尚未经过 Contract Compiler，也不能批准。"""

    goal: PlanningText
    acceptance: list[PlanningText] = Field(min_length=1, max_length=32)
    invariants: list[PlanningText] = Field(default_factory=list, max_length=32)
    non_goals: list[PlanningText] = Field(default_factory=list, max_length=32)
    authorized_risk_reviews: list[PlanningShortText] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "兼容字段；Planner 必须返回空数组。"
            "机器风险 ID 由 Contract Compiler 根据仓库策略和候选文件生成。"
        ),
    )
    side_effect_policy: ChangeSideEffectPolicy = Field(
        default_factory=ChangeSideEffectPolicy
    )
    verification_suggestions: list[PlanningCommand] = Field(
        min_length=1,
        max_length=20,
    )
    authority_envelope: ChangeAuthorityEnvelope


class PlanningExecutionPlan(StrictAgentModel):
    work_items: list[ExecutionWorkItem] = Field(min_length=1, max_length=8)
    implementation_strategy: list[PlanningText] = Field(
        default_factory=list,
        max_length=32,
    )
    additional_check_suggestions: list[PlanningCommand] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_work_items(self) -> PlanningExecutionPlan:
        seen: set[str] = set()
        for item in self.work_items:
            if item.work_item_id in seen:
                raise ValueError(f"work_item_id 不能重复：{item.work_item_id}")
            unknown_or_late = set(item.depends_on) - seen
            if unknown_or_late:
                raise ValueError(
                    f"{item.work_item_id} 只能依赖前面已经定义的 Work Item："
                    f"{sorted(unknown_or_late)}"
                )
            seen.add(item.work_item_id)
        return self


class PlanningProposal(StrictAgentModel):
    """只读调查的结构化结果；它是待编译输入，不是 Approved Contract。"""

    task_id: PlanningShortText
    proposal_revision: int = Field(
        default=1,
        ge=1,
        description="初始 Proposal 固定为 1；Planning attempt 不是 Proposal revision",
    )
    user_goal: PlanningText = Field(
        description="必须逐字复制输入中的用户目标，不得概括、改写或补充"
    )
    source_revision: GitOidText
    observed_facts: list[PlanningObservedFact] = Field(min_length=1, max_length=64)
    hypotheses: list[PlanningText] = Field(default_factory=list, max_length=32)
    unresolved_questions: list[PlanningText] = Field(
        default_factory=list,
        max_length=32,
    )
    contract_proposal: PlanningContractProposal
    execution_plan: PlanningExecutionPlan

    @model_validator(mode="after")
    def validate_proposal(self) -> PlanningProposal:
        envelope = self.contract_proposal.authority_envelope
        for item in self.execution_plan.work_items:
            for path in item.likely_files:
                if any(
                    path_matches_pattern(path, pattern)
                    for pattern in envelope.forbidden_paths
                ):
                    raise ValueError(f"候选路径命中禁止范围：{path}")
                if not any(
                    path_matches_pattern(path, pattern)
                    for pattern in envelope.allowed_paths
                ):
                    raise ValueError(f"候选路径越出建议授权范围：{path}")
        return self


class PlanningRequest(StrictAgentModel):
    """自然语言入口冻结的最小任务身份。"""

    task_id: PlanningShortText
    user_goal: PlanningText
    source_revision: GitOidText
    project_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def validate_planning_proposal(
    repo: Path,
    proposal: PlanningProposal,
    *,
    task_id: str,
    user_goal: str,
    source_revision: str,
) -> None:
    """把 Proposal 绑定到当前任务与固定 Git revision，并核对路径引用。"""

    if proposal.task_id != task_id:
        raise ValueError("Planning Proposal 绑定了其他 task_id")
    if proposal.user_goal != user_goal:
        raise ValueError("Planning Proposal 改写了用户原始目标")
    if proposal.source_revision.lower() != source_revision.lower():
        raise ValueError("Planning Proposal 的 source_revision 已漂移")
    for fact in proposal.observed_facts:
        for ref in fact.refs:
            _validate_source_ref(repo, source_revision, ref)


def validate_published_planning_proposal(
    run_dir: Path,
    repo: Path,
    state: AgentState,
    plan: AgentPlan,
    request: PlanningRequest,
) -> PlanningProposal:
    """确认已发布 Proposal、报告、Checkpoint 和 Plan 投影属于同一次发布。"""

    try:
        proposal = PlanningProposal.model_validate_json(
            (run_dir / PLANNING_PROPOSAL_ARTIFACT).read_text(encoding="utf-8")
        )
        report = (run_dir / PLANNING_REPORT_ARTIFACT).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise ValueError("已发布 Planning Proposal 无法读取") from exc
    validate_planning_proposal(
        repo,
        proposal,
        task_id=request.task_id,
        user_goal=request.user_goal,
        source_revision=request.source_revision,
    )
    if report != render_planning_proposal(proposal):
        raise ValueError("Planning Proposal 报告与结构化 Artifact 不一致")
    if state.latest_checkpoint_id is None:
        raise ValueError("Planning Proposal 缺少发布 Checkpoint")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    required_refs = {PLANNING_PROPOSAL_ARTIFACT, PLANNING_REPORT_ARTIFACT}
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.checkpoint_id != state.latest_checkpoint_id
        or (checkpoint.phase, checkpoint.status)
        not in {
            ("planning", "safe"),
            ("needs_human", "blocked"),
            ("stopped", "safe"),
        }
        or checkpoint.current_work_item != state.current_work_item
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.state_version + 1 != state.state_version
        or not required_refs.issubset(checkpoint.evidence_refs)
        or plan.observed_facts != [fact.statement for fact in proposal.observed_facts]
        or plan.hypotheses != proposal.hypotheses
        or plan.unresolved_decisions
        != [
            *proposal.unresolved_questions,
            "Planning Proposal 尚未经过 Contract Compiler",
        ]
    ):
        raise ValueError("Planning Proposal 发布绑定不完整")
    return proposal


def build_planning_prompt(
    *,
    task_id: str,
    user_goal: str,
    source_revision: str,
    project_context: str,
) -> str:
    """构建只读调查指令；结构化输出由 Runner 的 output schema 约束。"""

    return redact_text(
        "\n".join(
            [
                "# Vega 只读调查",
                "",
                f"- task_id：`{task_id}`",
                f"- source revision：`{source_revision}`",
                "",
                "## 用户目标",
                "",
                user_goal,
                "",
                "## 边界",
                "",
                "- 只调查当前固定 revision 的代码、测试、配置和调用关系。",
                "- 不修改文件、Git index、分支或提交，不执行部署、外部写入或数据库写操作。",
                "- task_id、user_goal 和 source_revision 是任务身份字段，必须逐字复制"
                "本指令给出的值；目标摘要只能写入 contract_proposal.goal。",
                "- proposal_revision 固定填写 1；重试次数不是 Proposal revision，"
                "不得随 Planning attempt 增长。",
                "- 已确认事实与根因假设分开写；每条事实至少附一个可追查来源。",
                "- command 引用只记录本轮实际执行的只读命令及结果摘要，不算验证通过证据。",
                "- verification_suggestions、每个 Work Item 的 verification 和 "
                "additional_check_suggestions 只能逐字复制下方“显式验证命令”；"
                "不要添加“运行”“检查”等前缀、Markdown 反引号或自然语言检查项。",
                "- 代码审查、覆盖范围和人工检查建议应写入 acceptance、risk_notes 或 "
                "implementation_strategy，不能伪装成验证命令。",
                "- Contract Compiler 会拒绝任何未在 `.vega.yaml` 登记的命令，"
                "也不会从自然语言中猜测或提取命令。",
                "- authorized_risk_reviews 固定填写空数组。风险 ID 由 Contract Compiler "
                "根据候选文件和 `.vega.yaml` 的 required_reviews 确定；语义风险写入 "
                "Work Item 的 risk_notes。",
                "- Reviewer 打回后还需要再次审查；若允许 N 次 Reviewer 驱动的 Repair，"
                "max_review_rounds 至少应为 N+1，不能把 repair=1、review=1 写成"
                "看似可修复但实际无法复审的预算。",
                "- unresolved_questions 只填写会阻止合同成立、确实需要人工选择的问题。"
                "运行时环境待验证、仓库外状态未知、兼容性风险或可采用 fail-closed 默认值的"
                "内容，应写入 observed_facts、hypotheses、risk_notes、acceptance 或 "
                "non_goals，不能仅因尚未验证就阻止批准。",
                "- 不能确认的事实不要编造；若它不影响合同边界，保留为假设或风险，而不是"
                "机械填入 unresolved_questions。",
                "",
                project_context.rstrip(),
                "",
                "只返回符合 PlanningProposal Schema 的 JSON。",
            ]
        )
        + "\n"
    )


def render_planning_proposal(proposal: PlanningProposal) -> str:
    lines = [
        "# Planning Proposal",
        "",
        f"- Task：`{proposal.task_id}`",
        f"- Revision：`{proposal.source_revision}`",
        f"- 原始目标：{proposal.user_goal}",
        f"- 建议合同目标：{proposal.contract_proposal.goal}",
        "",
        "## 已确认事实",
        "",
    ]
    for fact in proposal.observed_facts:
        lines.append(f"- {fact.statement}")
        lines.extend(f"  - {_render_source_ref(ref)}" for ref in fact.refs)
    lines.extend(["", "## 根因假设", ""])
    lines.extend(f"- {value}" for value in proposal.hypotheses or ["无"])
    lines.extend(["", "## 未决问题", ""])
    lines.extend(f"- {value}" for value in proposal.unresolved_questions or ["无"])
    lines.extend(["", "## 建议范围", ""])
    envelope = proposal.contract_proposal.authority_envelope
    lines.append(f"- 允许：{json.dumps(envelope.allowed_paths, ensure_ascii=False)}")
    lines.append(f"- 禁止：{json.dumps(envelope.forbidden_paths, ensure_ascii=False)}")
    lines.extend(["", "## 建议验证", ""])
    lines.extend(
        f"- `{value}`"
        for value in proposal.contract_proposal.verification_suggestions
    )
    lines.extend(["", "## Work Items", ""])
    for item in proposal.execution_plan.work_items:
        lines.append(f"- `{item.work_item_id}`：{item.objective}")
    lines.extend(
        [
            "",
            "> 这是待编译的只读调查结果，不是 Approved Contract，不能启动 Worker。",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_source_ref(
    repo: Path,
    source_revision: str,
    ref: PlanningSourceRef,
) -> None:
    if ref.path is None:
        return
    assert_not_sensitive_path(ref.path)
    try:
        payload = run_git_bytes(
            repo,
            ["git", "show", f"{source_revision}:{ref.path}"],
        )
    except RuntimeError as exc:
        raise ValueError(f"Planning 引用在 source revision 中不存在：{ref.path}") from exc
    if ref.line_start is None:
        return
    line_count = max(1, len(payload.splitlines()))
    if ref.line_end is None or ref.line_end > line_count:
        raise ValueError(
            f"Planning 引用行号越过文件范围：{ref.path}:{ref.line_start}-{ref.line_end}"
        )


def _normalize_repo_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or normalized.startswith("//")
        or any(part in {"", ".", ".."} for part in path.parts)
        or (len(normalized) >= 2 and normalized[1] == ":")
    ):
        raise ValueError(f"路径必须是仓库相对路径：{value}")
    return path.as_posix()


def _render_source_ref(ref: PlanningSourceRef) -> str:
    location = ref.path or ref.command or ref.kind
    if ref.line_start is not None:
        location += f":{ref.line_start}-{ref.line_end}"
    if ref.symbol is not None:
        location += f" / {ref.symbol}"
    return f"`{location}`：{ref.summary}"
