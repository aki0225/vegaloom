from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Literal

from .extensions import search_memory
from .git_read import coerce_git_output_bytes, run_git_capture
from .models import ProjectProfile
from .project_config import load_project_config
from .project_knowledge import list_agents_instruction_paths
from .redaction import filter_sensitive_memory_entries, redact_text
from .repository_identity import ResolvedGitRevision, repository_scope, resolve_git_revision

CONFIG_CANDIDATES = [
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Cargo.toml",
    "Dockerfile",
    "docker-compose.yml",
    ".vega.yaml",
    ".vega.yml",
]
KEY_DIRS = ["src", "tests", "docs", "app", "apps", "packages", "cmd", "internal", "pkg", "frontend", "backend"]
ENTRYPOINT_CANDIDATES = [
    "src/main.py",
    "main.py",
    "app.py",
    "src/index.ts",
    "src/main.ts",
    "src/App.tsx",
    "cmd/server/main.go",
    "main.go",
]
NodePackageManager = Literal["npm", "pnpm", "yarn"]
NODE_PACKAGE_MANAGER_BY_LOCKFILE: dict[str, NodePackageManager] = {
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
}
NODE_TEST_COMMANDS: dict[NodePackageManager, str] = {
    "npm": "npm test",
    "pnpm": "pnpm test",
    "yarn": "yarn test",
}
NODE_LINT_COMMANDS: dict[NodePackageManager, str] = {
    "npm": "npm run lint",
    "pnpm": "pnpm run lint",
    "yarn": "yarn lint",
}


def build_project_profile(
    workspace: Path,
    repo_path: Path,
    *,
    tracked_only: bool = False,
    tracked_revision: str | ResolvedGitRevision | None = None,
) -> ProjectProfile:
    repo = repo_path.resolve()
    resolved_revision = (
        resolve_git_revision(repo, tracked_revision or "HEAD")
        if tracked_only
        else None
    )
    tracked_files = (
        _tracked_files(repo, resolved_revision.commit if resolved_revision else None)
        if tracked_only
        else None
    )
    config_files = _existing_files(repo, CONFIG_CANDIDATES, tracked_files=tracked_files)
    project_config = load_project_config(
        repo,
        tracked_only=tracked_only,
        tracked_revision=resolved_revision,
    )
    agents_files = list_agents_instruction_paths(
        repo,
        tracked_only=tracked_only,
        tracked_revision=resolved_revision,
    )
    repo_scope = repository_scope(repo)
    memory_entries = search_memory(workspace, query=repo.name, accepted_only=True, repo=repo_scope)
    memory_hits = filter_sensitive_memory_entries(memory_entries)
    node_package_manager = _detect_node_package_manager(
        repo,
        config_files,
        tracked_revision=resolved_revision.commit if resolved_revision else None,
    )
    test_commands = _detect_test_commands(
        repo,
        config_files,
        tracked_files=tracked_files,
        node_package_manager=node_package_manager,
    )
    lint_commands = _detect_lint_commands(
        config_files,
        node_package_manager=node_package_manager,
    )
    if project_config.verification.commands:
        test_commands = project_config.verification.commands
        lint_commands = []
    return ProjectProfile(
        repo_name=repo.name,
        repo_path=str(repo),
        tech_stack=_detect_tech_stack(repo, config_files, tracked_files=tracked_files),
        package_managers=_detect_package_managers(
            config_files,
            node_package_manager=node_package_manager,
        ),
        test_commands=test_commands,
        lint_commands=lint_commands,
        entrypoints=_existing_files(repo, ENTRYPOINT_CANDIDATES, tracked_files=tracked_files),
        script_entrypoints=_detect_script_entrypoints(
            repo,
            config_files,
            tracked_revision=resolved_revision.commit if resolved_revision else None,
        ),
        key_directories=_existing_dirs(repo, KEY_DIRS, tracked_files=tracked_files),
        config_files=config_files,
        agents_files=agents_files,
        memory_hit_count=len(memory_hits),
    )


