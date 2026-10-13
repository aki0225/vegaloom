from pathlib import Path

import pytest

from vega.run_status_guidance import review_next_steps


@pytest.mark.parametrize("verdict", ["approve", "request_changes", "needs_human"])
def test_review_guidance_defers_to_change_run(verdict: str) -> None:
    steps = " ".join(review_next_steps(Path("runs/review"), {
        "verdict": verdict, "status": "needs_human", "current_step": "risk_gate_needs_human",
    }))
    assert "ChangeRun" in steps and "status" in steps and "explain" in steps
    assert "commit" not in steps and "reflect + review" not in steps


@pytest.mark.parametrize("step", ["context_budget", "evidence_truncated"])
def test_review_guidance_preserves_special_diagnostics(step: str) -> None:
    steps = " ".join(review_next_steps(Path("runs/review"), {"current_step": step}))
    assert "review-context" in steps
