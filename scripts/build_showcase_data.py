from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path("examples/evidence")
DEFAULT_OUTPUT = Path("site/data/cases.json")
PUBLIC_AGENT_SOURCES = {
    "release": {
        "label": "v0.2.0 发布说明",
        "path": "docs/RELEASE-NOTES-0.2.0.md",
    },
    "run": {
        "label": "发布验收记录",
        "path": "eval/real-world-runs.md",
    },
    "summary": {
        "label": "发布摘要",
        "path": "docs/RELEASE-SUMMARY-0.2.0.md",
    },
}

_RELEASE = {
    "tag": "v0.2.0",
    "commit": "2fb1bd856df55907a4d3ef1039ea62658b30b2b4",
}
_PROVIDER_FAILURE_RUN_ID = "20260818-221144-agent-resume"
_FINAL_RUN_ID = "20260818-231923-agent-resume"
_SCENARIO_FIELDS = {
    "id",
    "index",
    "label",
    "title",
    "summary",
    "run_ids",
    "duration_ms",
    "result",
    "events",
}
_EVENT_FIELDS = {
    "id",
    "at_ms",
    "type",
    "tone",
    "message",
    "detail",
    "source_refs",
    "status_card",
}
_STATUS_CARD_FIELDS = {
    "phase",
    "work_item",
    "worker",
    "workspace",
    "checkpoint",
    "verification",
    "risk",
    "reviewer",
    "allowed_actions",
    "next_step",
    "finish",
}
_ALLOWED_EVENT_TYPES = {
    "plan",
    "worker",
    "workspace",
    "checkpoint",
    "verification",
    "reviewer",
    "decision",
    "finish",
}
_ALLOWED_EVENT_TONES = {"neutral", "warning", "danger", "success"}
_EXPECTED_SCENARIOS = (
    {
        "id": "handoff-and-provider-failure",
        "index": 1,
        "result": "needs_human",
        "run_ids": (_PROVIDER_FAILURE_RUN_ID,),
        "events": (
            ("partial-wip", "worker", "warning", ("release", "run")),
            ("identity-bound-stop", "checkpoint", "warning", ("release", "run")),
            ("git-task-card", "workspace", "neutral", ("release", "summary", "run")),
            ("fresh-clone-resume", "workspace", "neutral", ("release", "summary", "run")),
            ("provider-429", "worker", "danger", ("release", "summary", "run")),
            ("fail-closed-human", "decision", "warning", ("release", "summary", "run")),
        ),
    },
    {
        "id": "reviewer-rejection-and-replan",
        "index": 2,
        "result": "replanned",
        "run_ids": (_FINAL_RUN_ID,),
        "events": (
            ("front-end-gates-passed", "verification", "success", ("run",)),
            ("reviewer-finding", "reviewer", "danger", ("release", "summary", "run")),
            ("claim-cannot-override-finding", "decision", "warning", ("run",)),
            ("human-replan", "plan", "warning", ("release", "summary", "run")),
            ("plan-revision-2-approved", "plan", "neutral", ("release", "summary", "run")),
            ("resume-with-new-evidence", "decision", "neutral", ("release", "summary", "run")),
        ),
    },
    {
        "id": "evidence-to-finish",
        "index": 3,
        "result": "ready_to_commit",
        "run_ids": (_FINAL_RUN_ID,),
        "events": (
            ("second-child-started", "worker", "neutral", ("run",)),
            ("backend-361-passed", "verification", "success", ("release", "summary", "run")),
            ("settings-7-passed", "verification", "success", ("release", "summary", "run")),
            ("frontend-180-passed", "verification", "success", ("release", "summary", "run")),
            ("core-gates-and-reviewer", "reviewer", "success", ("run",)),
            ("core-finish-ready-to-commit", "finish", "success", ("release", "summary", "run")),
        ),
    },
)

_ALLOWED_STATUS = {"ready_to_commit", "request_changes", "needs_human"}
_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(r"\\\\[^\\\s]+\\[^\\\s]+"),
    re.compile(r"(?<![A-Za-z0-9:])/(?:home|Users)/[^/\s\"'`<>]+(?:/|$)"),
)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|session[_-]?cookie)\s*[:=]"),
)
_UNSAFE_MODEL_CONTENT = (
    re.compile(r"(?i)\bworker prompt\b"),
    re.compile(r"(?i)\bchain[- ]of[- ]thought\b"),
    re.compile(r"中间推理"),
    re.compile(r"原始提示词"),
)

_SOURCE_CHECKS = {
    "pycodestyle-1187-rejection": (
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "reviewer-rejection/verification-summary.md",
            "`768 passed, 5 skipped`",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "reviewer-rejection/verification-summary.md",
            "固定测试通过不代表行为合同完整",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "reviewer-rejection/final-report.md",
            "隔离 reviewer 返回 `request_changes`",
        ),
    ),
    "pycodestyle-1187-success": (
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "success/verification-summary.md",
            "`768 passed, 5 skipped`",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/"
            "success/verification-summary.md",
            "8 个正反断言全部通过",
        ),
        (
            "examples/evidence/real-world-pycodestyle-1187/success/final-report.md",
            "Finish 状态：`ready_to_commit`",
        ),
    ),
    "click-2939-success": (
        (
            "examples/evidence/real-world-click-2939/"
            "success/verification-summary.md",
            "`688 passed, 72 skipped, 1 xfailed`",
        ),
        (
            "examples/evidence/real-world-click-2939/"
            "success/verification-summary.md",
            "独立 oracle",
        ),
        (
            "examples/evidence/real-world-click-2939/success/final-report.md",
            "Finish 状态：`ready_to_commit`",
        ),
    ),
}