def _detect_tech_stack(
    repo: Path,
    config_files: list[str],
    *,
    tracked_files: set[str] | None = None,
) -> list[str]:
    stack: list[str] = []
    if any(item in config_files for item in ["pyproject.toml", "requirements.txt"]):
        stack.append("Python")
    if "package.json" in config_files:
        stack.append("Node.js / TypeScript")
    if "go.mod" in config_files:
        stack.append("Go")
    if "pom.xml" in config_files or "build.gradle" in config_files:
        stack.append("Java")
    if "Cargo.toml" in config_files:
        stack.append("Rust")
    if tracked_files is not None:
        has_dotnet = any(
            "/" not in item and item.lower().endswith((".csproj", ".sln"))
            for item in tracked_files
        )
    else:
        has_dotnet = bool(list(repo.glob("*.csproj")) or list(repo.glob("*.sln")))
    if has_dotnet:
        stack.append(".NET")
    return _dedupe(stack)


def _detect_package_managers(
    config_files: list[str],
    *,
    node_package_manager: NodePackageManager | None,
) -> list[str]:
    managers: list[str] = []
    if "pyproject.toml" in config_files:
        managers.append("pip / pyproject")
    if "requirements.txt" in config_files:
        managers.append("pip requirements")
    if node_package_manager is not None:
        managers.append(node_package_manager)
    if "go.mod" in config_files:
        managers.append("go modules")
    if "pom.xml" in config_files:
        managers.append("maven")
    if "build.gradle" in config_files:
        managers.append("gradle")
    if "Cargo.toml" in config_files:
        managers.append("cargo")
    return managers


def _detect_node_package_manager(
    repo: Path,
    config_files: list[str],
    *,
    tracked_revision: str | None,
) -> NodePackageManager | None:
    if "package.json" in config_files:
        has_declaration, declared_manager = _read_declared_node_package_manager(
            repo,
            tracked_revision=tracked_revision,
        )
        # 显式字段一旦存在但无法识别，就停止猜测，避免陈旧 lockfile 覆盖项目声明。
        if has_declaration:
            return declared_manager

    lockfile_managers = [
        manager
        for lockfile, manager in NODE_PACKAGE_MANAGER_BY_LOCKFILE.items()
        if lockfile in config_files
    ]
    if len(lockfile_managers) == 1:
        return lockfile_managers[0]
    # 多 lockfile 常来自分支切换或错误合并；固定优先级只会稳定地产生错误命令。
    if lockfile_managers:
        return None
    if "package.json" in config_files:
        return "npm"
    return None


def _read_declared_node_package_manager(
    repo: Path,
    *,
    tracked_revision: str | None,
) -> tuple[bool, NodePackageManager | None]:
    content = _read_project_file(
        repo,
        "package.json",
        tracked_revision=tracked_revision,
    )
    if content is None:
        return True, None
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return True, None
    if not isinstance(document, dict):
        return True, None
    if "packageManager" not in document:
        return False, None

    value = document.get("packageManager")
    if not isinstance(value, str):
        return True, None
    manager = value.strip().partition("@")[0].strip()
    if manager == "npm":
        return True, "npm"
    if manager == "pnpm":
        return True, "pnpm"
    if manager == "yarn":
        return True, "yarn"
    return True, None


def _detect_script_entrypoints(
    repo: Path,
    config_files: list[str],
    *,
    tracked_revision: str | None,
) -> list[str]:
    if "pyproject.toml" not in config_files:
        return []
    content = _read_project_file(
        repo,
        "pyproject.toml",
        tracked_revision=tracked_revision,
    )
    if content is None:
        return []
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []
    project = document.get("project")
    if not isinstance(project, dict):
        return []
    scripts = project.get("scripts")
    if not isinstance(scripts, dict):
        return []
    return sorted(
        [
            f"{name.strip()} = {target.strip()}"
            for name, target in scripts.items()
            if isinstance(name, str)
            and isinstance(target, str)
            and name.strip()
            and target.strip()
        ],
        key=str.casefold,
    )


