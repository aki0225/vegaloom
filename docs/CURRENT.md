# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：33 / 35
- 最近事件：`20260910T040317Z-DAILY-01-completed`

## 当前事项

### 下一项：`EXEC-01` 尊重 Worker 已选择的有效执行权限

沿用现有 Provider 适配，取消非只读 Codex Worker 固定 on-request；可信继承主会话有效权限，无法证明时由用户一次显式选择。用户已批准有限实施，范围与预算见 docs/WORKER-PERMISSIONS-PLAN.md。

验收条件：

- 完整核验主会话有效 sandbox、approval、reviewer 及必要实际范围；用户 config 不充当主会话事实，来源缺失不猜 Full Access，无可信接口明确采用一次显式选择而非宣传自动继承
- Codex 启动和恢复均应用并核对实际生效权限；auto_review、Full Access、人工询问保持原生语义，组织限制及不支持组合明确拒绝，不静默降级
- 复用现有所有权与恢复绑定，防止另一端同时控制活动 Worker；旧 Run 显式选择权限仅适用于当前仍允许执行的协议版本，缺权限来源须显式选择并重新核验；已退役协议的只读/停止/可信交接限制保持不变，不因选择权限恢复执行；不重写全部状态、恢复协议或指纹
- Reviewer 独立只读、Planning 只读、任务授权、单 Writer、验证对应 Candidate、写审隔离和成功语义不变；明确 Full Access Worktree 非 OS 隔离，事后 Diff 不能阻止外部副作用
- Claude 仅保持可验证原生映射的兼容，未知组合拒绝，不声称等同 Codex；按计划白名单做有限定向回归，真实验收统一在 EXEC-02 的一个受控 Codex 小任务完成

要求检查：

- `affected-permission-tests`
- `claude-compatibility-tests`
- `architecture-growth`
- `repository-hygiene`
- `plan-state`
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
| 待开始 | `EXEC-01` | 尊重 Worker 已选择的有效执行权限 | `DAILY-01` |
| 待开始 | `EXEC-02` | 接通有限的 Codex 原生审批响应 | `EXEC-01` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实施机器计划事项的 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
