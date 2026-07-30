from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .agents_proposal import write_agents_md_proposals
from .brief_generator import (
    extract_related_paths,
    write_bug_artifacts,
    write_common_brief_artifacts,
    write_feature_artifacts,
)
from .models import BriefInput, BriefState
from .project_context import write_project_context
from .project_knowledge import load_project_knowledge, write_knowledge_context
from .redaction import redact_text
from .repository_identity import resolve_git_revision
from .run_utils import create_run_dir
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
