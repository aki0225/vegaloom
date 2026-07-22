# Selective Memory Reminder 实验

本目录记录 Vega `v0.1.0` 之后的独立离线实验。实验目标不是保存更多聊天内容，而是验证：

> 少量、带证据和生命周期的 Run-local Memory，能否只在重复失败、审批冲突、同级信息冲突
> 或 Session 恢复等具体风险出现时提醒，同时低于固定 Top-K 的上下文成本。

## 本轮范围

- Phase 0：记录冻结基线。
- Phase 1：独立 schema、append-only event、snapshot replay、候选与确定性策略。
- Phase 2：10 个离线 case、150 个 checkpoint 的 A/B/C/D 完整评估。

本轮明确不做：

- 不修改 `src/vega/`。
- 不接入真实 loop、worker prompt 或 reviewer prompt。
- 不调用真实 LLM。
- 不写 `memory/ledger.jsonl`，不自动接受长期 Memory。
- 不进入 Shadow Mode。
- 不声明真实编码任务成功率提升。

公开归档在保持 Phase 2 数据集不变的前提下，回补三个后续确认的离线正确性门禁：

- 普通无 action 范围失败不得提醒无关动作；
- 无 action 范围的高风险失败必须 fail-closed 升级人工；
- 高风险干预必须按精确决策类型计分，不能把弱化后的 `remind` 当成 `block/escalate` 命中。

## 当前结论

- 完整离线决策：`candidate-for-shadow`。
- D/C 注入字符比例：`10.6%`。
- 正常 `allow` 负样本：117。
- 真实 LLM 与真实 worker 调用：0。

这里的 `candidate-for-shadow` 只表示计划定义的离线门槛已通过。当前仍停在 Phase 2，
未经新的明确授权不会进入 Shadow，也不能据此宣称真实任务收益。

完整研究边界见
[`SELECTIVE-MEMORY-REMINDER-PLAN.md`](../SELECTIVE-MEMORY-REMINDER-PLAN.md)。
公开移植范围、复现命令与脱敏边界见
[`PUBLIC-ARCHIVE.md`](PUBLIC-ARCHIVE.md)。
