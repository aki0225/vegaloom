# Click #2939: Real-World Evidence

这个目录是 Click #2939 的一次真实 auto loop 脱敏证据包。

问题是 `CliRunner` 将 stdin 包装器的正常迭代结束错误转换为 EOF，使链式命令通过
`click.File("r")` 读取 `-` 输入后输出 `Aborted!` 并以非零状态结束。

## 这份样例保留什么

- 冻结的任务合同、允许路径和上游 oracle 修订哈希。
- 脱敏后的状态机、trace、实际 diff、验证摘要、范围/risk gate 和 reviewer verdict。
- `ready_to_commit` 的含义：证据链满足人工提交前检查，不表示 Vega 已提交目标仓库。

## 这份样例不包含什么

- 原始 `runs/`、worker/reviewer 完整输出、prompt 全文、本机绝对路径、PID、认证和网络诊断。
- 原始 `eval.md`、`finish-summary.json` 和 `finish-report.md`；本目录只能复核核心阶段的脱敏
  摘要，不能独立重建完整 Finish/Eval 判断。
- 上游修复 diff；执行副本不含远端或上游提交历史。
- 成功率、任意仓库适用性或模型未使用训练数据记忆的证明。

## 如何解读

基线的独立 oracle 会失败，随后真实 worker 只修改两条预注册路径。Vega 在验证、三阶段范围
门禁、风险门禁和隔离 reviewer 全部通过后才到达 `ready_to_commit`。若任何一个环节失败，产品
语义应是保留现场并 fail-closed，而不是自动提交或宣称成功。
