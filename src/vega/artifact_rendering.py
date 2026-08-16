from __future__ import annotations

from typing import Any

from .redaction import redact_value


def render_workspace_check(result: Any) -> str:
    lines = [
        "# Workspace Check",
        "",
        f"- 仓库：`{result.repo_path}`",
        f"- 状态：`{result.status}`",
        f"- 新增未跟踪文件：`{result.new_untracked_count}`",
        f"- 启动前已有 tracked diff：`{str(result.baseline_tracked_changes_present).lower()}`",
        f"- 启动前未跟踪文件发生变化：`{str(result.baseline_untracked_changed).lower()}`",
        f"- ignored 路径发生变化：`{str(result.baseline_ignored_changed).lower()}`",
        f"- ignored 基线清单完整：`{str(result.baseline_ignored_manifest_complete).lower()}`",
        f"- ignored 当前清单完整：`{str(result.current_ignored_manifest_complete).lower()}`",
        f"- ignored 基线内容完整：`{str(result.baseline_ignored_content_complete).lower()}`",
        f"- ignored 当前内容完整：`{str(result.current_ignored_content_complete).lower()}`",
        f"- Git 控制文件发生变化：`{str(result.git_control_changed).lower()}`",
        f"- Git 控制文件完整：`{str(result.git_control_complete).lower()}`",
        f"- 执行期间 HEAD 发生变化：`{str(result.baseline_head_changed).lower()}`",
        f"- 预算上限：`{result.max_new_files if result.max_new_files is not None else '未配置'}`",
        "",
        "## 结论",
        "",
    ]
    lines.extend(f"- {reason}" for reason in result.reasons)
    lines.extend(["", "## 启动前 tracked diff", ""])
    if result.baseline_tracked_files:
        lines.extend(f"- `{path}`" for path in result.baseline_tracked_files[:50])
        if len(result.baseline_tracked_files) > 50:
            lines.append(f"- ... 另有 {len(result.baseline_tracked_files) - 50} 个文件")
    else:
        lines.append("- 无")
    lines.extend(["", "## 新增未跟踪文件", ""])
    if result.new_untracked_files:
        lines.extend(f"- `{path}`" for path in result.new_untracked_files[:50])
        if len(result.new_untracked_files) > 50:
            lines.append(f"- ... 另有 {len(result.new_untracked_files) - 50} 个文件")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## Git Status",
            "",
            "```text",
            result.raw_status.strip() or "<clean>",
            "```",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_review_context(inputs: dict[str, Any]) -> dict[str, Any]:
    context = {
        "repo_path": inputs["repo_path"],
        "repo_name": inputs["repo_name"],
        "source_run": inputs["source_run"],
        "source_run_dir": inputs["source_run_dir"],
        "comparison_base_sha": inputs["comparison_base_sha"],
        "comparison_paths": inputs["comparison_paths"],
        "changed_files": inputs["changed_files"],
        "agents_files": inputs["agents_files"],
        "memory_hit_count": inputs["memory_hit_count"],
        "contains_worker_chat": False,
        "truncated_sections": inputs["truncated_sections"],
        "evidence_consistent": inputs["evidence_consistent"],
        "evidence_issues": inputs["evidence_issues"],
        "evidence_diagnostics": inputs["evidence_diagnostics"],
        "source_snapshot_id": inputs["source_snapshot_id"],
        "source_workspace_fingerprint": inputs["source_workspace_fingerprint"],
        "current_workspace_fingerprint": inputs["current_workspace_fingerprint"],
        "current_index_flags_sha256": inputs["current_index_flags_sha256"],
        "current_unsafe_index_paths": inputs["current_unsafe_index_paths"],
        "source_untracked_content_complete": inputs[
            "source_untracked_content_complete"
        ],
        "current_untracked_content_complete": inputs[
            "current_untracked_content_complete"
        ],
        "reviewer_start_workspace_fingerprint": inputs[
            "reviewer_start_workspace_fingerprint"
        ],
        "reviewer_end_workspace_fingerprint": inputs[
            "reviewer_end_workspace_fingerprint"
        ],
        "workspace_changed_during_review": inputs["workspace_changed_during_review"],
        "review_execution_issues": inputs["review_execution_issues"],
        "risk_gate": inputs.get("risk_gate"),
    }
    return redact_value(context)
