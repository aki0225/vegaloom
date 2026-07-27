from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import vega.review_evidence as review_evidence_module
from vega.models import BriefState
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import (
    ReviewPackRuntime,
    ReviewRuntime,
)
from vega.runner import RunnerResult
from vega.workspace_check import ReviewWorkspaceSnapshot


class RecordingRunner:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.execution_contexts: list[object] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        self.prompts.append(prompt)
        self.execution_contexts.append(execution_context)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "证据完整。",
                    "findings": [],
                    "checked_items": ["完整性"],
                },
                ensure_ascii=False,
            ),
            command=["recording-reviewer"],
        )


def test_review_evidence_binds_all_reviewer_inputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    brief_run = _make_brief_run(tmp_path)

    reflect_run = ReflectRuntime(tmp_path).run(repo, source_run=brief_run.name)
    evidence = _read_json(reflect_run / "review-evidence.json")

    assert evidence["schema_version"] == 5
    assert evidence["source_run"] == reflect_run.name
    assert evidence["upstream_source_run"] == brief_run.name
    assert evidence["changed_files"] == ["README.md"]
    assert evidence["changed_files_sha256"] == _sha256_json(["README.md"])
    assert evidence["source_brief_sha256"] == _sha256_text(
        (brief_run / "agent-brief.md").read_text(encoding="utf-8")
    )
    assert evidence["reflection_sha256"] == _sha256_file(
        reflect_run / "reflection.md"
    )
    assert evidence["diff_summary_sha256"] == _sha256_file(
        reflect_run / "diff-summary.md"
    )
    assert evidence["full_diff_sha256"] == _sha256_file(
        reflect_run / "full-diff.patch"
    )
    assert len(evidence["staged_diff_sha256"]) == 64
    assert len(evidence["unstaged_diff_sha256"]) == 64
    assert evidence["test_summary_sha256"] == _sha256_file(
        reflect_run / "test-summary.md"
    )
    assert evidence["untracked_content_complete"] is True
    assert evidence["ignored_manifest_complete"] is True
    assert evidence["ignored_content_complete"] is True
    assert evidence["git_control_complete"] is True
    assert len(evidence["git_control_sha256"]) == 64

    runner = RecordingRunner()
    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    assert len(runner.prompts) == 1
    assert _read_json(review_run / "state.json")["status"] == "success"


