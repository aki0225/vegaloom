from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import vega.goal_runtime as goal_runtime_module
from vega.goal_handoff import (
    GoalHandoffArtifactInput,
    GoalHandoffInput,
    compile_goal_handoff_context,
)
from vega.goal_runtime import GoalRuntime


def test_clean_create_and_compile(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        12_000,
    )

    handoff = _read_json(fixture.version_dir / "checkpoint-handoff.json")
    assert handoff["handoff_sha256"]
    assert handoff["goal_contract_sha256"]
    assert handoff["success_conditions"]
    assert _read_json(fixture.goal_run / "goal-state.json")["current_step"] == (
        "handoff_context_ready"
    )
    assert (fixture.version_dir / "consumers/session-b/checkpoint-context.md").is_file()


def test_same_session_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-a",
        "epoch-b",
        12_000,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-a/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert "source_consumer_session_must_differ" in blocked["issues"]


def test_worker_epoch_mismatch_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "wrong-epoch",
        12_000,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-b/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert "target_worker_epoch_mismatch" in blocked["issues"]


def test_source_and_target_worker_epochs_must_differ(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoff_input = GoalHandoffInput(
        handoff_version=2,
        source_worker_epoch="same-epoch",
        target_worker_epoch="same-epoch",
        source_session_id="session-a",
        next_action="继续完成 synthetic checkpoint 02。",
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


def test_blocked_handoff_can_recover_with_new_version(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    policy_path = fixture.repo / "AGENTS.md"
    original_policy = policy_path.read_bytes()
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
        12_000,
    )
    assert _read_json(fixture.goal_run / "goal-state.json")["status"] == "blocked"

    policy_path.write_bytes(original_policy)
    handoff_input = GoalHandoffInput(
        handoff_version=2,
        source_worker_epoch="epoch-a",
        target_worker_epoch="epoch-b",
        source_session_id="session-a",
        next_action="继续完成 synthetic checkpoint 02。",
        hard_constraints=["不读取 source chat。"],
        verified_facts=["checkpoint 01 已完成。"],
        failed_approaches=["不得覆盖当前 workspace 事实。"],
        open_questions=["由 fresh session 读取当前 workspace。"],
    )
    input_path = tmp_path / "recovery-handoff.json"
    input_path.write_text(
        handoff_input.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    fixture.runtime.handoff(fixture.goal_run.name, "01", str(input_path))
    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0002",
        "session-c",
        "epoch-b",
        12_000,
    )

    state = _read_json(fixture.goal_run / "goal-state.json")
    assert state["status"] == "checkpoint_done"
    assert state["current_step"] == "handoff_context_ready"
    assert (
        fixture.goal_run
        / "checkpoints/01/handoffs/v0002/consumers/session-c/checkpoint-context.md"
    ).is_file()


def test_goal_contract_drift_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    contract_path = fixture.goal_run / "goal-contract.json"
    contract = _read_json(contract_path)
    contract["success_conditions"].append("漂移后的成功条件")
    _write_json(contract_path, contract)

    result = compile_goal_handoff_context(
        workspace=fixture.workspace,
        run_id=fixture.goal_run.name,
        checkpoint="01",
        version="v0001",
        consumer_session_id="session-b",
        consumer_worker_epoch="epoch-b",
        max_chars=12_000,
        objective=fixture.objective,
        repo_path=fixture.repo,
    )

    assert result.status == "blocked"
    assert "goal_contract_drift" in result.issues
    assert "goal_success_conditions_drift" in result.issues

    repeated_field_cases = (
        (
            "scope_profile",
            "伪造的 scope profile",
            "goal_scope_profile_drift",
        ),
        (
            "non_goals",
            ["伪造的 non-goal"],
            "goal_non_goals_drift",
        ),
    )
    for index, (field, value, expected_issue) in enumerate(
        repeated_field_cases,
        start=1,
    ):
        repeated_field_fixture = _make_goal_fixture(tmp_path / f"f{index}")
        handoff_path = (
            repeated_field_fixture.version_dir / "checkpoint-handoff.json"
        )
        handoff = _read_json(handoff_path)
        handoff[field] = value
        _rewrite_handoff_with_hash(handoff_path, handoff)

        repeated_field_result = compile_goal_handoff_context(
            workspace=repeated_field_fixture.workspace,
            run_id=repeated_field_fixture.goal_run.name,
            checkpoint="01",
            version="v0001",
            consumer_session_id=f"b{index}",
            consumer_worker_epoch="epoch-b",
            max_chars=12_000,
            objective=repeated_field_fixture.objective,
            repo_path=repeated_field_fixture.repo,
        )

        assert repeated_field_result.status == "blocked"
        assert expected_issue in repeated_field_result.issues


def test_incomplete_untracked_content_rejects_new_handoff(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    sensitive = fixture.repo / ".env"
    sensitive.write_text("synthetic secret", encoding="utf-8", newline="\n")
    input_path = _write_handoff_input(tmp_path, handoff_version=2)

    with pytest.raises(ValueError, match="未跟踪文件内容快照不完整"):
        fixture.runtime.handoff(
            fixture.goal_run.name,
            "01",
            str(input_path),
        )

    assert not fixture.goal_run.joinpath(
        "checkpoints/01/handoffs/v0002"
    ).exists()

    hardlink_fixture = _make_goal_fixture(
        tmp_path / "h",
        create_handoff=False,
    )
    outside_artifact = tmp_path / "outside-hardlink.md"
    outside_artifact.write_text("outside\n", encoding="utf-8", newline="\n")
    hardlinked_artifact = hardlink_fixture.repo / "hardlinked-evidence.md"
    try:
        os.link(outside_artifact, hardlinked_artifact)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")
    hardlink_input = _handoff_input_with_artifact(
        hardlink_fixture.input_path,
        tmp_path / "hardlink-handoff.json",
        "hardlinked-evidence.md",
    )

    with pytest.raises(ValueError, match="hardlink"):
        hardlink_fixture.runtime.handoff(
            hardlink_fixture.goal_run.name,
            "01",
            str(hardlink_input),
        )

    assert not hardlink_fixture.version_dir.exists()


def test_handoff_consumer_rejects_reparse_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoffs_dir = fixture.version_dir.parent
    original_lstat = Path.lstat

    def fake_lstat(path: Path) -> object:
        metadata = original_lstat(path)
        if path == handoffs_dir:
            return type(
                "ReparseStat",
                (),
                {
                    "st_mode": stat.S_IFDIR,
                    "st_file_attributes": 0x400,
                },
            )()
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="reparse point"):
        fixture.runtime.handoff_context(
            fixture.goal_run.name,
            "01",
            "v0001",
            "session-b",
            "epoch-b",
            12_000,
        )


def test_unknown_compile_status_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    monkeypatch.setattr(
        goal_runtime_module,
        "_goal_handoff_status",
        lambda result: "future-status",
    )

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        12_000,
    )

    state = _read_json(fixture.goal_run / "goal-state.json")
    assert state["status"] == "blocked"
    assert state["current_step"] == "handoff_blocked"


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        ("workspace", "workspace_drift"),
        ("policy_agents", "project_policy_drift"),
        ("policy_contract", "project_policy_drift"),
    ],
)
def test_workspace_or_policy_drift_is_blocked(
    tmp_path: Path,
    mutation: str,
    expected_issue: str,
) -> None:
    fixture = _make_goal_fixture(tmp_path)
    if mutation == "workspace":
        outside_policy_dir = tmp_path / "outside-policy"
        outside_policy_dir.mkdir()
        outside_policy_dir.joinpath("AGENTS.md").write_text(
            "# Outside policy\n\n- should be ignored through alias\n",
            encoding="utf-8",
            newline="\n",
        )
        _create_directory_link_or_skip(
            outside_policy_dir,
            fixture.repo / "linked-policy",
        )
        fixture.repo.joinpath("README.md").write_text(
            "# Synthetic\nworkspace drift\n",
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "policy_agents":
        fixture.repo.joinpath("AGENTS.md").write_text(
            "# Rules\n\n- drifted policy\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        fixture.repo.joinpath("docs/PRODUCT-CONTRACT.md").write_text(
            "# Product Contract\n\n- drifted contract\n",
            encoding="utf-8",
            newline="\n",
        )

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        12_000,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-b/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert expected_issue in blocked["issues"]
    if mutation == "workspace":
        assert "project_policy_drift" not in blocked["issues"]


def test_self_hash_tamper_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    handoff_path = fixture.version_dir / "checkpoint-handoff.json"
    payload = _read_json(handoff_path)
    payload["next_action"] = "篡改后的下一步"
    _write_json(handoff_path, payload)

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        12_000,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-b/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert "handoff_self_hash_mismatch" in blocked["issues"]


def test_authoritative_artifact_stale_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    artifact = fixture.repo / "manual-evidence.md"
    artifact.write_text("# Evidence\nforged\n", encoding="utf-8", newline="\n")

    fixture.runtime.handoff_context(
        fixture.goal_run.name,
        "01",
        "v0001",
        "session-b",
        "epoch-b",
        12_000,
    )

    blocked = _read_json(
        fixture.version_dir / "consumers/session-b/handoff-blocked.json"
    )
    assert blocked["status"] == "blocked"
    assert any(
        issue.startswith(("artifact_hash_mismatch:", "artifact_missing_or_invalid:"))
        for issue in blocked["issues"]
    )

    persisted_fixture = _make_goal_fixture(tmp_path / "p")
    handoff_path = persisted_fixture.version_dir / "checkpoint-handoff.json"
    outside_handoff = tmp_path / "outside-handoff.json"
    outside_handoff.write_bytes(handoff_path.read_bytes())
    handoff_path.unlink()
    try:
        os.link(outside_handoff, handoff_path)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")

    hardlink_result = compile_goal_handoff_context(
        workspace=persisted_fixture.workspace,
        run_id=persisted_fixture.goal_run.name,
        checkpoint="01",
        version="v0001",
        consumer_session_id="b",
        consumer_worker_epoch="epoch-b",
        max_chars=12_000,
        objective=persisted_fixture.objective,
        repo_path=persisted_fixture.repo,
    )

    assert hardlink_result.status == "blocked"
    assert any(
        issue.startswith("handoff_schema_invalid:") and "hardlink" in issue
        for issue in hardlink_result.issues
    )


def test_checkpoint_evidence_stale_is_blocked(tmp_path: Path) -> None:
    fixture = _make_goal_fixture(tmp_path)
    evidence_path = fixture.goal_run / "checkpoints/01/checkpoint-evidence.json"
    evidence = _read_json(evidence_path)
    evidence["completed_note"] = "stale evidence"
    _write_json(evidence_path, evidence)

    result = compile_goal_handoff_context(
        workspace=fixture.workspace,
        run_id=fixture.goal_run.name,
        checkpoint="01",
        version="v0001",
        consumer_session_id="session-b",
        consumer_worker_epoch="epoch-b",
        max_chars=12_000,
        objective=fixture.objective,
        repo_path=fixture.repo,
    )

    assert result.status == "blocked"
    assert any(issue.startswith("checkpoint_evidence_stale:") for issue in result.issues)

    report_fixture = _make_goal_fixture(tmp_path / "r")
    report_path = report_fixture.goal_run / "checkpoints/01/checkpoint-report.md"
    outside_report = tmp_path / "outside-report.md"
    outside_report.write_bytes(report_path.read_bytes())
    report_path.unlink()
    try:
        os.link(outside_report, report_path)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")

    report_result = compile_goal_handoff_context(
        workspace=report_fixture.workspace,
        run_id=report_fixture.goal_run.name,
        checkpoint="01",
        version="v0001",
        consumer_session_id="b",
        consumer_worker_epoch="epoch-b",
        max_chars=12_000,
        objective=report_fixture.objective,
        repo_path=report_fixture.repo,
    )

    assert report_result.status == "blocked"
    assert any(
        issue.startswith("checkpoint_evidence_stale:") and "hardlink" in issue
        for issue in report_result.issues
    )


@pytest.mark.parametrize(
    "path",
    ["C:\\secrets\\token.txt", "file.txt:credential", "../outside.txt"],
)
def test_artifact_input_rejects_windows_escape_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        GoalHandoffArtifactInput(scope="repo", path=path)


def test_handoff_input_rejects_private_canary() -> None:
    with pytest.raises(ValidationError, match="private canary"):
        GoalHandoffInput(
            source_worker_epoch="epoch-a",
            target_worker_epoch="epoch-b",
            source_session_id="session-a",
            next_action="继续 checkpoint 02。",
            verified_facts=[
                "GATE6_SOURCE_CHAT_PRIVATE_CANARY_7f2a9d4c"
            ],
        )


def _make_goal_fixture(
    tmp_path: Path,
    *,
    create_handoff: bool = True,
) -> "GoalFixture":
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
        "synthetic-gate-6",
        "default",
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
    handoff_input = GoalHandoffInput(
        handoff_version=1,
        source_worker_epoch="epoch-a",
        target_worker_epoch="epoch-b",
        source_session_id="session-a",
        next_action="继续完成 synthetic checkpoint 02。",
        hard_constraints=["不读取 source chat。", "不写 accepted memory。"],
        verified_facts=["checkpoint 01 已完成。"],
        failed_approaches=["不得把 handoff 当作第二套业务状态。"],
        open_questions=["checkpoint 02 的实现细节由 fresh session 读取当前 workspace。"],
        authoritative_artifacts=[
            GoalHandoffArtifactInput(scope="repo", path="manual-evidence.md"),
        ],
    )
    input_path = tmp_path / "handoff-input.json"
    input_path.write_text(
        handoff_input.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    if create_handoff:
        runtime.handoff(goal_run.name, "01", str(input_path))
    return GoalFixture(
        workspace=workspace,
        repo=repo,
        runtime=runtime,
        goal_run=goal_run,
        version_dir=goal_run / "checkpoints/01/handoffs/v0001",
        objective="完成 synthetic checkpoint handoff",
        input_path=input_path,
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
    )
    input_path = tmp_path / f"handoff-v{handoff_version:04d}.json"
    input_path.write_text(
        handoff_input.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    return input_path


def _handoff_input_with_artifact(
    source_input: Path,
    output_path: Path,
    relative_path: str,
) -> Path:
    payload = _read_json(source_input)
    payload["authoritative_artifacts"].append(
        {
            "scope": "repo",
            "path": relative_path,
        }
    )
    _write_json(output_path, payload)
    return output_path


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


class GoalFixture:
    def __init__(
        self,
        *,
        workspace: Path,
        repo: Path,
        runtime: GoalRuntime,
        goal_run: Path,
        version_dir: Path,
        objective: str,
        input_path: Path,
    ) -> None:
        self.workspace = workspace
        self.repo = repo
        self.runtime = runtime
        self.goal_run = goal_run
        self.version_dir = version_dir
        self.objective = objective
        self.input_path = input_path


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "core.autocrlf", "false")
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
