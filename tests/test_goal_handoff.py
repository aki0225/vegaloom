from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import vega.goal_handoff as goal_handoff_module
import vega.goal_runtime as goal_runtime_module
from vega.cli import app
from vega.goal_handoff import (
    DEFAULT_CONTEXT_MAX_CHARS,
    GoalHandoffArtifactInput,
    GoalHandoffInput,
    compile_goal_handoff_context,
)
from vega.goal_runtime import GoalRuntime
from vega.run_lock import RunMutationBusyError, RunMutationLock
from vega.run_status import render_run_status


def test_create_and_compile_handoff(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    handoff = _read_json(fixture.version_dir / "checkpoint-handoff.json")
    context = _read_json(
        fixture.version_dir
        / "consumers/session-b/checkpoint-context.json"
    )
    compile_manifest = _read_json(
        fixture.version_dir
        / "consumers/session-b/handoff-compile-result.json"
    )
    state = _read_json(fixture.goal_run / "goal-state.json")
    assert handoff["handoff_sha256"]
    assert handoff["goal_contract_sha256"]
    assert handoff["success_conditions"]
    assert context["status"] == "ready"
    assert context["handoff_sha256"] == handoff["handoff_sha256"]
    assert compile_manifest["result"]["status"] == "ready"
    for binding in compile_manifest["artifact_bindings"]:
        artifact_path = fixture.goal_run.joinpath(
            *binding["path"].split("/")
        )
        assert artifact_path.stat().st_size == binding["size"]
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == (
            binding["sha256"]
        )
    assert state["status"] == "checkpoint_done"
    assert state["current_step"] == "handoff_context_ready"
    assert all("/.c-" not in artifact for artifact in state["artifacts"])
    assert (
        fixture.version_dir
        / "consumers/session-b/checkpoint-context.md"
    ).is_file()


def test_goal_handoff_cli_creates_and_compiles_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    monkeypatch.chdir(fixture.workspace)
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "goal",
            "handoff",
            "--run",
            fixture.goal_run.name,
            "--checkpoint",
            "01",
            "--input",
            str(fixture.input_path),
        ],
    )
    assert created.exit_code == 0, created.output
    assert "goal handoff 创建完成" in created.output
    assert "只有 context status 为 ready" in created.output

    compiled = runner.invoke(
        app,
        [
            "goal",
            "handoff-context",
            "--run",
            fixture.goal_run.name,
            "--checkpoint",
            "01",
            "--version",
            "v0001",
            "--consumer-session-id",
            "session-b",
            "--consumer-worker-epoch",
            "epoch-b",
        ],
    )
    assert compiled.exit_code == 0, compiled.output
    assert "goal handoff context 编译完成" in compiled.output
    assert "consumer worker 不得读取 source chat" in compiled.output
    assert fixture.version_dir.joinpath(
        "consumers/session-b/checkpoint-context.json"
    ).is_file()


@pytest.mark.parametrize(
    ("mode", "expected_step"),
    [
        ("blocked", "handoff_blocked"),
        ("split", "handoff_split_required"),
    ],
)
def test_goal_handoff_cli_returns_nonzero_until_context_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_step: str,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    if mode == "blocked":
        fixture.repo.joinpath("AGENTS.md").write_text(
            "# Rules\n\n- drifted policy\n",
            encoding="utf-8",
            newline="\n",
        )
    monkeypatch.chdir(fixture.workspace)
    command = [
        "goal",
        "handoff-context",
        "--run",
        fixture.goal_run.name,
        "--checkpoint",
        "01",
        "--version",
        "v0001",
        "--consumer-session-id",
        "session-b",
        "--consumer-worker-epoch",
        "epoch-b",
    ]
    if mode == "split":
        command.extend(["--max-chars", "1"])

    result = CliRunner().invoke(app, command)

    assert result.exit_code == 1
    state = _read_json(fixture.goal_run / "goal-state.json")
    assert state["current_step"] == expected_step
    assert "禁止启动" in result.output


def test_same_session_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-a",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-a/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert "source_consumer_session_must_differ" in blocked["issues"]
    assert _read_json(fixture.goal_run / "goal-state.json")["status"] == "blocked"


def test_worker_epoch_mismatch_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "wrong-epoch",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-b/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert "target_worker_epoch_mismatch" in blocked["issues"]


