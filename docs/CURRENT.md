# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：4 / 12
- 最近事件：`20260825T075308Z-PLAN-STATE-01-completed`

## 当前事项

### 下一项：`ARCH-01` 冻结实际模块边界与包级规则

确定 Core、Supervisor、Execution、Workspace 和 Context 的所有权、依赖方向及包级 AGENTS 规则。

验收条件：

- 每个主要职责边界都有明确所有者、允许依赖和禁止依赖
- 包级 AGENTS 说明修改范围、Artifact 和验证矩阵
- 目标结构不依赖一次性搬迁全部模块

要求检查：

- `architecture-review`
- `repository-hygiene`
- `pr-ci`

## 全部事项

| 状态 | ID | 事项 | 前置事项 |
|---|---|---|---|
| 已完成 | `GOV-01` | 整理事实、规则与产品入口 | — |
| 已完成 | `GOV-02` | 整理测试职责与 CI 成本 | `GOV-01` |
| 已完成 | `GOV-03` | 处理证据支持的源码重复 | `GOV-02` |
| 已完成 | `PLAN-STATE-01` | 让计划状态随实现进入主线 | `GOV-03` |
| 待开始 | `ARCH-01` | 冻结实际模块边界与包级规则 | `PLAN-STATE-01` |
| 待开始 | `ARCH-02` | 迁移 Supervisor 垂直职责 | `ARCH-01` |
| 待开始 | `ARCH-03` | 迁移 Core、Execution 与 Workspace 职责 | `ARCH-02` |
| 待开始 | `AUTO-01` | 实现多 Work Item 顺序执行 | `ARCH-03` |
| 待开始 | `AUTO-02` | 建立 Work Item Checkpoint 与增量证据 | `AUTO-01` |
| 待开始 | `AUTO-03` | 支持长任务恢复与上下文续接 | `AUTO-02` |
| 待开始 | `AUTO-04` | 完成主会话状态与人工控制 | `AUTO-03` |
| 待开始 | `VALID-01` | 完成全自动 Agent 真实验收 | `AUTO-04` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实现 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
