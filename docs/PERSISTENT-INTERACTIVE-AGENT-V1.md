# 持久交互式 Agent V1

## 目标

Vega v0.3.0 使用一条公开流程：

```text
主会话调查与计划
→ 人工批准 Change Contract
→ 持久 Worker 执行
→ Verification / Risk / 独立 Reviewer
→ Repair / Replan / 人工处理
→ 最终报告
```

主会话负责调查、计划、监控和人工决策。Vega 负责会话、Worktree、状态和确定性路由。
Codex 或其他 Coding Agent 负责实际实现与审查。

## 设计决定

- V1 使用 Codex App Server，Provider Session 合同不包含 Codex 专有业务字段。
- 一个 ChangeRun 复用一个 Worker Thread。
- 每个 Work Item 使用独立 Reviewer Thread；同一 Work Item 的修复复查可以复用该 Thread。
- 多 Work Item、Replan、高风险或审查后代码变化时，启动新的最终集成 Reviewer。
- Worker 沿用宿主授权配置；Reviewer 始终使用独立只读配置。
- 主会话可以查看安全进度、发送 Steer、响应请求和接管 Worker。
- Vega 补充的动态 Task Brief 使用 32 KiB 软上限，无下限；必要约束不得静默丢失。
- Provider Thread ID 只是本机优化。跨机器恢复仍以任务分支、Git Candidate 和 Task Card 为准。

## 代码边界

只增加 Provider Session 与 App Server 所需的窄模块。状态、Trace、Verification、Risk、
Reviewer 和 Finish 继续使用现有实现。

v0.3.0 删除 `do`、`loop`、`agent`、`goal` 和旧 inspection 入口，不保留兼容别名。
仍被 ChangeRun 使用的 Core Runtime 保留为内部实现。

新增测试只保护以下内容：

- App Server 协议和进程失败；
- 持久会话复用与写审隔离；
- Steer、人工输入、接管与恢复；
- 顶层 CLI 和最终报告；
- fail-closed 与跨机器恢复。

不为私有帮助函数、重复 Snapshot 或已删除入口增加测试。

## 真实验收

2026-08-28 的两 Work Item Codex App Server Dogfood 已完成。Worker 在两个 Turn 间复用
同一 Thread，人工 Steer 正常送达；每个 Work Item 使用独立 Reviewer，最终累计审查通过，
父状态为 `completed / ready_to_commit`。

验收过程中发现并修复三项问题：Windows 终端标题可能暴露子进程参数、长期 MCP 子进程阻塞
外层执行收尾、Contract 最终验证被过早用于中间 Work Item。失败 run 保持
`needs_human`，没有补写成成功。完整记录只追加在
[`../eval/real-world-runs.md`](../eval/real-world-runs.md)。
