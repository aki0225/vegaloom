from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml

from ...models import LoopSpec
from ...tools.git_tools import ALLOWED_CHECKS

SUPPORTED_TOOLS = {
    "file.search",
    "file.read",
    "repo.run_check",
    "report.write",
    "review.write",
}


def default_engineering_change_spec() -> LoopSpec:
    return LoopSpec(
        name="engineering-change",
        description="本地研发变更审查 loop。",
        input={"task_file": "required", "repo_path": "required"},
        tools={
            "allowed": [
                "file.search",
                "file.read",
                "repo.run_check",
                "report.write",
                "review.write",
            ],
            "disabled": ["patch.apply", "memory.write", "shell.run"],
        },
        inspect={
            "target_section_names": ["Target files", "Target files to search", "目标文件", "目标文件："],
            "search_queries": ["TODO", "FIXME", "risk", "风险", "breaking change", "兼容性"],
            "git_checks": ["git.status", "git.diff", "git.diff_check"],
        },
    )


def list_loop_specs(workspace: Path) -> list[LoopSpec]:
    specs_by_name: dict[str, LoopSpec] = {}
    loop_dir = workspace / "loops"
    if loop_dir.exists():
        for path in sorted(loop_dir.glob("*.loop.yaml")):
            spec = load_loop_spec_file(path)
            specs_by_name[spec.name] = spec

    # workspace 中的同名配置用于显式覆盖；wheel 安装后仍可回退到包内 baseline。
    for path in _packaged_loop_spec_paths():
        spec = load_loop_spec_file(path)
        specs_by_name.setdefault(spec.name, spec)
    return [specs_by_name[name] for name in sorted(specs_by_name)]


def load_loop_spec(workspace: Path, loop_name: str) -> LoopSpec:
    for spec in list_loop_specs(workspace):
        if spec.name == loop_name:
            return spec
    raise ValueError(f"未找到 loop 配置：{loop_name}")


def load_loop_spec_file(path: Path | Traversable) -> LoopSpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"loop 配置必须是 YAML 对象：{path}")
    spec = LoopSpec.model_validate(raw)
    _validate_tool_policy(spec, path)
    _validate_git_checks(spec, path)
    return spec


def _validate_tool_policy(spec: LoopSpec, path: Path | Traversable) -> None:
    allowed = set(spec.tools.allowed)
    disabled = set(spec.tools.disabled)
    unknown = sorted(allowed - SUPPORTED_TOOLS)
    if unknown:
        raise ValueError(f"loop 配置包含不支持的工具 {unknown}：{path}")
    overlap = sorted(allowed & disabled)
    if overlap:
        raise ValueError(f"工具不能同时 allowed 和 disabled {overlap}：{path}")


def _validate_git_checks(spec: LoopSpec, path: Path | Traversable) -> None:
    unknown = sorted(set(spec.inspect.git_checks) - set(ALLOWED_CHECKS))
    if unknown:
        raise ValueError(f"loop 配置包含未授权 git check {unknown}：{path}")


def loop_spec_to_public_dict(spec: LoopSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "allowed_tools": spec.tools.allowed,
        "git_checks": spec.inspect.git_checks,
        "search_queries": spec.inspect.search_queries,
    }


def _packaged_loop_spec_paths() -> list[Traversable]:
    loop_dir = files("vega").joinpath("resources", "loops")
    if not loop_dir.is_dir():
        return []
    return sorted(
        (
            path
            for path in loop_dir.iterdir()
            if path.is_file() and path.name.endswith(".loop.yaml")
        ),
        key=lambda path: path.name,
    )
