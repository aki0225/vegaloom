from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega import codex_workspace as codex_workspace_module
from vega.experimental.inspection import context_loader
from vega import workspace_check as workspace_check_module
from vega.experimental.inspection.context_loader import load_target_context
from vega.workspace_check import (
    capture_review_workspace,
    evaluate_workspace,
    snapshot_workspace,
)
from vega.workspace_inventory import (
    prepare_verification_temp_root,
    workspace_ignored_path_exclusions,
)


def test_untracked_content_hashing_respects_file_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    for index in range(3):
        repo.joinpath(f"{index}.txt").write_text(f"value-{index}\n", encoding="utf-8")
    opened: list[str] = []
    original_open = Path.open

    def tracking_open(path: Path, *args, **kwargs):
        if path.suffix == ".txt":
            opened.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(workspace_check_module, "MAX_UNTRACKED_CONTENT_FILES", 1)
    monkeypatch.setattr(Path, "open", tracking_open)

    first = capture_review_workspace(repo)
    second = capture_review_workspace(repo)

    assert opened == ["0.txt", "0.txt"]
    assert first.untracked_content_complete is False
    assert first.untracked_manifest_sha256 == second.untracked_manifest_sha256


def test_zero_byte_untracked_file_consumes_file_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("0-empty.bin").write_bytes(b"")
    repo.joinpath("1-data.bin").write_bytes(b"value")
    opened: list[str] = []
    original_open = Path.open

    def tracking_open(path: Path, *args, **kwargs):
        if path.suffix == ".bin":
            opened.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(workspace_check_module, "MAX_UNTRACKED_CONTENT_FILES", 1)
    monkeypatch.setattr(Path, "open", tracking_open)

    snapshot = capture_review_workspace(repo)

    assert opened == ["0-empty.bin"]
    assert snapshot.untracked_content_complete is False


def test_untracked_content_hashing_respects_single_file_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("large.bin").write_bytes(b"12345")
    opened: list[str] = []
    original_open = Path.open

    def tracking_open(path: Path, *args, **kwargs):
        if path.name == "large.bin":
            opened.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(workspace_check_module, "MAX_UNTRACKED_FILE_BYTES", 4)
    monkeypatch.setattr(Path, "open", tracking_open)

    snapshot = capture_review_workspace(repo)

    assert opened == []
    assert snapshot.untracked_content_complete is False


def test_untracked_content_hashing_respects_total_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("a.bin").write_bytes(b"1234")
    repo.joinpath("b.bin").write_bytes(b"5678")
    opened: list[str] = []
    original_open = Path.open

    def tracking_open(path: Path, *args, **kwargs):
        if path.suffix == ".bin":
            opened.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(workspace_check_module, "MAX_UNTRACKED_CONTENT_BYTES", 4)
    monkeypatch.setattr(Path, "open", tracking_open)

    first = capture_review_workspace(repo)
    second = capture_review_workspace(repo)

    assert opened == ["a.bin", "a.bin"]
    assert first.untracked_content_complete is False
    assert first.untracked_manifest_sha256 == second.untracked_manifest_sha256


def test_ignored_content_hashing_exposes_incomplete_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath(".gitignore").write_text("*.tmp\n", encoding="utf-8")
    _git(repo, "add", "--", ".gitignore")
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "ignore temp files",
    )
    for index in range(3):
        repo.joinpath(f"{index}.tmp").write_text(f"value-{index}\n", encoding="utf-8")

    monkeypatch.setattr(workspace_check_module, "MAX_IGNORED_CONTENT_FILES", 1)

    snapshot = capture_review_workspace(repo)

    assert snapshot.ignored_manifest_complete is True
    assert snapshot.ignored_content_complete is False
    assert snapshot.ignored_coverage_level == "metadata_bounded"
    assert len(snapshot.ignored_manifest_sha256) == 64


def test_workspace_check_excludes_same_repo_vega_run_artifacts(
    tmp_path: Path,
) -> None:
    repo = _init_repo_with_zero_new_file_budget(tmp_path)
    run_dir = repo / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("state.json").write_text("{}\n", encoding="utf-8")
    exclusions = workspace_ignored_path_exclusions(repo, repo)
    baseline = snapshot_workspace(repo, ignored_path_exclusions=exclusions)

    repo.joinpath("README.md").write_text("# Demo\nfixed\n", encoding="utf-8")
    run_dir.joinpath("state.json").write_text('{"status":"running"}\n', encoding="utf-8")
    run_dir.joinpath("worker-output.txt").write_text("worker done\n", encoding="utf-8")
    result = evaluate_workspace(repo, baseline=baseline)

    assert baseline.untracked_files == frozenset()
    assert result.status == "passed"
    assert result.new_untracked_count == 0
    assert result.new_untracked_files == []
    assert result.baseline_untracked_changed is False
    assert "runs/" not in result.raw_status


