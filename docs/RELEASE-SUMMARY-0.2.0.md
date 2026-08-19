# Vega v0.2.0 发布摘要

> 版本：v0.2.0

这份摘要用于 GitHub Release 文案。详细变更见
[`RELEASE-NOTES-0.2.0.md`](RELEASE-NOTES-0.2.0.md)，发布步骤见
[`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)。

## 一句话

Vega v0.2.0 增加一个 opt-in 软件工程 Supervisor Agent：先调查和批准计划，再约束一个
Coding Agent 执行；中断后可以从 Checkpoint 或 Git Task Card 恢复，最终仍由项目验证、
风险门禁和独立 Reviewer 决定能否交给人工提交。

## 本版本重点

- 结构化 Plan revision、人工批准、Task Brief 和单 Work Item/单 Writer 控制。
- Worker Claim、Machine Observation 与 Supervisor Decision 分层，不把模型自述当完成事实。
- 主会话可通过状态卡、`status` 和 `watch` 查看当前阶段、变更、风险、Checkpoint 和下一步。
- 支持 `pause / stop / recover / resume-local / checkpoint --handoff / resume / finalize`，
  并在未知副作用、身份冲突、Workspace 漂移或证据不足时 fail-closed。
- Git Task Card 可以把 WIP、计划、约束和下一步带到新的 clone；本机 Trace、SQLite 和凭据
  不进入 Git。
- Codex Writer 与 Reviewer 使用不同会话和受限执行配置，Reviewer 不继承 Worker 完整聊天。
- 修复恢复 run 继承旧 `handoff_ready` 的问题，已消费的交接不会阻断新的人工副作用裁决。

## 真实验收

- 使用真实前端并发缺陷完成一次 WIP 停止、Git Handoff、独立 clone 恢复、Reviewer 打回、
  Plan revision 2、重新执行与可信 Finish。
- Provider 429 attempt 保持 `needs_human`；首次 Reviewer 因测试与后端证据不足选择打回，
  两者都没有被包装成成功。
- 最终门禁：后端 `361 passed`、定向前端 `7 passed`、完整前端 `180 passed`、TypeScript、
  隔离构建和 `git diff --check` 全部通过。
- 目标变更通过人工 PR 合入；Vega 本身没有执行 commit、push 或 merge。

## 安装与入口

Supervisor Agent 需要可选依赖：

```powershell
python -m pip install -e ".[agent]"
vega agent capabilities
vega adapters init codex --repo .
```

既有 `vega do / loop / goal / finish` 保持兼容。`vega agent` 仍需显式调用，不会替换默认
Coding Harness 路径。

## 不变边界

- 当前只支持一个未完成 Work Item 和一个 Codex Writer。
- 不增加 Multi-Worker、Planner、长期 Memory 自动写入、Provider SDK、服务端或自动 Git。
- 会话隔离与只读视图不等于系统级安全沙箱。
- `ready_to_commit` 仍只表示进入人工提交前检查。
