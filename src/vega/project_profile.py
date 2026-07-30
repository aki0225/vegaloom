from __future__ import annotations

import json
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Literal

from .extensions import search_memory
from .git_read import coerce_git_output_bytes, run_git_capture
from .models import ProfileState, ProjectProfile
from .project_config import (
    ProjectConfigCheckResult,
    check_project_config,
    load_project_config,
    render_project_config_check,
)
from .project_knowledge import load_agents_instructions
from .redaction import filter_sensitive_memory_entries, redact_text, redact_value
from .repository_identity import ResolvedGitRevision, repository_scope, resolve_git_revision
from .run_utils import create_run_dir
from .trace import TraceWriter

PROFILE_ARTIFACTS = ["state.json", "trace.jsonl", "project-profile.json", "project-profile.md", "eval.md"]
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


class ProjectProfileRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, repo_path: Path) -> Path:
        base_run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-project-profile"
        run_id, run_dir = create_run_dir(self.workspace, base_run_id)
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = ProfileState(run_id=run_id, repo_path=str(repo_path.resolve()), status="running")
        state.current_step = "detect"
        state.save(run_dir / "state.json")
        trace.write("profile_started", repo_path=str(repo_path.resolve()))

        config_check = check_project_config(repo_path)
        if config_check.has_errors:
            return _finish_profile_config_failure(run_dir, trace, state, config_check)

        try:
            profile = build_project_profile(self.workspace, repo_path)
        except Exception as exc:  # noqa: BLE001 - profile run must close with failed state/artifacts
            return _finish_profile_failure(
                run_dir,
                trace,
                state,
                code="profile_build_failed",
                message="项目画像构建失败，已保留失败诊断供人工处理。",
                diagnostic=str(exc),
            )

        run_dir.joinpath("project-profile.json").write_text(
            json.dumps(
                redact_value(profile.model_dump(mode="json")),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        run_dir.joinpath("project-profile.md").write_text(render_project_profile(profile), encoding="utf-8")
        trace.write("profile_written", tech_stack=profile.tech_stack, test_commands=profile.test_commands)

        state.current_step = "eval"
        run_dir.joinpath("eval.md").write_text("# Eval\n\n(pending)\n", encoding="utf-8")
        eval_results = _run_profile_eval(run_dir, profile)
        run_dir.joinpath("eval.md").write_text(_render_eval(eval_results), encoding="utf-8")
        state.eval_results = eval_results
        state.artifacts = PROFILE_ARTIFACTS
        state.status = "failed" if any(item.startswith("FAIL:") for item in eval_results) else "success"
        state.current_step = "done"
        state.save(run_dir / "state.json")
        trace.write("eval_written", file="eval.md", results=eval_results)
        trace.write("run_finished", status=state.status)
        return run_dir


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
    agents = load_agents_instructions(
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
        agents_files=[item.path for item in agents],
        memory_hit_count=len(memory_hits),
    )


def render_project_profile(profile: ProjectProfile) -> str:
    lines = [
        "# Project Profile",
        "",
        f"- 项目：`{profile.repo_name}`",
        "",
        "## 技术栈",
        "",
        *_list_or_none(profile.tech_stack),
        "",
        "## 包管理器 / 构建工具",
        "",
        *_list_or_none(profile.package_managers),
        "",
        "## 推荐测试命令",
        "",
        *_list_or_none(profile.test_commands),
        "",
        "## 推荐静态检查命令",
        "",
        *_list_or_none(profile.lint_commands),
        "",
        "## 入口文件",
        "",
        *_list_or_none(profile.entrypoints),
        "",
        "## CLI / Script 入口",
        "",
        *_list_or_none(profile.script_entrypoints),
        "",
        "## 关键目录",
        "",
        *_list_or_none(profile.key_directories),
        "",
        "## 配置文件",
        "",
        *_list_or_none(profile.config_files),
        "",
        "## AGENTS.md",
        "",
        *_list_or_none(profile.agents_files),
        "",
        "## Memory",
        "",
        f"- 已接受 memory 命中数：{profile.memory_hit_count}",
    ]
    return redact_text("\n".join(lines).rstrip() + "\n")


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


def _run_profile_eval(run_dir: Path, profile: ProjectProfile) -> list[str]:
    results = [
        f"{'PASS' if (run_dir / artifact).exists() else 'FAIL'}: artifact 存在：{artifact}"
        for artifact in PROFILE_ARTIFACTS
    ]
    results.append("PASS: 已识别技术栈" if profile.tech_stack else "FAIL: 未识别技术栈")
    results.append("PASS: 已识别验证命令" if profile.test_commands else "FAIL: 未识别验证命令")
    return results


def _render_eval(results: list[str]) -> str:
    return redact_text("# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n")


def _finish_profile_config_failure(
    run_dir: Path,
    trace: TraceWriter,
    state: ProfileState,
    config_check: ProjectConfigCheckResult,
) -> Path:
    return _finish_profile_failure(
        run_dir,
        trace,
        state,
        code="project_config_invalid",
        message="项目配置预检失败，Project Profile 未继续读取该配置。",
        diagnostic=render_project_config_check(config_check),
        config_check=config_check,
    )


def _finish_profile_failure(
    run_dir: Path,
    trace: TraceWriter,
    state: ProfileState,
    *,
    code: str,
    message: str,
    diagnostic: str,
    config_check: ProjectConfigCheckResult | None = None,
) -> Path:
    safe_message = redact_text(message)
    safe_diagnostic = redact_text(diagnostic)
    payload: dict[str, object] = {
        "status": "failed",
        "code": redact_text(code),
        "message": safe_message,
        "diagnostic": safe_diagnostic,
    }
    if config_check is not None:
        payload["config_check"] = config_check.model_dump(mode="json")

    run_dir.joinpath("project-profile.json").write_text(
        json.dumps(redact_value(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    run_dir.joinpath("project-profile.md").write_text(
        redact_text(
            "\n".join(
                [
                    "# Project Profile",
                    "",
                    safe_message,
                    "",
                    "## Diagnostic",
                    "",
                    safe_diagnostic,
                ]
            ).rstrip()
            + "\n"
        ),
        encoding="utf-8",
    )
    eval_results = [f"FAIL: {safe_message}"]
    if config_check is not None:
        eval_results.extend(
            f"FAIL: {issue.code}：{issue.message}"
            for issue in config_check.issues
            if issue.severity == "error"
        )
    else:
        eval_results.append(f"FAIL: {code}：{safe_diagnostic[:500]}")
    run_dir.joinpath("eval.md").write_text(_render_eval(eval_results), encoding="utf-8")
    state.eval_results = eval_results
    state.artifacts = PROFILE_ARTIFACTS
    state.status = "failed"
    state.current_step = f"{code}_failed"
    state.save(run_dir / "state.json")
    trace.write("profile_failed", code=code, diagnostic=safe_diagnostic)
    trace.write("eval_written", file="eval.md", results=eval_results)
    trace.write("run_finished", status=state.status)
    return run_dir


def _list_or_none(items: list[str]) -> list[str]:
    if not items:
        return ["- 未识别"]
    return [f"- `{item}`" for item in items]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