def test_review_snapshot_ignores_owned_run_changes_but_keeps_other_untracked(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    run_dir = repo / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    output = run_dir / "process-output.txt"
    output.write_text("before\n", encoding="utf-8")
    repo.joinpath("notes.txt").write_text("keep visible\n", encoding="utf-8")
    exclusions = workspace_ignored_path_exclusions(repo, repo)
    before = capture_review_workspace(
        repo,
        ignored_path_exclusions=exclusions,
    )

    output.write_text("after\n", encoding="utf-8")
    after = capture_review_workspace(
        repo,
        ignored_path_exclusions=exclusions,
    )
    repo.joinpath("notes.txt").write_text("changed\n", encoding="utf-8")
    changed = capture_review_workspace(
        repo,
        ignored_path_exclusions=exclusions,
    )

    assert before.fingerprint == after.fingerprint
    assert changed.fingerprint != after.fingerprint
    assert before.untracked_files == ("notes.txt",)
    assert after.untracked_files == ("notes.txt",)


@pytest.mark.parametrize("relative_path", ["notes.txt", "runs-other/output.txt"])
def test_workspace_check_keeps_non_owned_untracked_paths_fail_closed(
    tmp_path: Path,
    relative_path: str,
) -> None:
    repo = _init_repo_with_zero_new_file_budget(tmp_path)
    exclusions = workspace_ignored_path_exclusions(repo, repo)
    baseline = snapshot_workspace(repo, ignored_path_exclusions=exclusions)
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unexpected\n", encoding="utf-8")

    result = evaluate_workspace(repo, baseline=baseline)

    assert result.status == "failed"
    assert result.new_untracked_count == 1
    assert result.new_untracked_files == [relative_path]


def test_verification_temp_root_rejects_logical_path_resolution_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    relocated = repo / "relocated-verification"
    relocated.mkdir(parents=True)
    logical_root = repo / ".tmp" / "vega-verification"
    original_resolve = Path.resolve

    def redirected_resolve(path: Path, *args, **kwargs) -> Path:
        if path == logical_root:
            return relocated
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)

    with pytest.raises(ValueError, match="不能经链接或 reparse point 改道"):
        prepare_verification_temp_root(repo)
    assert not logical_root.exists()