_AGENT_SOURCE_ANCHORS = {
    "release": (
        "Worker 在允许范围内形成 WIP，经身份绑定 stop、现场对账和人工副作用裁决后生成 Task Card；",
        "Provider 429 的恢复 attempt 保持 `needs_human`，没有自动重试或虚假成功；",
        "首次完整 Core 被独立 Reviewer 打回：原测试没有覆盖 React 状态提交前的同批次竞态，",
        "人工批准 Plan revision 2 后，新 child 补强同批次回归，重新执行全部门禁并得到",
        "目标仓库最终验证为后端 `361 passed`、设置页 `7 passed`、前端完整 `180 passed`，",
    ),
    "summary": (
        "Git Task Card 可以把 WIP、计划、约束和下一步带到新的 clone；本机 Trace、SQLite 和凭据",
        "Provider 429 attempt 保持 `needs_human`；首次 Reviewer 因测试与后端证据不足选择打回，",
        "Plan revision 2、重新执行与可信 Finish。",
        "最终门禁：后端 `361 passed`、定向前端 `7 passed`、完整前端 `180 passed`、TypeScript、",
        "`ready_to_commit` 仍只表示进入人工提交前检查。",
    ),
    "run": (
        "partial WIP",
        "Git Task Card",
        "恢复 run `20260818-221144-agent-resume` 启动 child",
        "`20260818-221159-167783-bug-loop` 后，Provider 返回 429",
        "provider-failure-fail-closed",
        "Reviewer finding",
        "人工随后批准 Plan revision 2，只增加同批次竞态测试要求与后端验证，",
        "后端完整测试：`361 passed`；",
        "设置页定向测试：`7 passed`；",
        "前端完整测试：`14` 个测试文件、`180 passed`；",
        "agent_run = 20260818-231923-agent-resume",
        "checkpoint = checkpoint-006",
        "terminal_status = ready_to_commit",
    ),
}


def _read_json(relative_path: str) -> dict[str, Any]:
    return json.loads(_read_release_source(relative_path))


