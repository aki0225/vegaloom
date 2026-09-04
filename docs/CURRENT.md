# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：31 / 32
- 最近事件：`20260904T132002Z-UX-03-completed`

## 当前事项

### 下一项：`RELEASE-05` 发布 Vega v0.5.0

把日常变更入口、状态解释和受限 Reviewer 超时恢复作为同一稳定版本发布，版本、制品、文档、Tag 与 GitHub Release 绑定同一主线提交。

验收条件：

- pyproject、vega.__version__、README、Capabilities、CI 版本断言和发布材料统一为 0.5.0
- Codex bounded 与 Claude human 两条真实路径完成固定仓库验收，自动 Reviewer timeout 使用确定性夹具验证
- 完整测试、package smoke、PR CI 与合并后 main CI 均有明确通过结果
- 注解 Tag v0.5.0 与 GitHub Release 绑定通过验证的 main 提交，wheel 与 sdist 来自该提交
- 发布后追加 RELEASE-05 完成事件并生成当前计划视图

要求检查：

- `real-provider-smoke`
- `full-test-shards`
- `package-smoke`
- `repository-hygiene`
- `pr-ci`
- `main-ci`
- `annotated-tag`
- `github-release`

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
| 待开始 | `RELEASE-05` | 发布 Vega v0.5.0 | `UX-03` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实施机器计划事项的 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
