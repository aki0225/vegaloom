from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from .extensions import search_memory
from .models import AgentsInstruction, MemoryHit, ProjectKnowledge
from .redaction import filter_sensitive_memory_entries, redact_text
from .repository_identity import repository_scope, resolve_git_revision

IGNORED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "target",
    "__pycache__",
    ".tmp",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".local-validation",
}
ROOT_IGNORED_DIRS = {"runs", "memory"}
IGNORED_DIR_PREFIXES = (
    "pytest-",
    ".tmp-pytest",
    ".pytest-tmp-",
    "pytest-cache-files-",
    "test-output-",
    "validation-",
)
MAX_AGENTS_FILES = 20
MAX_AGENTS_CHARS = 8000
MAX_MEMORY_HITS = 10


def load_project_knowledge(
    workspace: Path,
    repo_path: Path,
    input_text: str,
    related_paths: list[str] | None = None,
    *,
    tracked_only: bool = False,
    tracked_revision: str | None = None,
) -> ProjectKnowledge:
    repo = repo_path.resolve()
    revision = (
        resolve_git_revision(repo, tracked_revision or "HEAD")
        if tracked_only
        else None
    )
    instructions = load_agents_instructions(
        repo,
        related_paths or [],
        tracked_only=tracked_only,
        tracked_revision=revision,
    )
    memory_hits = search_related_memory(workspace, repo, input_text, related_paths or [])
    return ProjectKnowledge(
        repo_name=repo.name,
        repo_path=str(repo),
        agents_instructions=instructions,
        memory_hits=memory_hits,
        missing_agents_md=not instructions,
    )


def load_agents_instructions(
    repo_path: Path,
    related_paths: list[str] | None = None,
    *,
    tracked_only: bool = False,
    tracked_revision: str | None = None,
) -> list[AgentsInstruction]:
    repo = repo_path.resolve()
    if tracked_only:
        try:
            revision = resolve_git_revision(repo, tracked_revision or "HEAD")
        except RuntimeError:
            # 隔离 reviewer 的规则视图必须 fail-closed。非 Git 仓库或无法解析 revision
            # 时不能退回可变工作树内容，否则 worker 可在本轮修改规则后影响 reviewer。
            return []
        if revision is None:
            return []
        return _load_tracked_agents_instructions(repo, related_paths or [], revision)

    discovered: list[Path] = []

    root_agents = repo / "AGENTS.md"
    if _is_safe_worktree_agents_file(repo, root_agents):
        discovered.append(root_agents)

    # 相关路径的父目录 AGENTS.md 优先，避免在大仓库里盲目读取无关规则。
    for relative in related_paths or []:
        normalized = _normalize_repo_relative_path(relative)
        if normalized is None or _is_ignored_repo_path(PurePosixPath(normalized)):
            continue
        candidate = (repo / normalized).resolve()
        if not _path_stays_within_repo(repo, candidate):
            continue
        if candidate.is_file():
            parents = list(candidate.parents)
        else:
            parents = [candidate, *candidate.parents]
        for parent in parents:
            if parent == repo or repo in parent.parents:
                agents = parent / "AGENTS.md"
                if _is_safe_worktree_agents_file(repo, agents):
                    discovered.append(agents)
            if parent == repo:
                break

    for agents in _iter_agents_files(repo):
        discovered.append(agents)

    results: list[AgentsInstruction] = []
    for path in _dedupe_paths(discovered)[:MAX_AGENTS_FILES]:
        if not _is_safe_worktree_agents_file(repo, path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:MAX_AGENTS_CHARS]
        except OSError:
            continue
        results.append(
            AgentsInstruction(
                path=redact_text(_repo_relative(repo, path)),
                scope=_scope_for_agents_file(repo, path),
                content=redact_text(content),
            )
        )
    return results


