# Vega v0.5.1 发布说明

> 发布状态、下载附件与校验值见 [v0.5.1 Release](https://github.com/aki0225/vegaloom/releases/tag/v0.5.1)。

v0.5.1 是 v0.5.0 的兼容性与易用性修复版本。它不改变 ChangeRun 的成功语义、人工授权、
写审隔离或 fail-closed 边界。

## 主要变化

- `vega config check --change` 提前检查自然语言 Change 所需的已提交 `.vega.yaml` 和固定验证命令。
- Provider 预检与实际执行路径一致，支持显式 Codex/Claude 选择，并分别报告混合 runner 的 CLI 缺失。
- `status` 与 `explain` 使用同一份安全动作投影；批准提示明确要求先核对合同和执行计划。
- 完成状态显示已有 Worktree、任务分支、累计 Diff 基线、Candidate 和最终报告位置。
- 受控临时目录不再使批准基线失效；Windows 新建 Worktree 与较深的验证临时目录使用短名称。
- 普通高风险路径可以先完成独立只读审查，保留实际验证结果，最终仍要求人工确认。
- 风险待确认时允许经批准的验证专用恢复，保留原 Worker 与改动；风险不一致时拒绝恢复。
- YAML 的 `false`、`0`、空字符串和列表不再被误当成空配置；空文件与 `null` 保持兼容。
- README 和展示页改为安装本版本 Release 制品，不再指向旧候选提交。

## 保留的边界

- 默认人工批准；仅在仓库策略允许且调用方显式选择时使用 bounded 自动批准。
- 不自动 push、merge、release、部署或删除用户文件。
- 未提交或无法解释的配置、Workspace 和证据仍然 fail-closed。
- `status`、`explain` 不调用模型、不重新运行验证、不修改运行状态。
- 旧 Worktree 和失败 Run 不迁移、不覆盖；短目录不等于支持任意深度的路径。

## 验证

- 发布候选定向测试：`tests/core/test_smoke.py` 和 `tests/core/test_cli_recovery_hardening.py`，
  共 `91 passed, 1 skipped`；Ruff、Compileall、仓库卫生、计划和架构门禁通过。
- PR CI 和合并后的主线 CI 由 GitHub Actions 记录；未以本地结果替代跨平台证据。
- #113、#114 修复基线 `29299ad` 的主线 CI 十项全部通过，覆盖 Core、Supervisor、Security、
  历史实验、Windows/POSIX 与安装包。风险验证成败及命名风险兼容定向验证 `3 passed`，
  风险恢复和目录保护定向验证 `3 passed`；独立只读审阅未发现阻断问题。
- 本次没有重复运行完整真实 Provider 任务；历史 Provider 证据与本次确定性回归分开记录。
