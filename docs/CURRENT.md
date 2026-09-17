# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：36 / 37
- 最近事件：`20260917T025426Z-DAILY-03-started`

## 当前事项

### 进行中：`DAILY-03` 区分高风险范围内返修与最终交付确认

使用默认保守且绑定人工批准合同的窄授权，在证据完整时允许高风险范围内返修，最终交付仍交人工。

验收条件：

- 仅当前人工批准合同显式启用风险待确认返修，旧任务和 bounded 批准不静默扩大授权
- 固定验证通过、当前证据完整、无外部副作用、已授权风险领域、明确非 suggestion 缺陷且预算允许才自动 repair；复用 Fix Packet 与新鲜度检查
- Risk blocked/human-review 原值不改，Reviewer approve 后仍 human，不能自动 next/finalize；范围或业务约束变化重新批准
- 缺失披露、新风险、越界、证据无效、超时或进程未确认、needs_human verdict 不允许自动返修
- 用代表流程与少数关键拒绝回归证明边界，正常主会话日用验收和 PR CI 独立报告

要求检查：

- `affected-tests`
- `architecture-growth`
- `repository-hygiene`
- `plan-state`
- `daily-session-acceptance`
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
| 已完成 | `USAGE-02` | 修复真实使用中的高频摩擦 | `USAGE-01` |
| 已完成 | `PROVIDER-01` | 接入 Claude Code Provider | `USAGE-02` |
| 已完成 | `UX-01` | 让运行状态可以直接看懂 | `PROVIDER-01` |
| 已完成 | `UX-02` | 增加日常变更入口 | `UX-01` |
| 已完成 | `UX-03` | 自动恢复一次 Reviewer 超时 | `UX-02` |
| 已完成 | `RELEASE-05` | 发布 Vega v0.5.0 | `UX-03` |
| 已完成 | `DAILY-01` | 精简日常变更主路径 | `RELEASE-05` |
| 已完成 | `EXEC-01` | 尊重 Worker 已选择的有效执行权限 | `DAILY-01` |
| 已完成 | `EXEC-02` | 接通有限的 Codex 原生审批响应 | `EXEC-01` |
| 已完成 | `DAILY-02` | 工作主会话推进与送审前验收材料接线 | `EXEC-02` |
| 进行中 | `DAILY-03` | 区分高风险范围内返修与最终交付确认 | `DAILY-02` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实施机器计划事项的 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
