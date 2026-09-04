# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：28 / 32
- 最近事件：`20260903T072520Z-PROVIDER-01-completed`

## 当前事项

### 下一项：`UX-01` 让运行状态可以直接看懂

按当前仓库自动选择唯一未完成的 ChangeRun，并用稳定原因代码解释任务为什么继续、停止或等待人工。

验收条件：

- status 和 explain 可以从仓库子目录选择当前仓库唯一未完成的 ChangeRun，多个候选或损坏记录时拒绝猜测
- 运行选择使用源仓库绑定和 AgentState.updated_at，不复用目录 mtime 或受管 Worktree repository_id
- 新的确定性 Decision 写入稳定 reason_code，旧版本 Artifact 仍可读取，block category 由静态规则映射
- explain 只投影当前可信 Artifact，不调用模型、不重新验证、不修改状态
- status 默认展示简洁第一屏，full 和 json 保留完整排障信息

要求检查：

- `run-selection-tests`
- `explain-projection-tests`
- `supervisor-tests`
- `security-tests`
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
| 已完成 | `USAGE-02` | 修复真实使用中的高频摩擦 | `USAGE-01` |
| 已完成 | `PROVIDER-01` | 接入 Claude Code Provider | `USAGE-02` |
| 待开始 | `UX-01` | 让运行状态可以直接看懂 | `PROVIDER-01` |
| 待开始 | `UX-02` | 增加日常变更入口 | `UX-01` |
| 待开始 | `UX-03` | 自动恢复一次 Reviewer 超时 | `UX-02` |
| 待开始 | `RELEASE-05` | 发布 Vega v0.5.0 | `UX-03` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实施机器计划事项的 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
