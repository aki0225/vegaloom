# Vega v0.7.0 版本摘要

本页概述 v0.7.0 的能力与限制；发布状态和资产以 GitHub Release 为准。
安装前以 [GitHub Release](https://github.com/aki0225/vegaloom/releases/tag/v0.7.0)
实际资产为准；使用入口见 [README](../README.md)。

## 用户可以做什么

- 在目标项目登记必要验证后，用 `vega change "需求"` 开始自然语言任务；工作主会话加载
  `vega adapters init codex --repo .` 生成的 Skill 后，可自行调查、推进并直接给出技术汇报。
  用户做关键范围和业务授权，不必每次手写合同或搬运审查材料。
- Worker在批准范围内实现，Vega冻结Candidate、执行固定验证，并交给独立只读Reviewer。
  测试和审查各有职责；证据不足或高风险需要人工时不会自动交付。
- Codex Worker首次显式选择 `ask`、`auto-review` 或 `full-access`，同Run沿用绑定；
  支持的command/file审批可在同一终端查看上下文并决定本次允许或拒绝。
- 持续会话可用 `steer` 补充指令、`watch` 看进度，`status` / `explain` 查看阶段、风险和
  安全下一步。过期指令不会偷偷转交新合同会话。
- 中断后使用公开恢复、停止和交接入口；只有明确分类为验收缺失且其他门禁满足时，
  可通过 `change --acceptance-file` 补充材料再审原Candidate，不把宿主自述当机器验证。

## 本版验证与限制

发布准备期间已实际跑通受控本地权限样例：真实Worker→Candidate→固定验证
（15项测试及diff检查）→独立只读Reviewer→高风险人工确认。Reviewer未发现明确缺陷，
但选择needs_human；没有人为要求指定结论。早期失败保留，修复后独立运行不冒充旧Run恢复。

范围内自动返修有定向回归覆盖，本次真实样例未自然触发request_changes→repair→approve；
DAILY-03仍未标完成，计划视图保持36/37。该限制公开保留，不再以预设的审查结论序列作为
本版唯一发布前提。发布维护流程核对最终CI、干净制品安装及上传下载。

Provider能力有差异，不承诺Claude与Codex权限机制等价；可用模型与组织限制由实际环境决定。
Full Access与Git worktree不等于操作系统隔离。模型审查不保证绝对准确，权限、敏感数据和
高风险最终交付仍需人判断。Vega Runtime不会自动push、merge或release。

完整变更和升级注意事项见 [发布说明](RELEASE-NOTES-0.7.0.md)，
安全支持与私下报告渠道见 [安全策略](../SECURITY.md)。
