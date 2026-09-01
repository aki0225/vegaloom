# AUTONOMY-04 Codex 原生 Review Shadow

- 日期：`2026-09-01`
- Codex CLI：`0.149.1`
- 目标：判断 App Server `review/start` 能否替代 Vega 当前的独立 Reviewer
- 结论：**不替换**

## 运行设置

在临时 Git 仓库中提交一个权限判断基线，再留下一个未提交回归：把管理员判断改成恒为
`True`。Shadow 使用：

```text
thread/start
  sandbox=read-only
  approvalPolicy=never

review/start
  target=uncommittedChanges
  delivery=detached
```

运行前关闭个人 hooks、memories、plugins 和 MCP 配置。临时仓库、Thread ID 和完整模型正文
不进入公开证据。

## 观察结果

- `review/start` 返回 `reviewThreadId` 和 `turn`；
- detached Review 使用了不同的 Thread ID；
- Review Turn 正常完成；
- 事件包含 `userMessage`、`reasoning`、`commandExecution` 和 `agentMessage`；
- 最终 `agentMessage` 找到了权限绕过。

这证明原生 Review 能完成一次有效的代码审查，但还不能替代 Vega Reviewer：

1. `review/start` 请求没有 `outputSchema`，无法在 Provider 边界强制
   `ReviewVerdict`；
2. 响应本身不提供 `verdict`、`reviewed_files`、`risk_disclosures` 和结构化 finding；
3. detached Review 仍以现有 `threadId` 为输入，单凭该接口无法证明它没有继承 Worker
   会话历史；
4. Vega 还需要把 Reviewer 绑定到 Candidate SHA、项目验证结果和风险披露规则。

因此当前实现继续使用独立的只读 `thread/start + turn/start`，由严格 Structured Output
生成 Reviewer 结果。`review/start` 暂不接入成功判断，也不作为额外投票。
