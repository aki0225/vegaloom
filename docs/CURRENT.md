# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：10 / 14
- 最近事件：`20260827T133409Z-SIMP-01-started`

## 当前事项

### 进行中：`SIMP-01` 移除薄 LangGraph 路由层

由现有确定性状态机直接拥有路由与人工暂停语义，删除只重复记录 Decision 的 LangGraph 图和 SQLite 游标。

验收条件：

- next、repair、replan、human 和 finalize 仍由同一套确定性规则选择
- 人工暂停由 Agent State、allowed_actions 和 Checkpoint 表达，不再生成 graph-checkpoints.sqlite
- 安装 Agent 能力不再依赖 LangGraph，既有 fail-closed、恢复和 Finish 语义保持不变

要求检查：

- `supervisor-tests`
- `recovery-tests`
- `dependency-smoke`
- `repository-hygiene`
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
| 已完成 | `VALID-01` | 完成 Bounded Change Loop 真实验收 | `AUTO-03` |
| 进行中 | `SIMP-01` | 移除薄 LangGraph 路由层 | `VALID-01` |
| 待开始 | `SIMP-02` | 抽离 Provider 无关 Candidate 流程 | `SIMP-01` |
| 待开始 | `SIMP-03` | 收窄 legacy Plan 兼容面 | `SIMP-02` |
| 待开始 | `SIMP-04` | 收敛 Agent 状态解释 | `SIMP-03` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实现 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
