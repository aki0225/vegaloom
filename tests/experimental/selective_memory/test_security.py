from __future__ import annotations

from pathlib import Path

from eval.selective_memory.candidates import build_candidates
from eval.selective_memory.evaluator import load_cases
from eval.selective_memory.projector import replay_events

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = PROJECT_ROOT / "eval" / "selective_memory"


def test_prompt_injection_stays_untrusted_and_out_of_candidates() -> None:
    case = next(
        item
        for item in load_cases(EXPERIMENT_ROOT / "cases")
        if item.case_id == "prompt-injection-stale-evidence"
    )
    snapshot = replay_events(
        case.events,
        task_id=case.task_id,
        run_id=case.run_id,
        repo_identity=case.repo_identity,
        evidence_hashes=case.evidence_hashes,
    )
    checkpoint = case.checkpoints[-1]
    candidates = build_candidates(
        snapshot,
        checkpoint.canonical_candidates,
        checkpoint.planned_action,
    )

    assert [item.id for item in snapshot.candidate_items] == [
        "tool-prompt-injection"
    ]
    assert all(
        item.candidate_id != "memory:tool-prompt-injection"
        for item in candidates
    )
    assert all(
        "自动写入长期 Memory" not in item.statement
        for item in candidates
    )


def test_experiment_does_not_import_or_modify_runtime_modules() -> None:
    experiment_files = list(EXPERIMENT_ROOT.rglob("*.py"))
    source = "\n".join(path.read_text(encoding="utf-8") for path in experiment_files)

    assert "vega.loop_runtime" not in source
    assert "vega.memory" not in source
    assert "memory/ledger.jsonl" not in source
    assert "codex exec" not in source.lower()
