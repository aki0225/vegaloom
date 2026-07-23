from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from vega.experimental.inspection import context_loader
from vega import workspace_check as workspace_check_module
from vega.experimental.inspection.context_loader import load_target_context
from vega.workspace_check import capture_review_workspace


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
    assert len(commands) == 6
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
