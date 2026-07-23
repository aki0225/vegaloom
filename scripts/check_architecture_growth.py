from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path("src/vega")
MODULE_SOFT_LIMIT = 500
COMPLEXITY_PATTERN = re.compile(r"\((?P<actual>\d+)\s*>\s*(?P<limit>\d+)\)")
REMOVED_INTERNAL_MODULE_PATHS = (
    SOURCE_ROOT / "adapter_runtime.py",
    SOURCE_ROOT / "assurance.py",
    SOURCE_ROOT / "context_loader.py",
    SOURCE_ROOT / "eval.py",
    SOURCE_ROOT / "goal_evidence.py",
    SOURCE_ROOT / "goal_runtime.py",
    SOURCE_ROOT / "llm_client.py",
    SOURCE_ROOT / "loop_spec.py",
    SOURCE_ROOT / "memory.py",
    SOURCE_ROOT / "reviewer.py",
    SOURCE_ROOT / "runtime.py",
    SOURCE_ROOT / "state.py",
    SOURCE_ROOT / "tool_broker.py",
)


@dataclass(frozen=True)
class ComplexityFinding:
    path: str
    qualname: str
    complexity: int
    limit: int

    @property
    def key(self) -> tuple[str, str]:
        return self.path, self.qualname


def _run(
    command: list[str],
    *,
    cwd: Path,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
    )


def _git(repo_root: Path, *args: str) -> str:
    return _run(["git", *args], cwd=repo_root).stdout


def _tracked_python_paths(repo_root: Path, ref: str | None = None) -> list[str]:
    if ref:
        output = _git(repo_root, "ls-tree", "-r", "--name-only", ref, "--", SOURCE_ROOT.as_posix())
    else:
        output = _git(repo_root, "ls-files", SOURCE_ROOT.as_posix())
    return sorted(
        path
        for path in output.splitlines()
        if path.endswith(".py") and path.startswith(f"{SOURCE_ROOT.as_posix()}/")
    )


def _materialize_ref(repo_root: Path, ref: str, destination: Path) -> None:
    for relative_path in _tracked_python_paths(repo_root, ref):
        payload = subprocess.check_output(
            ["git", "show", f"{ref}:{relative_path}"],
            cwd=repo_root,
        )
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _ruff_executable() -> str:
    executable = shutil.which("ruff")
    if executable is None:
        raise RuntimeError("未找到 ruff；请先安装项目 dev 依赖。")
    return executable


