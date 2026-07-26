# pycodestyle #1187: Real-World Evidence

这个目录是一次真实公开 Issue 验证的脱敏证据包。它展示两条都应保留的结果：

1. `reviewer-rejection/`：初始修复让固定测试通过，但隔离 reviewer 发现了未覆盖的语义回归，
   因此 run 保持 `needs_human`。
2. `success/`：据本地运行记录，在新的隔离快照中先明确补齐 reviewer 提出的正反行为合同，
   再由 worker 完成修复、验证和独立审查。当前公开 Git 历史不能独立证明这一步的预注册顺序。

源 Issue：PyCQA/pycodestyle #1187，目标是修复反向 `type(...)` 比较的 E721 漏报。

## 这不是原始运行目录

- 每个 `state.json` 和 `trace.jsonl` 都是结构化的脱敏摘录，而不是原始文件逐字复制。
- 本机绝对路径、执行 PID、完整 worker 输出、认证/网络诊断、prompt 全文和未跟踪文件清单均未公开。
- 原始 `runs/` 与隔离仓库位于本地忽略目录，不能根据本目录重放同一个运行。
- 两个样例只保留可检查的状态机结论、范围门禁、验证结果、reviewer verdict 和最小 diff。
- 公开包没有包含原始 `eval.md`、`finish-summary.json` 或 `finish-report.md`，因此只能复核核心
  阶段的脱敏摘要，不能仅凭本目录重建完整 Finish/Eval 判断。

## 如何解读

这对样例支持以下观察：

- worker 产物不会仅因固定测试通过就被视为成功；
- reviewer 可以在不接收 worker 聊天记录的前提下，基于 diff、验证证据和项目规则发现遗漏；
- reviewer 的 `request_changes` 会保留现场并阻止成功终态；
- 在新的、明确记录的行为合同下，worker、验证、范围门禁和独立 reviewer 可以完成闭环。

它**不能**证明通用成功率、任意仓库适用性、模型没有训练数据记忆，或未公开的原始运行文件可以
被第三方完整复放。

初始案例的 worker 阶段还曾受到外部 runner 中断影响，后续才进入验证和审查。因此该案例记录了
“绿色测试不等于可交付，reviewer 能阻止语义过宽的补丁”，而不是稳定的全自动端到端成功。