def test_standalone_review_propagates_progress_reporter(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    brief_run = _make_brief_run(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo, source_run=brief_run.name)
    runner = RecordingRunner()

    def reporter(step: str, elapsed: int) -> None:
        del step, elapsed

    ReviewRuntime(
        tmp_path,
        runner=runner,
        progress_reporter=reporter,
    ).run(repo, reflect_run.name)

    assert runner.execution_contexts[0].progress_reporter is reporter


@pytest.mark.parametrize(
    ("artifact_name", "mutation", "expected_issue"),
    [
        ("agent-brief.md", "tamper", "source_brief_hash_mismatch"),
        ("reflection.md", "tamper", "reflection_hash_mismatch"),
        ("diff-summary.md", "missing", "diff_summary_missing"),
        ("full-diff.patch", "tamper", "full_diff_hash_mismatch"),
        ("test-summary.md", "missing", "test_summary_missing"),
    ],
)
def test_review_rejects_tampered_or_missing_consumed_artifacts(
    tmp_path: Path,
    artifact_name: str,
    mutation: str,
    expected_issue: str,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    brief_run = _make_brief_run(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo, source_run=brief_run.name)
    target = (
        brief_run / artifact_name
        if artifact_name == "agent-brief.md"
        else reflect_run / artifact_name
    )
    if mutation == "missing":
        target.unlink()
    else:
        target.write_text("tampered\n", encoding="utf-8")
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")
    verdict = _read_json(review_run / "review-verdict.json")
    assert runner.prompts == []
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_stale"
    assert verdict["verdict"] == "needs_human"
    assert expected_issue in context["evidence_issues"]
    assert f"review evidence issue：{expected_issue}" in (
        review_run / "eval.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("artifact_name", "expected_issue"),
    [
        ("state.json", "source_state_invalid"),
        ("review-evidence.json", "source_evidence_invalid"),
    ],
)
@pytest.mark.parametrize("runtime_kind", ["review", "review-pack"])
def test_corrupt_source_json_finishes_new_review_run_with_diagnostics(
    tmp_path: Path,
    artifact_name: str,
    expected_issue: str,
    runtime_kind: str,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    brief_run = _make_brief_run(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo, source_run=brief_run.name)
    (reflect_run / artifact_name).write_text('{"broken":', encoding="utf-8")
    runner = RecordingRunner()

    if runtime_kind == "review":
        review_run = ReviewRuntime(tmp_path, runner=runner).run(
            repo,
            reflect_run.name,
        )
    else:
        review_run = ReviewPackRuntime(tmp_path).run(repo, reflect_run.name)

    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")
    trace = _read_jsonl(review_run / "trace.jsonl")
    eval_text = (review_run / "eval.md").read_text(encoding="utf-8")
    assert state["status"] == "needs_human"
    assert state["status"] != "running"
    assert state["current_step"] == "evidence_stale"
    assert expected_issue in context["evidence_issues"]
    assert any(expected_issue in item for item in context["evidence_diagnostics"])
    assert expected_issue in eval_text
    assert trace[-1]["event"] == "run_finished"
    assert trace[-1]["status"] == "needs_human"
    assert runner.prompts == []
    if runtime_kind == "review":
        assert _read_json(review_run / "review-verdict.json")["verdict"] == "needs_human"


def test_changed_files_and_source_run_tampering_block_reviewer(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    brief_run = _make_brief_run(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo, source_run=brief_run.name)
    state = _read_json(reflect_run / "state.json")
    state["changed_files"] = ["README.md", "forged.py"]
    (reflect_run / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    evidence = _read_json(reflect_run / "review-evidence.json")
    evidence["source_run"] = "forged-reflect-run"
    (reflect_run / "review-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    assert runner.prompts == []
    assert "changed_files_mismatch" in context["evidence_issues"]
    assert "source_run_mismatch" in context["evidence_issues"]
    assert _read_json(review_run / "review-verdict.json")["verdict"] == "needs_human"


@pytest.mark.parametrize(
    ("field", "value", "expected_issue"),
    [
        ("ignored_manifest_complete", False, "source_ignored_manifest_incomplete"),
        ("ignored_content_complete", "unknown", "ignored_content_complete_invalid"),
        ("ignored_content_complete", 1, "ignored_content_complete_invalid"),
        ("git_control_sha256", "0" * 64, "git_control_sha256_mismatch"),
        ("git_control_complete", False, "source_git_control_incomplete"),
    ],
)
def test_schema_v5_evidence_fields_fail_closed(
    tmp_path: Path,
    field: str,
    value: object,
    expected_issue: str,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    evidence = _read_json(reflect_run / "review-evidence.json")
    evidence[field] = value
    evidence["snapshot_id"] = _sha256_json(
        {key: item for key, item in evidence.items() if key != "snapshot_id"}
    )
    (reflect_run / "review-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    assert runner.prompts == []
    assert expected_issue in context["evidence_issues"]
    assert _read_json(review_run / "review-verdict.json")["verdict"] == "needs_human"


def test_float_schema_version_is_not_accepted_as_v5(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    evidence_path = reflect_run / "review-evidence.json"
    evidence = _read_json(evidence_path)
    evidence["schema_version"] = 5.0
    evidence["snapshot_id"] = _sha256_json(
        {key: item for key, item in evidence.items() if key != "snapshot_id"}
    )
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    assert runner.prompts == []
    assert "source_evidence_schema_unsupported" in context["evidence_issues"]
    assert _read_json(review_run / "state.json")["status"] == "needs_human"


def test_legacy_schema_v4_requires_refresh(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    state_path = reflect_run / "state.json"
    evidence_path = reflect_run / "review-evidence.json"
    state = _read_json(state_path)
    evidence = _read_json(evidence_path)
    evidence["schema_version"] = 4
    evidence["ignored_content_complete"] = False
    evidence.pop("ignored_manifest_complete")
    evidence["snapshot_id"] = _sha256_json(
        {key: item for key, item in evidence.items() if key != "snapshot_id"}
    )
    state["review_snapshot_id"] = evidence["snapshot_id"]
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    review_pack = (review_run / "review-pack.md").read_text(encoding="utf-8")
    assert runner.prompts == []
    assert "legacy_review_evidence_requires_refresh" in context["evidence_issues"]
    assert "Reflect `incomplete`" in review_pack
    assert _read_json(review_run / "state.json")["status"] == "needs_human"


def test_bounded_ignored_content_keeps_metadata_trust_and_allows_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    repo.joinpath(".gitignore").write_text("*.tmp\n", encoding="utf-8")
    _run(["git", "add", ".gitignore"], repo)
    _run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "添加忽略规则",
        ],
        repo,
    )
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )
    for index in range(3):
        repo.joinpath(f"{index}.tmp").write_text(
            f"value-{index}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "vega.workspace_check.MAX_IGNORED_CONTENT_FILES",
        1,
    )
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    evidence = _read_json(reflect_run / "review-evidence.json")
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    review_pack = (review_run / "review-pack.md").read_text(encoding="utf-8")
    assert evidence["ignored_manifest_complete"] is True
    assert evidence["ignored_content_complete"] is False
    assert context["evidence_issues"] == []
    assert "Reflect `metadata_bounded`" in review_pack
    assert "当前 `metadata_bounded`" in review_pack
    assert len(runner.prompts) == 1
    assert _read_json(review_run / "state.json")["status"] == "success"


def test_tampered_ignored_manifest_cannot_self_attest(
) -> None:
    snapshot = ReviewWorkspaceSnapshot(
        fingerprint="f" * 64,
        head_sha="a" * 40,
        status_sha256="1" * 64,
        staged_diff_sha256="3" * 64,
        unstaged_diff_sha256="4" * 64,
        untracked_manifest_sha256="5" * 64,
        ignored_manifest_sha256="6" * 64,
        index_flags_sha256="7" * 64,
        full_diff="diff",
        staged_diff="diff",
        unstaged_diff="",
        changed_files=("README.md",),
        untracked_files=(),
        untracked_content_complete=True,
        ignored_manifest_complete=True,
        ignored_content_complete=False,
        git_control_sha256="8" * 64,
        git_control_complete=True,
    )
    evidence = {
        "schema_version": 5,
        "staged_diff_sha256": snapshot.staged_diff_sha256,
        "unstaged_diff_sha256": snapshot.unstaged_diff_sha256,
        "ignored_manifest_sha256": "0" * 64,
        "ignored_manifest_complete": True,
        "ignored_content_complete": True,
        "git_control_sha256": snapshot.git_control_sha256,
        "git_control_complete": True,
    }

    issues = review_evidence_module.review_evidence_schema_issues(
        evidence,
        snapshot,
    )

    assert "ignored_manifest_sha256_mismatch" in issues
    assert "ignored_content_complete_mismatch" in issues


def test_current_ignored_evidence_incomplete_blocks_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    original_capture = __import__(
        "vega.review_runtime",
        fromlist=["capture_review_workspace"],
    ).capture_review_workspace

    def incomplete_capture(repo_path: Path):
        return replace(
            original_capture(repo_path),
            ignored_manifest_complete=False,
        )

    monkeypatch.setattr(
        "vega.review_runtime.capture_review_workspace",
        incomplete_capture,
    )
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    assert runner.prompts == []
    assert "current_ignored_manifest_incomplete" in context["evidence_issues"]
    assert _read_json(review_run / "state.json")["status"] == "needs_human"


def test_review_rejects_git_control_change_since_reflect(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text(
        exclude.read_text(encoding="utf-8") + "\nlocal.log\n",
        encoding="utf-8",
    )
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    assert runner.prompts == []
    assert "git_control_sha256_mismatch" in context["evidence_issues"]
    assert "workspace_changed_since_reflect" in context["evidence_issues"]


def test_incomplete_untracked_content_fingerprint_blocks_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    original_capture = __import__(
        "vega.reflect_runtime",
        fromlist=["capture_review_workspace"],
    ).capture_review_workspace

    def incomplete_capture(repo_path: Path):
        return replace(
            original_capture(repo_path),
            untracked_content_complete=False,
        )

    monkeypatch.setattr(
        "vega.reflect_runtime.capture_review_workspace",
        incomplete_capture,
    )
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    context = _read_json(review_run / "review-context.json")
    assert runner.prompts == []
    assert "source_untracked_content_incomplete" in context["evidence_issues"]
    assert _read_json(review_run / "state.json")["status"] == "needs_human"


def test_untracked_summary_uses_budget_fingerprint_wording(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    repo.joinpath("untracked.txt").write_text("local content\n", encoding="utf-8")

    reflect_run = ReflectRuntime(tmp_path).run(repo)

    summary = (reflect_run / "diff-summary.md").read_text(encoding="utf-8")
    assert "内容不进入 reviewer" in summary
    assert "按本地预算指纹" in summary
    assert "未读取未跟踪文件内容" not in summary


def test_standalone_review_rejects_any_untracked_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    marker = "UNTRACKED_REVIEW_CONTENT_MUST_STAY_LOCAL"
    repo.joinpath("local.py").write_text(marker, encoding="utf-8")
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_stale"
    assert runner.prompts == []
    assert "source_untracked_files_present" in context["evidence_issues"]
    assert "current_untracked_files_present" in context["evidence_issues"]
    assert marker not in _read_tree(review_run)


def test_standalone_review_rejects_zero_tracked_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    reflect_run = ReflectRuntime(tmp_path).run(repo)
    runner = RecordingRunner()

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo, reflect_run.name)

    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_stale"
    assert runner.prompts == []
    assert "tracked_diff_empty" in context["evidence_issues"]


def test_review_evidence_snapshot_matches_persisted_redacted_payload(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    source = repo / "password=source.py"
    source.write_text("password = None\n", encoding="utf-8")
    _run(["git", "add", source.name], repo)
    _run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "add source",
        ],
        repo,
    )
    source.write_text(
        "password = get_secret('name')\n",
        encoding="utf-8",
        newline="\n",
    )

    reflect_run = ReflectRuntime(tmp_path).run(repo)
    evidence = _read_json(reflect_run / "review-evidence.json")
    snapshot_id = evidence.pop("snapshot_id")
    expected_snapshot_id = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert snapshot_id == expected_snapshot_id
    review_pack = ReviewPackRuntime(tmp_path).run(repo, reflect_run.name)
    context = _read_json(review_pack / "review-context.json")
    assert "snapshot_metadata_invalid" not in context["evidence_issues"]


def test_reflect_rejects_source_brief_without_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_repo(repo)
    source_run = tmp_path / "runs" / "missing-state-brief"
    source_run.mkdir(parents=True)
    source_run.joinpath("agent-brief.md").write_text(
        "# Agent Brief\n\n- 只修改 README.md。\n",
        encoding="utf-8",
    )

    reflect_run = ReflectRuntime(tmp_path).run(repo, source_run=source_run.name)

    state = _read_json(reflect_run / "state.json")
    evidence = _read_json(reflect_run / "review-evidence.json")
    assert state["status"] == "failed"
    assert state["current_step"] == "evidence_inconsistent"
    assert "source_brief_state_missing" in evidence["source_brief_evidence_issues"]
    assert set(state["artifacts"]) == {
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
    }


def test_reflect_rejects_source_brief_from_another_repository(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "source-repo"
    target_repo = tmp_path / "target-repo"
    _init_repo(source_repo)
    _init_changed_repo(target_repo)
    brief_run = _make_brief_run(tmp_path, source_repo)

    reflect_run = ReflectRuntime(tmp_path).run(
        target_repo,
        source_run=brief_run.name,
    )

    state = _read_json(reflect_run / "state.json")
    evidence = _read_json(reflect_run / "review-evidence.json")
    assert state["status"] == "failed"
    assert "source_brief_repo_mismatch" in evidence["source_brief_evidence_issues"]


def _make_brief_run(workspace: Path, repo: Path | None = None) -> Path:
    run_dir = workspace / "runs" / "upstream-brief"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("agent-brief.md").write_text(
        "# Agent Brief\n\n- 只修改 README.md。\n",
        encoding="utf-8",
    )
    BriefState(
        run_id=run_dir.name,
        mode="feature",
        status="success",
        repo_path=str((repo or workspace / "repo").resolve()),
        input_source="test",
        current_step="done",
        artifacts=["state.json", "agent-brief.md"],
    ).save(run_dir / "state.json")
    return run_dir


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "core.autocrlf", "false"], repo)
    repo.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    _run(["git", "add", "README.md"], repo)
    _run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        repo,
    )


def _init_changed_repo(repo: Path) -> None:
    _init_repo(repo)
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_tree(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file()
    )


def _sha256_file(path: Path) -> str:
    return _sha256_text(path.read_text(encoding="utf-8"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(serialized)