def search_related_memory(
    workspace: Path,
    repo_path: Path,
    input_text: str,
    related_paths: list[str] | None = None,
) -> list[MemoryHit]:
    repo_scope = repository_scope(repo_path)
    queries = _memory_queries(repo_path.name, input_text, related_paths or [])
    hits = []
    seen: set[str] = set()

    for query in queries:
        entries = search_memory(
            workspace,
            query=query,
            accepted_only=True,
            repo=repo_scope,
        )
        for entry in filter_sensitive_memory_entries(entries):
            if entry.proposal_id not in seen:
                hits.append(_to_memory_hit(entry))
                seen.add(entry.proposal_id)
            if len(hits) >= MAX_MEMORY_HITS:
                return hits

    # repo 为空的通用经验也可能有价值，按关键词再补一次。
    for query in queries[1:]:
        entries = search_memory(
            workspace,
            query=query,
            accepted_only=True,
            repo_unscoped_only=True,
        )
        for entry in filter_sensitive_memory_entries(entries):
            if entry.proposal_id not in seen:
                hits.append(_to_memory_hit(entry))
                seen.add(entry.proposal_id)
            if len(hits) >= MAX_MEMORY_HITS:
                return hits
    return hits


def write_knowledge_context(run_dir: Path, knowledge: ProjectKnowledge) -> None:
    run_dir.joinpath("knowledge-context.md").write_text(
        render_knowledge_context(knowledge),
        encoding="utf-8",
    )