def _canonical_json_bytes(value: Any) -> bytes:
    """生成跨平台稳定的 JSON 字节串，用于公开回放证据的完整性校验。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _build_replay_proof(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": "structured-event-replay-v1",
        "event_count": sum(len(scenario["events"]) for scenario in scenarios),
        "duration_ms": sum(scenario["duration_ms"] for scenario in scenarios),
        "sha256": hashlib.sha256(_canonical_json_bytes(scenarios)).hexdigest(),
        "disclosure": (
            "这是发布验收证据编排的低频状态回放，不是原始 Trace，也不是浏览器实时录制。"
        ),
    }


def _status_card(
    *,
    phase: str,
    worker: str,
    workspace: str,
    checkpoint: str,
    verification: str,
    risk: str,
    reviewer: str,
    allowed_actions: list[str],
    next_step: str,
    finish: str,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "work_item": "单项任务",
        "worker": worker,
        "workspace": workspace,
        "checkpoint": checkpoint,
        "verification": verification,
        "risk": risk,
        "reviewer": reviewer,
        "allowed_actions": allowed_actions,
        "next_step": next_step,
        "finish": finish,
    }


def _replay_event(
    event_id: str,
    at_ms: int,
    event_type: str,
    tone: str,
    message: str,
    detail: str,
    source_refs: list[str],
    status_card: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": event_id,
        "at_ms": at_ms,
        "type": event_type,
        "tone": tone,
        "message": message,
        "detail": detail,
        "source_refs": source_refs,
        "status_card": status_card,
    }


def _source_links(base_path: str) -> list[dict[str, str]]:
    labels = (
        ("diff", "实际 Diff", "diff.patch"),
        ("verification", "验证摘要", "verification-summary.md"),
        ("review", "Reviewer 结论", "review-verdict.json"),
        ("gate", "Gate 结果", "gate-result.json"),
        ("finish", "最终报告", "final-report.md"),
    )
    return [
        {
            "kind": kind,
            "label": label,
            "path": f"{base_path}/{filename}",
        }
        for kind, label, filename in labels
    ]


def _scope_summary(gate: dict[str, Any]) -> str:
    scope_gate = gate["scope_gate"]
    stages = ("pre_verification", "post_verification", "pre_review")
    changed_files = sorted(
        {
            path
            for stage in stages
            for path in scope_gate[stage]["changed_files"]
        }
    )
    if any(scope_gate[stage]["status"] != "success" for stage in stages):
        return "至少一个 Scope Gate 未通过。"
    return (
        "pre-verification、post-verification、pre-review 均通过；"
        f"变更仅涉及 {', '.join(changed_files)}。"
    )


def _risk_summary(gate: dict[str, Any]) -> str:
    risk_gate = gate["risk_gate"]
    return (
        f"{risk_gate['status']} · {risk_gate['risk']} · "
        f"{risk_gate['recommendation']}"
    )


def build_payload() -> dict[str, Any]:
    """从已提交的脱敏证据包生成展示数据。"""
    rejection_base = (
        "examples/evidence/real-world-pycodestyle-1187/reviewer-rejection"
    )
    success_base = "examples/evidence/real-world-pycodestyle-1187/success"
    click_base = "examples/evidence/real-world-click-2939/success"

    rejection_review = _read_json(f"{rejection_base}/review-verdict.json")
    rejection_gate = _read_json(f"{rejection_base}/gate-result.json")
    rejection_finding = rejection_review["findings"][0]

    success_review = _read_json(f"{success_base}/review-verdict.json")
    success_gate = _read_json(f"{success_base}/gate-result.json")

    click_review = _read_json(f"{click_base}/review-verdict.json")
    click_gate = _read_json(f"{click_base}/gate-result.json")

    cases = [
        {
            "id": "pycodestyle-1187-rejection",
            "kind": "Reviewer 阻断",
            "title": "pycodestyle #1187",
            "subtitle": "768 项测试通过，仍发现语义回归",
            "issue_url": "https://github.com/PyCQA/pycodestyle/issues/1187",
            "status": "request_changes",
            "status_label": "测试全绿，但不能交付",
            "summary": (
                "初始补丁修复了反向内建类型比较，却删除了原有歧义场景豁免。"
                "固定测试全部通过，隔离 Reviewer 仍识别出新的 E721 误报。"
            ),
            "diff": {
                "file": "pycodestyle.py",
                "summary": "1 个文件 · 删除 3 行保护逻辑",
                "excerpt": (
                    "-        inst = match.group(1)\n"
                    "-        if inst and inst.isidentifier() and inst not in SINGLETONS:\n"
                    "-            return  # Allow comparison for types which are not obvious"
                ),
            },
            "verification": {
                "headline": "768 passed, 5 skipped",
                "checks": [
                    "初始定向 oracle 能识别反向内建类型比较。",
                    "三阶段 Scope Gate 只检测到 pycodestyle.py。",
                    "固定测试没有覆盖普通小写变量的歧义比较。",
                ],
            },
            "review": {
                "verdict": rejection_review["verdict"],
                "severity": rejection_finding["severity"],
                "title": rejection_finding["title"],
                "evidence": rejection_finding["evidence"],
                "recommendation": rejection_finding["recommendation"],
            },
            "gates": {
                "scope": _scope_summary(rejection_gate),
                "risk": _risk_summary(rejection_gate),
                "finish": "needs_human · 保留候选现场 · 未自动提交",
            },
            "source_links": _source_links(rejection_base),
            "limitations": [
                "公开包是脱敏摘要，不包含 Worker 或 Reviewer 的完整会话。",
                "该案例证明 Reviewer 阻止了已知语义回归，不构成通用缺陷发现率。",
            ],
        },
        {
            "id": "pycodestyle-1187-success",
            "kind": "修正后通过",
            "title": "pycodestyle #1187",
            "subtitle": "补齐正反行为合同后的新候选",
            "issue_url": "https://github.com/PyCQA/pycodestyle/issues/1187",
            "status": "ready_to_commit",
            "status_label": "证据完整，可人工检查",
            "summary": (
                "新的隔离候选保留普通小写变量豁免，只对明确的内建类型或类型命名报告 E721；"
                "正反行为、静态检查、范围门禁和独立评审均通过。"
            ),
            "diff": {
                "file": "pycodestyle.py",
                "summary": "1 个文件 · 明确类型识别边界",
                "excerpt": (
                    "-        inst = match.group(1)\n"
                    "+        compared = match.group(1)\n"
                    "+        inst = match.group(2)\n"
                    "         if inst and inst.isidentifier() and inst not in SINGLETONS:\n"
                    "-            return  # Allow comparison for types which are not obvious\n"
                    "+            if not compared:\n"
                    "+                return  # Allow comparison for types which are not obvious\n"
                    "+            if (\n"
                    "+                    compared not in BUILTIN_TYPE_NAMES and\n"
                    "+                    not compared.lstrip('_')[:1].isupper()):\n"
                    "+                return  # Allow comparison for types which are not obvious"
                ),
            },
            "verification": {
                "headline": "768 passed, 5 skipped",
                "checks": [
                    "8 个正反行为断言全部通过。",
                    "compileall、pycodestyle 自检与 git diff --check 通过。",
                    "Scope Gate 始终只检测到 pycodestyle.py。",
                ],
            },
            "review": {
                "verdict": success_review["verdict"],
                "severity": "none",
                "title": "明确行为合同得到满足",
                "evidence": success_review["summary"],
                "recommendation": "人工检查最终 Diff 后再决定是否提交。",
            },
            "gates": {
                "scope": _scope_summary(success_gate),
                "risk": _risk_summary(success_gate),
                "finish": "ready_to_commit · 未自动提交",
            },
            "source_links": _source_links(success_base),
            "limitations": [
                "这是新的隔离候选，不是对原拒绝现场的自动覆盖或静默续跑。",
                "公开 Git 历史不能独立证明 follow-up 行为合同的预注册顺序。",
            ],
        },
        {
            "id": "click-2939-success",
            "kind": "完整验证通过",
            "title": "Click #2939",
            "subtitle": "真实 Diff、独立 oracle 与完整测试",
            "issue_url": "https://github.com/pallets/click/issues/2939",
            "status": "ready_to_commit",
            "status_label": "证据完整，可人工检查",
            "summary": (
                "Worker 将 stdin 文件迭代与 prompt EOF 的语义分离；独立 oracle、完整测试、"
                "三阶段范围门禁和只读 Reviewer 共同约束最终结论。"
            ),
            "diff": {
                "file": "src/click/testing.py",
                "summary": "2 个预注册文件 · 修复 EOF 语义",
                "excerpt": (
                    "+        def input_readline() -> str:\n"
                    "+            line = text_input.readline()\n"
                    "+\n"
                    '+            if line == "":\n'
                    "+                raise EOFError()"
                ),
            },
            "verification": {
                "headline": "688 passed, 72 skipped, 1 xfailed",
                "checks": [
                    "基线独立 oracle 在 Click 8.2.1 上稳定失败。",
                    "修复后 stdin 链式迭代正常结束且不输出 Aborted!。",
                    "prompt EOF 仍保持既有中止语义，git diff --check 通过。",
                ],
            },
            "review": {
                "verdict": click_review["verdict"],
                "severity": "none",
                "title": "stdin 迭代与 prompt EOF 语义已分离",
                "evidence": "；".join(click_review["checked_items"]),
                "recommendation": "人工复核两个允许文件的 Diff 后再决定是否提交。",
            },
            "gates": {
                "scope": _scope_summary(click_gate),
                "risk": _risk_summary(click_gate),
                "finish": "ready_to_commit · 未自动提交",
            },
            "source_links": _source_links(click_base),
            "limitations": [
                "公开包不包含原始运行目录、完整模型输出或 prompt 全文。",
                "单案例不能外推为任意仓库或任意任务的成功率。",
            ],
        },
    ]

    scenarios = [
        {
            "id": "handoff-and-provider-failure",
            "index": 1,
            "label": "中断后换 clone",
            "title": "partial WIP、Task Card 和 Provider 429",
            "summary": (
                "Writer 修改了 2 个批准文件后停止。Vega 生成 Task Card，并在 fresh clone "
                "恢复。第一个恢复 child 收到 429，状态停在 needs_human。"
            ),
            "run_ids": [_PROVIDER_FAILURE_RUN_ID],
            "duration_ms": 54_000,
            "result": "needs_human",
            "events": [
                _replay_event(
                    "partial-wip",
                    0,
                    "worker",
                    "warning",
                    "Writer 修改了 2 个批准文件",
                    "这是 partial WIP。尚未执行验证，不能进入 Finish。",
                    ["release", "run"],
                    _status_card(
                        phase="acting",
                        worker="active",
                        workspace="allowed_diff",
                        checkpoint="not_created",
                        verification="not_run",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["stop", "checkpoint"],
                        next_step="按 child 与 operation 身份停止 Writer。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "identity-bound-stop",
                    9_000,
                    "checkpoint",
                    "warning",
                    "停止 Writer 并核对工作区",
                    "进程已停止，Diff 原样保留。人工完成副作用裁决后生成交接记录。",
                    ["release", "run"],
                    _status_card(
                        phase="stopped",
                        worker="stopped",
                        workspace="reconciled",
                        checkpoint="created",
                        verification="not_run",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["handoff", "inspect_workspace"],
                        next_step="生成可移植的 Git Task Card。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "git-task-card",
                    18_000,
                    "workspace",
                    "neutral",
                    "生成 Git Task Card",
                    "Task Card 记录 committed WIP、Plan、约束和下一步。本机 State、Trace 与聊天不进入 Git。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="handoff_ready",
                        worker="stopped",
                        workspace="git_task_card_created",
                        checkpoint="handoff_created",
                        verification="historical",
                        risk="historical",
                        reviewer="historical",
                        allowed_actions=["resume"],
                        next_step="在新的隔离 clone 中重建任务现场。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "fresh-clone-resume",
                    29_000,
                    "workspace",
                    "neutral",
                    "在 fresh clone 读取 WIP 和 Task Card",
                    "新 run 重建 Goal、Plan、Work Item 和比较基线。旧门禁标记为 historical。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="resumed",
                        worker="starting",
                        workspace="fresh_clone",
                        checkpoint="historical",
                        verification="not_run",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["start_worker"],
                        next_step="启动恢复后的第一条 child。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "provider-429",
                    41_000,
                    "worker",
                    "danger",
                    "恢复 child 收到 Provider 429",
                    "Runner 返回非零，且没有 Worker Claim。外部副作用状态为 unknown。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="needs_human",
                        worker="stopped",
                        workspace="not_disclosed",
                        checkpoint="not_disclosed",
                        verification="not_run",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["inspect_side_effects", "human_decision"],
                        next_step="人工裁决未知副作用并决定后续动作。",
                        finish="needs_human",
                    ),
                ),
                _replay_event(
                    "fail-closed-human",
                    54_000,
                    "decision",
                    "warning",
                    "Supervisor 返回 needs_human",
                    "当前 attempt 不会自动重试，也不会启动第二个 Writer。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="needs_human",
                        worker="stopped",
                        workspace="not_disclosed",
                        checkpoint="not_disclosed",
                        verification="not_run",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["human_decision"],
                        next_step="保留现场，等待人工决定是否重新执行。",
                        finish="needs_human",
                    ),
                ),
            ],
        },
        {
            "id": "reviewer-rejection-and-replan",
            "index": 2,
            "label": "Reviewer 要求补测试",
            "title": "前端检查通过，但证据不够",
            "summary": (
                "4 项前端检查通过。Reviewer 发现同批次竞态没有覆盖，Plan 里也缺少项目要求的"
                "后端测试。Plan revision 2 增加这两项验证，允许修改文件不变。"
            ),
            "run_ids": [_FINAL_RUN_ID],
            "duration_ms": 50_000,
            "result": "replanned",
            "events": [
                _replay_event(
                    "front-end-gates-passed",
                    0,
                    "verification",
                    "success",
                    "4 项前端检查通过",
                    "这些检查没有覆盖同一 React 批次内的重复提交。",
                    ["run"],
                    _status_card(
                        phase="reviewing",
                        worker="stopped",
                        workspace="allowed_diff",
                        checkpoint="not_disclosed",
                        verification="passed",
                        risk="not_disclosed",
                        reviewer="not_disclosed",
                        allowed_actions=["review"],
                        next_step="由独立只读 Reviewer 检查 Diff 与测试证据。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "reviewer-finding",
                    9_000,
                    "reviewer",
                    "danger",
                    "Reviewer 要求补同批次回归和后端测试",
                    "当前证据无法说明同步 ref 锁是否必要，Plan 也没有运行项目要求的后端测试。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="needs_human",
                        worker="stopped",
                        workspace="allowed_diff",
                        checkpoint="not_disclosed",
                        verification="passed",
                        risk="not_disclosed",
                        reviewer="needs_human",
                        allowed_actions=["replan", "human_decision"],
                        next_step="人工修订 Plan 后再启动新的 child。",
                        finish="needs_human",
                    ),
                ),
                _replay_event(
                    "claim-cannot-override-finding",
                    19_000,
                    "decision",
                    "warning",
                    "Supervisor 忽略 completed Claim",
                    "Reviewer 已记录 finding，因此当前状态保持 needs_human。",
                    ["run"],
                    _status_card(
                        phase="needs_human",
                        worker="stopped",
                        workspace="allowed_diff",
                        checkpoint="not_disclosed",
                        verification="passed",
                        risk="not_disclosed",
                        reviewer="needs_human",
                        allowed_actions=["replan"],
                        next_step="记录 finding 并请求人工调整验证合同。",
                        finish="needs_human",
                    ),
                ),
                _replay_event(
                    "human-replan",
                    29_000,
                    "plan",
                    "warning",
                    "Plan revision 2 增加两项验证",
                    "新增同批次竞态回归和后端测试命令。允许修改的 2 个产品文件不变。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="planning",
                        worker="stopped",
                        workspace="allowed_diff",
                        checkpoint="not_disclosed",
                        verification="historical",
                        risk="historical",
                        reviewer="historical",
                        allowed_actions=["approve_plan"],
                        next_step="审核 Plan revision 2。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "plan-revision-2-approved",
                    39_000,
                    "plan",
                    "neutral",
                    "Plan revision 2 已批准",
                    "仍然只有 1 个 Work Item 和 1 个 Writer。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="awaiting_execution",
                        worker="not_started",
                        workspace="approved_scope",
                        checkpoint="not_disclosed",
                        verification="not_run",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["start_worker"],
                        next_step="在 revision 2 下启动新的 child。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "resume-with-new-evidence",
                    50_000,
                    "decision",
                    "neutral",
                    "旧门禁结果标记为 historical",
                    "Verification、Risk 和 Reviewer 将在当前工作区重新运行。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="replanned",
                        worker="not_started",
                        workspace="approved_scope",
                        checkpoint="not_disclosed",
                        verification="not_run",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["start_worker"],
                        next_step="按 revision 2 开始新的完整执行。",
                        finish="not_run",
                    ),
                ),
            ],
        },
        {
            "id": "evidence-to-finish",
            "index": 3,
            "label": "重新跑完验证",
            "title": "后端 361、定向 7、前端 180",
            "summary": (
                "revision 2 的 child 只修改批准的测试文件。后端、设置页和完整前端测试通过后，"
                "Risk Gate、Reviewer 和 Core Finish 依次运行。"
            ),
            "run_ids": [_FINAL_RUN_ID],
            "duration_ms": 65_000,
            "result": "ready_to_commit",
            "events": [
                _replay_event(
                    "second-child-started",
                    0,
                    "worker",
                    "neutral",
                    "启动 revision 2 child",
                    "child 在同一个 React act 批次内补充重复提交和交叉提交回归。",
                    ["run"],
                    _status_card(
                        phase="acting",
                        worker="active",
                        workspace="approved_scope",
                        checkpoint="not_disclosed",
                        verification="running",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["wait_for_verification"],
                        next_step="完成 revision 2 的验证合同。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "backend-361-passed",
                    11_000,
                    "verification",
                    "success",
                    "后端完整测试：361 passed",
                    "这是目标仓库 AGENTS.md 要求的后端测试。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="verifying",
                        worker="stopped",
                        workspace="allowed_diff",
                        checkpoint="not_disclosed",
                        verification="backend_passed",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["run_settings_tests"],
                        next_step="运行设置页定向测试。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "settings-7-passed",
                    22_000,
                    "verification",
                    "success",
                    "设置页定向测试：7 passed",
                    "7 个用例包含同批次重复提交和交叉提交。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="verifying",
                        worker="stopped",
                        workspace="allowed_diff",
                        checkpoint="not_disclosed",
                        verification="settings_passed",
                        risk="not_run",
                        reviewer="not_run",
                        allowed_actions=["run_frontend_suite"],
                        next_step="运行完整前端测试。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "frontend-180-passed",
                    34_000,
                    "verification",
                    "success",
                    "前端完整测试：180 passed",
                    "14 个测试文件通过。类型检查、隔离构建和 git diff --check 也通过。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="verifying",
                        worker="stopped",
                        workspace="reconciled",
                        checkpoint="not_disclosed",
                        verification="passed",
                        risk="running",
                        reviewer="not_run",
                        allowed_actions=["run_risk_gate"],
                        next_step="完成 Risk Gate 并交给 Reviewer。",
                        finish="not_run",
                    ),
                ),
                _replay_event(
                    "core-gates-and-reviewer",
                    47_000,
                    "reviewer",
                    "success",
                    "Risk Gate passed，Reviewer approve",
                    "Reviewer 复核新增回归后没有报告 finding。",
                    ["run"],
                    _status_card(
                        phase="finalizing",
                        worker="stopped",
                        workspace="reconciled",
                        checkpoint="not_disclosed",
                        verification="passed",
                        risk="passed",
                        reviewer="approve",
                        allowed_actions=["finalize"],
                        next_step="由 Core Finish 校验 Artifact 与证据新鲜度。",
                        finish="pending",
                    ),
                ),
                _replay_event(
                    "core-finish-ready-to-commit",
                    65_000,
                    "finish",
                    "success",
                    "Core Finish 返回 ready_to_commit",
                    "Verification、Risk、Reviewer、Artifact 完整性和证据新鲜度均通过。Git 提交仍由人工执行。",
                    ["release", "summary", "run"],
                    _status_card(
                        phase="completed",
                        worker="stopped",
                        workspace="reconciled",
                        checkpoint="checkpoint-006",
                        verification="passed",
                        risk="passed",
                        reviewer="approve",
                        allowed_actions=["inspect_evidence", "human_commit"],
                        next_step="人工检查 Diff 与发布验收证据后决定是否提交。",
                        finish="ready_to_commit",
                    ),
                ),
            ],
        },
    ]

    agent_replay = {
        "id": "echo-vault-concurrency-resume",
        "kind": "v0.2.0 发布验收",
        "title": "设置页并发竞态",
        "final_run_id": _FINAL_RUN_ID,
        "related_run_ids": [_PROVIDER_FAILURE_RUN_ID],
        "terminal_status": "ready_to_commit",
        "summary": (
            "设置页并发缺陷的发布验收。多个 run 串联 partial WIP、Git Task Card、fresh clone、"
            "Provider 429、Reviewer 打回和 Plan revision 2。"
        ),
        "release": dict(_RELEASE),
        "scenarios": scenarios,
        "proof": _build_replay_proof(scenarios),
        "source_links": [
            {"kind": kind, **source}
            for kind, source in PUBLIC_AGENT_SOURCES.items()
        ],
        "limitations": [
            "该运行只有 1 个 Work Item 和 1 个 Writer。",
            "前序 Handoff 的公开记录未列出 Agent run ID；场景 1 的 Run 引用从 Provider 429 开始。",
            "公开文件不包含完整 state、Trace、命令日志或模型会话。",
            "页面 SHA-256 只覆盖三个 scenarios 的规范化 JSON，不是完整 cases.json 或源证据文件的哈希。",
            "本案例没有覆盖多 Work Item、操作系统隔离、Provider 稳定性或无人值守运行。",
        ],
    }

    return {
        "schema_version": 4,
        "generated_from": [
            "examples/evidence/real-world-pycodestyle-1187",
            "examples/evidence/real-world-click-2939",
            "docs/RELEASE-NOTES-0.2.0.md",
            "docs/RELEASE-SUMMARY-0.2.0.md",
            "eval/real-world-runs.md",
        ],
        "evidence_through": "2026-08-19",
        "agent_replay": agent_replay,
        "cases": cases,
    }


def _validate_source_path(
    relative_path: str,
    *,
    allow_agent_source: bool = False,
) -> Path:
    if Path(relative_path).is_absolute():
        raise ValueError("公开证据路径必须是仓库相对路径")
    resolved = (REPO_ROOT / relative_path).resolve()
    evidence_root = (REPO_ROOT / EVIDENCE_ROOT).resolve()
    approved_agent_sources = {
        (REPO_ROOT / source["path"]).resolve()
        for source in PUBLIC_AGENT_SOURCES.values()
    }
    in_evidence_root = resolved.is_relative_to(evidence_root)
    is_approved_agent_source = resolved in approved_agent_sources
    if not in_evidence_root and not (
        allow_agent_source and is_approved_agent_source
    ):
        raise ValueError(f"公开证据路径越过允许目录：{relative_path}")
    if not resolved.is_file():
        raise ValueError(f"公开证据文件不存在：{relative_path}")
    return resolved


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"读取发布证据失败：git {' '.join(args)}；{detail}")
    return result.stdout


@lru_cache(maxsize=1)
def _validate_release_ref() -> None:
    actual_commit = _run_git("rev-list", "-n", "1", _RELEASE["tag"]).strip()
    if actual_commit != _RELEASE["commit"]:
        raise ValueError(
            "发布证据 Tag 指向的提交与人工核准清单不一致："
            f"{_RELEASE['tag']} -> {actual_commit}"
        )


@lru_cache(maxsize=None)
def _read_release_source(
    relative_path: str,
    *,
    allow_agent_source: bool = False,
) -> str:
    """读取页面实际链接的发布 Tag 内容，避免用当前工作区替代固定证据。"""
    _validate_source_path(
        relative_path,
        allow_agent_source=allow_agent_source,
    )
    _validate_release_ref()
    return _run_git("show", f"{_RELEASE['tag']}:{relative_path}")


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _validate_exact_keys(
    value: Any,
    expected_fields: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    actual_fields = set(value)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        unexpected = sorted(actual_fields - expected_fields)
        raise ValueError(
            f"{label} 字段不匹配：缺少 {missing}；额外 {unexpected}"
        )
    return value


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 4:
        raise ValueError("展示案例 schema_version 必须为 4")

    agent_replay = _validate_exact_keys(
        payload.get("agent_replay"),
        {
            "id",
            "kind",
            "title",
            "final_run_id",
            "related_run_ids",
            "terminal_status",
            "summary",
            "release",
            "scenarios",
            "proof",
            "source_links",
            "limitations",
        },
        "Agent 回放",
    )
    for field in {
        "id",
        "kind",
        "title",
        "final_run_id",
        "terminal_status",
        "summary",
    }:
        if not _is_nonempty_string(agent_replay[field]):
            raise ValueError(f"Agent 回放字段必须是非空字符串：{field}")
    if agent_replay["final_run_id"] != _FINAL_RUN_ID:
        raise ValueError("Agent 回放必须绑定 v0.2.0 最终验收 run")
    if agent_replay["related_run_ids"] != [_PROVIDER_FAILURE_RUN_ID]:
        raise ValueError("Agent 回放必须披露 Provider 失败的关联 run")
    if agent_replay["terminal_status"] != "ready_to_commit":
        raise ValueError("Agent 回放终态被改写")

    release = _validate_exact_keys(
        agent_replay["release"],
        {"tag", "commit"},
        "Agent 回放 release",
    )
    if release != _RELEASE:
        raise ValueError("Agent 回放 release 必须匹配 v0.2.0 发布提交")

    replay_sources = agent_replay["source_links"]
    expected_sources = [
        {"kind": kind, **source}
        for kind, source in PUBLIC_AGENT_SOURCES.items()
    ]
    if replay_sources != expected_sources:
        raise ValueError("Agent 回放证据链接必须匹配人工核准清单")
    for source in replay_sources:
        _validate_source_path(source["path"], allow_agent_source=True)

    limitations = agent_replay["limitations"]
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not _is_nonempty_string(item) for item in limitations)
    ):
        raise ValueError("Agent 回放必须披露证据限制")

    for source_key, anchors in _AGENT_SOURCE_ANCHORS.items():
        source_path = PUBLIC_AGENT_SOURCES[source_key]["path"]
        source_text = _read_release_source(
            source_path,
            allow_agent_source=True,
        )
        for anchor in anchors:
            if anchor not in source_text:
                raise ValueError(
                    f"Agent 回放来源证据缺失 {source_key} 锚点：{anchor!r}"
                )

    scenarios = agent_replay["scenarios"]
    if not isinstance(scenarios, list) or len(scenarios) != len(_EXPECTED_SCENARIOS):
        raise ValueError("Agent 回放必须恰好包含 3 段发布验收场景")

    seen_event_ids: set[str] = set()
    total_event_count = 0
    total_duration_ms = 0
    for scenario, expected_scenario in zip(scenarios, _EXPECTED_SCENARIOS, strict=True):
        scenario = _validate_exact_keys(scenario, _SCENARIO_FIELDS, "回放场景")
        for field in {"id", "label", "title", "summary", "result"}:
            if not _is_nonempty_string(scenario[field]):
                raise ValueError(f"回放场景字段必须是非空字符串：{field}")
        if type(scenario["index"]) is not int:
            raise ValueError("回放场景 index 必须是整数")
        if type(scenario["duration_ms"]) is not int or scenario["duration_ms"] <= 0:
            raise ValueError("回放场景 duration_ms 必须是正整数")
        if scenario["id"] != expected_scenario["id"]:
            raise ValueError("回放场景顺序与发布验收不一致")
        if scenario["index"] != expected_scenario["index"]:
            raise ValueError("回放场景 index 与发布验收不一致")
        if scenario["result"] != expected_scenario["result"]:
            raise ValueError("回放场景结果与发布验收不一致")
        run_ids = scenario["run_ids"]
        if (
            not isinstance(run_ids, list)
            or any(not _is_nonempty_string(run_id) for run_id in run_ids)
            or run_ids != list(expected_scenario["run_ids"])
        ):
            raise ValueError("回放场景 run_ids 与公开发布验收不一致")

        events = scenario["events"]
        expected_events = expected_scenario["events"]
        if not isinstance(events, list) or len(events) != len(expected_events):
            raise ValueError("每段发布验收场景必须保留 6 个事件")

        previous_at_ms: int | None = None
        for event, expected_event in zip(events, expected_events, strict=True):
            event = _validate_exact_keys(event, _EVENT_FIELDS, "回放事件")
            for field in {"id", "message", "detail"}:
                if not _is_nonempty_string(event[field]):
                    raise ValueError(f"回放事件字段必须是非空字符串：{field}")
            if type(event["at_ms"]) is not int:
                raise ValueError("回放事件 at_ms 必须是整数")
            if event["at_ms"] < 0:
                raise ValueError("回放事件 at_ms 不能为负数")
            if previous_at_ms is None:
                if event["at_ms"] != 0:
                    raise ValueError("每段回放的首个事件必须从 0ms 开始")
            elif event["at_ms"] <= previous_at_ms:
                raise ValueError("回放事件 at_ms 必须严格递增")
            previous_at_ms = event["at_ms"]

            event_id, event_type, event_tone, expected_refs = expected_event
            if event["id"] != event_id:
                raise ValueError("回放事件顺序与发布验收不一致")
            if event["id"] in seen_event_ids:
                raise ValueError(f"回放事件 id 重复：{event['id']}")
            seen_event_ids.add(event["id"])
            if event["type"] not in _ALLOWED_EVENT_TYPES:
                raise ValueError(f"回放事件 type 不受支持：{event['type']}")
            if event["tone"] not in _ALLOWED_EVENT_TONES:
                raise ValueError(f"回放事件 tone 不受支持：{event['tone']}")
            if event["type"] != event_type or event["tone"] != event_tone:
                raise ValueError("回放事件类型或风险语义与发布验收不一致")

            source_refs = event["source_refs"]
            if (
                not isinstance(source_refs, list)
                or not source_refs
                or any(type(source_ref) is not str for source_ref in source_refs)
                or len(source_refs) != len(set(source_refs))
            ):
                raise ValueError("回放事件 source_refs 必须是非空且不重复的字符串数组")
            if any(source_ref not in PUBLIC_AGENT_SOURCES for source_ref in source_refs):
                raise ValueError("回放事件 source_refs 只能引用人工核准的 PUBLIC_AGENT_SOURCES key")
            if source_refs != list(expected_refs):
                raise ValueError("回放事件 source_refs 与发布验收来源不一致")

            status_card = _validate_exact_keys(
                event["status_card"],
                _STATUS_CARD_FIELDS,
                "回放事件 status_card",
            )
            for field in _STATUS_CARD_FIELDS - {"allowed_actions"}:
                if not _is_nonempty_string(status_card[field]):
                    raise ValueError(f"status_card 字段必须是非空字符串：{field}")
            allowed_actions = status_card["allowed_actions"]
            if (
                not isinstance(allowed_actions, list)
                or not allowed_actions
                or any(not _is_nonempty_string(action) for action in allowed_actions)
            ):
                raise ValueError("status_card allowed_actions 必须是非空字符串数组")

        if previous_at_ms != scenario["duration_ms"]:
            raise ValueError("回放场景最后事件的 at_ms 必须等于 duration_ms")
        total_event_count += len(events)
        total_duration_ms += scenario["duration_ms"]

    handoff = scenarios[0]
    if (
        "429" not in handoff["events"][4]["message"]
        or handoff["events"][-1]["status_card"]["finish"] != "needs_human"
    ):
        raise ValueError("Provider 429 必须保持 fail-closed / needs_human")
    replan = scenarios[1]
    if (
        replan["events"][1]["status_card"]["reviewer"] != "needs_human"
        or "Plan revision 2" not in replan["events"][4]["message"]
    ):
        raise ValueError("Reviewer 打回与 Plan revision 2 语义被改写")
    finish = scenarios[2]
    finish_text = " ".join(
        [event["message"] + " " + event["detail"] for event in finish["events"]]
    )
    if (
        not all(value in finish_text for value in ("361 passed", "7 passed", "180 passed"))
        or finish["events"][-1]["status_card"]["finish"] != "ready_to_commit"
    ):
        raise ValueError("验证证据或 Core Finish 终态被改写")

    proof = _validate_exact_keys(
        agent_replay["proof"],
        {"format", "event_count", "duration_ms", "sha256", "disclosure"},
        "Agent 回放 proof",
    )
    if proof["format"] != "structured-event-replay-v1":
        raise ValueError("Agent 回放 proof.format 不受支持")
    if type(proof["event_count"]) is not int or proof["event_count"] != total_event_count:
        raise ValueError("Agent 回放 proof.event_count 与场景事件数不一致")
    if type(proof["duration_ms"]) is not int or proof["duration_ms"] != total_duration_ms:
        raise ValueError("Agent 回放 proof.duration_ms 与场景时长不一致")
    expected_sha256 = hashlib.sha256(_canonical_json_bytes(scenarios)).hexdigest()
    if proof["sha256"] != expected_sha256:
        raise ValueError("Agent 回放 proof.sha256 与 scenarios 规范 JSON 不一致")
    disclosure = proof["disclosure"]
    required_disclosure = (
        "发布验收证据编排",
        "低频状态回放",
        "不是原始 Trace",
        "不是浏览器实时录制",
    )
    if not _is_nonempty_string(disclosure) or not all(
        phrase in disclosure for phrase in required_disclosure
    ):
        raise ValueError("Agent 回放 proof.disclosure 必须说明低频证据回放边界")

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("展示站 V2 必须恰好保留 3 个审查案例")

    expected_status = {
        "pycodestyle-1187-rejection": "request_changes",
        "pycodestyle-1187-success": "ready_to_commit",
        "click-2939-success": "ready_to_commit",
    }
    seen_ids: set[str] = set()
    required_fields = {
        "id",
        "kind",
        "title",
        "subtitle",
        "issue_url",
        "status",
        "status_label",
        "summary",
        "diff",
        "verification",
        "review",
        "gates",
        "source_links",
        "limitations",
    }

    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("每个案例必须是对象")
        missing = sorted(required_fields - case.keys())
        if missing:
            raise ValueError(f"案例缺少字段：{missing}")

        case_id = case["id"]
        if case_id in seen_ids:
            raise ValueError(f"案例 id 重复：{case_id}")
        seen_ids.add(case_id)
        if case_id not in expected_status:
            raise ValueError(f"未知展示案例：{case_id}")
        if case["status"] not in _ALLOWED_STATUS:
            raise ValueError(f"未知 Finish 状态：{case['status']}")
        if case["status"] != expected_status[case_id]:
            raise ValueError(f"{case_id} 的真实终态被改写")

        if not isinstance(case["limitations"], list) or not case["limitations"]:
            raise ValueError(f"{case_id} 必须披露证据限制")
        if not isinstance(case["source_links"], list) or len(case["source_links"]) < 4:
            raise ValueError(f"{case_id} 必须链接可复核的证据文件")

        source_paths: dict[str, str] = {}
        for source in case["source_links"]:
            kind = source.get("kind")
            path = source.get("path")
            if not isinstance(kind, str) or not isinstance(path, str):
                raise ValueError(f"{case_id} 的证据链接格式无效")
            if kind in source_paths:
                raise ValueError(f"{case_id} 的证据类型重复：{kind}")
            _validate_source_path(path)
            source_paths[kind] = path

        diff_source = source_paths.get("diff")
        if diff_source is None:
            raise ValueError(f"{case_id} 缺少 Diff 证据")
        excerpt = case["diff"].get("excerpt")
        if not isinstance(excerpt, str) or excerpt not in _read_release_source(
            diff_source
        ):
            raise ValueError(f"{case_id} 的 Diff 摘录与证据文件不一致")

        review_source = source_paths.get("review")
        if review_source is None:
            raise ValueError(f"{case_id} 缺少 Reviewer 证据")
        review_payload = json.loads(_read_release_source(review_source))
        if case["review"].get("verdict") != review_payload.get("verdict"):
            raise ValueError(f"{case_id} 的 Reviewer verdict 与证据文件不一致")

        for source_path, expected in _SOURCE_CHECKS[case_id]:
            source_text = _read_release_source(source_path)
            if expected not in source_text:
                raise ValueError(f"{case_id} 的来源证据缺失：{expected!r}")

    if seen_ids != set(expected_status):
        raise ValueError("展示案例集合与人工核准清单不一致")

    rejection = next(
        case for case in cases if case["id"] == "pycodestyle-1187-rejection"
    )
    if rejection["review"]["severity"] != "major":
        raise ValueError("Reviewer 阻断案例必须保留 major finding")
    if "needs_human" not in rejection["gates"]["finish"]:
        raise ValueError("Reviewer 阻断案例必须保留 needs_human 终态")

    for text in _iter_strings(payload):
        for pattern in (*_PATH_PATTERNS, *_SECRET_PATTERNS, *_UNSAFE_MODEL_CONTENT):
            match = pattern.search(text)
            if match:
                raise ValueError(f"公开案例包含禁止内容：{match.group(0)!r}")

    serialized = json.dumps(payload, ensure_ascii=False)
    if re.search(r"(?i)success[_ -]?rate|成功率\s*[:=]\s*\d", serialized):
        raise ValueError("展示案例不得生成总体成功率")


def render_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成或核对 GitHub Pages 展示案例")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只核对已提交的数据，不写入文件",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出路径，默认 site/data/cases.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.output
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path

    payload = build_payload()
    validate_payload(payload)
    expected = render_payload(payload)

    if args.check:
        if not output_path.exists():
            print(f"展示案例文件不存在：{output_path.relative_to(REPO_ROOT)}")
            return 1
        actual = output_path.read_text(encoding="utf-8")
        if actual != expected:
            print("展示案例与脱敏证据包及人工核准清单不一致，请重新运行生成命令")
            return 1
        print("展示案例数据与脱敏证据包及人工核准清单一致")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(expected, encoding="utf-8", newline="\n")
    print(f"已生成展示案例：{output_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