def test_handoff_version_cannot_be_overwritten(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoff_path = fixture.version_dir / "checkpoint-handoff.json"
    original = handoff_path.read_bytes()

    with pytest.raises(ValueError, match="已存在.*不能覆盖"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(fixture.input_path),
        )

    assert handoff_path.read_bytes() == original
    assert sorted(path.name for path in fixture.version_dir.parent.iterdir()) == [
        "v0001"
    ]


def test_source_and_target_worker_epochs_must_differ(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    handoff_input = GoalHandoffInput(
        source_worker_epoch="same-epoch",
        target_worker_epoch="same-epoch",
        source_session_id="session-a",
        next_action="继续 checkpoint 02。",
    )
    input_path = tmp_path / "same-epoch-handoff.json"
    input_path.write_text(
        handoff_input.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="worker_epoch.*必须不同"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(input_path),
        )

    assert not fixture.version_dir.exists()


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        ("workspace", "workspace_drift"),
        ("policy", "project_policy_drift"),
    ],
)
def test_workspace_or_policy_drift_is_blocked(
    tmp_path: Path,
    mutation: str,
    expected_issue: str,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    if mutation == "workspace":
        fixture.repo.joinpath("README.md").write_text(
            "# Synthetic\nworkspace drift\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        fixture.repo.joinpath("AGENTS.md").write_text(
            "# Rules\n\n- drifted policy\n",
            encoding="utf-8",
            newline="\n",
        )

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-b/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert expected_issue in blocked["issues"]


def test_ignored_file_fingerprint_drift_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    ignored = fixture.repo / "cache.tmp"
    original_stat = ignored.stat()

    ignored.write_text("bravo\n", encoding="utf-8", newline="\n")
    os.utime(
        ignored,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-b/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert "workspace_drift" in blocked["issues"]


def test_goal_contract_drift_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    contract_path = fixture.goal_run / "goal-contract.json"
    contract = _read_json(contract_path)
    contract["success_conditions"].append("漂移后的成功条件")
    _write_json(contract_path, contract)

    result = _compile_handoff(fixture, "session-b")

    assert result.status == "blocked"
    assert "goal_contract_drift" in result.issues


def test_handoff_self_hash_tamper_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoff_path = fixture.version_dir / "checkpoint-handoff.json"
    handoff = _read_json(handoff_path)
    handoff["next_action"] = "篡改后的下一步"
    _write_json(handoff_path, handoff)

    result = _compile_handoff(fixture, "session-b")

    assert result.status == "blocked"
    assert "handoff_self_hash_mismatch" in result.issues


def test_recomputed_handoff_hash_cannot_bypass_safe_text_validation(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoff_path = fixture.version_dir / "checkpoint-handoff.json"
    handoff = _read_json(handoff_path)
    handoff["verified_facts"] = [
        "GATE6_SOURCE_CHAT_PRIVATE_CANARY_7f2a9d4c"
    ]
    canonical_payload = {
        key: value
        for key, value in handoff.items()
        if key != "handoff_sha256"
    }
    handoff["handoff_sha256"] = hashlib.sha256(
        json.dumps(
            canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    _write_json(handoff_path, handoff)

    result = _compile_handoff(fixture, "session-b")

    assert result.status == "blocked"
    assert any(
        issue.startswith("handoff_schema_invalid:")
        for issue in result.issues
    )


@pytest.mark.parametrize(
    "index_flag",
    ["--assume-unchanged", "--skip-worktree"],
)
def test_handoff_creation_rejects_unsafe_index_flags(
    tmp_path: Path,
    index_flag: str,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    _run_git(
        fixture.repo,
        "update-index",
        index_flag,
        "--",
        "README.md",
    )

    with pytest.raises(ValueError) as exc_info:
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(fixture.input_path),
        )

    message = str(exc_info.value).casefold()
    assert any(
        marker in message
        for marker in ("index", "assume-unchanged", "skip-worktree")
    )
    assert not fixture.version_dir.exists()


def test_authoritative_artifact_drift_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    fixture.repo.joinpath("manual-evidence.md").write_text(
        "# Evidence\n\nforged\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _compile_handoff(fixture, "session-b")

    assert result.status == "blocked"
    assert any(
        issue.startswith(
            ("artifact_hash_mismatch:", "artifact_size_mismatch:")
        )
        for issue in result.issues
    )


def test_manual_checkpoint_evidence_is_bound_automatically(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    handoff_input = GoalHandoffInput(
        source_worker_epoch="epoch-a",
        target_worker_epoch="epoch-b",
        source_session_id="session-a",
        next_action="继续 checkpoint 02。",
    )
    input_path = tmp_path / "handoff-auto-evidence.json"
    input_path.write_text(
        handoff_input.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    fixture.runtime.handoff(
        fixture.goal_run.name,
        "01",
        str(input_path),
    )
    handoff = _read_json(fixture.version_dir / "checkpoint-handoff.json")
    assert any(
        item["scope"] == "repo" and item["path"] == "manual-evidence.md"
        for item in handoff["authoritative_artifacts"]
    )

    fixture.repo.joinpath("manual-evidence.md").write_text(
        "# Evidence\n\nforged after handoff\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _compile_handoff(fixture, "session-b")

    assert result.status == "blocked"
    assert any(
        issue.startswith(
            ("artifact_hash_mismatch:", "artifact_size_mismatch:")
        )
        for issue in result.issues
    )


def test_checkpoint_evidence_drift_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    evidence_path = (
        fixture.goal_run / "checkpoints/01/checkpoint-evidence.json"
    )
    evidence = _read_json(evidence_path)
    evidence["completed_note"] = "stale checkpoint evidence"
    _write_json(evidence_path, evidence)

    result = _compile_handoff(fixture, "session-b")

    assert result.status == "blocked"
    assert any(
        issue.startswith("checkpoint_evidence_stale:")
        for issue in result.issues
    )


@pytest.mark.parametrize(
    "path",
    [
        "C:\\secrets\\token.txt",  # repo-path-policy: allow-test-fixture
        "file.txt:credential",
        "../outside.txt",
        "source-session-a.chat",
        "memory/accepted-memory.json",
    ],
)
def test_artifact_input_rejects_escape_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        GoalHandoffArtifactInput(scope="repo", path=path)


def test_handoff_input_cannot_read_arbitrary_workspace_artifact() -> None:
    with pytest.raises(ValidationError):
        GoalHandoffArtifactInput(scope="workspace", path="notes.md")


def test_consumer_session_rejects_path_escape(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    with pytest.raises(ValueError, match="consumer_session_id"):
        fixture.runtime.handoff_context(
            fixture.goal_run.name,
            "01",
            "v0001",
            "../outside",
            "epoch-b",
            DEFAULT_CONTEXT_MAX_CHARS,
        )

    assert not fixture.goal_run.joinpath("outside").exists()


def test_handoff_input_rejects_source_chat_canary() -> None:
    with pytest.raises(ValidationError, match="source chat|private canary"):
        GoalHandoffInput(
            source_worker_epoch="epoch-a",
            target_worker_epoch="epoch-b",
            source_session_id="session-a",
            next_action="继续 checkpoint 02。",
            verified_facts=[
                "GATE6_SOURCE_CHAT_PRIVATE_CANARY_7f2a9d4c"
            ],
        )


def test_fresh_session_context_does_not_leak_source_chat_or_memory(
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

    consumer_dir = (
        fixture.version_dir / "consumers/fresh-session-b"
    )
    payload = _read_json(consumer_dir / "checkpoint-context.json")
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in consumer_dir.rglob("*")
        if path.is_file()
    )
    assert payload["status"] == "ready"
    assert payload["source_chat_included"] is False
    assert payload["memory_mode"] == "off"
    assert canary not in persisted
    assert "pre-existing synthetic memory" not in persisted
    assert memory_ledger.read_bytes() == before_memory


def test_context_budget_returns_split_plan(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        1,
    )

    consumer_dir = fixture.version_dir / "consumers/session-b"
    split = _read_json(consumer_dir / "checkpoint-split-plan.json")
    metrics = _read_json(consumer_dir / "context-metrics.json")
    assert metrics["status"] == "split_required"
    assert split["status"] == "split_required"
    assert split["truncation_applied"] is False
    assert split["automatic_checkpoint_creation"] is False
    assert split["context_chars"] > split["max_chars"]
    assert split["recommended_checkpoint_groups"]
    assert not consumer_dir.joinpath("checkpoint-context.md").exists()
    state = _read_json(fixture.goal_run / "goal-state.json")
    assert state["current_step"] == "handoff_split_required"


def test_split_plan_group_chars_include_section_separators() -> None:
    sections = [("alpha", "A"), ("beta", "B")]

    plan = goal_handoff_module._build_split_plan(
        sections,
        max_chars=4,
        context_chars=5,
    )

    assert plan["recommended_checkpoint_groups"] == [
        {
            "sections": ["alpha"],
            "chars": 2,
            "within_budget": True,
        },
        {
            "sections": ["beta"],
            "chars": 2,
            "within_budget": True,
        },
    ]


def test_blocked_handoff_recovers_with_new_version(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    policy_path = fixture.repo / "AGENTS.md"
    original_policy = policy_path.read_bytes()
    v1_handoff = (
        fixture.version_dir / "checkpoint-handoff.json"
    ).read_bytes()
    policy_path.write_text(
        "# Rules\n\n- temporary drift\n",
        encoding="utf-8",
        newline="\n",
    )

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )
    assert _read_json(fixture.goal_run / "goal-state.json")["status"] == (
        "blocked"
    )

    policy_path.write_bytes(original_policy)
    v2_input = _write_handoff_input(
        tmp_path,
        handoff_version=2,
    )
    fixture.runtime.handoff(
        fixture.goal_run.name,
        "01",
        str(v2_input),
    )
    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0002",
        "session-c",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    v2_dir = fixture.version_dir.parent / "v0002"
    state = _read_json(fixture.goal_run / "goal-state.json")
    assert state["status"] == "checkpoint_done"
    assert state["current_step"] == "handoff_context_ready"
    assert (
        fixture.version_dir
        / "consumers/session-b/handoff-blocked.json"
    ).is_file()
    assert (
        v2_dir / "consumers/session-c/checkpoint-context.md"
    ).is_file()
    assert (
        fixture.version_dir / "checkpoint-handoff.json"
    ).read_bytes() == v1_handoff


@pytest.mark.parametrize(
    ("field", "replacement", "expected_issue"),
    [
        ("scope_profile", "tampered-profile", "goal_scope_profile_drift"),
        ("non_goals", ["tampered non-goal"], "goal_non_goals_drift"),
        (
            "success_conditions",
            ["tampered success condition"],
            "goal_success_conditions_drift",
        ),
    ],
)
def test_recomputed_handoff_hash_cannot_change_repeated_contract_fields(
    tmp_path: Path,
    field: str,
    replacement: object,
    expected_issue: str,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoff_path = fixture.version_dir / "checkpoint-handoff.json"
    handoff = _read_json(handoff_path)
    handoff[field] = replacement
    _rewrite_handoff_with_hash(handoff_path, handoff)

    result = _compile_handoff(fixture, "contract-tamper-consumer")

    assert result.status == "blocked"
    assert expected_issue in result.issues


@pytest.mark.parametrize("mode", ["handoff", "context"])
def test_handoff_refresh_does_not_mutate_checkpoint_state_mirror(
    tmp_path: Path,
    mode: str,
) -> None:
    fixture = _make_goal_fixture(
        tmp_path,
        create_handoff=mode == "context",
    )
    evidence_path = (
        fixture.goal_run / "checkpoints/01/checkpoint-evidence.json"
    )
    before_record = _read_json(evidence_path)

    if mode == "handoff":
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(fixture.input_path),
        )
    else:
        fixture.runtime.handoff_context(
            fixture.goal_run.name,
            "01",
            "v0001",
            "mirror-consumer",
            "epoch-b",
            DEFAULT_CONTEXT_MAX_CHARS,
        )

    state = _read_json(fixture.goal_run / "goal-state.json")
    mirror = _read_json(fixture.goal_run / "state.json")
    persisted_record = _read_json(evidence_path)
    assert state == mirror
    assert state["checkpoint_records"][0] == persisted_record
    assert persisted_record == before_record


def test_requested_artifact_file_symlink_is_rejected(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    target = tmp_path / "outside-file.md"
    target.write_text("outside\n", encoding="utf-8", newline="\n")
    link = fixture.repo / "linked-evidence.md"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"当前平台不能创建文件 symlink：{exc}")
    input_path = _handoff_input_with_artifact(
        fixture,
        tmp_path / "symlink-input.json",
        "linked-evidence.md",
    )

    with pytest.raises(ValueError, match="链接|reparse"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(input_path),
        )

    assert not fixture.version_dir.exists()


def test_requested_artifact_directory_alias_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    target = tmp_path / "outside-directory"
    target.mkdir()
    target.joinpath("evidence.md").write_text(
        "outside\n",
        encoding="utf-8",
        newline="\n",
    )
    link = fixture.repo / "linked-directory"
    _create_directory_link_or_skip(target, link)
    input_path = _handoff_input_with_artifact(
        fixture,
        tmp_path / "directory-alias-input.json",
        "linked-directory/evidence.md",
    )

    with pytest.raises(ValueError, match="链接|junction|reparse"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(input_path),
        )

    assert not fixture.version_dir.exists()


def test_requested_artifact_hardlink_is_rejected(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    target = tmp_path / "outside-hardlink.md"
    target.write_text("preserve\n", encoding="utf-8", newline="\n")
    link = fixture.repo / "hardlinked-evidence.md"
    try:
        os.link(target, link)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")
    input_path = _handoff_input_with_artifact(
        fixture,
        tmp_path / "hardlink-input.json",
        "hardlinked-evidence.md",
    )

    with pytest.raises(ValueError, match="hardlink"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(input_path),
        )

    assert target.read_text(encoding="utf-8") == "preserve\n"
    assert not fixture.version_dir.exists()


def test_policy_hardlink_is_rejected_during_handoff_creation(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    policy_path = fixture.repo / "AGENTS.md"
    policy_path.unlink()
    outside_policy = tmp_path / "outside-agents.md"
    outside_policy.write_text(
        "# Outside policy\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        os.link(outside_policy, policy_path)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")

    with pytest.raises(ValueError, match="hardlink"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(fixture.input_path),
        )

    assert not fixture.version_dir.exists()


def test_persisted_handoff_hardlink_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoff_path = fixture.version_dir / "checkpoint-handoff.json"
    outside_copy = tmp_path / "outside-handoff.json"
    outside_copy.write_bytes(handoff_path.read_bytes())
    handoff_path.unlink()
    try:
        os.link(outside_copy, handoff_path)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")

    result = _compile_handoff(fixture, "hardlink-consumer")

    assert result.status == "blocked"
    assert any(
        issue.startswith("handoff_schema_invalid:")
        and "hardlink" in issue
        for issue in result.issues
    )


def test_handoff_publish_failure_leaves_no_final_directory_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    original_write = goal_handoff_module._write_text_atomic

    def fail_after_first_artifact(path: Path, content: str) -> None:
        if path.name == "handoff-report.md":
            raise RuntimeError("synthetic handoff publish crash")
        original_write(path, content)

    monkeypatch.setattr(
        goal_handoff_module,
        "_write_text_atomic",
        fail_after_first_artifact,
    )
    with pytest.raises(RuntimeError, match="synthetic handoff publish crash"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(fixture.input_path),
        )

    handoffs_dir = fixture.version_dir.parent
    assert not fixture.version_dir.exists()
    assert any(path.name.startswith(".h-") for path in handoffs_dir.iterdir())

    monkeypatch.setattr(
        goal_handoff_module,
        "_write_text_atomic",
        original_write,
    )
    fixture.runtime.handoff(
        fixture.goal_run.name,
        "01",
        str(fixture.input_path),
    )

    assert sorted(path.name for path in fixture.version_dir.iterdir()) == [
        "checkpoint-handoff.json",
        "handoff-report.md",
    ]


def test_consumer_publish_failure_leaves_no_final_directory_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    original_write = goal_handoff_module._write_json_atomic
    consumer_dir = fixture.version_dir / "consumers/session-b"

    def fail_after_metrics(path: Path, payload: object) -> None:
        if path.name == "checkpoint-context.json":
            raise RuntimeError("synthetic consumer publish crash")
        original_write(path, payload)

    monkeypatch.setattr(
        goal_handoff_module,
        "_write_json_atomic",
        fail_after_metrics,
    )
    with pytest.raises(RuntimeError, match="synthetic consumer publish crash"):
        fixture.runtime.handoff_context(
            fixture.goal_run.name,
            "01",
            "v0001",
            "session-b",
            "epoch-b",
            DEFAULT_CONTEXT_MAX_CHARS,
        )

    assert not consumer_dir.exists()
    assert any(
        path.name.startswith(".c-")
        for path in fixture.version_dir.iterdir()
    )

    monkeypatch.setattr(
        goal_handoff_module,
        "_write_json_atomic",
        original_write,
    )
    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    assert sorted(path.name for path in consumer_dir.iterdir()) == [
        "checkpoint-context.json",
        "checkpoint-context.md",
        "context-metrics.json",
        "handoff-compile-result.json",
    ]


def test_publish_revalidates_exact_file_set_after_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    original_rename = goal_handoff_module.os.rename

    def remove_report_then_rename(source: str, target: str) -> None:
        Path(source).joinpath("handoff-report.md").unlink()
        original_rename(source, target)

    monkeypatch.setattr(
        goal_handoff_module.os,
        "rename",
        remove_report_then_rename,
    )

    with pytest.raises(ValueError, match="发布后内容发生变化"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(fixture.input_path),
        )

    state = _read_json(fixture.goal_run / "goal-state.json")
    assert fixture.version_dir.joinpath("checkpoint-handoff.json").is_file()
    assert not fixture.version_dir.joinpath("handoff-report.md").exists()
    assert not any(
        "/handoffs/v0001/" in artifact
        for artifact in state["artifacts"]
    )


def test_orphan_handoff_publish_is_adopted_after_state_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path, create_handoff=False)
    original_save = goal_runtime_module._save_goal_state

    def fail_state_write(run_dir: Path, state: object) -> None:
        raise RuntimeError("synthetic goal state failure")

    monkeypatch.setattr(
        goal_runtime_module,
        "_save_goal_state",
        fail_state_write,
    )
    with pytest.raises(RuntimeError, match="synthetic goal state failure"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(fixture.input_path),
        )

    assert fixture.version_dir.joinpath("checkpoint-handoff.json").is_file()
    state = _read_json(fixture.goal_run / "goal-state.json")
    assert not any(
        "/handoffs/v0001/" in artifact
        for artifact in state["artifacts"]
    )

    with pytest.raises(ValueError, match="尚未完整登记.*orphan adoption"):
        fixture.runtime.handoff_context(
            fixture.goal_run.name,
            "01",
            "v0001",
            "session-b",
            "epoch-b",
            DEFAULT_CONTEXT_MAX_CHARS,
        )
    assert not fixture.version_dir.joinpath("consumers/session-b").exists()

    monkeypatch.setattr(
        goal_runtime_module,
        "_save_goal_state",
        original_save,
    )
    fixture.runtime.handoff(
        fixture.goal_run.name,
        "01",
        str(fixture.input_path),
    )

    state = _read_json(fixture.goal_run / "goal-state.json")
    assert any(
        artifact.endswith(
            "handoffs/v0001/checkpoint-handoff.json"
        )
        for artifact in state["artifacts"]
    )
    assert "adopted" in fixture.goal_run.joinpath(
        "goal-handoff-report.md"
    ).read_text(encoding="utf-8")


def test_orphan_consumer_publish_is_adopted_after_state_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    original_save = goal_runtime_module._save_goal_state
    consumer_dir = fixture.version_dir / "consumers/session-b"

    def fail_state_write(run_dir: Path, state: object) -> None:
        raise RuntimeError("synthetic goal state failure")

    monkeypatch.setattr(
        goal_runtime_module,
        "_save_goal_state",
        fail_state_write,
    )
    with pytest.raises(RuntimeError, match="synthetic goal state failure"):
        fixture.runtime.handoff_context(
            fixture.goal_run.name,
            "01",
            "v0001",
            "session-b",
            "epoch-b",
            DEFAULT_CONTEXT_MAX_CHARS,
        )

    assert consumer_dir.joinpath("handoff-compile-result.json").is_file()
    state = _read_json(fixture.goal_run / "goal-state.json")
    assert not any(
        "/consumers/session-b/" in artifact
        for artifact in state["artifacts"]
    )

    monkeypatch.setattr(
        goal_runtime_module,
        "_save_goal_state",
        original_save,
    )
    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        DEFAULT_CONTEXT_MAX_CHARS,
    )

    state = _read_json(fixture.goal_run / "goal-state.json")
    assert state["current_step"] == "handoff_context_ready"
    assert any(
        artifact.endswith(
            "consumers/session-b/handoff-compile-result.json"
        )
        for artifact in state["artifacts"]
    )


def test_goal_mutation_lock_blocks_before_business_artifacts_change(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    before = _goal_business_snapshot(fixture.goal_run)

    with RunMutationLock.acquire(fixture.goal_run, "goal.step"):
        with pytest.raises(RunMutationBusyError):
            fixture.runtime.step(fixture.goal_run.name)
        with pytest.raises(RunMutationBusyError):
            fixture.runtime.handoff(
                fixture.goal_run.name,
                "01",
                str(fixture.input_path),
            )
        with pytest.raises(RunMutationBusyError):
            fixture.runtime.handoff_context(
                fixture.goal_run.name,
                "01",
                "v0001",
                "locked-consumer",
                "epoch-b",
                DEFAULT_CONTEXT_MAX_CHARS,
            )

    assert _goal_business_snapshot(fixture.goal_run) == before


@pytest.mark.parametrize(
    ("surface", "expected_issue"),
    [
        ("workspace", "workspace_drift_during_compile"),
        ("policy", "project_policy_drift_during_compile"),
    ],
)
def test_compile_rechecks_workspace_and_policy_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    expected_issue: str,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    function_name = (
        "_capture_workspace_binding"
        if surface == "workspace"
        else "_capture_policy_snapshot"
    )
    original_capture = getattr(goal_handoff_module, function_name)
    calls = 0

    def capture_with_drift(repo: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            drift_path = (
                fixture.repo / "README.md"
                if surface == "workspace"
                else fixture.repo / "AGENTS.md"
            )
            drift_path.write_text(
                f"# Synthetic\n\n{surface} drift during compile\n",
                encoding="utf-8",
                newline="\n",
            )
        return original_capture(repo)

    monkeypatch.setattr(
        goal_handoff_module,
        function_name,
        capture_with_drift,
    )
    result = _compile_handoff(fixture, f"{surface}-drift-consumer")
    consumer_dir = (
        fixture.version_dir / "consumers" / f"{surface}-drift-consumer"
    )

    assert result.status == "blocked"
    assert expected_issue in result.issues
    assert consumer_dir.joinpath("handoff-blocked.json").is_file()
    assert not consumer_dir.joinpath("checkpoint-context.json").exists()


def test_status_uses_latest_complete_registered_handoff_version(
    tmp_path: Path,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    v2_input = _write_handoff_input(tmp_path, handoff_version=2)
    fixture.runtime.handoff(
        fixture.goal_run.name,
        "01",
        str(v2_input),
    )

    status = render_run_status(
        fixture.workspace,
        fixture.goal_run.name,
    )
    assert "--version v0002" in status

    fixture.version_dir.parent.joinpath(
        "v0002/handoff-report.md"
    ).unlink()
    fallback_status = render_run_status(
        fixture.workspace,
        fixture.goal_run.name,
    )
    assert "--version v0001" in fallback_status
    assert "--version v0002" not in fallback_status


def _compile_handoff(
    fixture: GoalFixture,
    consumer_session_id: str,
):
    return compile_goal_handoff_context(
        workspace=fixture.workspace,
        run_id=fixture.goal_run.name,
        checkpoint="01",
        version="v0001",
        consumer_session_id=consumer_session_id,
        consumer_worker_epoch="epoch-b",
        max_chars=DEFAULT_CONTEXT_MAX_CHARS,
        objective=fixture.objective,
        repo_path=fixture.repo,
    )


def _make_goal_fixture(
    tmp_path: Path,
    *,
    create_handoff: bool = True,
) -> GoalFixture:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime = GoalRuntime(workspace)
    goal_run = runtime.start(
        repo,
        "\n".join(
            [
                "# Goal",
                "",
                "Objective: 完成 synthetic checkpoint handoff",
                "",
                "Success conditions:",
                "- Session B 只依赖新鲜 context 继续工作。",
            ]
        ),
        "synthetic-goal",
        None,
    )
    runtime.step(goal_run.name)
    evidence = repo / "manual-evidence.md"
    evidence.write_text(
        "# Evidence\n\n- checkpoint 01 已人工验收。\n",
        encoding="utf-8",
        newline="\n",
    )
    runtime.attach(
        goal_run.name,
        "01",
        str(evidence),
        "manual",
        "synthetic checkpoint evidence",
    )
    runtime.checkpoint_done(
        goal_run.name,
        "01",
        note="人工验收 synthetic checkpoint 01",
        allow_manual_evidence=True,
    )
    repo.joinpath("cache.tmp").write_text(
        "alpha\n",
        encoding="utf-8",
        newline="\n",
    )
    input_path = _write_handoff_input(tmp_path, handoff_version=1)
    if create_handoff:
        runtime.handoff(goal_run.name, "01", str(input_path))
    return GoalFixture(
        workspace=workspace,
        repo=repo,
        runtime=runtime,
        goal_run=goal_run,
        version_dir=goal_run / "checkpoints/01/handoffs/v0001",
        input_path=input_path,
        objective="完成 synthetic checkpoint handoff",
    )


def _write_handoff_input(
    tmp_path: Path,
    *,
    handoff_version: int,
) -> Path:
    handoff_input = GoalHandoffInput(
        handoff_version=handoff_version,
        source_worker_epoch="epoch-a",
        target_worker_epoch="epoch-b",
        source_session_id="session-a",
        next_action="继续完成 synthetic checkpoint 02。",
        hard_constraints=["不读取 source chat。", "不写 accepted memory。"],
        verified_facts=["checkpoint 01 已完成。"],
        failed_approaches=["不得把 handoff 当作第二套业务状态。"],
        open_questions=[
            "checkpoint 02 的实现细节由 fresh session 读取当前 workspace。"
        ],
        authoritative_artifacts=[
            GoalHandoffArtifactInput(
                scope="repo",
                path="manual-evidence.md",
            ),
        ],
    )
    input_path = tmp_path / f"handoff-v{handoff_version:04d}.json"
    input_path.write_text(
        handoff_input.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    return input_path


def _handoff_input_with_artifact(
    fixture: GoalFixture,
    input_path: Path,
    relative_path: str,
) -> Path:
    payload = _read_json(fixture.input_path)
    payload["authoritative_artifacts"].append(
        {
            "scope": "repo",
            "path": relative_path,
        }
    )
    _write_json(input_path, payload)
    return input_path


def _rewrite_handoff_with_hash(path: Path, payload: dict) -> None:
    canonical_payload = {
        key: value
        for key, value in payload.items()
        if key != "handoff_sha256"
    }
    payload["handoff_sha256"] = hashlib.sha256(
        json.dumps(
            canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    _write_json(path, payload)


def _create_directory_link_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (NotImplementedError, OSError) as exc:
        if os.name != "nt":
            pytest.skip(f"当前平台不能创建目录 symlink：{exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(
                "当前平台不能创建目录 symlink 或 junction："
                f"{exc}; {result.stderr}"
            )


def _goal_business_snapshot(run_dir: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(run_dir)
        if relative.parts and relative.parts[0] == ".control":
            continue
        snapshot[relative.as_posix()] = path.read_bytes()
    return snapshot


class GoalFixture:
    def __init__(
        self,
        *,
        workspace: Path,
        repo: Path,
        runtime: GoalRuntime,
        goal_run: Path,
        version_dir: Path,
        input_path: Path,
        objective: str,
    ) -> None:
        self.workspace = workspace
        self.repo = repo
        self.runtime = runtime
        self.goal_run = goal_run
        self.version_dir = version_dir
        self.input_path = input_path
        self.objective = objective


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "core.autocrlf", "false")
    repo.joinpath(".gitignore").write_text(
        "cache.tmp\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- Run tests.\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("README.md").write_text(
        "# Synthetic\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("docs").mkdir()
    repo.joinpath("docs/PRODUCT-CONTRACT.md").write_text(
        "# Product Contract\n\n- No automatic commit or memory writes.\n",
        encoding="utf-8",
        newline="\n",
    )
    _run_git(
        repo,
        "add",
        "--",
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "docs/PRODUCT-CONTRACT.md",
    )
    _run_git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "init",
    )


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