def render_knowledge_context(knowledge: ProjectKnowledge) -> str:
    lines = ["# 项目知识上下文", ""]
    lines.extend(["## 项目", "", f"- 名称：`{knowledge.repo_name}`", ""])

    lines.extend(["## AGENTS.md 规则", ""])
    if knowledge.agents_instructions:
        for item in knowledge.agents_instructions:
            lines.extend(
                [
                    f"### {item.path}",
                    "",
                    f"- 作用域：`{item.scope}`",
                    "",
                    "```md",
                    item.content.strip(),
                    "```",
                    "",
                ]
            )
    else:
        lines.extend(["- 未发现目标仓库 `AGENTS.md`。", ""])

    if knowledge.memory_hits:
        lines.extend(["## 可选的相关历史经验", ""])
        for hit in knowledge.memory_hits:
            tag_text = ", ".join(hit.tags) if hit.tags else "无"
            lines.extend(
                [
                    f"### {hit.title}",
                    "",
                    f"- ID：`{hit.proposal_id}`",
                    f"- 来源仓库：`{hit.repo or '通用'}`",
                    f"- 标签：{tag_text}",
                    f"- 内容：{hit.content}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _iter_agents_files(repo_path: Path) -> list[Path]:
    results: list[Path] = []
    repo = repo_path.resolve()
    for current_root, dir_names, file_names in os.walk(repo, topdown=True, followlinks=False):
        current = Path(current_root)
        current_relative = current.resolve().relative_to(repo)
        dir_names[:] = sorted(
            name
            for name in dir_names
            if not _is_ignored_directory_name(
                name,
                root_level=current_relative == Path("."),
            )
            and _path_stays_within_repo(repo, current / name)
        )
        if "AGENTS.md" in file_names:
            candidate = current / "AGENTS.md"
            if _is_safe_worktree_agents_file(repo, candidate):
                results.append(candidate)
    return sorted(
        results,
        key=lambda item: (
            len(item.resolve().relative_to(repo).parts),
            item.resolve().relative_to(repo).as_posix().casefold(),
        ),
    )


def _load_tracked_agents_instructions(
    repo_path: Path,
    related_paths: list[str],
    revision: str,
) -> list[AgentsInstruction]:
    tracked_paths = _tracked_agents_paths(repo_path, revision)
    discovered: list[str] = []
    if "AGENTS.md" in tracked_paths:
        discovered.append("AGENTS.md")

    for related_path in related_paths:
        normalized = _normalize_repo_relative_path(related_path)
        if normalized is None or _is_ignored_repo_path(PurePosixPath(normalized)):
            continue
        related_parts = PurePosixPath(normalized).parts
        applicable = [
            agents_path
            for agents_path in tracked_paths
            if _agents_scope_applies(PurePosixPath(agents_path), related_parts)
        ]
        discovered.extend(
            sorted(
                applicable,
                key=lambda item: len(PurePosixPath(item).parts),
                reverse=True,
            )
        )

    discovered.extend(tracked_paths)
    results: list[AgentsInstruction] = []
    for relative_path in _dedupe_strings(discovered)[:MAX_AGENTS_FILES]:
        try:
            content = _read_git_blob(repo_path, revision, relative_path)
        except RuntimeError:
            continue
        parent = PurePosixPath(relative_path).parent
        results.append(
            AgentsInstruction(
                path=redact_text(relative_path),
                scope="." if parent == PurePosixPath(".") else parent.as_posix(),
                content=redact_text(content[:MAX_AGENTS_CHARS]),
            )
        )
    return results


def _tracked_agents_paths(repo_path: Path, revision: str) -> list[str]:
    payload = _run_git(
        repo_path,
        ["git", "ls-tree", "-r", "-z", revision, "--"],
    )
    paths: list[str] = []
    for item in payload.split(b"\0"):
        if not item or b"\t" not in item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode = metadata.split(b" ", 1)[0]
        if mode not in {b"100644", b"100755"}:
            continue
        paths.append(raw_path.decode("utf-8", errors="replace"))
    return sorted(
        [
            path
            for path in paths
            if PurePosixPath(path).name == "AGENTS.md"
            and not _is_ignored_repo_path(PurePosixPath(path))
        ],
        key=lambda item: len(PurePosixPath(item).parts),
    )


def _agents_scope_applies(agents_path: PurePosixPath, related_parts: tuple[str, ...]) -> bool:
    scope_parts = agents_path.parent.parts
    if scope_parts == ():
        return True
    return related_parts[: len(scope_parts)] == scope_parts


def _normalize_repo_relative_path(path: str) -> str | None:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        return None
    return normalized.as_posix()


def _is_ignored_directory_name(name: str, *, root_level: bool = False) -> bool:
    normalized = name.casefold()
    return (
        normalized in {item.casefold() for item in IGNORED_DIRS}
        or (root_level and normalized in {item.casefold() for item in ROOT_IGNORED_DIRS})
        or (
            root_level
            and any(
                normalized.startswith(prefix.casefold())
                for prefix in IGNORED_DIR_PREFIXES
            )
        )
    )


def _is_ignored_repo_path(path: PurePosixPath) -> bool:
    return any(
        _is_ignored_directory_name(part, root_level=index == 0)
        for index, part in enumerate(path.parts)
    )


def _path_stays_within_repo(repo_path: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(repo_path.resolve())
    except (OSError, ValueError):
        return False
    return True


def _is_safe_worktree_agents_file(repo_path: Path, candidate: Path) -> bool:
    if candidate.is_symlink() or not candidate.is_file():
        return False
    if not _path_stays_within_repo(repo_path, candidate):
        return False
    relative_path = PurePosixPath(_repo_relative(repo_path, candidate))
    return not _is_ignored_repo_path(relative_path)


def _read_git_blob(repo_path: Path, revision: str, relative_path: str) -> str:
    return _run_git(
        repo_path,
        ["git", "show", f"{revision}:{relative_path}"],
    ).decode("utf-8", errors="replace")


def _run_git(repo_path: Path, command: list[str]) -> bytes:
    result = subprocess.run(
        command,
        cwd=repo_path,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        output = (result.stdout or b"") + (result.stderr or b"")
        raise RuntimeError(output.decode("utf-8", errors="replace").strip())
    return result.stdout or b""

def _memory_queries(repo_name: str, input_text: str, related_paths: list[str]) -> list[str]:
    queries = [repo_name]
    queries.extend(_extract_keywords(input_text))
    for item in related_paths:
        queries.extend(Path(item.replace("\\", "/")).parts)
    return _dedupe_strings([query for query in queries if len(query.strip()) >= 2])[:12]


def _extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"/[\w/.-]+|[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
    stop_words = {"the", "and", "for", "with", "是否", "一个", "这个", "用户", "功能", "问题"}
    return [token for token in tokens if token.lower() not in stop_words]


def _to_memory_hit(entry) -> MemoryHit:
    return MemoryHit(
        proposal_id=entry.proposal_id,
        title=redact_text(entry.title),
        content=redact_text(entry.content),
        tags=[redact_text(tag) for tag in entry.tags],
        repo=redact_text(entry.repo) if entry.repo else None,
        paths=[redact_text(path) for path in entry.paths],
    )


def _scope_for_agents_file(repo_path: Path, agents_path: Path) -> str:
    parent = agents_path.parent
    if parent == repo_path:
        return "."
    return _repo_relative(repo_path, parent)


def _repo_relative(repo_path: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_path.resolve()).as_posix()


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        key = normalized.lower()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result
