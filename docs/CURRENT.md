# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：7 / 10
- 最近事件：`20260825T185103Z-AUTO-01-completed`

## 当前事项

### 下一项：`AUTO-02` 实现自动 Repair 与 Contract-aware Replan

Reviewer 只返回结构化裁决；合同内问题自动修复或调整执行计划，触及冻结边界时生成具体审批问题。

验收条件：

- approve、repair、replan 和 needs_human 具有唯一结构化语义
- 普通 Finding 自动生成 Fix Packet 并回到单 Writer
- Contract 字段差异与实际 Git Diff、风险路径共同决定是否需要重新批准
- Repair、Replan、验证重试和 Review 都有明确预算与停止条件

要求检查：

- `supervisor-tests`
- `security-tests`
- `real-agent-dogfood`
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
| 待开始 | `AUTO-02` | 实现自动 Repair 与 Contract-aware Replan | `AUTO-01` |
| 待开始 | `AUTO-03` | 完成进度、Review Queue 与恢复 | `AUTO-02` |
| 待开始 | `VALID-01` | 完成 Bounded Change Loop 真实验收 | `AUTO-03` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实现 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
