# Eval 规则

- `eval/` 是追加式实验与真实运行证据。已有结果、失败、超时、计数和裁决不得删除、重排或润色。
- 发现历史错误时追加勘误，写明原记录、修正依据和影响；不能静默覆盖旧结论。
- 只登记脱敏、可公开复核的摘要。API key、Authorization header、私有 endpoint token、本机绝对
  路径、原始模型正文和未审查 run artifact 不得进入 Git。
- `cases.jsonl` 等机器输入必须保持一行一个完整对象；Schema 或 evaluator 变化必须有对应测试。
- 实验材料不能改变 Core、Supervisor、CLI 退出码或成功语义；进入产品前必须回到 `ROADMAP.md`
  重新登记范围和门槛。
