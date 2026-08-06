from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

# 脚本可能被从仓库外直接调用；优先加载当前仓库的源码，避免普通安装包漂移。
_LOCAL_SRC = Path(__file__).resolve().parents[1] / "src"
if _LOCAL_SRC.is_dir():
    sys.path.insert(0, str(_LOCAL_SRC))

from vega.project_config import (  # noqa: E402
    load_project_config,
    render_project_config_summary,
)
from vega.project_context import render_project_context  # noqa: E402
from vega.project_knowledge import load_project_knowledge  # noqa: E402
from vega.project_profile import build_project_profile  # noqa: E402
from vega.redaction import redact_text, sensitive_path_reason  # noqa: E402
from vega.review_runtime import render_review_prompt  # noqa: E402


EXPERIMENT_ID = "RCB-01"
SCHEMA_VERSION = 1
CONTROL_MANIFEST = Path("scripts/rcb01_cases.json")
SCRIPT_PATH = Path("scripts/rcb01_materializer.py")
LOCAL_OUTPUT_ROOT = Path(".local-validation/rcb-01")
MAX_CANDIDATES = 12
MAX_CANDIDATE_BYTES = 200_000
MAX_VERIFICATION_COMMAND_SECONDS = 60.0
ALLOWED_ROLES = frozenset(
    {"caller", "callee", "test", "config", "contract", "architecture"}
)
ALLOWED_VERIFICATION_STATUSES = frozenset({"passed", "failed", "timed_out"})
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{5,}\b")
CONFIG_NAMES = frozenset(
    {
        ".vega.yaml",
        ".vega.yml",
        "Cargo.toml",
        "Dockerfile",
        "build.gradle",
        "docker-compose.yml",
        "go.mod",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
    }
)
EXCLUDED_PATH_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "generated",
        "node_modules",
        "target",
        "vendor",
    }
)
HIGH_CONFIDENCE_SENSITIVE_DIRS = frozenset(
    {
        ".credential",
        ".credentials",
        ".secret",
        ".secrets",
        "credential",
        "credentials",
        "secret",
        "secrets",
    }
)
HIGH_CONFIDENCE_SENSITIVE_FILENAMES = frozenset(
    {
        "credential",
        "credentials",
        "credential.yaml",
        "credential.yml",
        "credentials.yaml",
        "credentials.yml",
        "secret",
        "secrets",
        "secret.yaml",
        "secret.yml",
        "secrets.yaml",
        "secrets.yml",
    }
)
MATERIALIZATION_ARTIFACT_NAMES = frozenset(
    {
        "arm-a-prompt.md",
        "arm-b-prompt.md",
        "changed-files.json",
        "context-appendix.md",
        "core-review-prompt.md",
        "diff-summary.md",
        "full-diff.patch",
        "impact-candidates.json",
        "materialization.json",
        "project-context.md",
        "task.md",
        "test-summary.md",
        "verification-result.json",
    }
)
MATERIALIZATION_MANIFEST_FIELDS = frozenset(
    {
        "artifacts",
        "base_revision",
        "budgets",
        "candidate_revision",
        "candidate_tree",
        "case_id",
        "changed_files_sha256",
        "context_appendix_chars",
        "core_pack_chars",
        "diff_sha256",
        "diff_size",
        "experiment_id",
        "generator_revision",
        "impact_candidate_count",
        "runtime_commit",
        "runtime_src_tree",
        "schema_version",
        "total_prompt_chars",
    }
)
class MaterializationError(ValueError):
    """RCB-01 输入或 Artifact 不能满足预注册合同时使用。"""


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    base_revision: str
    candidate_revision: str
    expected_changed_file_count: int
    expected_diff_size: int
    expected_diff_sha256: str
    task: str
    verification_commands: tuple[str, ...]


@dataclass(frozen=True)
class ControlSpec:
    runtime_commit: str
    runtime_src_tree: str
    budgets: dict[str, int]
    cases: dict[str, CaseSpec]
    raw_bytes: bytes


@dataclass(frozen=True)
class TreeEntry:
    path: str
    mode: str
    object_type: str
    object_id: str
    size: int | None


@dataclass(frozen=True)
class ImpactCandidate:
    path: str
    role: Literal["caller", "callee", "test", "config", "contract", "architecture"]
    reason: str
    rank: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "rank": self.rank,
            "reason": self.reason,
            "role": self.role,
        }


