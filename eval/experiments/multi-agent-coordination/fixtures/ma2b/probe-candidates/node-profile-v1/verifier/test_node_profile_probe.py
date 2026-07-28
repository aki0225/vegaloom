from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.models import ProjectKnowledge, ProjectProfile
from vega.project_context import render_project_context
from vega.project_profile import build_project_profile


def test_build_only_script_omits_node_test_and_lint(tmp_path: Path) -> None:
    profile = _build_node_profile(tmp_path, scripts={"build": "vite build"})

    assert profile.package_managers == ["npm"]
    assert profile.test_commands == []
    assert profile.lint_commands == []
    assert profile.profile_issues == []


@pytest.mark.parametrize(
    ("scripts", "expected_test", "expected_lint"),
    [
        pytest.param(
            {"test": "node --test"},
            ["pnpm test"],
            [],
            id="test-only",
        ),
        pytest.param(
            {"lint": "eslint ."},
            [],
            ["pnpm run lint"],
            id="lint-only",
        ),
        pytest.param(
            {"test": "  ", "lint": ""},
            [],
            [],
            id="empty-scripts",
        ),
    ],
)
def test_only_real_scripts_are_recommended(
    tmp_path: Path,
    scripts: dict[str, object],
    expected_test: list[str],
    expected_lint: list[str],
) -> None:
    profile = _build_node_profile(
        tmp_path,
        scripts=scripts,
        package_manager="pnpm@10.13.1",
    )

    assert profile.test_commands == expected_test
    assert profile.lint_commands == expected_lint
    assert profile.profile_issues == []


def test_conflicting_lockfiles_produce_stable_issue_code(tmp_path: Path) -> None:
    profile = _build_node_profile(
        tmp_path,
        scripts={"test": "node --test", "lint": "eslint ."},
        lockfiles=("package-lock.json", "pnpm-lock.yaml"),
    )

    assert profile.package_managers == []
    assert profile.test_commands == []
    assert profile.lint_commands == []
    assert profile.profile_issues == ["node_lockfile_conflict"]


@pytest.mark.parametrize(
    "package_manager",
    [
        pytest.param(42, id="invalid-type"),
        pytest.param("bun@1.2.3", id="unsupported-manager"),
    ],
)
def test_invalid_package_manager_produces_distinct_issue_code(
    tmp_path: Path,
    package_manager: object,
) -> None:
    profile = _build_node_profile(
        tmp_path,
        scripts={"test": "node --test"},
        package_manager=package_manager,
        lockfiles=("pnpm-lock.yaml",),
    )

    assert profile.package_managers == []
    assert profile.test_commands == []
    assert profile.lint_commands == []
    assert profile.profile_issues == ["node_package_manager_invalid"]


def test_valid_declaration_disambiguates_stale_lockfiles(tmp_path: Path) -> None:
    profile = _build_node_profile(
        tmp_path,
        scripts={"test": "node --test"},
        package_manager="yarn@4.9.2",
        lockfiles=("package-lock.json", "pnpm-lock.yaml"),
    )

    assert profile.package_managers == ["yarn"]
    assert profile.test_commands == ["yarn test"]
    assert profile.lint_commands == []
    assert profile.profile_issues == []


def test_explicit_verification_remains_highest_priority(tmp_path: Path) -> None:
    repo = _write_node_repo(
        tmp_path,
        scripts={"test": "node --test", "lint": "eslint ."},
        package_manager="pnpm@10.13.1",
    )
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\n"
        "verification:\n"
        "  commands:\n"
        "    - python -c \"print('configured verification')\"\n",
        encoding="utf-8",
    )

    profile = build_project_profile(tmp_path, repo)

    assert profile.test_commands == [
        "python -c \"print('configured verification')\""
    ]
    assert profile.lint_commands == []
    assert profile.profile_issues == []


def test_tracked_profile_reads_manager_and_scripts_from_same_revision(
    tmp_path: Path,
) -> None:
    repo = _write_node_repo(
        tmp_path,
        scripts={"test": "node --test"},
        package_manager="pnpm@10.13.1",
        lockfiles=("pnpm-lock.yaml",),
    )
    _init_repo(repo)

    repo.joinpath("package.json").write_text(
        _package_json(
            scripts={"lint": "eslint ."},
            package_manager="yarn@4.9.2",
        ),
        encoding="utf-8",
    )
    repo.joinpath("pnpm-lock.yaml").unlink()
    repo.joinpath("yarn.lock").write_text("tracked worktree replacement\n", encoding="utf-8")

    profile = build_project_profile(tmp_path, repo, tracked_only=True)

    assert profile.package_managers == ["pnpm"]
    assert profile.test_commands == ["pnpm test"]
    assert profile.lint_commands == []
    assert profile.profile_issues == []


def test_project_context_renders_profile_issue_codes() -> None:
    profile = ProjectProfile(
        repo_name="demo",
        repo_path="$repoRoot",
        profile_issues=[
            "node_lockfile_conflict",
            "node_package_manager_invalid",
        ],
    )
    knowledge = ProjectKnowledge(repo_name="demo", repo_path="$repoRoot")

    rendered = render_project_context(profile, knowledge)

    assert "node_lockfile_conflict" in rendered
    assert "node_package_manager_invalid" in rendered
    assert "多个 Node lockfile" in rendered
    assert "packageManager 声明无效" in rendered


def _build_node_profile(
    root: Path,
    *,
    scripts: dict[str, object],
    package_manager: object | None = None,
    lockfiles: tuple[str, ...] = (),
) -> ProjectProfile:
    repo = _write_node_repo(
        root,
        scripts=scripts,
        package_manager=package_manager,
        lockfiles=lockfiles,
    )
    return build_project_profile(root, repo)


def _write_node_repo(
    root: Path,
    *,
    scripts: dict[str, object],
    package_manager: object | None = None,
    lockfiles: tuple[str, ...] = (),
) -> Path:
    repo = root / "repo"
    repo.mkdir()
    repo.joinpath("package.json").write_text(
        _package_json(scripts=scripts, package_manager=package_manager),
        encoding="utf-8",
    )
    for lockfile in lockfiles:
        repo.joinpath(lockfile).write_text("fixture\n", encoding="utf-8")
    return repo


def _package_json(
    *,
    scripts: dict[str, object],
    package_manager: object | None,
) -> str:
    payload: dict[str, object] = {"scripts": scripts}
    if package_manager is not None:
        payload["packageManager"] = package_manager
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "probe@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Vega Probe"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "初始化候选仓库"], cwd=repo, check=True)
