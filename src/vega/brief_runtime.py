from __future__ import annotations

import json
from .execution_paths import ExecutionPathGuard

from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .agents_proposal import write_agents_md_proposals
from .brief_generator import (
    extract_related_paths,
    write_bug_artifacts,
    write_common_brief_artifacts,
    write_feature_artifacts,
)
from .models import BriefInput, BriefState, ReflectState
from .project_context import write_project_context
from .project_knowledge import load_project_knowledge, write_knowledge_context
from .redaction import redact_text
from .repository_identity import resolve_git_revision
from .run_utils import create_run_dir, resolve_run_dir
from .trace import TraceWriter

COMMON_ARTIFACTS = [
    "state.json",
    "trace.jsonl",
    "knowledge-context.md",
    "project-context.md",
    "agent-brief.md",
    "agents-md-proposals.md",
    "eval.md",
]
BUG_ARTIFACTS = ["repro-plan.md", "root-cause-hypotheses.md", "regression-check.md"]
FEATURE_ARTIFACTS = ["feature-spec.md", "implementation-plan.md", "acceptance-criteria.md", "risk.md"]


class BriefRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, brief_input: BriefInput) -> Path:
        safe_input = brief_input.model_copy(
            update={
                "text": redact_text(brief_input.text),
                "source": redact_text(brief_input.source),
            }
        )
        base_run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_input.mode}-brief"
        run_id, run_dir = create_run_dir(self.workspace, base_run_id)

        trace = TraceWriter(run_dir / "trace.jsonl")
        state = BriefState(
            run_id=run_id,
            mode=safe_input.mode,
            repo_path=safe_input.repo_path,
            input_source=safe_input.source,
        )
        state.status = "running"
        state.current_step = "input_loaded"
        self._save_state(run_dir, state)
        trace.write("input_loaded", mode=safe_input.mode, source=safe_input.source)

        related_paths = extract_related_paths(safe_input.text)
        state.current_step = "knowledge"
        repo = Path(safe_input.repo_path)
        tracked_revision = resolve_git_revision(repo)
        knowledge = load_project_knowledge(
            self.workspace,
            repo,
            safe_input.text,
            related_paths,
            tracked_only=True,
            tracked_revision=tracked_revision,
        )
        state.agents_files = [item.path for item in knowledge.agents_instructions]
        state.memory_hits = knowledge.memory_hits
        write_knowledge_context(run_dir, knowledge)
        write_project_context(
            run_dir,
            self.workspace,
            repo,
            safe_input.text,
            related_paths,
            tracked_only=True,
            tracked_revision=tracked_revision,
            knowledge=knowledge,
        )
        trace.write(
            "knowledge_loaded",
            agents_files=state.agents_files,
            memory_hits=[hit.proposal_id for hit in state.memory_hits],
        )
        self._save_state(run_dir, state)

        state.current_step = "brief"
        write_common_brief_artifacts(run_dir, safe_input, knowledge)
        if safe_input.mode == "bug":
            write_bug_artifacts(run_dir, safe_input, knowledge)
        else:
            write_feature_artifacts(run_dir, safe_input, knowledge)
        write_agents_md_proposals(run_dir, safe_input, knowledge)
        trace.write("brief_written", mode=safe_input.mode)

        state.current_step = "eval"
        run_dir.joinpath("eval.md").write_text("# Eval\n\n(pending)\n", encoding="utf-8")
        expected = _expected_artifacts(safe_input.mode)
        eval_results = _run_brief_eval(run_dir, expected)
        run_dir.joinpath("eval.md").write_text(_render_eval(eval_results), encoding="utf-8")
        state.eval_results = eval_results
        state.artifacts = expected
        trace.write("eval_written", file="eval.md", results=eval_results)

        state.status = "failed" if any(item.startswith("FAIL:") for item in eval_results) else "success"
        state.current_step = "done"
        self._save_state(run_dir, state)
        trace.write("run_finished", status=state.status)
        return run_dir

    @staticmethod
    def _save_state(run_dir: Path, state: BriefState) -> None:
        state.save(run_dir / "state.json")


def _expected_artifacts(mode: str) -> list[str]:
    return [*COMMON_ARTIFACTS, *(BUG_ARTIFACTS if mode == "bug" else FEATURE_ARTIFACTS)]


