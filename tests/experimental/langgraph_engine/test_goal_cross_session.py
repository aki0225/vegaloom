from __future__ import annotations

import json
from pathlib import Path

from vega.goal_handoff import DEFAULT_CONTEXT_MAX_CHARS

from test_checkpoint_handoff import _make_goal_fixture, _read_json


def test_fresh_session_handoff_does_not_leak_source_chat_or_memory(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    canary = "GATE6_SOURCE_CHAT_PRIVATE_CANARY_7f2a9d4c"
    source_chat = fixture.workspace / "source-session-a.chat"
    source_chat.write_text(canary, encoding="utf-8", newline="\n")
    memory_ledger = fixture.workspace / "memory/accepted-memory.json"
    memory_ledger.parent.mkdir(parents=True)
    memory_ledger.write_text(
        json.dumps({"accepted": ["pre-existing synthetic memory"]}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    before_memory = memory_ledger.read_bytes()

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "fresh-session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    context = (
        fixture.version_dir
        / "consumers/fresh-session-b/checkpoint-context.md"
    ).read_text(encoding="utf-8")
    payload = _read_json(
        fixture.version_dir
        / "consumers/fresh-session-b/checkpoint-context.json"
    )
    assert payload["status"] == "ready"
    assert canary not in context
    assert canary not in json.dumps(payload, ensure_ascii=False)
    assert payload["source_chat_included"] is False
    assert payload["memory_mode"] == "off"
    assert memory_ledger.read_bytes() == before_memory


def test_context_budget_returns_deterministic_split_plan(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "fresh-session-b",
        "epoch-b",
        1,
    )

    split_path = fixture.version_dir / (
        "consumers/fresh-session-b/checkpoint-split-plan.json"
    )
    metrics_path = fixture.version_dir / (
        "consumers/fresh-session-b/context-metrics.json"
    )
    split = _read_json(split_path)
    metrics = _read_json(metrics_path)
    assert metrics["status"] == "split_required"
    assert split["status"] == "split_required"
    assert split["truncation_applied"] is False
    assert split["automatic_checkpoint_creation"] is False
    assert split["context_chars"] > split["max_chars"]
    assert split["recommended_checkpoint_groups"]


def test_cross_session_context_has_no_duplicate_or_unsafe_resume_signal(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "fresh-session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    context = (
        fixture.version_dir
        / "consumers/fresh-session-b/checkpoint-context.md"
    ).read_text(encoding="utf-8")
    assert _read_json(
        fixture.version_dir
        / "consumers/fresh-session-b/checkpoint-context.json"
    )["status"] == "ready"
    assert "source session" in context
    assert "consumer session" in context
    assert "不自动 commit、push、release" in context
    assert "accepted memory" in context
    assert "source chat" in context
    assert context.count("- source session：") == 1
