# Vega v0.4.0 发布摘要

> 状态：已发布。注解 Tag `v0.4.0` 绑定提交
> `bcc079e0fe4fce99bb20637fa09b021537f27abe`。

v0.4.0 增加从自然语言目标开始的有界自主执行：

- Coding Agent 先在只读 Workspace 中调查 Bug 或功能目标；
- Vega 把调查结果编译为 Change Contract 和有限 Execution Plan；
- 默认由人批准，也可按仓库预先登记的 bounded 策略放行低风险任务；
- Worker 在隔离 Worktree 中实现，Reviewer 使用独立只读会话；
- 普通 Finding 自动生成 Fix Packet 并返回同一个 Worker Thread；
- Git Candidate、项目验证、Scope、Risk 和 Reviewer 共同决定下一步；
- 压缩、中断和换机器恢复后重新核对现场，不复用过期 Gate。

真实验收覆盖低风险自动批准、人工批准与自动返修、高风险人工门禁、Codex 原生压缩、Worker
中断和 Task Card 换目录恢复。

本版本只完成 Codex Provider。Claude Code、未知外部副作用重放以及自动 push、PR、merge 和
release 不在 v0.4.0 范围内。

详细内容见
[`RELEASE-NOTES-0.4.0.md`](https://github.com/aki0225/vegaloom/blob/v0.4.0/docs/RELEASE-NOTES-0.4.0.md)。

发布事实以注解 Tag 和
[GitHub Release](https://github.com/aki0225/vegaloom/releases/tag/v0.4.0) 为准。
