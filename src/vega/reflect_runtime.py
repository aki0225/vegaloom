from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .agents_proposal import render_agents_md_proposals
from .memory_artifacts import MemoryProposalStore, make_memory_proposal_id
from .models import BriefInput, BriefState, MemoryProposal, ProjectKnowledge, ReflectState
from .project_config import load_project_config
from .project_context import write_project_context
from .project_knowledge import load_project_knowledge
from .redaction import assert_not_sensitive_path, redact_text
from .review_evidence import make_review_evidence as _make_review_evidence
from .repository_identity import repository_scope, resolve_git_revision
from .run_utils import create_run_dir, resolve_run_dir
from .runtime_workspace import capture_runtime_workspace
from .trace import TraceWriter
from .workspace_check import (
    ReviewWorkspaceSnapshot,
    collect_tracked_diff_parts,
    render_tracked_diff_sections,
)

REFLECT_ARTIFACTS = [
    "state.json",
    "trace.jsonl",
    "diff-summary.md",
    "full-diff.patch",
    "test-summary.md",
    "review-evidence.json",
    "project-context.md",
    "reflection.md",
    "agents-md-proposals.md",
    "eval.md",
]


class ReflectRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(
        self,
        repo_path: Path,
        source_run: str | None = None,
        test_log: Path | None = None,
        note: str | None = None,
        lesson: str | None = None,
    ) -> Path:
        repo = repo_path.resolve()
        test_text = _read_optional_log(
            test_log,
            workspace=self.workspace,
            repo_path=repo,
        )
        safe_source_run = redact_text(source_run) if source_run else None
        safe_note = redact_text(note) if note else None
        safe_lesson = redact_text(lesson) if lesson else None

        base_run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-reflect"
        run_id, run_dir = create_run_dir(self.workspace, base_run_id)
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = ReflectState(
            run_id=run_id,
            repo_path=str(repo),
            source_run=safe_source_run,
            status="running",
        )
        state.current_step = "collect"
        state.save(run_dir / "state.json")
        trace.write("reflect_started", repo_path=str(repo), source_run=safe_source_run)

        git_data = collect_git_reflection(repo)
        workspace_snapshot = capture_runtime_workspace(self.workspace, repo)
        changed_files = _tracked_changed_files(workspace_snapshot)
        untracked_files = [redact_text(path) for path in workspace_snapshot.untracked_files]
        state.changed_files = changed_files
        state.workspace_fingerprint = workspace_snapshot.fingerprint
        test_summary = render_test_summary(test_text)
        full_diff = redact_text(workspace_snapshot.full_diff)
        (
            profile_text,
            source_brief_issues,
            source_brief_diagnostics,
        ) = _read_source_run_brief(self.workspace, source_run, repo)
        trace.write(
            "source_brief_checked",
            consistent=not source_brief_issues,
            issues=source_brief_issues,
            diagnostics=source_brief_diagnostics,
        )
        context_input = "\n".join([safe_note or "", profile_text, "\n".join(changed_files)])
        tracked_revision = resolve_git_revision(repo)
        knowledge = load_project_knowledge(
            self.workspace,
            repo,
            context_input,
            changed_files,
            tracked_only=True,
            tracked_revision=tracked_revision,
        )

        diff_summary = render_diff_summary(git_data, changed_files, untracked_files)
        reflection = render_reflection(
            repo,
            git_data,
            changed_files,
            test_text,
            safe_note,
            safe_lesson,
            safe_source_run,
            knowledge,
        )
        run_dir.joinpath("diff-summary.md").write_text(
            diff_summary,
            encoding="utf-8",
        )
        run_dir.joinpath("full-diff.patch").write_text(
            full_diff,
            encoding="utf-8",
            newline="\n",
        )
        run_dir.joinpath("test-summary.md").write_text(test_summary, encoding="utf-8")
        write_project_context(
            run_dir,
            self.workspace,
            repo,
            context_input,
            changed_files,
            tracked_only=True,
            tracked_revision=tracked_revision,
            knowledge=knowledge,
        )
        run_dir.joinpath("reflection.md").write_text(
            reflection,
            encoding="utf-8",
        )
        review_evidence = _make_review_evidence(
            workspace_snapshot,
            test_summary,
            full_diff,
            changed_files,
            review_source_run=run_id,
            upstream_source_run=safe_source_run,
            source_brief=profile_text,
            reflection=reflection,
            diff_summary=diff_summary,
            source_brief_issues=source_brief_issues,
            source_brief_diagnostics=source_brief_diagnostics,
        )
        state.review_snapshot_id = review_evidence["snapshot_id"]
        run_dir.joinpath("review-evidence.json").write_text(
            json.dumps(review_evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        proposal_brief = BriefInput(
            mode="bug",
            text=safe_note or "执行后复盘",
            source=safe_source_run or "reflect",
            repo_path=str(repo),
        )
        run_dir.joinpath("agents-md-proposals.md").write_text(
            redact_text(render_agents_md_proposals(proposal_brief, knowledge)),
            encoding="utf-8",
        )
        trace.write("reflection_written", changed_files=changed_files)
        trace.write(
            "review_snapshot_written",
            snapshot_id=state.review_snapshot_id,
            workspace_fingerprint=state.workspace_fingerprint,
        )
        normalized_lesson = safe_lesson.strip() if safe_lesson else ""
        if normalized_lesson:
            proposal = self._make_memory_proposal(
                repo,
                run_id,
                changed_files,
                normalized_lesson,
            )
            state.memory_proposals.append(proposal)
            MemoryProposalStore(run_dir).append(proposal)
            trace.write("memory_proposal_written", proposal_id=proposal.id, title=proposal.title)

        state.current_step = "eval"
        run_dir.joinpath("eval.md").write_text("# Eval\n\n(pending)\n", encoding="utf-8")
        expected_artifacts = [
            *REFLECT_ARTIFACTS,
            *(["memory-proposals.jsonl"] if normalized_lesson else []),
        ]
        eval_results = _run_reflect_eval(run_dir, git_data, expected_artifacts)
        eval_results.extend(
            f"FAIL: source brief evidence issue：{issue}"
            for issue in source_brief_issues
        )
        eval_results.extend(
            f"FAIL: source brief evidence diagnostic：{diagnostic}"
            for diagnostic in source_brief_diagnostics
        )
        run_dir.joinpath("eval.md").write_text(_render_eval(eval_results), encoding="utf-8")
        state.eval_results = eval_results
        state.artifacts = expected_artifacts
        state.status = "failed" if any(item.startswith("FAIL:") for item in eval_results) else "success"
        state.current_step = "evidence_inconsistent" if source_brief_issues else "done"
        state.save(run_dir / "state.json")
        trace.write("eval_written", file="eval.md", results=eval_results)
        trace.write("run_finished", status=state.status)
        return run_dir

    def _make_memory_proposal(
        self,
        repo_path: Path,
        run_id: str,
        changed_files: list[str],
        lesson: str,
    ) -> MemoryProposal:
        content = redact_text(lesson)
        title = redact_text(f"经验候选：{_first_non_empty_line(content)}")
        proposal_id = make_memory_proposal_id(run_id, title, content)
        config = load_project_config(repo_path)
        tags = list(
            dict.fromkeys(
                [
                    "Vega",
                    "reflect",
                    repo_path.name,
                    *config.memory.default_tags,
                ]
            )
        )
        return MemoryProposal(
            id=proposal_id,
            type="lesson_candidate",
            title=title,
            content=content,
            source_run_id=run_id,
            confidence=0.75,
            sensitivity="internal",
            tags=tags,
            repo=repository_scope(repo_path),
            paths=changed_files,
        )


def collect_git_reflection(repo_path: Path) -> dict[str, str]:
    """以 staged + unstaged 双事实流收集 Reflect 的确定性检查结果。

    不能只使用 ``git diff HEAD``：同一文件处于 ``MM`` 时，HEAD 到工作区的净差异
    可能抵消 index 中已有修改。完整 diff 和文件列表已经由 review workspace snapshot
    提供，这里只保留不能从 snapshot 推导的 ``diff --check``。
    """
    staged_check, unstaged_check = collect_tracked_diff_parts(repo_path, ["--check"])
    return {
        "check": redact_text(render_tracked_diff_sections(staged_check, unstaged_check)),
    }


def render_diff_summary(
    git_data: dict[str, str],
    changed_files: list[str],
    untracked_files: list[str] | None = None,
) -> str:
    lines = ["# Diff Summary", "", "## 变更文件", ""]
    if changed_files:
        lines.extend(f"- `{item}`" for item in changed_files)
    else:
        lines.append("- 当前没有工作区 diff 文件。")
    lines.extend(["", "## 未跟踪文件（仅路径）", ""])
    if untracked_files:
        lines.extend(f"- `{item}`" for item in untracked_files)
        lines.append(
            "- 未跟踪文件内容不进入 reviewer；这些路径按本地预算指纹参与完整性校验，"
            "需要人工确认后才能视为已审查实现。"
        )
    else:
        lines.append("- 无")
    lines.extend(["", "## Git Diff Check", "", "```text", git_data["check"].strip() or "<ok>", "```"])
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_test_summary(test_text: str | None) -> str:
    lines = ["# Test Summary", ""]
    if not test_text:
        lines.append("- 未提供测试日志；请在最终交付中明确实际验证命令和结果。")
    else:
        excerpt = redact_text(test_text).strip()[:5000]
        lines.extend(["```text", excerpt, "```"])
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_reflection(
    repo_path: Path,
    git_data: dict[str, str],
    changed_files: list[str],
    test_text: str | None,
    note: str | None,
    lesson: str | None,
    source_run: str | None,
    knowledge: ProjectKnowledge,
) -> str:
    lines = [
        "# 执行后复盘",
        "",
        f"- 仓库：`{repo_path.name}`",
        f"- 关联 run：`{source_run or '未提供'}`",
        f"- 备注：{note or '无'}",
        "",
        "## 变更概览",
        "",
    ]
    if changed_files:
        lines.extend(f"- `{item}`" for item in changed_files)
    else:
        lines.append("- 未发现未提交 diff。")
    lines.extend(
        [
            "",
            "## 验证情况",
            "",
            "- 已提供测试日志。" if test_text else "- 未提供测试日志，需要人工补充验证结果。",
            "- `git diff --check` 结果：" + ("通过" if not git_data["check"].strip() else "存在问题"),
            "",
            "## 经验沉淀建议",
            "",
        ]
    )
    if knowledge.memory_hits:
        lines.extend(f"- 相关历史 memory：{hit.title}" for hit in knowledge.memory_hits)
    else:
        lines.append("- 未命中相关历史 memory。")
    if lesson and lesson.strip():
        lines.append("- 已根据显式 `--lesson` 生成可选 Memory Proposal，仍需人工 accept/reject。")
    else:
        lines.append("- 本次未提供 `--lesson`，不会生成 Memory Proposal。")
    lines.extend(
        [
            "",
            "## AGENTS.md 建议",
            "",
            "- 若本次复盘发现长期规则，应采纳 `agents-md-proposals.md` 中的建议后手动更新 AGENTS.md。",
            "- 不建议把一次性业务结论直接写入 AGENTS.md。",
        ]
    )
    return redact_text("\n".join(lines).rstrip() + "\n")


def _run_reflect_eval(
    run_dir: Path,
    git_data: dict[str, str],
    expected_artifacts: list[str],
) -> list[str]:
    results = [
        f"{'PASS' if (run_dir / artifact).exists() else 'FAIL'}: artifact 存在：{artifact}"
        for artifact in expected_artifacts
    ]
    results.append("PASS: git diff --check 通过" if not git_data["check"].strip() else "FAIL: git diff --check 存在问题")
    return results


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:60]
    return "未命名经验"


def _read_optional_log(
    path: Path | None,
    *,
    workspace: Path,
    repo_path: Path,
) -> str | None:
    if not path:
        return None
    resolved = _resolve_allowed_input_file(path, workspace, repo_path)
    return redact_text(resolved.read_text(encoding="utf-8", errors="replace"))


def _read_source_run_brief(
    workspace: Path,
    source_run: str | None,
    repo_path: Path,
) -> tuple[str, list[str], list[str]]:
    if not source_run:
        return "", [], []
    try:
        run_dir = resolve_run_dir(workspace, source_run)
    except (FileNotFoundError, ValueError) as exc:
        issue = "source_brief_run_invalid"
        return "", [issue], [f"{issue}: 无法解析 source run：{type(exc).__name__}"]
    state_path = run_dir / "state.json"
    if not state_path.exists():
        issue = "source_brief_state_missing"
        return "", [issue], [f"{issue}: source run 缺少 state.json"]
    try:
        source_state = BriefState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as exc:
        issue = "source_brief_state_invalid"
        return "", [issue], [f"{issue}: state.json 无法验证：{type(exc).__name__}"]

    issues: list[str] = []
    diagnostics: list[str] = []
    if source_state.run_id != run_dir.name:
        issues.append("source_brief_run_id_mismatch")
        diagnostics.append(
            "source_brief_run_id_mismatch: state.run_id 与 source run 目录不一致"
        )
    if Path(source_state.repo_path).resolve() != repo_path.resolve():
        issues.append("source_brief_repo_mismatch")
        diagnostics.append(
            "source_brief_repo_mismatch: source brief 与当前仓库不一致"
        )
    if source_state.status != "success":
        issues.append("source_brief_state_not_success")
        diagnostics.append(
            f"source_brief_state_not_success: source brief 状态为 {source_state.status}"
        )
    if issues:
        return "", list(dict.fromkeys(issues)), list(dict.fromkeys(diagnostics))
    brief = run_dir / "agent-brief.md"
    if not brief.exists():
        issues.append("source_brief_missing")
        diagnostics.append("source_brief_missing: source run 缺少 agent-brief.md")
        return "", list(dict.fromkeys(issues)), list(dict.fromkeys(diagnostics))
    try:
        brief_text = brief.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        issues.append("source_brief_unreadable")
        diagnostics.append(
            f"source_brief_unreadable: agent-brief.md 无法读取：{type(exc).__name__}"
        )
        return "", list(dict.fromkeys(issues)), list(dict.fromkeys(diagnostics))
    return (
        redact_text(brief_text),
        list(dict.fromkeys(issues)),
        list(dict.fromkeys(diagnostics)),
    )


def _render_eval(results: list[str]) -> str:
    return redact_text("# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n")


def _tracked_changed_files(workspace_snapshot: ReviewWorkspaceSnapshot) -> list[str]:
    untracked = set(workspace_snapshot.untracked_files)
    return [
        redact_text(path)
        for path in workspace_snapshot.changed_files
        if path not in untracked
    ]


def _resolve_allowed_input_file(path: Path, workspace: Path, repo_path: Path) -> Path:
    assert_not_sensitive_path(path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"测试日志不存在或无法解析：{redact_text(str(path))}") from exc
    assert_not_sensitive_path(resolved)
    if not resolved.is_file():
        raise ValueError(f"测试日志不是普通文件：{redact_text(str(path))}")

    allowed_roots = (workspace.resolve(), repo_path.resolve())
    if not any(_is_within(resolved, root) for root in allowed_roots):
        raise ValueError("测试日志必须位于 Vega workspace 或目标仓库内。")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
