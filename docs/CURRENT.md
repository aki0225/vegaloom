# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：26 / 28
- 最近事件：`20260902T102705Z-USAGE-01-completed`

## 当前事项

### 下一项：`USAGE-02` 修复真实使用中的高频摩擦

只处理 USAGE-01 可复现且明显影响完成率、人工操作或现场解释的缺陷，避免按假设继续扩张控制面。

验收条件：

- 每项代码修改都能关联至少一个真实任务中的可复现问题
- 优先删除重复状态、提示或适配代码，除非现有合同无法表达真实缺口
- 新增测试只覆盖公开行为、故障恢复或安全边界，不复制私有实现
- 修复后重跑对应真实任务或等价最小复现，并保持 fail-closed 和写审隔离

要求检查：

- `targeted-regression`
- `full-test-shards`
- `architecture-growth`
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
| 已完成 | `SIMP-01` | 移除薄 LangGraph 路由层 | `VALID-01` |
| 已完成 | `SIMP-02` | 抽离 Provider 无关 Candidate 流程 | `SIMP-01` |
| 已完成 | `SIMP-03` | 收窄 legacy Plan 兼容面 | `SIMP-02` |
| 已完成 | `SIMP-04` | 收敛 Agent 状态解释 | `SIMP-03` |
| 已完成 | `SESSION-01` | 接入持久 Provider Session | `SIMP-04` |
| 已完成 | `SESSION-02` | 完成持久 Worker 与独立 Reviewer | `SESSION-01` |
| 已完成 | `SESSION-03` | 统一 Agent 入口与交付报告 | `SESSION-02` |
| 已完成 | `SESSION-04` | 完成真实 Agent 验收与 v0.3.0 准备 | `SESSION-03` |
| 已完成 | `VALID-02` | 修复真实验收暴露的恢复与高风险审查问题 | `SESSION-04` |
| 已完成 | `AUTONOMY-01` | 从自然语言生成 Planning Proposal | `VALID-02` |
| 已完成 | `AUTONOMY-02` | 编译 Change Contract 与 Execution Plan | `AUTONOMY-01` |
| 已完成 | `AUTONOMY-03` | 增加有界自动批准 | `AUTONOMY-02` |
| 已完成 | `AUTONOMY-04` | 精简 Provider 会话适配 | `AUTONOMY-03` |
| 已完成 | `AUTONOMY-05` | 完成有界自主执行真实验收 | `AUTONOMY-04` |
| 已完成 | `RELEASE-04` | 发布 Vega v0.4.0 | `AUTONOMY-05` |
| 已完成 | `USAGE-01` | 连续运行五个真实任务 | `RELEASE-04` |
| 待开始 | `USAGE-02` | 修复真实使用中的高频摩擦 | `USAGE-01` |
| 待开始 | `PROVIDER-01` | 接入 Claude Code Provider | `USAGE-02` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实现 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
