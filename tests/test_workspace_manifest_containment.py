from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from vega import workspace_inventory as workspace_inventory_module
from vega.workspace_inventory import ContentManifestBudget, build_content_manifest


def test_parent_traversal_does_not_read_outside_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    _guard_file_open(monkeypatch, outside)

    manifest = build_content_manifest(
        repo,
        ["../outside.txt"],
        version="test-v1",
        budget=_budget(),
    )

    assert manifest.metadata_complete is False
    assert manifest.content_complete is False


@pytest.mark.parametrize(
    "relative_path",
    [
        "C:" + "\\" + "outside.txt",
        "C:outside.txt",
        "\\" + "outside.txt",
        "\\" * 2 + "server" + "\\" + "share" + "\\" + "outside.txt",
        ".." + "\\" + "outside.txt",
        "/" + "outside.txt",
    ],
)
def test_cross_platform_absolute_forms_are_rejected(
    tmp_path: Path,
    relative_path: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    assert (
        workspace_inventory_module._contained_manifest_path(
            repo.resolve(),
            relative_path,
        )
        is None
    )


def test_parent_symlink_does_not_read_outside_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("outside\n", encoding="utf-8")
    link = repo / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建目录符号链接：{exc}")
    _guard_file_open(monkeypatch, secret)

    manifest = build_content_manifest(
        repo,
        ["link/secret.txt"],
        version="test-v1",
        budget=_budget(),
    )

    assert manifest.metadata_complete is False
    assert manifest.content_complete is False


@pytest.mark.skipif(os.name != "nt", reason="Windows junction 专用回归")
def test_parent_junction_does_not_read_outside_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("outside\n", encoding="utf-8")
    junction = repo / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("当前环境不能创建 Windows junction")
    _guard_file_open(monkeypatch, secret)

    manifest = build_content_manifest(
        repo,
        ["junction/secret.txt"],
        version="test-v1",
        budget=_budget(),
    )

    assert manifest.metadata_complete is False
    assert manifest.content_complete is False


def test_final_symlink_records_link_without_reading_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = repo / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建文件符号链接：{exc}")
    _guard_file_open(monkeypatch, outside)

    manifest = build_content_manifest(
        repo,
        ["link.txt"],
        version="test-v1",
        budget=_budget(),
    )

    assert manifest.metadata_complete is True
    assert manifest.content_complete is True


def test_regular_unicode_file_remains_readable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "证据 file.txt"
    target.write_text("content\n", encoding="utf-8")

    manifest = build_content_manifest(
        repo,
        [target.name],
        version="test-v1",
        budget=_budget(),
    )

    assert manifest.metadata_complete is True
    assert manifest.content_complete is True


def _budget() -> ContentManifestBudget:
    return ContentManifestBudget(
        max_content_files=16,
        max_file_bytes=1024,
        max_content_bytes=4096,
        max_metadata_files=16,
    )


def _guard_file_open(
    monkeypatch: pytest.MonkeyPatch,
    forbidden: Path,
) -> None:
    forbidden_resolved = forbidden.resolve()
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path.resolve(strict=False) == forbidden_resolved:
            raise AssertionError("manifest 不得读取仓库外文件")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
