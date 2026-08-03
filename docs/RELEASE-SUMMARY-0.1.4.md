# Vega v0.1.4 发布摘要

> 版本：v0.1.4

这份摘要用于 GitHub Release 文案。详细变更见
[`RELEASE-NOTES-0.1.4.md`](RELEASE-NOTES-0.1.4.md)，发布步骤见
[`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)。

## 一句话

Vega v0.1.4 是 v0.1.x 的可信执行维护版本：强化外部进程、验证临时目录和路径边界，
同时让 Codex worker/reviewer 在不暴露正文或推理的前提下显示实时安全进度。

## 本版本重点

- execution lease、heartbeat 和输出持久化拒绝符号链接、junction 与 reparse point 改道。
- Windows 验证命令预检收紧不兼容语法，验证临时叶目录改为独占创建。
- auto 模式继续对新增未跟踪文件 fail closed，不把文件数量预算解释为读取授权。
- Codex worker/reviewer 使用 JSONL 提取固定进度事件与最终消息；空、损坏、未知事件或缺少
  最终 `agent_message` 的成功退出不会退回裸文本解析。
- Codex stdout/stderr 分离保存，JSONL 证据与脱敏诊断互不污染。
- 实时提示只包含角色、事件名称和耗时；原始命令、路径、输出、模型正文、推理和工具参数
  不进入终端进度流。
- PR CI 采用 Python 3.12 完整分片与 Windows 平台专项，减少重复执行但不减少节点覆盖。

## 不变边界

- 不增加新的 Runtime、Agent 角色、模型 SDK、数据库、Web UI 或外部运行时依赖。
- Vega 不自动 commit、push、release、删除目标文件或写入长期 Memory。
- reviewer 与 worker 保持会话上下文隔离，但不宣称容器或操作系统级隔离。
- 实时进度是安全事件投影，不是模型思维链、完整会话或原始工具输出。

## 发布动作

本版本发布前要求同一提交通过 GitHub CI、真实 Codex JSONL smoke 与 wheel/sdist smoke，
再由人工创建 annotated `v0.1.4` tag 和 GitHub Release；Vega 自身不会执行这些动作。
