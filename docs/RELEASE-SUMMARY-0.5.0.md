# Vega v0.5.0 发布摘要（候选）

> 状态：候选，日期：2026-09-04。发布事实待验证。

v0.5.0 把 Vega 的常用操作收敛为三条日常命令：

```powershell
vega change "描述变更"
vega status
vega explain
```

本候选包含：

- 按当前 Git 仓库优先选择唯一未完成 ChangeRun，没有活动任务时显示最近更新的终态 Run，
  多个候选时拒绝猜测；
- 只读的状态和原因解释投影，不调用模型、不重新验证、不修改状态；
- Codex 与 Claude Code 共用 Provider Session 和既有 Core 成功语义；
- Provider 请求在当前终端显示安全摘要；缺少完整原始上下文时停止 attempt 并关闭 pending，
  不自动批准；
- 对满足严格前提的 Core Reviewer `timed_out` 自动恢复一次，第二次或其他终态交还人工。

`start`、`approve`、`run` 仍是高级路径；Candidate、Verification、Risk、Reviewer、Finish
和人工 Git 交付边界没有改变。

真实 Codex bounded 与 Claude Code human 路径已完成候选验收；Reviewer timeout 使用
确定性故障注入验证。完整 CI、package smoke、Tag 和 GitHub Release 仍待验证，不能从本摘要
推断已发布。脱敏记录见
[`v0.5.0-daily-ux-smoke.md`](../examples/evidence/v0.5.0-daily-ux-smoke.md)。
