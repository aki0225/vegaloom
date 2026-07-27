from __future__ import annotations

from pathlib import Path
from typing import Any


def latest_iteration_file(run_dir: Path, filename: str) -> Path:
    matches = sorted(run_dir.glob(f"iterations/*/{filename}"))
    if not matches:
        return run_dir / filename
    return matches[-1]


def verification_failure_next_steps(
    run_dir: Path,
    iteration: dict[str, Any],
) -> list[str]:
    verification = latest_iteration_file(run_dir, "verification-summary.md")
    if iteration.get("verification_failure_kind") == "project_config_invalid":
        return [
            f"项目配置预检失败，先读取 `{verification}`。",
            "修复目标仓库中的 `.vega.yaml` / `.vega.yml` 后重新运行；"
            "本轮未执行任何验证命令，也未启动 reviewer。",
            "配置恢复前不要把该结果解释为测试失败或代码回归。",
        ]
    if iteration.get("verification_failure_kind") == "workspace_capture_failed":
        return [
            f"工作区指纹采集失败，先读取 `{verification}`。",
            "人工确认目标仓库 Git 状态、ignored/untracked 清单和权限是否可读取；"
            "当前验证结果不能绑定到可信工作区快照。",
            "现场稳定后重新运行本轮任务，不要复用当前 verification artifact。",
        ]
    fix_prompt = latest_iteration_file(run_dir, "fix-prompt.md")
    return [
        f"自动验证失败，先读取 `{verification}`。",
        f"按 `{fix_prompt}` 修复后重新运行："
        f"`vega loop continue --repo <repo> --run {run_dir.name}`。",
    ]
