from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from vega.adapter_runtime import init_adapter


def _create_directory_link(link_path: Path, target_path: Path) -> None:
    """创建能被 pathlib 解析的目录链接，Windows 使用无需管理员权限的 junction。"""

    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(
                "无法创建 Windows junction："
                f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
            )
        return
    link_path.symlink_to(target_path, target_is_directory=True)


def _remove_directory_link(link_path: Path) -> None:
    if not os.path.lexists(link_path):
        return
    if os.name == "nt":
        link_path.rmdir()
        return
    link_path.unlink()


def test_adapter_init_rejects_external_link_before_writing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    link_path = repo / ".codex"
    _create_directory_link(link_path, outside)

    try:
        with pytest.raises(ValueError, match="越过目标仓库边界"):
            init_adapter(repo, "codex", force=True)

        assert not outside.joinpath(
            "skills",
            "vega-loop",
            "SKILL.md",
        ).exists()
        assert not outside.joinpath(
            "skills",
            "vega-review",
            "SKILL.md",
        ).exists()
    finally:
        _remove_directory_link(link_path)


def test_adapter_init_preflights_all_targets_before_writing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skills_dir = repo / ".codex" / "skills"
    outside = tmp_path / "outside-review"
    skills_dir.mkdir(parents=True)
    outside.mkdir()
    review_link = skills_dir / "vega-review"
    _create_directory_link(review_link, outside)

    try:
        with pytest.raises(ValueError, match="越过目标仓库边界"):
            init_adapter(repo, "codex")

        assert not skills_dir.joinpath("vega-loop", "SKILL.md").exists()
        assert not outside.joinpath("SKILL.md").exists()
    finally:
        _remove_directory_link(review_link)