def test_ignored_directory_is_folded_without_exhausting_metadata_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath(".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(repo, "add", "--", ".gitignore")
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "ignore dependency directory",
    )
    package_dir = repo / "node_modules" / "pkg"
    package_dir.mkdir(parents=True)
    package_dir.joinpath("index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    package_dir.joinpath("types.d.ts").write_text("export {};\n", encoding="utf-8")

    monkeypatch.setattr(workspace_check_module, "MAX_IGNORED_METADATA_FILES", 1)

    baseline = snapshot_workspace(repo)
    review_snapshot = capture_review_workspace(repo)

    assert baseline.ignored_manifest_complete is True
    assert baseline.ignored_content_complete is False
    assert baseline.capture_complete is True
    assert review_snapshot.ignored_manifest_complete is True
    assert review_snapshot.ignored_content_complete is False
    assert review_snapshot.ignored_coverage_level == "metadata_bounded"


def test_workspace_check_allows_new_empty_root_agents_directory(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    _ignore_root_agents_directory(repo)
    baseline = snapshot_workspace(repo)

    repo.joinpath(".agents").mkdir()
    result = evaluate_workspace(repo, baseline=baseline)

    assert result.status == "passed"
    assert result.baseline_ignored_changed is False


@pytest.mark.parametrize("entry_kind", ["file", "directory"])
def test_workspace_check_rejects_nonempty_root_agents_directory(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    repo = _init_repo(tmp_path)
    _ignore_root_agents_directory(repo)
    baseline = snapshot_workspace(repo)
    agents_dir = repo / ".agents"
    agents_dir.mkdir()
    if entry_kind == "file":
        agents_dir.joinpath("local.md").write_text("local\n", encoding="utf-8")
    else:
        agents_dir.joinpath("skills").mkdir()

    result = evaluate_workspace(repo, baseline=baseline)

    assert result.status == "failed"
    assert result.baseline_ignored_changed is True
    assert any("ignored 路径" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    ("mode", "file_attributes"),
    [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)],
)
def test_root_agents_link_metadata_is_not_exempted(
    mode: int,
    file_attributes: int,
) -> None:
    path_stat = SimpleNamespace(
        st_mode=mode,
        st_dev=1,
        st_ino=2,
        st_ctime_ns=3,
        st_mtime_ns=4,
        st_file_attributes=file_attributes,
    )

    assert codex_workspace_module._plain_directory_identity(path_stat) is None


def test_workspace_check_fails_when_ignored_manifest_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath(".gitignore").write_text("*.tmp\n", encoding="utf-8")
    _git(repo, "add", "--", ".gitignore")
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "ignore temp files",
    )
    for index in range(2):
        repo.joinpath(f"{index}.tmp").write_text(f"value-{index}\n", encoding="utf-8")
    monkeypatch.setattr(workspace_check_module, "MAX_IGNORED_METADATA_FILES", 1)

    baseline = snapshot_workspace(repo)
    result = evaluate_workspace(repo, baseline=baseline)

    assert baseline.ignored_manifest_complete is False
    assert baseline.ignored_content_complete is False
    assert baseline.capture_complete is False
    assert result.status == "failed"
    assert any("无法完整构建 ignored 清单" in reason for reason in result.reasons)
    assert not any(
        "路径与元数据清单完整" in reason
        for reason in result.reasons
    )


def test_incomplete_ignored_path_enumeration_is_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    original_ignored_paths = workspace_check_module._ignored_paths

    def incomplete_ignored_paths(
        repo_path: Path,
        exclusions: frozenset[str] = frozenset(),
    ) -> tuple[list[str], bool]:
        paths, _ = original_ignored_paths(repo_path, exclusions)
        return paths, False

    monkeypatch.setattr(
        workspace_check_module,
        "_ignored_paths",
        incomplete_ignored_paths,
    )

    snapshot = capture_review_workspace(repo)

    assert snapshot.ignored_manifest_complete is False
    assert snapshot.ignored_coverage_level == "incomplete"


def test_untracked_content_change_during_read_marks_snapshot_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "changing.bin"
    target.write_bytes(b"old")
    original_open = Path.open

    class GrowingReader:
        def __init__(self, stream) -> None:
            self._stream = stream
            self._changed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._stream.__exit__(exc_type, exc_value, traceback)

        def read(self, size: int = -1) -> bytes:
            if not self._changed:
                self._changed = True
                with original_open(target, "wb") as writer:
                    writer.write(b"new-content")
            return self._stream.read(size)

    def changing_open(path: Path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == target and args and args[0] == "rb":
            return GrowingReader(stream)
        return stream

    monkeypatch.setattr(Path, "open", changing_open)

    snapshot = capture_review_workspace(repo)

    assert snapshot.untracked_content_complete is False
    assert len(snapshot.untracked_manifest_sha256) == 64


def test_small_untracked_file_content_change_updates_manifest(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    target = repo / "small.txt"
    target.write_text("alpha\n", encoding="utf-8")
    before = capture_review_workspace(repo)
    original_stat = target.stat()

    target.write_text("bravo\n", encoding="utf-8")
    os.utime(
        target,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    after = capture_review_workspace(repo)

    assert before.untracked_content_complete is True
    assert after.untracked_content_complete is True
    assert before.untracked_manifest_sha256 != after.untracked_manifest_sha256
    assert before.fingerprint != after.fingerprint


def test_existing_tracked_diff_does_not_skip_untracked_baseline_check(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("README.md").write_text(
        "# Demo\nexisting diff\n",
        encoding="utf-8",
        newline="\n",
    )
    local_file = repo / "local.txt"
    local_file.write_text("before\n", encoding="utf-8", newline="\n")
    baseline = snapshot_workspace(repo)

    local_file.write_text("after\n", encoding="utf-8", newline="\n")
    result = evaluate_workspace(
        repo,
        baseline=baseline,
        allow_existing_tracked_diff=True,
    )

    assert result.status == "failed"
    assert result.baseline_tracked_changes_present is True
    assert result.baseline_untracked_changed is True
    assert any(
        "worker 修改或删除了启动前已存在的未跟踪文件" in reason
        for reason in result.reasons
    )


def test_sensitive_untracked_file_is_not_opened_and_is_not_content_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath(".env").write_text("API_KEY=fake\n", encoding="utf-8")
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path.name == ".env":
            raise AssertionError("敏感未跟踪文件不得读取内容")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    snapshot = capture_review_workspace(repo)

    assert snapshot.untracked_content_complete is False
    assert len(snapshot.untracked_manifest_sha256) == 64


def test_review_snapshot_reuses_status_paths_and_preserves_rename_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    source = repo / "old-name.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "--", source.name)
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "add rename source",
    )
    _git(repo, "mv", "--", source.name, "new-name.py")
    repo.joinpath("notes.txt").write_text("untracked\n", encoding="utf-8")

    commands: list[tuple[str, ...]] = []
    original_run_git_bytes = workspace_check_module._run_git_bytes

    def recording_run_git_bytes(
        repo_path: Path,
        command: list[str],
        *,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> bytes:
        commands.append(tuple(command))
        return original_run_git_bytes(
            repo_path,
            command,
            allowed_returncodes=allowed_returncodes,
        )

    monkeypatch.setattr(
        workspace_check_module,
        "_run_git_bytes",
        recording_run_git_bytes,
    )

    snapshot = capture_review_workspace(repo)

    assert snapshot.changed_files == (
        "old-name.py",
        "new-name.py",
        "notes.txt",
    )
    assert snapshot.untracked_files == ("notes.txt",)
    assert len(commands) == 7
    assert (
        "git",
        "rev-parse",
        "--path-format=absolute",
        "--git-dir",
    ) in commands
    assert (
        "git",
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    ) in commands
    assert not any("--name-only" in command for command in commands)
    assert not any(
        command[:4] == ("git", "ls-files", "--others", "--exclude-standard")
        for command in commands
    )


@pytest.mark.parametrize(
    ("payload", "expected_tracked", "expected_untracked"),
    [
        (
            b"MM module.py\0?? notes with spaces.txt\0",
            ["module.py"],
            ["notes with spaces.txt"],
        ),
        (
            b"R  new name.py\0old -> name.py\0"
            b"C  copied.py\0source.py\0"
            b"UU conflict.py\0",
            ["old -> name.py", "new name.py", "copied.py", "conflict.py"],
            [],
        ),
        (
            " M quote\"and\nnewline.py\0?? untracked\nfile.txt\0".encode(),
            ["quote\"and\nnewline.py"],
            ["untracked\nfile.txt"],
        ),
    ],
)
def test_porcelain_v1_path_parser_covers_mm_copy_conflict_and_special_names(
    payload: bytes,
    expected_tracked: list[str],
    expected_untracked: list[str],
) -> None:
    tracked, untracked = workspace_check_module._parse_porcelain_v1_paths(payload)

    assert tracked == expected_tracked
    assert untracked == expected_untracked


def test_target_file_excerpt_uses_bounded_streaming_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "large.py"
    target.write_text("x" * (context_loader.MAX_FILE_CHARS + 100), encoding="utf-8")
    read_sizes: list[int] = []
    original_open = Path.open

    class TrackingReader:
        def __init__(self, stream) -> None:
            self._stream = stream

        def __enter__(self):
            self._stream.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._stream.__exit__(exc_type, exc_value, traceback)

        def read(self, size: int = -1) -> str:
            read_sizes.append(size)
            return self._stream.read(size)

    def tracking_open(path: Path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == target and args and args[0] == "r":
            return TrackingReader(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracking_open)

    result = load_target_context(tmp_path, ["large.py"])[0]

    assert read_sizes == [context_loader.MAX_FILE_CHARS]
    assert result.output["content"] == "x" * context_loader.MAX_FILE_CHARS


def test_directory_context_stops_consuming_entries_at_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for index in range(5):
        source_dir.joinpath(f"{index}.bin").write_bytes(b"x")

    consumed: list[str] = []
    original_scandir = context_loader.os.scandir

    class TrackingScandir:
        def __init__(self, path: Path) -> None:
            self._iterator = original_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._iterator.close()

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self._iterator)
            consumed.append(entry.name)
            return entry

    monkeypatch.setattr(context_loader, "MAX_DIRECTORY_ENTRIES", 2)
    monkeypatch.setattr(context_loader.os, "scandir", TrackingScandir)

    result = load_target_context(tmp_path, ["src"])[0]

    assert result.output["files"] == []
    assert len(consumed) == 2


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8")
    _git(repo, "add", "--", "README.md")
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "init",
    )
    return repo


def _init_repo_with_zero_new_file_budget(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nbudget:\n  max_new_files: 0\n",
        encoding="utf-8",
    )
    _git(repo, "add", "--", ".vega.yaml")
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "add Vega budget",
    )
    return repo


def _ignore_root_agents_directory(repo: Path) -> None:
    repo.joinpath(".gitignore").write_text("/.agents/\n", encoding="utf-8")
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nbudget:\n  max_new_files: 0\n",
        encoding="utf-8",
    )
    _git(repo, "add", "--", ".gitignore", ".vega.yaml")
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "ignore local agent directory",
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=True,
    )
    return result.stdout
