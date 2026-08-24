from __future__ import annotations

import json
from pathlib import Path

from .project_config import ProjectConfig, load_project_config, project_policy_snapshot
from .redaction import redact_text, redact_value
from .workspace_check import read_head_sha


def load_stable_start_policy(
    repo_path: Path,
) -> tuple[ProjectConfig, dict[str, str | None], str]:
    """在创建 run 前稳定绑定 HEAD、策略文件 bytes 与解析后的配置。"""

    policy_before = project_policy_snapshot(repo_path)
    head_before = read_head_sha(repo_path)
    config = load_project_config(repo_path)
    policy_after = project_policy_snapshot(repo_path)
    head_after = read_head_sha(repo_path)
    if policy_before != policy_after or head_before != head_after:
        raise RuntimeError("loop 启动时 HEAD 或项目策略发生变化，请在工作区稳定后重试")
    return config, policy_after, head_after


def project_policy_changed(
    repo_path: Path,
    initial_snapshot: dict[str, str | None],
) -> bool:
    return project_policy_snapshot(repo_path) != initial_snapshot


def write_project_policy_snapshot(
    run_dir: Path,
    snapshot: dict[str, str | None],
) -> None:
    run_dir.joinpath("project-policy-snapshot.json").write_text(
        project_policy_snapshot_text(snapshot),
        encoding="utf-8",
    )


def project_policy_snapshot_text(snapshot: dict[str, str | None]) -> str:
    return json.dumps(redact_value(snapshot), ensure_ascii=False, indent=2) + "\n"


def write_project_policy_change_report(
    iteration_dir: Path,
    initial_snapshot: dict[str, str | None],
    current_snapshot: dict[str, str | None],
) -> Path:
    path = iteration_dir / "project-policy-change-report.md"
    content = "\n".join(
        [
            "# Project Policy Change Report",
            "",
            "- worker 或人工执行后检测到 `.vega.yaml` / `.vega.yml` 发生变化。",
            "- 为避免执行策略被运行过程改写，本轮未继续自动 verification、reflect 或 reviewer。",
            "- Vega 未自动回滚、删除、提交、推送或发布任何内容。",
            "",
            "## 启动时策略快照",
            "",
            "```json",
            json.dumps(initial_snapshot, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 当前策略快照",
            "",
            "```json",
            json.dumps(current_snapshot, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 建议下一步",
            "",
            "- 人工审查策略文件改动是否属于本次任务。",
            "- 确认后重新创建 loop，或人工完成验证和隔离审查。",
        ]
    ).rstrip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact_text(content + "\n"), encoding="utf-8", newline="\n")
    return path


def apply_runner_defaults(
    config: ProjectConfig,
    worker_name: str,
    reviewer_name: str,
) -> tuple[str, str]:
    if worker_name == "codex-exec" and config.runner.worker:
        worker_name = config.runner.worker
    if reviewer_name == "codex-exec" and config.runner.reviewer:
        reviewer_name = config.runner.reviewer
    return worker_name, reviewer_name