def _qualname_at(source_path: Path, row: int) -> str:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    parents: dict[ast.AST, ast.AST] = {}
    candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates.append(node)

    exact = [node for node in candidates if node.lineno == row]
    if not exact:
        exact = [
            node
            for node in candidates
            if node.lineno <= row <= int(getattr(node, "end_lineno", node.lineno))
        ]
    if not exact:
        raise RuntimeError(f"无法把 Ruff C901 行号映射到函数：{source_path}:{row}")

    node = min(exact, key=lambda item: int(getattr(item, "end_lineno", item.lineno)) - item.lineno)
    owners: list[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            owners.append(current.name)
        current = parents.get(current)
    return ".".join([*reversed(owners), node.name])


def _ruff_complexity_findings(root: Path) -> dict[tuple[str, str], ComplexityFinding]:
    source_dir = root / SOURCE_ROOT
    if not source_dir.exists():
        return {}
    result = _run(
        [
            _ruff_executable(),
            "check",
            str(source_dir),
            "--select",
            "C901",
            "--output-format",
            "json",
            "--exit-zero",
            "--no-cache",
        ],
        cwd=root,
    )
    payload = json.loads(result.stdout or "[]")
    findings: dict[tuple[str, str], ComplexityFinding] = {}
    for item in payload:
        message = str(item.get("message") or "")
        match = COMPLEXITY_PATTERN.search(message)
        if match is None:
            raise RuntimeError(f"无法解析 Ruff C901 消息：{message}")
        absolute_path = Path(str(item["filename"])).resolve()
        relative_path = absolute_path.relative_to(root.resolve()).as_posix()
        row = int(item["location"]["row"])
        qualname = _qualname_at(absolute_path, row)
        finding = ComplexityFinding(
            path=relative_path,
            qualname=qualname,
            complexity=int(match.group("actual")),
            limit=int(match.group("limit")),
        )
        findings[finding.key] = finding
    return findings


def _module_line_counts(root: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    source_dir = root / SOURCE_ROOT
    if not source_dir.exists():
        return result
    for path in sorted(source_dir.rglob("*.py")):
        relative_path = path.relative_to(root).as_posix()
        result[relative_path] = len(path.read_text(encoding="utf-8").splitlines())
    return result


def _rename_map(repo_root: Path, base_ref: str) -> dict[str, str]:
    output = _git(
        repo_root,
        "diff",
        "--name-status",
        "-M50%",
        base_ref,
        "--",
        SOURCE_ROOT.as_posix(),
    )
    current_to_base: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            current_to_base[parts[2]] = parts[1]
    return current_to_base


def _complexity_issues(
    base: dict[tuple[str, str], ComplexityFinding],
    current: dict[tuple[str, str], ComplexityFinding],
    current_to_base: dict[str, str],
) -> list[str]:
    issues: list[str] = []
    for finding in sorted(current.values(), key=lambda item: item.key):
        base_path = current_to_base.get(finding.path, finding.path)
        previous = base.get((base_path, finding.qualname))
        if previous is None:
            issues.append(
                "新增 C901："
                f"{finding.path}:{finding.qualname} complexity={finding.complexity}>{finding.limit}"
            )
            continue
        if finding.complexity > previous.complexity:
            issues.append(
                "C901 复杂度增长："
                f"{finding.path}:{finding.qualname} "
                f"{previous.complexity}->{finding.complexity}"
            )
    return issues


def _module_size_issues(
    base: dict[str, int],
    current: dict[str, int],
    current_to_base: dict[str, str],
) -> list[str]:
    issues: list[str] = []
    for path, line_count in sorted(current.items()):
        base_path = current_to_base.get(path, path)
        previous = base.get(base_path)
        if previous is None:
            if line_count > MODULE_SOFT_LIMIT:
                issues.append(
                    f"新增模块超过 {MODULE_SOFT_LIMIT} 行：{path}={line_count}"
                )
            continue
        if previous > MODULE_SOFT_LIMIT and line_count > previous:
            issues.append(f"既有大模块继续增长：{path} {previous}->{line_count}")
        elif previous <= MODULE_SOFT_LIMIT < line_count:
            issues.append(
                f"模块越过 {MODULE_SOFT_LIMIT} 行门槛：{path} {previous}->{line_count}"
            )
    return issues


def _experimental_import(node: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(node, ast.Import):
        return any(name.name.startswith("vega.experimental") for name in node.names)
    module = node.module or ""
    if node.level:
        return module == "experimental" or module.startswith("experimental.")
    return module == "vega.experimental" or module.startswith("vega.experimental.")


def _core_import_issues(repo_root: Path) -> list[str]:
    issues: list[str] = []
    source_dir = repo_root / SOURCE_ROOT
    for path in sorted(source_dir.rglob("*.py")):
        relative_path = path.relative_to(repo_root).as_posix()
        if relative_path.startswith("src/vega/experimental/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if not _experimental_import(node):
                continue
            if relative_path == "src/vega/cli.py" and _inside_function(node, parents):
                continue
            issues.append(
                f"核心模块静态依赖实验模块：{relative_path}:{node.lineno}"
            )
    return issues


def _inside_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        current = parents.get(current)
    return False


def _removed_internal_module_issues(repo_root: Path) -> list[str]:
    issues: list[str] = []
    for relative_path in REMOVED_INTERNAL_MODULE_PATHS:
        if repo_root.joinpath(relative_path).exists():
            issues.append(
                "已移除的内部模块不得恢复兼容层："
                f"{relative_path.as_posix()}；稳定入口是 CLI"
            )
    return issues


def check_architecture_growth(repo_root: Path, base_ref: str) -> list[str]:
    repo_root = repo_root.resolve()
    _git(repo_root, "rev-parse", "--verify", base_ref)
    with tempfile.TemporaryDirectory(prefix="vega-architecture-base-") as temp_dir:
        base_root = Path(temp_dir)
        _materialize_ref(repo_root, base_ref, base_root)
        base_complexity = _ruff_complexity_findings(base_root)
        current_complexity = _ruff_complexity_findings(repo_root)
        base_sizes = _module_line_counts(base_root)
        current_sizes = _module_line_counts(repo_root)

    renames = _rename_map(repo_root, base_ref)
    issues = _complexity_issues(base_complexity, current_complexity, renames)
    issues.extend(_module_size_issues(base_sizes, current_sizes, renames))
    issues.extend(_core_import_issues(repo_root))
    issues.extend(_removed_internal_module_issues(repo_root))
    if not issues:
        print(
            "架构增长门禁通过："
            f"C901 {len(base_complexity)}->{len(current_complexity)}，"
            f"Python 模块 {len(base_sizes)}->{len(current_sizes)}"
        )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Vega 架构复杂度和依赖方向是否继续增长。")
    parser.add_argument(
        "--base-ref",
        required=True,
        help="可信比较基线，例如 origin/main 或 PR base SHA。",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="仓库根目录，默认当前目录。",
    )
    args = parser.parse_args()

    try:
        issues = check_architecture_growth(args.repo_root, args.base_ref)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"架构增长门禁无法完成：{exc}")
        return 2
    if issues:
        print("架构增长门禁失败：")
        for issue in issues:
            print(f"- {issue}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
