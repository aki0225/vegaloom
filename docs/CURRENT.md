# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：9 / 10
- 最近事件：`20260826T112133Z-AUTO-03-completed`

## 当前事项

### 下一项：`VALID-01` 完成 Bounded Change Loop 真实验收

用真实任务比较 Vega 与原生 Review、CI 和人工中转的操作成本、恢复能力与错误放行情况。

验收条件：

- 普通多轮 Repair、合同越界 Replan 和 Worker 或 Reviewer 中断各有一个真实案例
- 正常路径除初始批准和最终 PR 判断外不需要人工转贴上下文或命令
- 验证失败、风险越界或 Review 未完成不能进入 ready_to_commit
- 验收记录人工操作数、恢复耗时、最终理解耗时和运行开销，并据此决定发布或删除无价值机制

要求检查：

- `full-test-shards`
- `package-smoke`
- `real-agent-acceptance`
- `pr-ci`

## 全部事项

| 状态 | ID | 事项 | 前置事项 |
|---|---|---|---|
| 已完成 | `GOV-01` | 整理事实、规则与产品入口 | — |
| 已完成 | `GOV-02` | 整理测试职责与 CI 成本 | `GOV-01` |
| 已完成 | `GOV-03` | 处理证据支持的源码重复 | `GOV-02` |
| 已完成 | `PLAN-STATE-01` | 让计划状态随实现进入主线 | `GOV-03` |
| 已完成 | `ARCH-01` | 冻结 Bounded Change Loop 权威边界 | `PLAN-STATE-01` |
| 已完成 | `ARCH-02` | 建立隔离 Worktree 与 Git Candidate | `ARCH-01` |
| 已完成 | `AUTO-01` | 统一 ChangeRun 与 Work Item 执行 | `ARCH-02` |
| 已完成 | `AUTO-02` | 实现自动 Repair 与 Contract-aware Replan | `AUTO-01` |
| 已完成 | `AUTO-03` | 完成进度、Review Queue 与恢复 | `AUTO-02` |
| 待开始 | `VALID-01` | 完成 Bounded Change Loop 真实验收 | `AUTO-03` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实现 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
