# 计划

1. 读取任务文件，明确目标、约束和需要回答的问题。
2. 按 loop YAML 配置读取目标文件、搜索关键词并收集 git 检查结果。
3. 通过 ToolBroker 记录所有只读工具调用，保持工具边界可审计。
4. 基于证据生成工程审查报告。
5. 执行 reviewer pass 与 eval，确认报告、工具策略和 artifact 完整性。

## 任务摘录

# 任务：检查 Vega Runtime 文档一致性

审查 Vega 自身文档、loop YAML 和 runtime 行为是否一致，确认项目没有被描述成通用 Agent 框架或自动执行平台。

目标文件：

- `README.md`
- `docs/MVP-SCOPE.md`
- `docs/ARCHITECTURE.md`
- `loops/engineering-change.loop.yaml`

问题：

1. 文档是否清楚说明 Vega 是本地 Agent Loop Runtime，而不是 LangGraph/Letta 替代品？
2. 文档是否明确禁止自动 patch、自动 commit、自动 release 和自动长期 memory 写入？
3. YAML 中的工具 allowlist 是否与文档描述一致？
4. 文档是否解释了 state、trace、review、eval 和 memory proposal 的关系？

输出：

- 计划
- 发现
- 风险
- 建议修改
- 记忆提案

约束：

- 本任务只做只读审查。
- 不修改代码或文档。
- 不提交、不发布。
