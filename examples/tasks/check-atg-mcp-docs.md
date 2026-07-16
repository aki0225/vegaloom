# 任务：检查 ATG MCP 客户端文档

审查 AgentToolGate 文档里的 MCP 客户端接入表述是否一致。

目标文件：

- `README.md`
- `docs/ai-client-integration.md`
- `examples/client-configs/`

问题：

1. Codex 和 Claude Code 文档是否明确优先推荐 Streamable HTTP `/mcp`？
2. SSE `/mcp/sse` 是否被描述为 fallback，而不是唯一推荐路径？
3. 文档是否避免把 ATG 说成 OS sandbox？
4. 文档是否避免声称 ATG 会自动修改用户全局客户端配置？

输出：

- 计划
- 发现
- 风险
- 建议修改
- 记忆提案

约束：

- v0.1 不编辑目标仓库文件。
- 不自动提交。
- 不自动发布。