def _run_brief_eval(run_dir: Path, expected_artifacts: list[str]) -> list[str]:
    results: list[str] = []
    for artifact in expected_artifacts:
        exists = (run_dir / artifact).exists()
        results.append(f"{'PASS' if exists else 'FAIL'}: artifact 存在：{artifact}")

    brief_text = run_dir.joinpath("agent-brief.md").read_text(encoding="utf-8", errors="replace")
    proposal_text = run_dir.joinpath("agents-md-proposals.md").read_text(encoding="utf-8", errors="replace")
    results.append(
        "PASS: agent-brief.md 包含禁止动作" if "不自动提交" in brief_text else "FAIL: agent-brief.md 缺少禁止动作"
    )
    results.append(
        "PASS: agents-md-proposals.md 未自动应用"
        if "不会自动修改" in proposal_text
        else "FAIL: agents-md-proposals.md 未声明只提议不应用"
    )
    return results


def _render_eval(results: list[str]) -> str:
    return "# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n"


def read_source_brief_artifact(
    workspace: Path,
    source_run: object,
    repo_path: Path,
) -> tuple[str, list[str], list[str]]:
    if not source_run:
        return "", [], []
    if not isinstance(source_run, str):
        issue = "source_brief_run_invalid"
        return "", [issue], [f"{issue}: 上游 source_run 不是字符串"]
    try:
        run_dir = resolve_run_dir(workspace, source_run)
    except (FileNotFoundError, ValueError) as exc:
        issue = "source_brief_run_invalid"
        return "", [issue], [f"{issue}: 无法解析上游 source_run：{type(exc).__name__}"]
    state_path = run_dir / "state.json"
    if not state_path.exists():
        issue = "source_brief_state_missing"
        return "", [issue], [f"{issue}: 上游 source_run 缺少 state.json"]
    try:
        source_state = BriefState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as exc:
        issue = "source_brief_state_invalid"
        return "", [issue], [f"{issue}: 上游 state.json 无法验证：{type(exc).__name__}"]

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
    brief_text, brief_issues, brief_diagnostics = read_text_artifact(
        run_dir / "agent-brief.md",
        "source_brief",
    )
    return (
        brief_text,
        list(dict.fromkeys([*issues, *brief_issues])),
        list(dict.fromkeys([*diagnostics, *brief_diagnostics])),
    )



def read_text_artifact(
    path: Path,
    issue_prefix: str,
) -> tuple[str, list[str], list[str]]:
    if not path.exists():
        issue = f"{issue_prefix}_missing"
        return "", [issue], [f"{issue}: 缺少 {path.name}"]
    try:
        return path.read_text(encoding="utf-8", errors="replace"), [], []
    except OSError as exc:
        issue = f"{issue_prefix}_unreadable"
        return "", [issue], [f"{issue}: {path.name} 无法读取：{type(exc).__name__}"]



ACCEPTANCE_SUPPLEMENT_ARTIFACT = "acceptance-supplement.json"


def read_acceptance_supplement(
    workspace: Path, source_run: object, head_sha: str, *, reflect_run: str | None = None,
) -> str:
    """独立审查数据，不参与 Brief、规则或项目知识编译。"""
    if reflect_run is not None:
        reflect_dir = resolve_run_dir(workspace, reflect_run)
        ExecutionPathGuard(workspace, reflect_dir).validate_artifact(reflect_dir / "state.json")
        reflect_state = ReflectState.model_validate_json((reflect_dir / "state.json").read_text(encoding="utf-8"))
        if (ACCEPTANCE_SUPPLEMENT_ARTIFACT in reflect_state.artifacts
                or (reflect_dir / ACCEPTANCE_SUPPLEMENT_ARTIFACT).exists()):
            source_run = reflect_run
    if not source_run:
        return ""
    if not isinstance(source_run, str):
        raise ValueError("验收补充的来源 Run 无效")
    run_dir = resolve_run_dir(workspace, source_run)
    path = run_dir / ACCEPTANCE_SUPPLEMENT_ARTIFACT
    guard = ExecutionPathGuard(run_dir.parent, run_dir)
    guard.validate_artifact(path)
    guard.validate_artifact(run_dir / "state.json")
    model = ReflectState if source_run == reflect_run else BriefState
    state = model.model_validate_json((run_dir / "state.json").read_text(encoding="utf-8"))
    if not path.exists():
        if ACCEPTANCE_SUPPLEMENT_ARTIFACT in state.artifacts:
            raise ValueError("已登记的验收补充缺失")
        return ""
    if ACCEPTANCE_SUPPLEMENT_ARTIFACT not in state.artifacts:
        raise ValueError("验收补充未由控制器登记")
    with path.open("rb") as stream:
        content = stream.read(100001)
    guard.validate_artifact(path)
    if len(content) > 100000:
        raise ValueError("验收补充超出读取上限")
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("candidate_sha") != head_sha:
        raise ValueError("验收补充不属于当前 Candidate")
    return content.decode("utf-8")