def load_control_spec(
    repo_root: Path,
    *,
    revision: str | None = None,
) -> ControlSpec:
    repo = repo_root.resolve()
    raw = (
        _git_bytes(repo, "show", f"{revision}:{CONTROL_MANIFEST.as_posix()}")
        if revision
        else repo.joinpath(CONTROL_MANIFEST).read_bytes()
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("RCB-01 控制 manifest 不是合法 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise MaterializationError("RCB-01 控制 manifest 顶层必须是 JSON 对象")
    if set(payload) != {
        "budgets",
        "cases",
        "experiment_id",
        "runtime_commit",
        "runtime_src_tree",
        "schema_version",
    }:
        raise MaterializationError("RCB-01 控制 manifest 顶层字段不完整")
    if payload["schema_version"] != SCHEMA_VERSION or payload["experiment_id"] != EXPERIMENT_ID:
        raise MaterializationError("RCB-01 控制 manifest 版本或实验 ID 不一致")
    runtime_commit = _require_hex(payload["runtime_commit"], 40, "runtime_commit")
    runtime_src_tree = _require_hex(payload["runtime_src_tree"], 40, "runtime_src_tree")
    budgets = _validate_budgets(payload["budgets"])
    case_items = payload["cases"]
    if not isinstance(case_items, list) or len(case_items) != 5:
        raise MaterializationError("RCB-01 必须精确登记 5 个案例")
    cases: dict[str, CaseSpec] = {}
    for item in case_items:
        case = _parse_case(item)
        if case.case_id in cases:
            raise MaterializationError(f"RCB-01 案例重复：{case.case_id}")
        cases[case.case_id] = case
    if list(cases) != ["C1", "C2", "C3", "C4", "C5"]:
        raise MaterializationError("RCB-01 案例顺序必须固定为 C1 至 C5")
    lowered = raw.lower()
    if b'"oracle' in lowered or b'"golden' in lowered:
        raise MaterializationError("控制 manifest 不得包含 oracle 或 Golden")
    return ControlSpec(
        runtime_commit=runtime_commit,
        runtime_src_tree=runtime_src_tree,
        budgets=budgets,
        cases=cases,
        raw_bytes=raw,
    )


def materialize_case(
    repo_root: Path,
    output_dir: Path,
    case_id: str,
    verification_payload: dict[str, Any],
    *,
    generator_revision: str,
    require_formal_state: bool = True,
) -> dict[str, Any]:
    repo = repo_root.resolve()
    control = load_control_spec(repo)
    case = _require_case(control, case_id)
    generator_revision = _resolve_commit(repo, generator_revision)
    if require_formal_state:
        _assert_formal_generator_state(repo, control, generator_revision)
    else:
        _assert_runtime_binding(repo, control, generator_revision)
    verification = _validate_verification_payload(case, verification_payload)
    output = _validate_output_dir(repo, output_dir)
    output.mkdir(parents=True, exist_ok=False)

    artifacts, metadata = _build_case_artifacts(
        repo,
        control,
        case,
        verification,
        generator_revision,
    )
    for name, content in artifacts.items():
        _write_bytes_exclusive(output / name, content)
    manifest = {
        "artifacts": {
            name: {"sha256": _sha256_bytes(content), "size": len(content)}
            for name, content in sorted(artifacts.items())
        },
        "base_revision": case.base_revision,
        "budgets": dict(sorted(control.budgets.items())),
        "candidate_revision": case.candidate_revision,
        "candidate_tree": metadata["candidate_tree"],
        "case_id": case.case_id,
        "changed_files_sha256": metadata["changed_files_sha256"],
        "context_appendix_chars": metadata["context_appendix_chars"],
        "core_pack_chars": metadata["core_pack_chars"],
        "diff_sha256": case.expected_diff_sha256,
        "diff_size": case.expected_diff_size,
        "experiment_id": EXPERIMENT_ID,
        "generator_revision": generator_revision,
        "impact_candidate_count": metadata["impact_candidate_count"],
        "runtime_commit": control.runtime_commit,
        "runtime_src_tree": control.runtime_src_tree,
        "schema_version": SCHEMA_VERSION,
        "total_prompt_chars": metadata["total_prompt_chars"],
    }
    _write_bytes_exclusive(
        output / "materialization.json",
        _canonical_json_bytes(manifest),
    )
    validate_materialization(repo, output, require_formal_state=require_formal_state)
    return manifest


def validate_materialization(
    repo_root: Path,
    case_dir: Path,
    *,
    require_formal_state: bool = True,
) -> dict[str, Any]:
    """离线重建并校验 Artifact。

    正式校验默认要求当前 checkout 与 generator_revision 完全一致。仅供构造性
    单测使用的非正式路径必须由调用方显式传入 ``False``，CLI 不暴露该绕过。
    """
    repo = repo_root.resolve()
    output = _validate_output_dir(repo, case_dir)
    actual_names = _validate_case_directory_entries(output)
    manifest_path = output / "materialization.json"
    if not manifest_path.is_file():
        raise MaterializationError("materialization.json 不存在")
    manifest = _read_json(manifest_path)
    if set(manifest) != MATERIALIZATION_MANIFEST_FIELDS:
        raise MaterializationError("materialization.json 顶层字段不完整或包含额外字段")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise MaterializationError("materialization.json schema_version 不一致")
    if manifest.get("experiment_id") != EXPERIMENT_ID:
        raise MaterializationError("materialization.json experiment_id 不一致")
    generator_revision = _require_hex(
        manifest.get("generator_revision"),
        40,
        "generator_revision",
    )
    control = load_control_spec(repo, revision=generator_revision)
    if require_formal_state:
        _assert_formal_generator_state(repo, control, generator_revision)
    else:
        _assert_runtime_binding(repo, control, generator_revision)
    case = _require_case(control, str(manifest.get("case_id") or ""))
    verification = _validate_verification_payload(
        case,
        _read_json(output / "verification-result.json"),
    )
    expected_artifacts, expected_metadata = _build_case_artifacts(
        repo,
        control,
        case,
        verification,
        generator_revision,
    )
    expected_names = {*expected_artifacts, "materialization.json"}
    if actual_names != expected_names:
        raise MaterializationError("案例目录 Artifact 集合不完整或包含额外文件")
    artifact_contract = manifest.get("artifacts")
    if not isinstance(artifact_contract, dict) or set(artifact_contract) != set(
        expected_artifacts
    ):
        raise MaterializationError("materialization.json Artifact 合同不完整")
    for name, expected_content in expected_artifacts.items():
        actual_content = output.joinpath(name).read_bytes()
        if actual_content != expected_content:
            raise MaterializationError(f"Artifact 字节与确定性重建不一致：{name}")
        expected_contract = {
            "sha256": _sha256_bytes(expected_content),
            "size": len(expected_content),
        }
        if artifact_contract.get(name) != expected_contract:
            raise MaterializationError(f"Artifact 哈希合同不一致：{name}")
    _validate_manifest_metadata(manifest, case, control, expected_metadata)
    return manifest


def generate_impact_candidates(
    repo_root: Path,
    case: CaseSpec,
) -> list[ImpactCandidate]:
    repo = repo_root.resolve()
    entries = _tree_entries(repo, case.candidate_revision)
    changed_files = _changed_files(repo, case)
    changed_set = set(changed_files)
    texts = _eligible_texts(repo, entries)
    module_index = _python_module_index(texts)
    changed_source_paths = [
        path
        for path in changed_files
        if path.startswith("src/") and path.endswith(".py") and path in texts
    ]
    candidates: dict[str, ImpactCandidate] = {}

    direct_callees: dict[str, int] = {}
    for path in changed_source_paths:
        for target, weight in _local_import_targets(
            path,
            texts[path],
            module_index,
        ).items():
            if target not in changed_set:
                direct_callees[target] = direct_callees.get(target, 0) + weight
    for path, weight in direct_callees.items():
        _add_candidate(
            candidates,
            path,
            "callee",
            30 - min(weight, 9),
            f"变更模块直接导入该模块，导入符号数为 {weight}",
            texts,
            changed_set,
        )

    for path, weight in direct_callees.items():
        text = texts.get(path)
        if text is None or not path.endswith(".py"):
            continue
        for target, nested_weight in _local_import_targets(
            path,
            text,
            module_index,
        ).items():
            if target in changed_set or target in direct_callees:
                continue
            _add_candidate(
                candidates,
                target,
                "callee",
                38 - min(nested_weight, 3),
                f"变更模块的直接依赖继续导入该模块，导入符号数为 {nested_weight}",
                texts,
                changed_set,
            )

    for path, text in texts.items():
        if path in changed_set or not path.endswith(".py"):
            continue
        imports = _local_import_targets(path, text, module_index)
        weight = sum(
            value for target, value in imports.items() if target in changed_source_paths
        )
        if not weight:
            continue
        role: Literal["caller", "test"] = "test" if path.startswith("tests/") else "caller"
        base_rank = 10 if role == "test" else 20
        _add_candidate(
            candidates,
            path,
            role,
            base_rank - min(weight, 5),
            f"该文件直接导入 {weight} 个变更模块符号",
            texts,
            changed_set,
        )

    tokens = _changed_reference_tokens(
        repo,
        case,
        changed_source_paths,
        texts,
    )
    for path in _git_grep_paths(repo, case.candidate_revision, tokens):
        if path in changed_set or path not in texts:
            continue
        role, rank = _classify_reference_path(path)
        if role is None:
            continue
        _add_candidate(
            candidates,
            path,
            role,
            rank,
            "该文件命中变更模块或变更符号的确定性文本引用",
            texts,
            changed_set,
        )

    ordered = sorted(
        candidates.values(),
        key=lambda item: (item.rank, item.role, item.path),
    )[:MAX_CANDIDATES]
    _validate_candidate_list(ordered, case.candidate_revision)
    return ordered


def _build_case_artifacts(
    repo: Path,
    control: ControlSpec,
    case: CaseSpec,
    verification: dict[str, Any],
    generator_revision: str,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    diff = _case_diff(repo, case)
    changed_status = _changed_status(repo, case)
    changed_paths = [item["path"] for item in changed_status]
    changed_payload = {
        "base_revision": case.base_revision,
        "files": changed_status,
        "schema_version": SCHEMA_VERSION,
        "source_revision": case.candidate_revision,
    }
    changed_bytes = _canonical_json_bytes(changed_payload)
    changed_sha256 = _sha256_bytes(changed_bytes)
    diff_summary = _render_diff_summary(case, changed_status)
    test_summary = _render_test_summary(verification)
    project_context = _build_project_context(
        repo,
        control,
        case,
        changed_paths,
        diff_summary,
    )
    core_prompt = _build_core_prompt(
        case,
        control,
        changed_paths,
        diff,
        diff_summary,
        test_summary,
        project_context,
    )
    candidates = generate_impact_candidates(repo, case)
    impact_payload = {
        "candidates": [item.as_dict() for item in candidates],
        "changed_files_sha256": changed_sha256,
        "generator_revision": generator_revision,
        "schema_version": SCHEMA_VERSION,
        "source_revision": case.candidate_revision,
    }
    impact_bytes = _canonical_json_bytes(impact_payload)
    appendix = _render_context_appendix(
        case,
        impact_payload,
        _sha256_bytes(impact_bytes),
    )
    _enforce_budgets(control, diff, core_prompt, appendix)
    arm_a = core_prompt
    arm_b = core_prompt + "\n" + appendix
    artifacts = {
        "arm-a-prompt.md": arm_a.encode("utf-8"),
        "arm-b-prompt.md": arm_b.encode("utf-8"),
        "changed-files.json": changed_bytes,
        "context-appendix.md": appendix.encode("utf-8"),
        "core-review-prompt.md": core_prompt.encode("utf-8"),
        "diff-summary.md": diff_summary.encode("utf-8"),
        "full-diff.patch": diff,
        "impact-candidates.json": impact_bytes,
        "project-context.md": project_context.encode("utf-8"),
        "task.md": (case.task.rstrip() + "\n").encode("utf-8"),
        "test-summary.md": test_summary.encode("utf-8"),
        "verification-result.json": _canonical_json_bytes(verification),
    }
    metadata = {
        "candidate_tree": _git_text(
            repo,
            "rev-parse",
            f"{case.candidate_revision}^{{tree}}",
        ).strip(),
        "changed_files_sha256": changed_sha256,
        "context_appendix_chars": len(appendix),
        "core_pack_chars": len(core_prompt),
        "impact_candidate_count": len(candidates),
        "total_prompt_chars": len(arm_b),
    }
    return artifacts, metadata


def _build_project_context(
    repo: Path,
    control: ControlSpec,
    case: CaseSpec,
    changed_files: list[str],
    diff_summary: str,
) -> str:
    with tempfile.TemporaryDirectory(prefix="rcb01-empty-workspace-") as temp_dir:
        workspace = Path(temp_dir)
        profile = build_project_profile(
            workspace,
            repo,
            tracked_only=True,
            tracked_revision=case.candidate_revision,
        ).model_copy(update={"repo_path": "<candidate-worktree-path>"})
        knowledge = load_project_knowledge(
            workspace,
            repo,
            f"{case.task}\n{diff_summary}",
            changed_files,
            tracked_only=True,
            tracked_revision=case.candidate_revision,
        )
        if knowledge.memory_hits:
            raise MaterializationError("RCB-01 项目上下文不得注入 accepted memory")
        config = load_project_config(
            repo,
            tracked_only=True,
            tracked_revision=case.candidate_revision,
        )
        prompt_budget = config.prompt_budget.model_copy(
            update={
                "reviewer_diff_max_chars": control.budgets[
                    "reviewer_diff_max_chars"
                ],
                "reviewer_max_chars": control.budgets["core_pack_max_chars"],
            }
        )
        config = config.model_copy(update={"prompt_budget": prompt_budget})
        return render_project_context(
            profile,
            knowledge,
            render_project_config_summary(config),
        )


def _build_core_prompt(
    case: CaseSpec,
    control: ControlSpec,
    changed_files: list[str],
    diff: bytes,
    diff_summary: str,
    test_summary: str,
    project_context: str,
) -> str:
    full_diff = redact_text(diff.decode("utf-8", errors="replace"))
    if len(full_diff) > control.budgets["reviewer_diff_max_chars"]:
        raise MaterializationError("Full Diff 超过预注册 reviewer_diff_max_chars")
    inputs = {
        "changed_files": changed_files,
        "current_ignored_coverage_level": "full_content",
        "diff_summary": diff_summary,
        "evidence_diagnostics": [],
        "evidence_issues": [],
        "full_diff": full_diff,
        "project_context": project_context,
        "reflection": "- 历史 candidate 静态重放；控制端没有提供 Worker 对话或实现自述。",
        "risk_gate": None,
        "source_brief": case.task,
        "source_ignored_coverage_level": "full_content",
        "test_summary": test_summary,
        "truncated_sections": [],
    }
    return render_review_prompt(inputs)


def _render_context_appendix(
    case: CaseSpec,
    impact_payload: dict[str, Any],
    impact_sha256: str,
) -> str:
    impact_json = json.dumps(
        impact_payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return "\n".join(
        [
            "# Context Appendix",
            "",
            f"- Case：`{case.case_id}`",
            f"- candidate：`{case.candidate_revision}`",
            f"- impact-candidates.json SHA-256：`{impact_sha256}`",
            "",
            "## 只读 Reconnaissance",
            "",
            "在输出最终 JSON Verdict 前：",
            "",
            "1. 先读取完整变更文件在 candidate revision 中的必要上下文，不能只看 Diff hunk。",
            "2. 再按 `rank`、`role`、`path` 顺序检查下列影响面候选。",
            "3. 只在判断当前任务确有必要时继续跟随直接引用，不通读全仓。",
            "4. 不运行测试、构建、安装、格式化或任何可能写入文件和缓存的命令。",
            "5. 候选清单只是导航，不是已确认事实；最终 finding 必须引用可复核代码证据。",
            "",
            "## impact-candidates.json",
            "",
            "```json",
            impact_json,
            "```",
            "",
        ]
    )


def _render_diff_summary(
    case: CaseSpec,
    changed_status: list[dict[str, str]],
) -> str:
    return "\n".join(
        [
            "# Diff Summary",
            "",
            f"- base：`{case.base_revision}`",
            f"- candidate：`{case.candidate_revision}`",
            f"- 变更文件数：`{len(changed_status)}`",
            f"- 原始 Diff 字节数：`{case.expected_diff_size}`",
            f"- 原始 Diff SHA-256：`{case.expected_diff_sha256}`",
            "",
            "## 文件",
            "",
            *[
                f"- `{item['status']}` `{item['path']}`"
                for item in changed_status
            ],
            "",
        ]
    )


def _render_test_summary(verification: dict[str, Any]) -> str:
    lines = [
        "# Test Summary",
        "",
        f"- Case：`{verification['case_id']}`",
        f"- candidate：`{verification['source_revision']}`",
        "",
        "## 命令",
        "",
    ]
    for item in verification["commands"]:
        lines.extend(
            [
                f"### `{item['command']}`",
                "",
                f"- 状态：`{item['status']}`",
                f"- 退出码：`{item['exit_code'] if item['exit_code'] is not None else 'unavailable'}`",
                f"- 耗时秒数：`{item['duration_seconds']}`",
                f"- stdout SHA-256：`{item['stdout_sha256']}`",
                f"- stderr SHA-256：`{item['stderr_sha256']}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _case_diff(repo: Path, case: CaseSpec) -> bytes:
    diff = _git_bytes(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--binary",
        "--full-index",
        case.base_revision,
        case.candidate_revision,
        "--",
    )
    if len(diff) != case.expected_diff_size:
        raise MaterializationError(f"{case.case_id} Diff 字节数与预注册不一致")
    if _sha256_bytes(diff) != case.expected_diff_sha256:
        raise MaterializationError(f"{case.case_id} Diff SHA-256 与预注册不一致")
    return diff


def _changed_status(repo: Path, case: CaseSpec) -> list[dict[str, str]]:
    output = _git_text(
        repo,
        "diff",
        "--name-status",
        "--no-renames",
        case.base_revision,
        case.candidate_revision,
        "--",
    )
    result: list[dict[str, str]] = []
    for line in output.splitlines():
        status, separator, path = line.partition("\t")
        if not separator or status not in {"A", "D", "M", "T"}:
            raise MaterializationError(f"{case.case_id} 无法解析变更文件状态")
        _require_repo_relative_path(path)
        result.append({"path": path, "status": status})
    if len(result) != case.expected_changed_file_count:
        raise MaterializationError(f"{case.case_id} 变更文件数与预注册不一致")
    return result


def _changed_files(repo: Path, case: CaseSpec) -> list[str]:
    return [item["path"] for item in _changed_status(repo, case)]


def _tree_entries(repo: Path, revision: str) -> dict[str, TreeEntry]:
    output = _git_bytes(repo, "ls-tree", "-r", "-z", "-l", revision)
    entries: dict[str, TreeEntry] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 4:
            raise MaterializationError("无法解析 candidate Git 树")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        _require_repo_relative_path(path)
        size = None if fields[3] == b"-" else int(fields[3])
        entries[path] = TreeEntry(
            path=path,
            mode=fields[0].decode("ascii"),
            object_type=fields[1].decode("ascii"),
            object_id=fields[2].decode("ascii"),
            size=size,
        )
    return entries


def _eligible_texts(
    repo: Path,
    entries: dict[str, TreeEntry],
) -> dict[str, str]:
    texts: dict[str, str] = {}
    for path, entry in entries.items():
        if entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
            continue
        if entry.size is None or entry.size > MAX_CANDIDATE_BYTES:
            continue
        if _is_generated_or_vendor(path) or _sensitive_candidate_path_reason(path):
            continue
        content = _git_bytes(repo, "cat-file", "-p", entry.object_id)
        if b"\0" in content:
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if redact_text(text) != text:
            continue
        texts[path] = text
    return texts


def _python_module_index(texts: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in texts:
        module = _module_name(path)
        if module:
            result[module] = path
    return result


def _module_name(path: str) -> str | None:
    pure = PurePosixPath(path)
    if pure.suffix != ".py" or len(pure.parts) < 2 or pure.parts[0] != "src":
        return None
    parts = list(pure.with_suffix("").parts[1:])
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _local_import_targets(
    path: str,
    text: str,
    module_index: dict[str, str],
) -> dict[str, int]:
    current_module = _module_name(path)
    if current_module is None:
        return {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    pure = PurePosixPath(path)
    is_package = pure.name == "__init__.py"
    module_parts = current_module.split(".")
    package_parts = module_parts if is_package else module_parts[:-1]
    targets: dict[str, int] = {}
    for node in ast.walk(tree):
        modules: list[tuple[str, int]] = []
        if isinstance(node, ast.Import):
            modules.extend((item.name, 1) for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                ascend = node.level - 1
                if ascend > len(package_parts):
                    continue
                base = package_parts[: len(package_parts) - ascend]
                if node.module:
                    base.extend(node.module.split("."))
                module = ".".join(base)
            else:
                module = node.module or ""
            modules.append((module, max(1, len(node.names))))
            if not node.module:
                modules.extend(
                    (f"{module}.{item.name}".strip("."), 1)
                    for item in node.names
                )
        for module, weight in modules:
            target = module_index.get(module)
            if target:
                targets[target] = targets.get(target, 0) + weight
    return targets


def _changed_reference_tokens(
    repo: Path,
    case: CaseSpec,
    changed_source_paths: list[str],
    texts: dict[str, str],
) -> list[str]:
    diff_text = _case_diff(repo, case).decode("utf-8", errors="replace")
    changed_lines = "\n".join(
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith(("+", "-"))
        and not line.startswith(("+++", "---"))
    )
    changed_identifiers = set(IDENTIFIER_PATTERN.findall(changed_lines))
    tokens: set[str] = set()
    for path in changed_source_paths:
        stem = PurePosixPath(path).stem
        if len(stem) >= 6 and stem != "__init__":
            tokens.add(stem)
        try:
            tree = ast.parse(texts[path])
        except SyntaxError:
            continue
        for node in tree.body:
            name = getattr(node, "name", None)
            if (
                isinstance(name, str)
                and len(name) >= 6
                and name in changed_identifiers
            ):
                tokens.add(name)
    return sorted(tokens)[:24]


def _git_grep_paths(
    repo: Path,
    revision: str,
    patterns: list[str],
) -> list[str]:
    if not patterns:
        return []
    command = ["grep", "-l", "-z", "-F"]
    for pattern in patterns:
        command.extend(["-e", pattern])
    command.extend([revision, "--"])
    result = _run_git(repo, *command, allowed_returncodes={0, 1})
    if result.returncode == 1:
        return []
    prefix = f"{revision}:".encode("ascii")
    paths: list[str] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        if not record.startswith(prefix):
            raise MaterializationError("git grep 输出未绑定 candidate revision")
        path = record[len(prefix):].decode("utf-8", errors="surrogateescape")
        _require_repo_relative_path(path)
        paths.append(path)
    return sorted(set(paths))


def _classify_reference_path(
    path: str,
) -> tuple[
    Literal["caller", "test", "config", "contract", "architecture"] | None,
    int,
]:
    pure = PurePosixPath(path)
    if path.startswith("tests/"):
        return "test", 8
    if pure.name in CONFIG_NAMES:
        return "config", 40
    if pure.name == "AGENTS.md" or "CONTRACT" in pure.name.upper():
        return "contract", 50
    if path.startswith("docs/") or pure.name == "README.md":
        return "architecture", 60
    if pure.suffix == ".py":
        return "caller", 18
    return None, 99


def _add_candidate(
    candidates: dict[str, ImpactCandidate],
    path: str,
    role: str,
    rank: int,
    reason: str,
    texts: dict[str, str],
    changed_set: set[str],
) -> None:
    if path in changed_set or path not in texts or role not in ALLOWED_ROLES:
        return
    candidate = ImpactCandidate(
        path=redact_text(path),
        role=role,  # type: ignore[arg-type]
        reason=redact_text(reason),
        rank=rank,
    )
    previous = candidates.get(path)
    if previous is None or (
        candidate.rank,
        candidate.role,
        candidate.path,
    ) < (
        previous.rank,
        previous.role,
        previous.path,
    ):
        candidates[path] = candidate


def _validate_candidate_list(
    candidates: list[ImpactCandidate],
    source_revision: str,
) -> None:
    if len(candidates) > MAX_CANDIDATES:
        raise MaterializationError("impact candidate 超过预注册上限")
    if candidates != sorted(
        candidates,
        key=lambda item: (item.rank, item.role, item.path),
    ):
        raise MaterializationError("impact candidate 排序不稳定")
    paths: set[str] = set()
    for item in candidates:
        _require_repo_relative_path(item.path)
        if item.path in paths:
            raise MaterializationError("impact candidate 路径重复")
        paths.add(item.path)
        if item.role not in ALLOWED_ROLES or item.rank < 1:
            raise MaterializationError("impact candidate role 或 rank 不合法")
        if not item.reason.strip():
            raise MaterializationError("impact candidate reason 不能为空")
    _require_hex(source_revision, 40, "source_revision")


def _validate_verification_payload(
    case: CaseSpec,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if set(payload) != {
        "case_id",
        "commands",
        "schema_version",
        "source_revision",
    }:
        raise MaterializationError("verification-result 顶层字段不完整")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise MaterializationError("verification-result schema_version 不一致")
    if payload["case_id"] != case.case_id:
        raise MaterializationError("verification-result case_id 不一致")
    if payload["source_revision"] != case.candidate_revision:
        raise MaterializationError("verification-result source_revision 不一致")
    commands = payload["commands"]
    if not isinstance(commands, list) or len(commands) != len(
        case.verification_commands
    ):
        raise MaterializationError("verification-result 命令数量不一致")
    normalized_commands: list[dict[str, Any]] = []
    for expected_command, item in zip(case.verification_commands, commands, strict=True):
        if not isinstance(item, dict) or set(item) != {
            "command",
            "duration_seconds",
            "exit_code",
            "status",
            "stderr_sha256",
            "stdout_sha256",
        }:
            raise MaterializationError("verification-result 命令字段不完整")
        if item["command"] != expected_command:
            raise MaterializationError("verification-result 命令文本或顺序被改变")
        status = item["status"]
        if status not in ALLOWED_VERIFICATION_STATUSES:
            raise MaterializationError("verification-result status 不合法")
        exit_code = item["exit_code"]
        if isinstance(exit_code, bool) or (
            exit_code is not None and not isinstance(exit_code, int)
        ):
            raise MaterializationError("verification-result exit_code 不合法")
        if status == "passed" and exit_code != 0:
            raise MaterializationError("passed 命令必须记录 exit_code=0")
        if status == "failed" and (exit_code is None or exit_code == 0):
            raise MaterializationError("failed 命令必须记录非零 exit_code")
        if status == "timed_out" and exit_code is not None:
            raise MaterializationError("timed_out 命令的 exit_code 必须为 null")
        duration = item["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
            or duration > MAX_VERIFICATION_COMMAND_SECONDS
        ):
            raise MaterializationError(
                "verification-result duration_seconds 不合法或超过 60 秒上限"
            )
        _require_hex(item["stdout_sha256"], 64, "stdout_sha256")
        _require_hex(item["stderr_sha256"], 64, "stderr_sha256")
        normalized_commands.append(dict(item))
    return {
        "case_id": case.case_id,
        "commands": normalized_commands,
        "schema_version": SCHEMA_VERSION,
        "source_revision": case.candidate_revision,
    }


def _validate_manifest_metadata(
    manifest: dict[str, Any],
    case: CaseSpec,
    control: ControlSpec,
    metadata: dict[str, Any],
) -> None:
    expected = {
        "base_revision": case.base_revision,
        "budgets": dict(sorted(control.budgets.items())),
        "candidate_revision": case.candidate_revision,
        "candidate_tree": metadata["candidate_tree"],
        "case_id": case.case_id,
        "changed_files_sha256": metadata["changed_files_sha256"],
        "context_appendix_chars": metadata["context_appendix_chars"],
        "core_pack_chars": metadata["core_pack_chars"],
        "diff_sha256": case.expected_diff_sha256,
        "diff_size": case.expected_diff_size,
        "experiment_id": EXPERIMENT_ID,
        "impact_candidate_count": metadata["impact_candidate_count"],
        "runtime_commit": control.runtime_commit,
        "runtime_src_tree": control.runtime_src_tree,
        "schema_version": SCHEMA_VERSION,
        "total_prompt_chars": metadata["total_prompt_chars"],
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise MaterializationError(f"materialization.json 元数据不一致：{key}")


def _enforce_budgets(
    control: ControlSpec,
    raw_diff: bytes,
    core_prompt: str,
    appendix: str,
) -> None:
    diff_chars = len(redact_text(raw_diff.decode("utf-8", errors="replace")))
    if diff_chars > control.budgets["reviewer_diff_max_chars"]:
        raise MaterializationError("Full Diff 超过 reviewer_diff_max_chars")
    if len(core_prompt) > control.budgets["core_pack_max_chars"]:
        raise MaterializationError("Core Review Pack 超过 core_pack_max_chars")
    if len(appendix) > control.budgets["context_appendix_max_chars"]:
        raise MaterializationError("Context Appendix 超过 context_appendix_max_chars")
    if len(core_prompt) + 1 + len(appendix) > control.budgets[
        "total_prompt_max_chars"
    ]:
        raise MaterializationError("B 组 Prompt 超过 total_prompt_max_chars")


def _assert_formal_generator_state(
    repo: Path,
    control: ControlSpec,
    generator_revision: str,
) -> None:
    head = _resolve_commit(repo, "HEAD")
    if head != generator_revision:
        raise MaterializationError("generator_revision 必须精确等于当前 HEAD")
    status = _git_text(
        repo,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
    )
    if status.strip():
        raise MaterializationError("正式物化要求控制仓库处于干净已提交状态")
    for path in (CONTROL_MANIFEST, SCRIPT_PATH):
        relative = path.as_posix()
        _git_bytes(repo, "ls-files", "--error-unmatch", "--", relative)
        committed = _git_bytes(repo, "show", f"{generator_revision}:{relative}")
        if committed != repo.joinpath(path).read_bytes():
            raise MaterializationError(f"控制文件与 generator_revision 不一致：{relative}")
    _assert_loaded_runtime_sources(repo, generator_revision)
    _assert_runtime_binding(repo, control, generator_revision)


def _assert_runtime_binding(
    repo: Path,
    control: ControlSpec,
    generator_revision: str,
) -> None:
    runtime_commit = _resolve_commit(repo, control.runtime_commit)
    if runtime_commit != control.runtime_commit:
        raise MaterializationError("runtime_commit 未解析到预注册 SHA")
    actual_runtime_src_tree = _git_text(
        repo,
        "rev-parse",
        f"{runtime_commit}:src",
    ).strip()
    generator_src_tree = _git_text(
        repo,
        "rev-parse",
        f"{generator_revision}:src",
    ).strip()
    if (
        actual_runtime_src_tree != control.runtime_src_tree
        or generator_src_tree != control.runtime_src_tree
    ):
        raise MaterializationError("Reviewer Runtime src tree 已偏离预注册基线")


def _assert_loaded_runtime_sources(repo: Path, generator_revision: str) -> None:
    source_root = (repo / "src").resolve(strict=False)
    if not source_root.is_dir():
        raise MaterializationError("无法确认当前仓库存在 Vega Runtime 源码目录")
    module_names = sorted(
        name
        for name in sys.modules
        if name == "vega" or name.startswith("vega.")
    )
    if "vega.review_runtime" not in module_names:
        raise MaterializationError("Vega Reviewer Runtime 模块尚未加载")
    for module_name in module_names:
        module = sys.modules.get(module_name)
        origin = getattr(module, "__file__", None) if module is not None else None
        if not isinstance(origin, str) or not origin:
            raise MaterializationError(f"Vega Runtime 模块未暴露源文件：{module_name}")
        origin_path = Path(origin).resolve(strict=False)
        if origin_path.suffix != ".py" or not origin_path.is_file():
            raise MaterializationError(f"Vega Runtime 模块不是可核验 Python 源文件：{module_name}")
        try:
            relative = origin_path.relative_to(repo).as_posix()
        except ValueError as exc:
            raise MaterializationError(
                f"Vega Runtime 模块未从当前仓库 src 加载：{module_name}"
            ) from exc
        if not relative.startswith("src/"):
            raise MaterializationError(f"Vega Runtime 模块未从当前仓库 src 加载：{module_name}")
        committed = _git_bytes(repo, "show", f"{generator_revision}:{relative}")
        if committed != origin_path.read_bytes():
            raise MaterializationError(f"Vega Runtime 源文件与 generator_revision 不一致：{relative}")


def _validate_case_directory_entries(output: Path) -> set[str]:
    names: set[str] = set()
    try:
        entries = list(output.iterdir())
    except OSError as exc:
        raise MaterializationError("无法枚举案例 Artifact 目录") from exc
    for entry in entries:
        try:
            if _is_link_or_reparse_point(entry):
                raise MaterializationError(
                    f"案例 Artifact 不得使用 symlink、junction 或 reparse point：{entry.name}"
                )
            stat_result = entry.lstat()
        except MaterializationError:
            raise
        except OSError as exc:
            raise MaterializationError(f"无法检查案例 Artifact：{entry.name}") from exc
        if not stat.S_ISREG(stat_result.st_mode):
            raise MaterializationError(f"案例 Artifact 必须是普通文件：{entry.name}")
        if entry.name not in MATERIALIZATION_ARTIFACT_NAMES:
            raise MaterializationError(f"案例 Artifact 目录包含额外条目：{entry.name}")
        names.add(entry.name)
    return names


def _validate_output_dir(repo: Path, output_dir: Path) -> Path:
    root = (repo / LOCAL_OUTPUT_ROOT).resolve(strict=False)
    candidate = Path(
        os.path.abspath(output_dir if output_dir.is_absolute() else repo / output_dir)
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MaterializationError(
            "RCB-01 输出目录必须位于 .local-validation/rcb-01/ 下"
        ) from exc
    _reject_link_or_reparse_components(repo, candidate)
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise MaterializationError(
            "RCB-01 输出目录解析后越过 .local-validation/rcb-01/"
        ) from exc
    return candidate


def _reject_link_or_reparse_components(repo: Path, candidate: Path) -> None:
    current = repo
    for part in candidate.relative_to(repo).parts:
        current /= part
        if os.path.lexists(current) and _is_link_or_reparse_point(current):
            raise MaterializationError(
                "RCB-01 输出路径不能包含 symlink、junction 或 reparse point"
            )


def _is_link_or_reparse_point(path: Path) -> bool:
    stat_result = path.lstat()
    attributes = int(getattr(stat_result, "st_file_attributes", 0))
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(stat_result.st_mode) or bool(attributes & reparse_flag)


def _is_generated_or_vendor(path: str) -> bool:
    parts = [part.casefold() for part in PurePosixPath(path).parts]
    if any(part in EXCLUDED_PATH_PARTS for part in parts):
        return True
    name = parts[-1] if parts else ""
    return (
        name.endswith((".min.js", ".min.css", ".map"))
        or ".generated." in name
        or name.startswith("generated_")
    )


def _sensitive_candidate_path_reason(path: str) -> str | None:
    reason = sensitive_path_reason(path)
    if reason:
        return reason
    parts = [part.casefold() for part in PurePosixPath(path).parts]
    name = parts[-1] if parts else ""
    if any(part in HIGH_CONFIDENCE_SENSITIVE_DIRS for part in parts[:-1]):
        return "high_confidence_sensitive_directory"
    if name in HIGH_CONFIDENCE_SENSITIVE_FILENAMES:
        return "high_confidence_sensitive_filename"
    return None


def _parse_case(item: Any) -> CaseSpec:
    if not isinstance(item, dict) or set(item) != {
        "base_revision",
        "candidate_revision",
        "case_id",
        "expected_changed_file_count",
        "expected_diff_sha256",
        "expected_diff_size",
        "task",
        "verification_commands",
    }:
        raise MaterializationError("RCB-01 Case 字段不完整")
    case_id = str(item["case_id"])
    if not re.fullmatch(r"C[1-5]", case_id):
        raise MaterializationError(f"RCB-01 Case ID 不合法：{case_id}")
    changed_count = item["expected_changed_file_count"]
    diff_size = item["expected_diff_size"]
    if (
        isinstance(changed_count, bool)
        or not isinstance(changed_count, int)
        or changed_count < 1
        or isinstance(diff_size, bool)
        or not isinstance(diff_size, int)
        or diff_size < 1
    ):
        raise MaterializationError(f"{case_id} Diff 数量字段不合法")
    task = item["task"]
    commands = item["verification_commands"]
    if not isinstance(task, str) or not task.strip():
        raise MaterializationError(f"{case_id} task 不能为空")
    if (
        not isinstance(commands, list)
        or len(commands) != 4
        or not all(isinstance(command, str) and command.strip() for command in commands)
    ):
        raise MaterializationError(f"{case_id} 必须精确登记 4 条验证命令")
    return CaseSpec(
        case_id=case_id,
        base_revision=_require_hex(item["base_revision"], 40, "base_revision"),
        candidate_revision=_require_hex(
            item["candidate_revision"],
            40,
            "candidate_revision",
        ),
        expected_changed_file_count=changed_count,
        expected_diff_size=diff_size,
        expected_diff_sha256=_require_hex(
            item["expected_diff_sha256"],
            64,
            "expected_diff_sha256",
        ),
        task=task.strip(),
        verification_commands=tuple(commands),
    )


def _validate_budgets(value: Any) -> dict[str, int]:
    expected = {
        "context_appendix_max_chars",
        "core_pack_max_chars",
        "reviewer_diff_max_chars",
        "timeout_seconds",
        "total_prompt_max_chars",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise MaterializationError("RCB-01 budgets 字段不完整")
    budgets: dict[str, int] = {}
    for key in sorted(expected):
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise MaterializationError(f"RCB-01 budget 不合法：{key}")
        budgets[key] = item
    if budgets["reviewer_diff_max_chars"] > budgets["core_pack_max_chars"]:
        raise MaterializationError("reviewer diff 预算不能大于 Core Pack 预算")
    if (
        budgets["core_pack_max_chars"] + budgets["context_appendix_max_chars"]
        > budgets["total_prompt_max_chars"]
    ):
        raise MaterializationError("A/B Prompt 预算关系不合法")
    return budgets


def _require_case(control: ControlSpec, case_id: str) -> CaseSpec:
    try:
        return control.cases[case_id]
    except KeyError as exc:
        raise MaterializationError(f"未知 RCB-01 Case：{case_id}") from exc


def _require_hex(value: Any, length: int, field: str) -> str:
    pattern = HEX_40 if length == 40 else HEX_64
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise MaterializationError(f"{field} 必须是 {length} 位小写十六进制")
    return value


def _require_repo_relative_path(path: str) -> str:
    if "\\" in path or ":" in path:
        raise MaterializationError(f"路径不是规范仓库相对路径：{path}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise MaterializationError(f"路径不是规范仓库相对路径：{path}")
    return path


def _resolve_commit(repo: Path, revision: str) -> str:
    resolved = _git_text(
        repo,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
    ).strip()
    return _require_hex(resolved, 40, "Git commit")


def _run_git(
    repo: Path,
    *args: str,
    allowed_returncodes: set[int] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        },
        check=False,
    )
    allowed = allowed_returncodes or {0}
    if result.returncode not in allowed:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise MaterializationError(
            f"Git 命令失败（exit={result.returncode}）：git {' '.join(args)}；{diagnostic}"
        )
    return result


def _git_bytes(repo: Path, *args: str) -> bytes:
    return _run_git(repo, *args).stdout


def _git_text(repo: Path, *args: str) -> str:
    return _git_bytes(repo, *args).decode("utf-8", errors="replace")


def _canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"无法读取 JSON Artifact：{path.name}") from exc
    if not isinstance(payload, dict):
        raise MaterializationError(f"JSON Artifact 顶层必须是对象：{path.name}")
    return payload


def _write_bytes_exclusive(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="物化并离线校验 RCB-01 Reviewer 上下文对照实验输入；不调用模型。"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Vega Git 仓库根目录，默认当前目录。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="物化单个 Case 的 A/B Prompt 与哈希合同。",
    )
    materialize_parser.add_argument("--case", required=True, choices=[f"C{i}" for i in range(1, 6)])
    materialize_parser.add_argument(
        "--verification-result",
        required=True,
        type=Path,
        help="首个模型调用前冻结的结构化 verification-result.json。",
    )
    materialize_parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="必须位于 .local-validation/rcb-01/ 下，且目标目录尚不存在。",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="重新生成并校验已物化 Case 的全部 Artifact。",
    )
    validate_parser.add_argument(
        "--case-dir",
        required=True,
        type=Path,
        help="已物化的 Case 目录。",
    )
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    try:
        if args.command == "materialize":
            verification = _read_json(args.verification_result)
            generator_revision = _resolve_commit(repo, "HEAD")
            result = materialize_case(
                repo,
                args.output_dir,
                args.case,
                verification,
                generator_revision=generator_revision,
                require_formal_state=True,
            )
            print(
                "RCB-01 Case 物化完成："
                f"case={result['case_id']}，candidate_count={result['impact_candidate_count']}，"
                f"artifact={args.output_dir / 'materialization.json'}"
            )
            return 0
        result = validate_materialization(repo, args.case_dir)
        print(
            "RCB-01 Case 离线校验通过："
            f"case={result['case_id']}，generator={result['generator_revision']}"
        )
        return 0
    except (MaterializationError, OSError) as exc:
        print(f"RCB-01 无法完成：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
