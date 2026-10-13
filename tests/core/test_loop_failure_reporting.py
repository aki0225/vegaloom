from pathlib import Path

import pytest

from vega.loop_failure_reporting import classify_verification_pause
from vega.verification import VerificationRunResult


def result(**fields):
    return VerificationRunResult(Path("summary.md"), Path("result.json"), 2, 1, **fields)


@pytest.mark.parametrize("verification", [None, result(), VerificationRunResult(
    Path("summary.md"), Path("result.json"), 2, 0,
)])
def test_ordinary_verification_does_not_pause(verification):
    assert classify_verification_pause(verification) is None


@pytest.mark.parametrize(("fields", "event", "step"), [
    ({"interruption_status": "timed_out"}, "verification_interrupted", "timed_out"),
    ({"interruption_status": "stopped"}, "verification_interrupted", "stopped"),
    ({"interruption_status": "termination-unconfirmed"}, "verification_interrupted",
     "verification_termination_unconfirmed"),
    ({"failure_kind": "project_config_invalid"}, "project_config_invalid", "project_config_invalid"),
    ({"failure_kind": "workspace_capture_failed"}, "verification_workspace_capture_failed",
     "verification_workspace_capture_failed"),
])
def test_verification_pause_classification(fields, event, step):
    pause = classify_verification_pause(result(**fields))
    assert pause.event == event
    assert pause.current_step == step
    assert pause.conclusion
    assert "iteration" not in pause.event_fields
    if event == "verification_interrupted":
        assert pause.event_fields == {"status": fields["interruption_status"],
                                      "command": None, "reason": None}
    else:
        assert pause.event_fields == {"failure_kind": fields["failure_kind"],
                                      "commands": 2, "failed": 1}


def test_interruption_precedes_failure_kind():
    pause = classify_verification_pause(result(interruption_status="stopped",
                                               failure_kind="project_config_invalid"))
    assert pause.current_step == "stopped"


@pytest.mark.parametrize("fields", [{"interruption_status": "unknown"},
                                    {"failure_kind": "unknown"}])
def test_unknown_verification_pause_is_rejected(fields):
    with pytest.raises(ValueError, match="受支持"):
        classify_verification_pause(result(**fields))
