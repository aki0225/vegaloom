# Vega 当前计划状态

> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。
> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。

- 计划：Vega Agent 演进计划
- 计划 ID：`vega-agent-evolution`
- 已完成：8 / 10
- 最近事件：`20260826T045725Z-AUTO-02-completed`

## 当前事项

### 下一项：`AUTO-03` 完成进度、Review Queue 与恢复

主会话显示低频进度并接受人工控制；Provider 会话优先续接，失效后由 Git 与 Task Card 重建任务语义。

验收条件：

- 主会话能查看阶段、Work Item、命令、变更、验证、Reviewer 和下一步
- steer、pause、stop 和 resume 不会打通 Worker 与 Reviewer 的会话边界
- Reviewer 输入超过软预算时才拆分 Review Queue，并保存 covered、remaining 和 findings
- 跨会话或换机后可以从任务分支、Candidate SHA 和 Git 跟踪的 Task Card 恢复

要求检查：

- `status-tests`
- `context-tests`
- `recovery-tests`
- `git-only-resume-dogfood`
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
| 已完成 | `AUTO-02` | 实现自动 Repair 与 Contract-aware Replan | `AUTO-01` |
| 待开始 | `AUTO-03` | 完成进度、Review Queue 与恢复 | `AUTO-02` |
| 待开始 | `VALID-01` | 完成 Bounded Change Loop 真实验收 | `AUTO-03` |

## 状态规则

- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。
- 实现 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。
- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。
- 已进入主线的事件只允许追加，不允许改写或删除。
- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。
- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。
