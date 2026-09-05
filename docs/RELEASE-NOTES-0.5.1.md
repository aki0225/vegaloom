# Vega v0.5.1 发布说明

> 状态：待发布。发布前以 `v0.5.1` Tag、GitHub Release、制品和 CI 结果为准。

v0.5.1 是 v0.5.0 的兼容性与易用性修复版本。它不改变 ChangeRun 的成功语义、人工授权、
写审隔离或 fail-closed 边界。

## 主要变化

- `vega config check --change` 提前检查自然语言 Change 所需的已提交 `.vega.yaml` 和固定验证命令。
- Provider 预检与实际执行路径一致，支持显式 Codex/Claude 选择，并分别报告混合 runner 的 CLI 缺失。
- `status` 与 `explain` 使用同一份安全动作投影；批准提示明确要求先核对合同和执行计划。
- 完成状态显示已有 Worktree、任务分支、累计 Diff 基线、Candidate 和最终报告位置。
- README、CLI 帮助和稳定版安装命令与发布制品保持一致。

## 保留的边界

- 没有自动批准、自动 push、merge、release、部署或删除用户文件。
- 未提交或无法解释的配置、Workspace 和证据仍然 fail-closed。
- `status`、`explain` 不调用模型、不重新运行验证、不修改运行状态。

## 验证

- 受影响的 Core/Supervisor 定向测试、Ruff、Compileall、仓库卫生、计划和架构门禁通过。
- PR CI 和合并后的主线 CI 由 GitHub Actions 记录；未以本地结果替代跨平台证据。
