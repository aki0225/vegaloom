from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega.experimental.adapter_runtime import init_adapter
from vega.cli import app


def _create_directory_link(link_path: Path, target_path: Path) -> None:
    """创建能被 pathlib 解析的真实目录链接，Windows 使用无需管理员权限的 junction。"""

    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
            capture_output=True,
            text=True,
            errors="replace",
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


@pytest.mark.parametrize(
    ("force", "preexisting"),
    [
        pytest.param(False, True, id="skip-existing"),
        pytest.param(True, False, id="force"),
    ],
)
def test_adapter_init_rejects_external_directory_link(
    tmp_path: Path,
    force: bool,
    preexisting: bool,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    outside_skills = [
        outside / "skills" / skill_name / "SKILL.md"
        for skill_name in ("vega-loop", "vega-review")
    ]
    if preexisting:
        for skill_path in outside_skills:
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text("outside sentinel\n", encoding="utf-8")
    link_path = repo / ".agents"
    _create_directory_link(link_path, outside)

    args = ["adapters", "init", "codex", "--repo", str(repo)]
    if force:
        args.append("--force")
    try:
        with pytest.raises(ValueError) as exc_info:
            init_adapter(repo, "codex", force=force)

        assert "adapter 写入路径越过目标仓库边界" in str(exc_info.value)
        assert str(outside) not in str(exc_info.value)

        result = CliRunner().invoke(app, args)

        assert result.exit_code != 0
        assert ".agents/skills/vega-loop/SKILL.md" in result.output
        assert str(outside) not in result.output
        if preexisting:
            assert all(
                skill_path.read_text(encoding="utf-8") == "outside sentinel\n"
                for skill_path in outside_skills
            )
        else:
            assert all(not skill_path.exists() for skill_path in outside_skills)
    finally:
        _remove_directory_link(link_path)


def test_adapter_init_preflights_all_targets_before_writing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skills_dir = repo / ".agents" / "skills"
    outside = tmp_path / "outside-review"
    skills_dir.mkdir(parents=True)
    outside.mkdir()
    review_link = skills_dir / "vega-review"
    _create_directory_link(review_link, outside)

    try:
        with pytest.raises(ValueError) as exc_info:
            init_adapter(repo, "codex")

        assert "adapter 写入路径越过目标仓库边界" in str(exc_info.value)
        assert not skills_dir.joinpath("vega-loop", "SKILL.md").exists()
        assert not outside.joinpath("SKILL.md").exists()
    finally:
        _remove_directory_link(review_link)


def test_adapter_init_allows_directory_link_resolving_inside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    internal_target = repo / "adapter-data"
    repo.mkdir()
    internal_target.mkdir()
    link_path = repo / ".agents"
    _create_directory_link(link_path, internal_target)

    try:
        result = CliRunner().invoke(
            app,
            ["adapters", "init", "codex", "--repo", str(repo)],
        )

        assert result.exit_code == 0, result.output
        for skill_name in ("vega-loop", "vega-review"):
            skill_path = link_path / "skills" / skill_name / "SKILL.md"
            assert skill_path.exists()
            assert skill_path.resolve().is_relative_to(repo.resolve())
    finally:
        _remove_directory_link(link_path)
