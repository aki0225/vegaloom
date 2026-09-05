# Vega v0.5.1 发布摘要

v0.5.1 修复了 v0.5.0 发布后发现的制品与主线使用说明错位，并收紧了日常入口的提示：

- 稳定版 wheel/sdist 与 README 使用同一版本；
- 新任务在调用 Provider 前检查已提交的固定验证配置；
- `status`、`explain` 和批准提示指向同一份可复查事实；
- 完成后能直接找到 Candidate、任务分支、Diff 基线和报告。

本版本不新增 Provider、核心状态或自动交付动作。发布事实以 `v0.5.1` Tag 和 GitHub Release 为准。