def _read_project_file(
    repo: Path,
    relative_path: str,
    *,
    tracked_revision: str | None,
) -> str | None:
    if tracked_revision is None:
        try:
            return repo.joinpath(relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    try:
        result = run_git_capture(
            repo,
            ["git", "show", f"{tracked_revision}:{relative_path}"],
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"无法读取 tracked project profile 文件 `{redact_text(relative_path)}`。"
        ) from exc
    if result.returncode != 0:
        diagnostic = coerce_git_output_bytes(result.stderr).decode(
            "utf-8",
            errors="replace",
        ).strip()
        suffix = f" Git 诊断：{redact_text(diagnostic)}" if diagnostic else ""
        raise RuntimeError(
            "无法读取 tracked project profile 文件 "
            f"`{redact_text(relative_path)}`。{suffix}"
        )
    return coerce_git_output_bytes(result.stdout).decode(
        "utf-8",
        errors="replace",
    )


def _detect_test_commands(
    repo: Path,
    config_files: list[str],
    *,
    tracked_files: set[str] | None = None,
    node_package_manager: NodePackageManager | None,
) -> list[str]:
    commands: list[str] = []
    if "pyproject.toml" in config_files or _directory_exists(
        repo,
        "tests",
        tracked_files=tracked_files,
    ):
        commands.append("python -m pytest -q")
    if "package.json" in config_files and node_package_manager is not None:
        commands.append(NODE_TEST_COMMANDS[node_package_manager])
    if "go.mod" in config_files:
        commands.append("go test ./...")
    if "pom.xml" in config_files:
        commands.append("mvn test")
    if "build.gradle" in config_files:
        commands.append("gradle test")
    if "Cargo.toml" in config_files:
        commands.append("cargo test")
    return _dedupe(commands)


def _detect_lint_commands(
    config_files: list[str],
    *,
    node_package_manager: NodePackageManager | None,
) -> list[str]:
    commands: list[str] = []
    if "pyproject.toml" in config_files:
        commands.append("python -m ruff check .")
    if "package.json" in config_files and node_package_manager is not None:
        commands.append(NODE_LINT_COMMANDS[node_package_manager])
    if "go.mod" in config_files:
        commands.append("go vet ./...")
    if "Cargo.toml" in config_files:
        commands.append("cargo clippy")
    return _dedupe(commands)


def _existing_files(
    repo: Path,
    candidates: list[str],
    *,
    tracked_files: set[str] | None = None,
) -> list[str]:
    if tracked_files is not None:
        return [item for item in candidates if item in tracked_files]
    return [item for item in candidates if (repo / item).is_file()]


def _existing_dirs(
    repo: Path,
    candidates: list[str],
    *,
    tracked_files: set[str] | None = None,
) -> list[str]:
    return [
        item
        for item in candidates
        if _directory_exists(repo, item, tracked_files=tracked_files)
    ]


def _directory_exists(
    repo: Path,
    relative_path: str,
    *,
    tracked_files: set[str] | None,
) -> bool:
    if tracked_files is None:
        return (repo / relative_path).is_dir()
    prefix = f"{relative_path}/"
    return relative_path in tracked_files or any(
        item.startswith(prefix)
        for item in tracked_files
    )


def _tracked_files(repo: Path, revision: str | None) -> set[str]:
    if revision is None:
        return set()
    command = ["git", "ls-tree", "-r", "--name-only", "-z", revision, "--"]
    try:
        result = run_git_capture(repo, command)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"无法读取 tracked project profile revision `{redact_text(revision)}`。"
        ) from exc
    if result.returncode != 0:
        diagnostic = coerce_git_output_bytes(result.stderr).decode(
            "utf-8",
            errors="replace",
        ).strip()
        suffix = f" Git 诊断：{redact_text(diagnostic)}" if diagnostic else ""
        raise RuntimeError(
            "无法读取 tracked project profile revision "
            f"`{redact_text(revision)}`；已拒绝使用空画像继续。{suffix}"
        )
    return {
        item.decode("utf-8", errors="replace")
        for item in coerce_git_output_bytes(result.stdout).split(b"\0")
        if item
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
